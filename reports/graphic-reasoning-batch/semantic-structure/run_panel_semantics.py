#!/usr/bin/env python3
"""Extract compact semantic JSON one enlarged panel at a time, then summarize relations."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from qwen_client import MODEL, json_candidate  # noqa: E402
from run_batch import forbidden, read_jsonl, write_json, write_panel_crops  # noqa: E402


PANEL_SCHEMA = "graphic-reasoning-panel-semantics-v3"
RELATION_SCHEMA = "graphic-reasoning-relations-v3"
PROMPT_VERSION = "panel-focused-compact-v3"
KINDS = {
    "line", "lineNetwork", "polyline", "polygon", "rect", "circle", "ellipse", "arc", "curve",
    "dot", "grid", "arrow", "textGlyph", "lifeObject", "objectPart", "wireframe", "face",
    "textureSector", "semanticSymbol",
}
ROLES = {"stem", "sequence", "missing", "option", "sample"}
FRAME_KINDS = {"none", "square", "rect", "circle", "grid", "other"}
LINE_CHARACTERS = {"straight", "curved", "mixed", "not_applicable", "unknown"}
FILL_STATES = {"hollow", "solid", "textured", "none", "unknown"}
COUNT_KEYS = {"seg", "curve", "circle", "closed", "dot", "filled", "symbol", "text"}


def option_label(roles: list[str], index: int) -> str | None:
    if roles[index] != "option":
        return None
    number = sum(role == "option" for role in roles[: index + 1])
    return chr(ord("A") + number - 1)


def panel_prompt(job: dict[str, Any], position: int, role: str, label: str | None, errors: list[str] | None = None) -> str:
    correction = f"上一轮错误：{errors}。必须修正。" if errors else ""
    return f"""只观察这个放大面板，不解题。p={position}，role={role}，opt={label}。
视觉语法候选={job.get('visualGrammarCandidate')}，子类候选={job.get('regularSubtypeCandidate')}。
贴裁片边缘的粗框通常只是面板边框，不计入；内缩方框、圆、网格属于题意。直线=straight，曲线/圆弧=curved；直角写 feat；文字写 txt；箭头保留方向；空心不能写 solid；生活物体写名称和 parts。
同类可合并 n；n>1 时 reg 和 ori 必须各有 n 项。网格写 frame.grid=[行,列]，不展开网格线。count 不含布局框和网格线。禁止坐标、尺寸、bbox、点集、SVG、path、答案。{correction}
短键图例：p=面板序号，opt=选项，obj=对象，k=种类，n=数量，reg=相对区域，ori=方向，lc=线型，fill=填充，txt=文字，feat=特征，topo=[组件数,端点数,交点数,闭环数]。
只输出一行严格 JSON，不加 Markdown：
{{"schema":"{PANEL_SCHEMA}","p":{position},"role":"{role}","opt":{json.dumps(label, ensure_ascii=False)},"frame":{{"k":"none|square|rect|circle|grid|other","grid":null}},"obj":[{{"k":"line|lineNetwork|polyline|polygon|rect|circle|ellipse|arc|curve|dot|grid|arrow|textGlyph|lifeObject|objectPart|wireframe|face|textureSector|semanticSymbol","n":1,"reg":["center"],"ori":["vertical"],"lc":"straight|curved|mixed|not_applicable|unknown","fill":"hollow|solid|textured|none|unknown","txt":null,"feat":[],"parts":[]}}],"count":{{"seg":0,"curve":0,"circle":0,"closed":0,"dot":0,"filled":0,"symbol":0,"text":0}},"topo":[null,null,null,null],"facts":["准确短句"],"uncertain":[]}}"""


def validate_panel(candidate: Any, position: int, role: str, label: str | None) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate_not_object"]
    errors = forbidden(candidate)
    if candidate.get("schema") != PANEL_SCHEMA:
        errors.append("schema_version")
    if candidate.get("p") != position:
        errors.append("position")
    if candidate.get("role") != role or role not in ROLES:
        errors.append("role")
    if candidate.get("opt") != label:
        errors.append("option_label")
    frame = candidate.get("frame")
    if not isinstance(frame, dict) or frame.get("k") not in FRAME_KINDS:
        errors.append("frame")
    elif frame.get("k") == "grid":
        grid = frame.get("grid")
        if not isinstance(grid, list) or len(grid) != 2 or any(not isinstance(value, int) or value < 1 for value in grid):
            errors.append("grid_dimensions")
    objects = candidate.get("obj")
    if not isinstance(objects, list) or (role != "missing" and not objects):
        errors.append("objects")
    elif isinstance(objects, list):
        for index, item in enumerate(objects):
            prefix = f"objects[{index}]"
            if not isinstance(item, dict) or item.get("k") not in KINDS:
                errors.append(f"{prefix}.kind")
                continue
            count = item.get("n")
            if not isinstance(count, int) or count < 1:
                errors.append(f"{prefix}.count")
                continue
            regions = item.get("reg")
            orientations = item.get("ori")
            if not isinstance(regions, list) or len(regions) != count:
                errors.append(f"{prefix}.regions")
            if not isinstance(orientations, list) or len(orientations) != count:
                errors.append(f"{prefix}.orientations")
            line_character = item.get("lc")
            fill_state = item.get("fill")
            if line_character not in LINE_CHARACTERS:
                errors.append(f"{prefix}.line_character")
            if fill_state not in FILL_STATES:
                errors.append(f"{prefix}.fill_state")
            if item.get("k") in {"circle", "ellipse", "arc", "curve"} and line_character != "curved":
                errors.append(f"{prefix}.curve_conflict")
            if item.get("k") == "line" and line_character != "straight":
                errors.append(f"{prefix}.line_conflict")
    counts = candidate.get("count")
    if not isinstance(counts, dict) or set(counts) != COUNT_KEYS:
        errors.append("counts")
    elif any(not isinstance(value, int) or value < 0 for value in counts.values()):
        errors.append("count_values")
    topology = candidate.get("topo")
    if not isinstance(topology, list) or len(topology) != 4 or any(value is not None and (not isinstance(value, int) or value < 0) for value in topology):
        errors.append("topology")
    if not isinstance(candidate.get("facts"), list):
        errors.append("facts")
    if not isinstance(candidate.get("uncertain"), list):
        errors.append("uncertainties")
    return sorted(set(errors))


def missing_panel(position: int) -> dict[str, Any]:
    return {
        "schema": PANEL_SCHEMA,
        "p": position,
        "role": "missing",
        "opt": None,
        "frame": {"k": "none", "grid": None},
        "obj": [],
        "count": {key: 0 for key in sorted(COUNT_KEYS)},
        "topo": [None, None, None, None],
        "facts": ["missing panel"],
        "uncertain": [],
    }


def relation_prompt(job: dict[str, Any], panels: list[dict[str, Any]], errors: list[str] | None = None) -> str:
    correction = f"上一轮错误：{errors}。必须修正。" if errors else ""
    compact = json.dumps(panels, ensure_ascii=False, separators=(",", ":"))
    return f"""完整原图和逐面板 JSON 如下。只写题干面板间可直接观察的关系，不解题、不选答案。
layout={job['layoutCandidate']['layoutType']}，roles={job['layoutCandidate']['panelRoles']}。面板 JSON={compact}
关系必须有 obj/count/reg/ori 支撑；可写旋转、翻转、移动、叠加、增减、置换、连接、填充、纹理。禁止坐标、SVG、path、答案。{correction}
只输出一行严格 JSON：
{{"schema":"{RELATION_SCHEMA}","patterns":[{{"type":"rotation|reflection|translation|overlay_union|overlay_intersection|overlay_difference|overlay_xor|count_delta|add_remove|permutation|connection_change|fill_change|texture_change|direction_change|sequence_repeat|analogy_mapping|unknown","from":[1],"to":[2],"subjects":[],"fact":"准确短句","confidence":0.0}}],"uncertain":[]}}"""


def validate_relations(candidate: Any) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate_not_object"]
    errors = forbidden(candidate)
    if candidate.get("schema") != RELATION_SCHEMA:
        errors.append("schema_version")
    if not isinstance(candidate.get("patterns"), list):
        errors.append("patterns")
    if not isinstance(candidate.get("uncertain"), list):
        errors.append("uncertainties")
    return sorted(set(errors))


def checkpoint_key(job: dict[str, Any], position: int, box: Any) -> str:
    material = json.dumps(
        {"source": job["sourceSha256"], "position": position, "box": box, "model": MODEL, "prompt": PROMPT_VERSION},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def solver_document(job: dict[str, Any], panels: list[dict[str, Any]], relation_candidate: dict[str, Any]) -> dict[str, Any]:
    layout = job["layoutCandidate"]
    sanitized_layout = {key: layout.get(key) for key in ("layoutType", "panelCount", "optionCount", "panelRoles", "source")}
    return {
        "schemaVersion": "graphic-reasoning-ai-semantic-v3",
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
        "layout": {**sanitized_layout, "authority": "deterministic_cv_plus_missing_slot_rules"},
        "visualGrammar": job.get("visualGrammarCandidate"),
        "regularSubtype": job.get("regularSubtypeCandidate"),
        "semanticLegend": {
            "p": "panel position", "opt": "option label", "obj": "objects", "k": "kind", "n": "count",
            "reg": "relative regions", "ori": "orientations", "lc": "line character", "fill": "fill state",
            "txt": "visible text", "feat": "visible features", "parts": "semantic parts",
            "count": {"seg": "straight segments excluding layout/grid", "curve": "curves or arcs", "circle": "circles", "closed": "closed shapes", "dot": "dots", "filled": "filled regions", "symbol": "semantic symbols", "text": "text glyphs"},
            "topo": ["components", "endpoints", "junctions", "loops"],
        },
        "panels": panels,
        "crossPanelPatterns": relation_candidate["patterns"],
        "uncertainties": relation_candidate["uncertain"],
        "vectorEnhancement": {"blocking": False, "status": "optional"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=HERE / "queue.jsonl")
    parser.add_argument("--output-root", type=Path, default=HERE / "model-benchmarks" / MODEL)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    selected = set(args.asset)
    jobs = [job for job in read_jsonl(args.queue) if not selected or job["assetId"] in selected]
    jobs = jobs[: max(0, args.limit)]
    rows: list[dict[str, Any]] = []
    for job in jobs:
        asset_root = output_root / "assets" / job["assetId"]
        write_panel_crops(job, asset_root)
        layout = job["layoutCandidate"]
        roles = [str(role) for role in layout["panelRoles"]]
        boxes = layout.get("panelBoxes", [])
        panels: list[dict[str, Any]] = []
        panel_errors: dict[str, list[str]] = {}
        usage: list[dict[str, Any]] = []
        for index, role in enumerate(roles):
            position = index + 1
            label = option_label(roles, index)
            if role == "missing":
                panels.append(missing_panel(position))
                continue
            box = boxes[index] if index < len(boxes) else None
            crop_path = asset_root / "panel-crops" / f"panel-{position:02d}.png"
            panel_root = asset_root / "panel-candidates"
            candidate_path = panel_root / f"panel-{position:02d}.json"
            acceptance_path = panel_root / f"panel-{position:02d}-acceptance.json"
            checkpoint = checkpoint_key(job, position, box)
            if not args.force and candidate_path.is_file() and acceptance_path.is_file():
                acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
                if acceptance.get("checkpointKey") == checkpoint and acceptance.get("schemaStatus") == "validated":
                    panels.append(json.loads(candidate_path.read_text(encoding="utf-8")))
                    print(json.dumps({"assetId": job["assetId"], "position": position, "status": "checkpoint_reused"}, ensure_ascii=False), flush=True)
                    continue
            candidate = None
            errors: list[str] = []
            attempts = 0
            runtime_error = None
            if not crop_path.is_file():
                errors = ["panel_crop_missing"]
            else:
                for attempt in range(2):
                    attempts += 1
                    try:
                        result = json_candidate(
                            panel_prompt(job, position, role, label, errors if attempt else None),
                            [(f"放大面板 {position}", crop_path)],
                            max_tokens=768,
                            timeout=300,
                        )
                        usage.append(result.get("usage") or {})
                        candidate = result["candidate"]
                        errors = validate_panel(candidate, position, role, label)
                        if not errors:
                            break
                    except Exception as exc:
                        runtime_error = f"{type(exc).__name__}: {exc}"
                        errors = [runtime_error]
            write_json(candidate_path, candidate)
            write_json(acceptance_path, {
                "schemaVersion": "graphic-reasoning-panel-acceptance-v3",
                "assetId": job["assetId"], "position": position, "model": MODEL,
                "checkpointKey": checkpoint, "attempts": attempts,
                "schemaStatus": "validated" if candidate is not None and not errors else "invalid",
                "qualityStatus": "unreviewed" if candidate is not None and not errors else "blocked_by_schema",
                "validationErrors": errors, "runtimeError": runtime_error, "status": "repair_pending",
            })
            if candidate is not None and not errors:
                panels.append(candidate)
            else:
                panel_errors[str(position)] = errors
            print(json.dumps({"assetId": job["assetId"], "position": position, "status": "schema_validated" if not errors else "repair_pending", "errors": errors}, ensure_ascii=False), flush=True)
        relation_candidate = None
        relation_errors: list[str] = []
        if not panel_errors and len(panels) == len(roles):
            for attempt in range(2):
                try:
                    result = json_candidate(
                        relation_prompt(job, panels, relation_errors if attempt else None),
                        [("完整题目，仅用于核对面板间可见关系", job["sourcePaths"][0])],
                        max_tokens=1024,
                        timeout=300,
                    )
                    usage.append(result.get("usage") or {})
                    relation_candidate = result["candidate"]
                    relation_errors = validate_relations(relation_candidate)
                    if not relation_errors:
                        break
                except Exception as exc:
                    relation_errors = [f"{type(exc).__name__}: {exc}"]
        else:
            relation_errors = ["panel_schema_incomplete"]
        write_json(asset_root / "raw-relations.json", relation_candidate)
        source_hash_ok = hashlib.sha256(Path(job["sourcePaths"][0]).read_bytes()).hexdigest() == job["sourceSha256"]
        if not panel_errors and relation_candidate is not None and not relation_errors:
            solver = solver_document(job, panels, relation_candidate)
            write_json(asset_root / "solver.json", solver)
            write_json(asset_root / "evaluation.json", {
                "schemaVersion": "graphic-reasoning-semantic-evaluation-v1",
                "assetId": job["assetId"], "solverInputExcludesAnswer": True,
                "questionAnswers": [
                    {"questionId": reference.get("questionId"), "answerLabel": reference.get("answerLabel"), "optionLabels": reference.get("optionLabels", [])}
                    for reference in job["questionReferences"]
                ],
            })
        acceptance = {
            "schemaVersion": "graphic-reasoning-semantic-acceptance-v3",
            "assetId": job["assetId"], "model": MODEL, "sourceSha256": job["sourceSha256"],
            "sourceHash": "pass" if source_hash_ok else "fail",
            "panelSchema": "pass" if not panel_errors and len(panels) == len(roles) else "fail",
            "relationSchema": "pass" if relation_candidate is not None and not relation_errors else "fail",
            "panelErrors": panel_errors, "relationErrors": relation_errors,
            "solverInputExcludesAnswer": True, "geometryAuthority": False,
            "qualityStatus": "unreviewed", "repairReasons": ["factual_quality_not_reviewed"],
            "status": "repair_pending", "usage": usage,
        }
        write_json(asset_root / "acceptance.json", acceptance)
        row = {"assetId": job["assetId"], "status": "repair_pending", "panelSchema": acceptance["panelSchema"], "relationSchema": acceptance["relationSchema"]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    state = {
        "schemaVersion": "graphic-reasoning-panel-semantic-run-state-v3",
        "observedAt": datetime.now(timezone.utc).isoformat(), "model": MODEL,
        "requested": len(jobs), "schemaComplete": sum(row["panelSchema"] == "pass" and row["relationSchema"] == "pass" for row in rows),
        "qualityAccepted": 0, "repairPending": len(rows), "rows": rows,
    }
    write_json(output_root / "run-state.json", state)
    print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
