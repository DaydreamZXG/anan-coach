#!/usr/bin/env python3
"""Focused semantic extraction for the regular-geometry visual grammar."""

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


PANEL_SCHEMA = "graphic-reasoning-regular-panel-v1"
RELATION_SCHEMA = "graphic-reasoning-regular-relations-v1"
PROMPT_VERSION = "regular-geometry-focused-v1"
REGIONS = {"top_left", "top", "top_right", "left", "center", "right", "bottom_left", "bottom", "bottom_right", "spanning", "unknown"}
ORIENTATIONS = {"horizontal", "vertical", "diagonal_up", "diagonal_down", "up", "down", "left", "right", "clockwise", "counterclockwise", "mixed", "none", "unknown"}


def option_label(roles: list[str], index: int) -> str | None:
    if roles[index] != "option":
        return None
    return chr(ord("A") + sum(role == "option" for role in roles[: index + 1]) - 1)


def prompt(job: dict[str, Any], position: int, role: str, label: str | None, errors: list[str] | None = None) -> str:
    correction = f"上轮错误={errors}，必须修正。" if errors else ""
    return f"""只观察这个放大的规则几何面板，不解题。p={position}, role={role}, opt={label}, subtype={job.get('regularSubtypeCandidate')}。
紧贴裁片边缘的框通常是面板布局边框，忽略；内缩方框、圆或网格是题意，写 frame。grid=[行,列]，不展开网格线。
lines：只列独立直线段；在拐角、交点、分叉处断开计段。形状边、frame 边、网格线不重复列入 lines。
curves：每条圆弧/曲线单列；直线绝不能写曲线。shapes：三角形/多边形等闭合图形，可用 n 分组，但 reg/ori 各保留 n 项；right_angle 等写 feat。
dots：每个点单列并写空实。symbols：网格中作为移动符号的箭头、星、五边形、三角形、文字等单列；箭头/文字方向和文字内容不能丢。
topo=[组件数,端点数,交点或分叉数,闭环数]，看不清填 null 并在 uncertain 说明。禁止坐标、尺寸、bbox、点集、SVG、path、答案。{correction}
只输出一行严格 JSON，不加 Markdown：
{{"schema":"{PANEL_SCHEMA}","p":{position},"role":"{role}","opt":{json.dumps(label, ensure_ascii=False)},"frame":{{"k":"none|square|rect|circle|grid|other","grid":null}},"lines":[{{"ori":"horizontal|vertical|diagonal_up|diagonal_down","reg":"center","connects":"简短端点/连接说明","feat":[]}}],"curves":[{{"k":"arc|curve|circle|ellipse","reg":"center","connects":"简短说明","feat":[]}}],"shapes":[{{"k":"triangle|rect|polygon|circle|ellipse|sector|other","n":1,"reg":["center"],"ori":["up"],"fill":"hollow|solid|textured|unknown","feat":[]}}],"dots":[{{"reg":"center","fill":"solid|hollow"}}],"symbols":[{{"k":"arrow|star|pentagon|triangle|letter|number|hanzi|other","reg":"center","ori":"right","fill":"hollow|solid|textured|unknown","txt":null,"feat":[]}}],"topo":[null,null,null,null],"facts":["准确短句"],"uncertain":[]}}
没有的数组必须写 []。"""


def validate(candidate: Any, position: int, role: str, label: str | None) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate_not_object"]
    errors = forbidden(candidate)
    if candidate.get("schema") != PANEL_SCHEMA:
        errors.append("schema")
    if candidate.get("p") != position:
        errors.append("position")
    if candidate.get("role") != role:
        errors.append("role")
    if candidate.get("opt") != label:
        errors.append("option")
    frame = candidate.get("frame")
    if not isinstance(frame, dict) or frame.get("k") not in {"none", "square", "rect", "circle", "grid", "other"}:
        errors.append("frame")
    elif frame.get("k") == "grid":
        grid = frame.get("grid")
        if not isinstance(grid, list) or len(grid) != 2 or any(not isinstance(value, int) or value < 1 for value in grid):
            errors.append("grid")
    for key in ("lines", "curves", "shapes", "dots", "symbols", "facts", "uncertain"):
        if not isinstance(candidate.get(key), list):
            errors.append(key)
    for index, line in enumerate(candidate.get("lines", [])):
        if not isinstance(line, dict) or line.get("ori") not in {"horizontal", "vertical", "diagonal_up", "diagonal_down"} or line.get("reg") not in REGIONS:
            errors.append(f"lines[{index}]")
    for index, curve in enumerate(candidate.get("curves", [])):
        if not isinstance(curve, dict) or curve.get("k") not in {"arc", "curve", "circle", "ellipse"} or curve.get("reg") not in REGIONS:
            errors.append(f"curves[{index}]")
    for index, shape in enumerate(candidate.get("shapes", [])):
        if not isinstance(shape, dict) or shape.get("k") not in {"triangle", "rect", "polygon", "circle", "ellipse", "sector", "other"}:
            errors.append(f"shapes[{index}]")
            continue
        count = shape.get("n")
        if not isinstance(count, int) or count < 1 or not isinstance(shape.get("reg"), list) or len(shape["reg"]) != count or not isinstance(shape.get("ori"), list) or len(shape["ori"]) != count:
            errors.append(f"shapes[{index}].instances")
    for index, dot in enumerate(candidate.get("dots", [])):
        if not isinstance(dot, dict) or dot.get("reg") not in REGIONS or dot.get("fill") not in {"solid", "hollow"}:
            errors.append(f"dots[{index}]")
    for index, symbol in enumerate(candidate.get("symbols", [])):
        if not isinstance(symbol, dict) or symbol.get("k") not in {"arrow", "star", "pentagon", "triangle", "letter", "number", "hanzi", "other"} or symbol.get("reg") not in REGIONS or symbol.get("ori") not in ORIENTATIONS:
            errors.append(f"symbols[{index}]")
    topology = candidate.get("topo")
    if not isinstance(topology, list) or len(topology) != 4 or any(value is not None and (not isinstance(value, int) or value < 0) for value in topology):
        errors.append("topology")
    if role != "missing" and not any(candidate.get(key) for key in ("lines", "curves", "shapes", "dots", "symbols")) and (not isinstance(frame, dict) or frame.get("k") == "none"):
        errors.append("empty_panel")
    return sorted(set(errors))


def missing_panel(position: int) -> dict[str, Any]:
    return {
        "schema": PANEL_SCHEMA, "p": position, "role": "missing", "opt": None,
        "frame": {"k": "none", "grid": None}, "lines": [], "curves": [], "shapes": [], "dots": [], "symbols": [],
        "topo": [None, None, None, None], "facts": ["missing panel"], "uncertain": [],
    }


def relation_prompt(job: dict[str, Any], panels: list[dict[str, Any]], errors: list[str] | None = None) -> str:
    correction = f"上轮错误={errors}，必须修正。" if errors else ""
    payload = json.dumps(panels, ensure_ascii=False, separators=(",", ":"))
    return f"""完整图和逐面板规则几何 JSON 如下。只记录题干之间可观察且由 JSON 支撑的关系，不解题、不选答案。
layout={job['layoutCandidate']['layoutType']}, roles={job['layoutCandidate']['panelRoles']}, panels={payload}
可写旋转、翻转、移动、直线/曲线数量增减、形状置换、连接变化、填充变化、矩阵类比。禁止坐标、SVG、path、答案。{correction}
只输出一行 JSON：{{"schema":"{RELATION_SCHEMA}","patterns":[{{"type":"rotation|reflection|translation|count_delta|add_remove|permutation|connection_change|fill_change|direction_change|sequence_repeat|analogy_mapping|unknown","from":[1],"to":[2],"subjects":[],"fact":"准确短句","confidence":0.0}}],"uncertain":[]}}"""


def validate_relations(candidate: Any) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate_not_object"]
    errors = forbidden(candidate)
    if candidate.get("schema") != RELATION_SCHEMA:
        errors.append("schema")
    if not isinstance(candidate.get("patterns"), list):
        errors.append("patterns")
    if not isinstance(candidate.get("uncertain"), list):
        errors.append("uncertain")
    return sorted(set(errors))


def checkpoint(job: dict[str, Any], position: int, box: Any) -> str:
    material = json.dumps({"sha": job["sourceSha256"], "p": position, "box": box, "model": MODEL, "prompt": PROMPT_VERSION}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def solver_document(job: dict[str, Any], panels: list[dict[str, Any]], relations: dict[str, Any]) -> dict[str, Any]:
    layout = job["layoutCandidate"]
    return {
        "schemaVersion": "graphic-reasoning-ai-semantic-regular-v1", "assetId": job["assetId"],
        "source": {
            "sha256": job["sourceSha256"], "imagePaths": job["sourcePaths"],
            "questionReferences": [{key: ref.get(key) for key in ("questionId", "role", "field", "knowledgePoint", "sourceJsonlLine")} for ref in job["questionReferences"]],
        },
        "presentation": {"mode": "original_image", "imageAuthority": "source_sha256", "vectorRequired": False},
        "layout": {key: layout.get(key) for key in ("layoutType", "panelCount", "optionCount", "panelRoles", "source")},
        "visualGrammar": "regular_geometry", "regularSubtype": job.get("regularSubtypeCandidate"),
        "semanticLegend": {
            "lines": "one entry per straight segment, split at bends/intersections/junctions", "curves": "one entry per curved stroke",
            "shapes": "closed geometric shapes; edges are not duplicated in lines", "symbols": "movable semantic glyphs such as arrows or stars",
            "frame.grid": "[rows, columns]; grid lines are not expanded", "topo": ["components", "endpoints", "junctions", "loops"],
        },
        "panels": panels, "crossPanelPatterns": relations["patterns"], "uncertainties": relations["uncertain"],
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
    jobs = [job for job in read_jsonl(args.queue) if (not selected or job["assetId"] in selected)][: max(0, args.limit)]
    rows: list[dict[str, Any]] = []
    for job in jobs:
        asset_root = output_root / "assets" / job["assetId"]
        if job.get("visualGrammarCandidate") != "regular_geometry":
            write_json(asset_root / "acceptance.json", {"assetId": job["assetId"], "status": "repair_pending", "repairReasons": ["not_regular_geometry"]})
            rows.append({"assetId": job["assetId"], "status": "repair_pending", "panelSchema": "not_applicable", "relationSchema": "not_applicable"})
            continue
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
            crop_path = asset_root / "panel-crops" / f"panel-{position:02d}.png"
            candidate_root = asset_root / "panel-candidates"
            candidate_path = candidate_root / f"panel-{position:02d}.json"
            acceptance_path = candidate_root / f"panel-{position:02d}-acceptance.json"
            panel_checkpoint = checkpoint(job, position, boxes[index] if index < len(boxes) else None)
            if not args.force and candidate_path.is_file() and acceptance_path.is_file():
                prior = json.loads(acceptance_path.read_text(encoding="utf-8"))
                if prior.get("checkpointKey") == panel_checkpoint and prior.get("schemaStatus") == "validated":
                    panels.append(json.loads(candidate_path.read_text(encoding="utf-8")))
                    print(json.dumps({"assetId": job["assetId"], "position": position, "status": "checkpoint_reused"}, ensure_ascii=False), flush=True)
                    continue
            candidate = None
            errors: list[str] = []
            runtime_error = None
            attempts = 0
            if not crop_path.is_file():
                errors = ["panel_crop_missing"]
            else:
                for attempt in range(2):
                    attempts += 1
                    try:
                        result = json_candidate(prompt(job, position, role, label, errors if attempt else None), [(f"面板 {position}", crop_path)], max_tokens=640, timeout=180)
                        usage.append(result.get("usage") or {})
                        candidate = result["candidate"]
                        errors = validate(candidate, position, role, label)
                        if not errors:
                            break
                    except Exception as exc:
                        runtime_error = f"{type(exc).__name__}: {exc}"
                        errors = [runtime_error]
            write_json(candidate_path, candidate)
            write_json(acceptance_path, {
                "schemaVersion": "graphic-reasoning-regular-panel-acceptance-v1", "assetId": job["assetId"], "position": position,
                "model": MODEL, "checkpointKey": panel_checkpoint, "attempts": attempts,
                "schemaStatus": "validated" if candidate is not None and not errors else "invalid", "qualityStatus": "unreviewed",
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
                    result = json_candidate(relation_prompt(job, panels, relation_errors if attempt else None), [("完整题图", job["sourcePaths"][0])], max_tokens=768, timeout=240)
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
            write_json(asset_root / "solver.json", solver_document(job, panels, relation_candidate))
            write_json(asset_root / "evaluation.json", {
                "schemaVersion": "graphic-reasoning-semantic-evaluation-v1", "assetId": job["assetId"], "solverInputExcludesAnswer": True,
                "questionAnswers": [{"questionId": ref.get("questionId"), "answerLabel": ref.get("answerLabel"), "optionLabels": ref.get("optionLabels", [])} for ref in job["questionReferences"]],
            })
        acceptance = {
            "schemaVersion": "graphic-reasoning-regular-semantic-acceptance-v1", "assetId": job["assetId"], "model": MODEL,
            "sourceSha256": job["sourceSha256"], "sourceHash": "pass" if source_hash_ok else "fail",
            "panelSchema": "pass" if not panel_errors and len(panels) == len(roles) else "fail",
            "relationSchema": "pass" if relation_candidate is not None and not relation_errors else "fail",
            "panelErrors": panel_errors, "relationErrors": relation_errors, "solverInputExcludesAnswer": True,
            "geometryAuthority": False, "qualityStatus": "unreviewed", "repairReasons": ["factual_quality_not_reviewed"],
            "status": "repair_pending", "usage": usage,
        }
        write_json(asset_root / "acceptance.json", acceptance)
        row = {"assetId": job["assetId"], "status": "repair_pending", "panelSchema": acceptance["panelSchema"], "relationSchema": acceptance["relationSchema"]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    state = {
        "schemaVersion": "graphic-reasoning-regular-semantic-run-state-v1", "observedAt": datetime.now(timezone.utc).isoformat(),
        "model": MODEL, "requested": len(jobs), "schemaComplete": sum(row["panelSchema"] == "pass" and row["relationSchema"] == "pass" for row in rows),
        "qualityAccepted": 0, "repairPending": len(rows), "rows": rows,
    }
    write_json(output_root / "run-state.json", state)
    print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
