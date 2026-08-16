#!/usr/bin/env python3
"""Generate answer-isolated, AI-readable semantic JSON from full source images."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from qwen_client import MODEL, semantic_candidate  # noqa: E402


KINDS = {
    "line", "polyline", "polygon", "rect", "circle", "ellipse", "arc", "curve", "dot", "grid",
    "arrow", "textGlyph", "lifeObject", "objectPart", "wireframe", "face", "textureSector", "semanticSymbol",
}
ROLES = {"stem", "sequence", "missing", "option", "sample"}
REGIONS = {"top_left", "top", "top_right", "left", "center", "right", "bottom_left", "bottom", "bottom_right", "spanning", "unknown"}
REGION_ALIASES = {"top_center": "top", "bottom_center": "bottom", "center_left": "left", "center_right": "right"}
FORBIDDEN_KEYS = {"x", "y", "width", "height", "bbox", "box", "point", "points", "coordinates", "path", "paths", "svg", "control", "control1", "control2", "answer", "correct_answer", "correct_option", "solution"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def write_panel_crops(job: dict[str, Any], asset_root: Path) -> list[tuple[str, Path]]:
    layout = job.get("layoutCandidate", {})
    boxes = layout.get("panelBoxes", [])
    roles = layout.get("panelRoles", [])
    if not isinstance(boxes, list) or len(boxes) != layout.get("panelCount"):
        return []
    crop_root = asset_root / "panel-crops"
    crop_root.mkdir(parents=True, exist_ok=True)
    evidence: list[tuple[str, Path]] = []
    with Image.open(job["sourcePaths"][0]) as source:
        source = source.convert("RGB")
        for index, box in enumerate(boxes):
            if box is None:
                continue
            try:
                left = int(box["x"])
                top = int(box["y"])
                right = left + int(box["width"])
                bottom = top + int(box["height"])
            except (KeyError, TypeError, ValueError):
                return []
            crop_path = crop_root / f"panel-{index + 1:02d}.png"
            source.crop((left, top, right, bottom)).save(crop_path, format="PNG", optimize=True)
            role = str(roles[index]) if index < len(roles) else "unknown"
            option_index = sum(1 for prior in roles[:index + 1] if prior == "option") if role == "option" else 0
            option_label = chr(ord("A") + option_index - 1) if option_index else ""
            label = f"放大证据：面板 {index + 1}，role={role}"
            if option_label:
                label += f"，option_label={option_label}"
            evidence.append((label, crop_path))
    return evidence


COUNT_FIELDS = {
    "straight_segment_count", "curve_count", "circle_count", "closed_shape_count", "dot_count",
    "filled_region_count", "symbol_count", "text_count",
}


def normalize(candidate: Any, job: dict[str, Any] | None = None) -> tuple[Any, list[dict[str, str]]]:
    value = copy.deepcopy(candidate)
    changes: list[dict[str, str]] = []
    if not isinstance(value, dict):
        return value, changes
    panels = value.get("panels")
    layout = (job or {}).get("layoutCandidate", {})
    if (
        isinstance(panels, list)
        and isinstance(value.get("panel_count"), int)
        and value["panel_count"] == layout.get("panelCount")
        and len(panels) == value["panel_count"] + 1
        and sum(isinstance(panel, dict) and panel.get("role") == "missing" for panel in panels) == 1
        and sum(isinstance(panel, dict) and panel.get("role") == "option" for panel in panels) == value.get("option_count") == layout.get("optionCount")
    ):
        before = value["panel_count"]
        value["panel_count"] = len(panels)
        changes.append({"field": "panel_count", "from": str(before), "to": str(len(panels)), "reason": "model_list_contains_one_unboxed_missing_marker_not_counted_by_cv"})
    for pindex, panel in enumerate(value.get("panels", [])):
        if not isinstance(panel, dict):
            continue
        for eindex, element in enumerate(panel.get("elements", [])):
            if not isinstance(element, dict):
                continue
            before = element.get("relative_region")
            if before in REGION_ALIASES:
                element["relative_region"] = REGION_ALIASES[before]
                changes.append({"field": f"panels[{pindex}].elements[{eindex}].relative_region", "from": str(before), "to": REGION_ALIASES[before]})
    return value, changes


def forbidden(value: Any, prefix: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            location = f"{prefix}.{key}" if prefix else str(key)
            if str(key).lower() in FORBIDDEN_KEYS:
                errors.append(f"forbidden_key:{location}")
            errors.extend(forbidden(nested, location))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            errors.extend(forbidden(nested, f"{prefix}[{index}]"))
    return errors


def validate(candidate: Any, expected_subtype: str | None = None) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate_not_object"]
    errors = forbidden(candidate)
    if candidate.get("schema_version") != "graphic-reasoning-model-semantics-v1":
        errors.append("schema_version")
    panel_count = candidate.get("panel_count")
    option_count = candidate.get("option_count")
    panels = candidate.get("panels")
    if not isinstance(panel_count, int) or not 1 <= panel_count <= 32:
        errors.append("panel_count")
    if not isinstance(option_count, int) or not 0 <= option_count <= 16:
        errors.append("option_count")
    if not isinstance(panels, list) or len(panels) != panel_count:
        errors.append("panels")
    else:
        positions = []
        for pindex, panel in enumerate(panels):
            if not isinstance(panel, dict):
                errors.append(f"panels[{pindex}]")
                continue
            positions.append(panel.get("position"))
            if panel.get("role") not in ROLES:
                errors.append(f"panels[{pindex}].role")
            elements = panel.get("elements")
            if not isinstance(elements, list):
                errors.append(f"panels[{pindex}].elements")
                continue
            if panel.get("role") != "missing" and not elements:
                errors.append(f"panels[{pindex}].elements_empty")
            for eindex, element in enumerate(elements):
                if not isinstance(element, dict) or element.get("kind") not in KINDS:
                    errors.append(f"panels[{pindex}].elements[{eindex}].kind")
                    continue
                if not isinstance(element.get("count"), int) or element["count"] < 1:
                    errors.append(f"panels[{pindex}].elements[{eindex}].count")
                elif element["count"] > 1:
                    instances = element.get("instances")
                    if not isinstance(instances, list) or len(instances) != element["count"]:
                        errors.append(f"panels[{pindex}].elements[{eindex}].instances")
                if element.get("relative_region") not in REGIONS:
                    errors.append(f"panels[{pindex}].elements[{eindex}].relative_region")
                kind = element.get("kind")
                line_character = element.get("line_character")
                fill_state = element.get("fill_state")
                if kind in {"circle", "ellipse", "arc", "curve"} and line_character != "curved":
                    errors.append(f"panels[{pindex}].elements[{eindex}].line_character_kind_conflict")
                if kind == "line" and line_character != "straight":
                    errors.append(f"panels[{pindex}].elements[{eindex}].line_character_kind_conflict")
                if kind in {"line", "polyline", "arc", "curve", "grid"} and fill_state not in {"none", "unknown"}:
                    errors.append(f"panels[{pindex}].elements[{eindex}].fill_state_kind_conflict")
            counts = panel.get("counts")
            if not isinstance(counts, dict) or set(counts) != COUNT_FIELDS:
                errors.append(f"panels[{pindex}].counts")
            elif any(value is not None and (not isinstance(value, int) or value < 0) for value in counts.values()):
                errors.append(f"panels[{pindex}].counts_values")
            if panel.get("role") != "missing" and expected_subtype == "straight_line_network" and (not isinstance(counts, dict) or not isinstance(counts.get("straight_segment_count"), int) or counts["straight_segment_count"] < 1):
                errors.append(f"panels[{pindex}].straight_segment_count_required")
            if panel.get("role") != "missing" and expected_subtype == "line_curve_composition" and (not isinstance(counts, dict) or not isinstance(counts.get("curve_count"), int) or counts["curve_count"] < 1):
                errors.append(f"panels[{pindex}].curve_count_required")
            if panel.get("role") != "missing" and expected_subtype == "circle_composition" and (not isinstance(counts, dict) or not isinstance(counts.get("circle_count"), int) or counts["circle_count"] < 1):
                errors.append(f"panels[{pindex}].circle_count_required")
            if panel.get("role") != "missing" and expected_subtype == "closed_polygon_composition" and (not isinstance(counts, dict) or not isinstance(counts.get("closed_shape_count"), int) or counts["closed_shape_count"] < 1):
                errors.append(f"panels[{pindex}].closed_shape_count_required")
            if panel.get("role") != "missing" and expected_subtype == "grid_partition" and not any(isinstance(element, dict) and element.get("kind") == "grid" for element in elements):
                errors.append(f"panels[{pindex}].grid_element_required")
        if positions != list(range(1, len(panels) + 1)):
            errors.append("panel_positions")
        if sum(isinstance(panel, dict) and panel.get("role") == "option" for panel in panels) != option_count:
            errors.append("option_role_count")
    if not isinstance(candidate.get("cross_panel_patterns"), list):
        errors.append("cross_panel_patterns")
    if not isinstance(candidate.get("uncertainties"), list):
        errors.append("uncertainties")
    return sorted(set(errors))


def prompt(job: dict[str, Any], prior_errors: list[str] | None = None) -> str:
    layout = job["layoutCandidate"]
    correction = f"上一轮格式错误为 {prior_errors}，本轮必须修正。" if prior_errors else ""
    return f"""你正在把一整道公务员考试图形推理题转换为可供另一个 AI 解题的语义 JSON。不要解题，不要选择答案。
确定性分框候选：layout={layout['layoutType']}，panel_count={layout['panelCount']}，option_count={layout['optionCount']}，roles={layout['panelRoles']}。
视觉子类候选：{job.get('regularSubtypeCandidate')}；视觉语法候选：{job.get('visualGrammarCandidate')}。
先按从左到右、从上到下顺序观察所有题干格、问号缺失格和选项格。若你看到的数量与候选不同，按实际视觉输出，并在 uncertainties 说明冲突。
必须严格区分直线、折线、曲线、圆弧、圆、椭圆、闭合多边形、空心轮廓、实心区域、纹理、网格、点、箭头、文字、生活物体部件和立体线框。
每个非缺失面板必须填写 counts，逐条数清可见直线段、独立曲线、圆、闭合图形、点、实心区域、符号和文字。多条连接线合成 polyline 时，straight_segment_count 仍须填写真实线段数。
同类图形只要位置或朝向不同，就必须分别列出；若确实用 count>1 分组，必须提供等长 instances 数组，每个实例分别写 relative_region 与 orientation。网格是题意的一部分，不得当背景省略。
只写相对方位和语义关系；严禁输出坐标、尺寸、边界框、点集、SVG、路径或控制点。严禁输出答案、正确选项或解题结论。{correction}
只输出严格 JSON：
{{
  "schema_version": "graphic-reasoning-model-semantics-v1",
  "visual_grammar": "简短类别",
  "panel_count": 整数,
  "option_count": 整数,
  "panels": [{{
    "position": 1,
    "role": "stem|sequence|missing|option|sample",
    "option_label": null,
    "elements": [{{
      "id": "p1e1",
      "kind": "line|polyline|polygon|rect|circle|ellipse|arc|curve|dot|grid|arrow|textGlyph|lifeObject|objectPart|wireframe|face|textureSector|semanticSymbol",
      "label": "简短且可区分的名称",
      "text_content": null,
      "count": 1,
      "instances": [],
      "relative_region": "top_left|top|top_right|left|center|right|bottom_left|bottom|bottom_right|spanning|unknown",
      "orientation": "horizontal|vertical|diagonal_up|diagonal_down|clockwise|counterclockwise|mixed|none|unknown",
      "line_character": "straight|curved|mixed|not_applicable|unknown",
      "fill_state": "hollow|solid|textured|none|unknown",
      "texture": "none|dots|grid|diagonal|crosshatch|solid|unknown",
      "parts": [],
      "features": []
    }}],
    "relations": [{{"type": "connected|touching|intersecting|inside|contains|overlapping|disjoint|aligned|parallel|perpendicular|holds|pointsTo|frontOf|behind", "subjects": ["p1e1", "p1e2"], "description": "简短说明"}}],
    "topology": {{"component_count": null, "endpoint_count": null, "junction_count": null, "loop_count": null, "notes": []}},
    "counts": {{"straight_segment_count": 0, "curve_count": 0, "circle_count": 0, "closed_shape_count": 0, "dot_count": 0, "filled_region_count": 0, "symbol_count": 0, "text_count": 0}},
    "observations": []
  }}],
  "cross_panel_patterns": [{{
    "type": "rotation|reflection|translation|overlay_union|overlay_intersection|overlay_difference|overlay_xor|count_delta|add_remove|permutation|connection_change|fill_change|texture_change|direction_change|sequence_repeat|analogy_mapping|unknown",
    "from_panels": [1], "to_panels": [2], "subjects": [], "description": "只写可观察变化，不选答案", "confidence": 0.0
  }}],
  "uncertainties": []
}}
circle/ellipse/arc/curve 的 line_character 必须为 curved；line 必须为 straight；开放 line/polyline/arc/curve 与 grid 的 fill_state 必须为 none。
cross_panel_patterns 只写可由各 panel 元素和 counts 直接支持的变化；所有 fill_state 相同时禁止写 fill_change，不确定就省略并写入 uncertainties。
"""


def solver_document(job: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    layout_candidate = job["layoutCandidate"]
    solver_layout_candidate = {
        key: layout_candidate.get(key)
        for key in ("layoutType", "panelCount", "optionCount", "panelRoles", "source")
    }
    missing_marker_recovery = candidate["panel_count"] == layout_candidate["panelCount"] + 1 and candidate["option_count"] == layout_candidate["optionCount"] and sum(panel.get("role") == "missing" for panel in candidate["panels"] if isinstance(panel, dict)) == 1
    count_agreement = candidate["panel_count"] == layout_candidate["panelCount"] and candidate["option_count"] == layout_candidate["optionCount"]
    vector_acceptance_path = ROOT / "assets" / job["assetId"] / "acceptance.json"
    vector_status = "not_attempted"
    if vector_acceptance_path.is_file():
        vector_acceptance = json.loads(vector_acceptance_path.read_text(encoding="utf-8"))
        vector_status = "available" if vector_acceptance.get("repairStatus", {}).get("status") == "clear" else "repair_pending"
    return {
        "schemaVersion": "graphic-reasoning-ai-semantic-v1",
        "assetId": job["assetId"],
        "source": {
            "sha256": job["sourceSha256"],
            "imagePaths": job["sourcePaths"],
            "questionReferences": [
                {key: reference.get(key) for key in ("questionId", "role", "field", "knowledgePoint", "sourceJsonlLine")}
                for reference in job["questionReferences"]
            ],
        },
        "presentation": {"mode": "original_image", "imageAuthority": "source_sha256", "vectorRequired": False},
        "layout": {
            "layoutType": layout_candidate["layoutType"],
            "panelCount": candidate["panel_count"],
            "optionCount": candidate["option_count"],
            "authority": "cv_model_agreement" if count_agreement else "qwen_missing_marker_recovery" if missing_marker_recovery else "review_required",
            "cvCandidate": solver_layout_candidate,
        },
        "visualGrammar": candidate.get("visual_grammar"),
        "panels": candidate["panels"],
        "crossPanelPatterns": candidate["cross_panel_patterns"],
        "uncertainties": candidate["uncertainties"],
        "vectorEnhancement": {"blocking": False, "status": vector_status},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=HERE / "queue.jsonl")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=HERE,
        help="Write generated assets and run-state here; use a model-specific directory for comparisons.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    queue = read_jsonl(args.queue)
    selected = set(args.asset)
    targets = [row for row in queue if (not selected or row["assetId"] in selected)]
    targets = targets[:max(0, args.limit)]
    run_rows: list[dict[str, Any]] = []
    for job in targets:
        asset_root = output_root / "assets" / job["assetId"]
        panel_evidence = write_panel_crops(job, asset_root)
        acceptance_path = asset_root / "acceptance.json"
        if not args.force and acceptance_path.is_file():
            existing = json.loads(acceptance_path.read_text(encoding="utf-8"))
            if existing.get("checkpointKey") == job["checkpointKey"] and existing.get("schemaStatus") == "validated":
                run_rows.append({"assetId": job["assetId"], "status": "checkpoint_reused"})
                continue
        candidate = None
        raw_candidate = None
        normalizations: list[dict[str, str]] = []
        errors: list[str] = []
        attempts = 0
        runtime_error = None
        for attempt in range(2):
            attempts += 1
            try:
                result = semantic_candidate(
                    job["sourcePaths"][0],
                    prompt(job, errors if attempt else None),
                    timeout=600,
                    panel_evidence=panel_evidence,
                )
                raw_candidate = result.get("candidate")
                candidate, normalizations = normalize(raw_candidate, job)
                errors = validate(candidate, str(job.get("regularSubtypeCandidate") or ""))
                if not errors:
                    break
            except Exception as exc:
                runtime_error = f"{type(exc).__name__}: {exc}"
                errors = [runtime_error]
        source_hash_ok = hashlib.sha256(Path(job["sourcePaths"][0]).read_bytes()).hexdigest() == job["sourceSha256"]
        if candidate is not None and not errors:
            solver = solver_document(job, candidate)
            write_json(asset_root / "solver.json", solver)
            evaluation = {
                "schemaVersion": "graphic-reasoning-semantic-evaluation-v1",
                "assetId": job["assetId"],
                "solverInputExcludesAnswer": True,
                "questionAnswers": [
                    {"questionId": reference.get("questionId"), "answerLabel": reference.get("answerLabel"), "optionLabels": reference.get("optionLabels", [])}
                    for reference in job["questionReferences"]
                ],
            }
            write_json(asset_root / "evaluation.json", evaluation)
        else:
            solver = None
        layout = job["layoutCandidate"]
        missing_marker_recovery = bool(candidate and candidate.get("panel_count") == layout["panelCount"] + 1 and candidate.get("option_count") == layout["optionCount"] and sum(panel.get("role") == "missing" for panel in candidate.get("panels", []) if isinstance(panel, dict)) == 1)
        panel_agreement = bool(candidate and (candidate.get("panel_count") == layout["panelCount"] or missing_marker_recovery))
        option_agreement = bool(candidate and candidate.get("option_count") == layout["optionCount"])
        gates = {
            "sourceHash": "pass" if source_hash_ok else "fail",
            "modelSchema": "pass" if candidate is not None and not errors else "fail",
            "panelCountAgreement": "pass" if panel_agreement else "fail",
            "panelCountResolution": "pass" if not missing_marker_recovery or any(change.get("reason") == "model_list_contains_one_unboxed_missing_marker_not_counted_by_cv" for change in normalizations) else "fail",
            "optionCountAgreement": "pass" if option_agreement else "fail",
            "answerIsolation": "pass" if candidate is not None and not forbidden(candidate) else "fail",
            "originalImagePresentation": "pass",
            "vectorEnhancement": "not_applicable",
        }
        validated = all(value in {"pass", "not_applicable"} for value in gates.values())
        acceptance = {
            "schemaVersion": "graphic-reasoning-semantic-acceptance-v1",
            "assetId": job["assetId"],
            "sourceSha256": job["sourceSha256"],
            "checkpointKey": job["checkpointKey"],
            "model": MODEL,
            "attempts": attempts,
            "geometryAuthority": False,
            "panelCropEvidence": {
                "available": len(panel_evidence) == sum(isinstance(box, dict) for box in layout.get("panelBoxes", [])),
                "count": len(panel_evidence),
                "missingSlotsWithoutCrop": sum(box is None for box in layout.get("panelBoxes", [])),
                "source": "deterministic_cv_panel_boxes",
            },
            "normalizations": normalizations,
            "validationErrors": errors,
            "runtimeError": runtime_error,
            "gates": gates,
            "schemaStatus": "validated" if validated else "invalid",
            "qualityStatus": "unreviewed" if validated else "blocked_by_schema",
            "repairReasons": ["factual_quality_not_reviewed"] if validated else ["schema_or_runtime_failure"],
            "status": "repair_pending",
            "finalVectorAcceptance": False,
        }
        write_json(acceptance_path, acceptance)
        write_json(asset_root / "raw-model-candidate.json", raw_candidate)
        run_rows.append({"assetId": job["assetId"], "status": acceptance["status"], "attempts": attempts, "validationErrors": errors})
    state = {
        "schemaVersion": "graphic-reasoning-semantic-run-state-v1",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "requested": len(targets),
        "schemaValidated": sum(
            (output_root / "assets" / row["assetId"] / "acceptance.json").is_file()
            and json.loads((output_root / "assets" / row["assetId"] / "acceptance.json").read_text(encoding="utf-8")).get("schemaStatus") == "validated"
            for row in run_rows
        ),
        "qualityAccepted": 0,
        "repairPending": len(run_rows),
        "rows": run_rows,
    }
    write_json(output_root / "run-state.json", state)
    print(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
