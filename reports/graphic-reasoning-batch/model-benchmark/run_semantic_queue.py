#!/usr/bin/env python3
"""Run Qwen semantic-only candidates for the frozen representative queue.

The model may name and group visual elements, but is never a geometry source.
Coordinates, boxes, SVG, paths, and control points are rejected recursively.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
BATCH_ROOT = HERE.parent
sys.path.insert(0, str(BATCH_ROOT))
from qwen_client import MODEL, semantic_candidate  # noqa: E402


ALLOWED_GRAMMARS = {
    "regular_geometry",
    "curve_circle_arc",
    "glyph_life_object",
    "folded_3d",
    "textured_geometry",
    "textured_pie",
    "rare_vector",
}
ALLOWED_KINDS = {
    "line", "polyline", "polygon", "rect", "circle", "ellipse", "arc", "curve",
    "dot", "grid", "arrow", "textGlyph", "lifeObject", "objectPart", "wireframe",
    "face", "textureSector", "semanticSymbol",
}
FORBIDDEN_KEYS = {
    "x", "y", "width", "height", "bbox", "box", "point", "points", "coordinate",
    "coordinates", "path", "paths", "svg", "control", "control1", "control2", "d",
}
ALLOWED_RELATIVE_REGIONS = {
    "top_left", "top", "top_right", "left", "center", "right",
    "bottom_left", "bottom", "bottom_right", "spanning", "unknown",
}
GRAMMARS_BY_SUBTYPE = {
    "line_curve_composition": {"regular_geometry", "curve_circle_arc"},
    "straight_line_network": {"regular_geometry"},
    "circle_composition": {"regular_geometry", "curve_circle_arc"},
    "closed_polygon_composition": {"regular_geometry"},
    "grid_partition": {"regular_geometry"},
}
RELATIVE_REGION_ALIASES = {
    "top_center": "top",
    "bottom_center": "bottom",
    "center_left": "left",
    "center_right": "right",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def forbidden_keys(value: Any, prefix: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            location = f"{prefix}.{key}" if prefix else str(key)
            if normalized in FORBIDDEN_KEYS:
                errors.append(f"forbidden_key:{location}")
            errors.extend(forbidden_keys(nested, location))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            errors.extend(forbidden_keys(nested, f"{prefix}[{index}]"))
    return errors


def normalize_candidate(candidate: Any) -> tuple[Any, list[dict[str, str]]]:
    normalized = copy.deepcopy(candidate)
    changes: list[dict[str, str]] = []
    if not isinstance(normalized, dict) or not isinstance(normalized.get("elements"), list):
        return normalized, changes
    for index, element in enumerate(normalized["elements"]):
        if not isinstance(element, dict):
            continue
        before = element.get("relative_region")
        after = RELATIVE_REGION_ALIASES.get(before)
        if after:
            element["relative_region"] = after
            changes.append({"field": f"elements[{index}].relative_region", "from": str(before), "to": after})
    return normalized, changes


def validate_candidate(candidate: Any, expected_subtype: str | None = None) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate_not_object"]
    errors = forbidden_keys(candidate)
    if candidate.get("schema_version") != "graphic-semantic-candidate-v1":
        errors.append("schema_version")
    if candidate.get("authority") != "semantic_candidate":
        errors.append("authority")
    if candidate.get("visual_grammar") not in ALLOWED_GRAMMARS:
        errors.append("visual_grammar")
    elif expected_subtype in GRAMMARS_BY_SUBTYPE and candidate.get("visual_grammar") not in GRAMMARS_BY_SUBTYPE[expected_subtype]:
        errors.append("visual_grammar_subtype_conflict")
    elements = candidate.get("elements")
    if not isinstance(elements, list) or len(elements) > 64:
        errors.append("elements")
    else:
        for index, element in enumerate(elements):
            if not isinstance(element, dict):
                errors.append(f"elements[{index}]")
                continue
            if element.get("kind") not in ALLOWED_KINDS:
                errors.append(f"elements[{index}].kind")
            if not isinstance(element.get("count"), int) or element["count"] < 1:
                errors.append(f"elements[{index}].count")
            if element.get("relative_region") not in ALLOWED_RELATIVE_REGIONS:
                errors.append(f"elements[{index}].relative_region")
            kind = element.get("kind")
            line_character = element.get("line_character")
            fill_state = element.get("fill_state")
            if kind in {"circle", "ellipse", "arc", "curve"} and line_character != "curved":
                errors.append(f"elements[{index}].line_character_kind_conflict")
            if kind == "line" and line_character != "straight":
                errors.append(f"elements[{index}].line_character_kind_conflict")
            if kind in {"line", "polyline", "arc", "curve", "grid"} and fill_state not in {"none", "unknown"}:
                errors.append(f"elements[{index}].fill_state_kind_conflict")
    relations = candidate.get("relations")
    if not isinstance(relations, list):
        errors.append("relations")
    if not isinstance(candidate.get("topology_candidates"), dict):
        errors.append("topology_candidates")
    if not isinstance(candidate.get("uncertainties"), list):
        errors.append("uncertainties")
    return sorted(set(errors))


def prompt(job: dict[str, Any]) -> str:
    allowed_grammars = sorted(GRAMMARS_BY_SUBTYPE.get(job["regularSubtype"], ALLOWED_GRAMMARS))
    return f"""你正在观察公务员考试图形推理题的单个面板。该面板预分类为 {job['regularSubtype']}。
只做可供后续算法核验的视觉语义候选；严禁输出任何坐标、边界框、点、SVG、路径、贝塞尔控制点或画布尺寸。
必须区分直线、折线、曲线、圆弧、圆、椭圆、闭合多边形、空心轮廓、实心区域、纹理、网格、点、箭头和文字；看不清就写入 uncertainties，不能猜。
visual_grammar 只能从 {allowed_grammars} 中选择，不能填写预分类子类名称。
circle/ellipse/arc/curve 的 line_character 必须是 curved，line 必须是 straight；开放的 line/polyline/arc/curve 和 grid 的 fill_state 必须是 none，不能把“没有填充”写成 hollow。
只输出严格 JSON，结构如下：
{{
  "schema_version": "graphic-semantic-candidate-v1",
  "authority": "semantic_candidate",
  "visual_grammar": "regular_geometry|curve_circle_arc|glyph_life_object|folded_3d|textured_geometry|textured_pie|rare_vector",
  "elements": [{{
    "id": "e1",
    "kind": "line|polyline|polygon|rect|circle|ellipse|arc|curve|dot|grid|arrow|textGlyph|lifeObject|objectPart|wireframe|face|textureSector|semanticSymbol",
    "count": 1,
    "label": null,
    "text_content": null,
    "fill_state": "hollow|solid|textured|none|unknown",
    "line_character": "straight|curved|mixed|not_applicable|unknown",
    "relative_region": "top_left|top|top_right|left|center|right|bottom_left|bottom|bottom_right|spanning|unknown",
    "orientation_bucket": "horizontal|vertical|diagonal_up|diagonal_down|circular|mixed|unknown",
    "parts": [],
    "observations": []
  }}],
  "relations": [{{"type": "connected|touching|intersecting|inside|contains|overlapping|disjoint|aligned|parallel|perpendicular", "subjects": ["e1", "e2"], "confidence": 0.0}}],
  "topology_candidates": {{"component_count": null, "endpoint_count": null, "junction_count": null, "loop_count": null, "notes": []}},
  "uncertainties": []
}}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=HERE / "runtime/run-queue.jsonl")
    parser.add_argument("--output-root", type=Path, default=HERE / "semantic-candidates")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--asset", action="append", default=[])
    args = parser.parse_args()
    jobs = [row for row in read_jsonl(args.queue) if row.get("stage") == "representative_first" and row.get("status") == "blocked_waiting_gpu_ready"]
    if args.asset:
        selected = set(args.asset)
        jobs = [row for row in jobs if row.get("assetId") in selected]
    jobs = jobs[: max(0, args.limit)]
    summary_rows: list[dict[str, Any]] = []
    for job in jobs:
        output_path = args.output_root / job["assetId"] / f"{job['panelId']}.json"
        try:
            result = semantic_candidate(job["modelInput"], prompt(job), timeout=90)
            raw_candidate = result.get("candidate")
            candidate, normalizations = normalize_candidate(raw_candidate)
            errors = validate_candidate(candidate, str(job.get("regularSubtype") or ""))
            payload = {
                "schemaVersion": "graphic-semantic-observation-v1",
                "assetId": job["assetId"],
                "panelId": job["panelId"],
                "role": job["role"],
                "regularSubtype": job["regularSubtype"],
                "sourceSha256": job["sourceSha256"],
                "modelInputSha256": job["modelInputSha256"],
                "model": MODEL,
                "geometryAuthority": False,
                "status": "valid_semantic_candidate" if not errors else "invalid_semantic_candidate",
                "validationErrors": errors,
                "normalizations": normalizations,
                "rawCandidate": raw_candidate,
                "candidate": candidate,
            }
        except Exception as exc:
            payload = {
                "schemaVersion": "graphic-semantic-observation-v1",
                "assetId": job["assetId"],
                "panelId": job["panelId"],
                "sourceSha256": job["sourceSha256"],
                "modelInputSha256": job["modelInputSha256"],
                "model": MODEL,
                "geometryAuthority": False,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        write_json(output_path, payload)
        summary_rows.append({
            "assetId": payload["assetId"],
            "panelId": payload["panelId"],
            "status": payload["status"],
            "output": str(output_path),
            "outputSha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        })
    summary = {
        "schemaVersion": "graphic-semantic-run-v1",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "requested": len(jobs),
        "valid": sum(row["status"] == "valid_semantic_candidate" for row in summary_rows),
        "invalid": sum(row["status"] == "invalid_semantic_candidate" for row in summary_rows),
        "errors": sum(row["status"] == "error" for row in summary_rows),
        "geometryAuthority": False,
        "rows": summary_rows,
    }
    write_json(args.output_root / "semantic-run-state.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
