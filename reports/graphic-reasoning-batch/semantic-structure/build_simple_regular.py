#!/usr/bin/env python3
"""Finish two simple regular-geometry families with deterministic geometry.

Supported in v1:
- closed_polygon_composition: deterministic triangle primitives and right angles
- grid_partition: deterministic 4x4 grid/cell geometry plus VLM symbol labels
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from qwen_client import MODEL, json_candidate  # noqa: E402
from run_batch import read_jsonl, write_json, write_panel_crops  # noqa: E402


SUPPORTED = {"closed_polygon_composition", "grid_partition"}
SEMANTIC_OUTPUTS = ("solver.json", "evaluation.json", "geometry-evidence.json", "raw-relations.json")


def clear_stale_semantic_outputs(asset_root: Path) -> None:
    """Prevent an older model candidate from surviving an unsupported rerun."""
    for name in SEMANTIC_OUTPUTS:
        path = asset_root / name
        if path.is_file():
            path.unlink()


def option_label(roles: list[str], index: int) -> str | None:
    if roles[index] != "option":
        return None
    return chr(ord("A") + sum(role == "option" for role in roles[: index + 1]) - 1)


def relative_region(x: float, y: float) -> str:
    # Existing deterministic primitives use a normalized 0..1000 panel space.
    # This family is explicitly a two-by-two composition, so the panel
    # midlines—not an arbitrary dead band—define the four semantic regions.
    vertical = "top" if y < 500 else "bottom"
    horizontal = "left" if x < 500 else "right"
    return f"{vertical}_{horizontal}"


def right_angle(points: list[dict[str, Any]]) -> tuple[int, float]:
    best_index, best_error = 0, float("inf")
    for index in range(3):
        pivot = np.array([points[index]["x"], points[index]["y"]], dtype=float)
        first = np.array([points[(index + 1) % 3]["x"], points[(index + 1) % 3]["y"]], dtype=float) - pivot
        second = np.array([points[(index + 2) % 3]["x"], points[(index + 2) % 3]["y"]], dtype=float) - pivot
        cosine = float(np.dot(first, second) / (np.linalg.norm(first) * np.linalg.norm(second)))
        angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        error = abs(angle - 90.0)
        if error < best_error:
            best_index, best_error = index, error
    return best_index, best_error


def corner_within_shape(point: dict[str, Any], points: list[dict[str, Any]]) -> str:
    xs = [float(item["x"]) for item in points]
    ys = [float(item["y"]) for item in points]
    horizontal = "left" if float(point["x"]) <= (min(xs) + max(xs)) / 2 else "right"
    vertical = "top" if float(point["y"]) <= (min(ys) + max(ys)) / 2 else "bottom"
    return f"{vertical}_{horizontal}"


def line_orientation(first: dict[str, Any], second: dict[str, Any]) -> str:
    dx = float(second["x"]) - float(first["x"])
    dy = float(second["y"]) - float(first["y"])
    if abs(dx) > abs(dy) * 3:
        return "horizontal"
    if abs(dy) > abs(dx) * 3:
        return "vertical"
    return "diagonal_down" if dx * dy > 0 else "diagonal_up"


def triangle_signature(instances: list[dict[str, Any]]) -> str:
    ordered = sorted(instances, key=lambda item: str(item["region"]))
    return "|".join(
        f"{item['region']}:right_angle={item['rightAngleAt']},hypotenuse={item['hypotenuse']},fill={item['fill']}"
        for item in ordered
    )


def triangle_panels(job: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    structured_path = ROOT / "assets" / job["assetId"] / "structured.json"
    if not structured_path.is_file():
        return [], {}, ["deterministic_structure_missing"]
    structured = json.loads(structured_path.read_text(encoding="utf-8"))
    source_panels = structured.get("panels", [])
    roles = [str(role) for role in job["layoutCandidate"]["panelRoles"]]
    if len(source_panels) != len(roles):
        return [], {}, ["panel_count_mismatch"]
    panels: list[dict[str, Any]] = []
    evidence_panels: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, (role, source_panel) in enumerate(zip(roles, source_panels)):
        position = index + 1
        label = option_label(roles, index)
        if role == "missing":
            panels.append({"p": position, "role": role, "opt": None, "frame": "none", "shapes": [], "facts": ["missing panel"]})
            evidence_panels.append({"p": position, "role": role, "polygonCount": 0})
            continue
        polygons = [primitive for primitive in source_panel.get("primitives", []) if primitive.get("type") == "polygon" and len(primitive.get("points", [])) == 3]
        if len(polygons) != 4:
            errors.append(f"p{position}:expected_4_triangles_got_{len(polygons)}")
            continue
        instances: list[dict[str, Any]] = []
        instance_evidence: list[dict[str, Any]] = []
        for primitive_index, polygon in enumerate(polygons):
            points = polygon["points"]
            center_x = sum(float(point["x"]) for point in points) / 3
            center_y = sum(float(point["y"]) for point in points) / 3
            right_index, angle_error = right_angle(points)
            other = [item for item in range(3) if item != right_index]
            instances.append({
                "region": relative_region(center_x, center_y),
                "rightAngleAt": corner_within_shape(points[right_index], points),
                "hypotenuse": line_orientation(points[other[0]], points[other[1]]),
                "fill": "hollow",
            })
            instance_evidence.append({"primitiveIndex": primitive_index, "vertices": 3, "rightAngleErrorDegrees": round(angle_error, 3)})
            if angle_error > 8:
                errors.append(f"p{position}:right_angle_error_{primitive_index}")
        panels.append({
            "p": position, "role": role, "opt": label, "frame": "none",
            "shapes": [{"kind": "rightTriangle", "count": 4, "instances": instances}],
            "canonicalSignature": triangle_signature(instances),
            "topology": {"components": 4, "endpoints": 0, "junctions": 0, "loops": 4},
            "facts": ["exactly four separate hollow right triangles in a two-by-two layout"],
        })
        evidence_panels.append({"p": position, "role": role, "polygonCount": 4, "instances": instance_evidence})
    return panels, {"method": "deterministic_polygon_vertices_v1", "panels": evidence_panels}, errors


def line_centers(mask: np.ndarray, axis: int, minimum: int) -> list[int]:
    projection = np.count_nonzero(mask, axis=axis)
    indexes = np.flatnonzero(projection >= minimum)
    groups: list[list[int]] = []
    for value in indexes.tolist():
        if not groups or value > groups[-1][-1] + 1:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [int(round(sum(group) / len(group))) for group in groups]


def cell_for(center: float, lines: list[int]) -> int | None:
    for index in range(len(lines) - 1):
        if lines[index] <= center <= lines[index + 1]:
            return index + 1
    return None


def grid_symbol_geometry_array(gray: np.ndarray, symbol_root: Path | None, position: int) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    ink = (gray < 180).astype(np.uint8) * 255
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (20, 1)))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 20)))
    horizontal_lines = line_centers(horizontal, axis=1, minimum=int(gray.shape[1] * 0.75))
    vertical_lines = line_centers(vertical, axis=0, minimum=int(gray.shape[0] * 0.75))
    errors: list[str] = []
    if len(horizontal_lines) != 5 or len(vertical_lines) != 5:
        errors.append(f"grid_lines:h{len(horizontal_lines)}_v{len(vertical_lines)}")
    # Morphological opening is used only to locate full-width/full-height grid
    # lines.  Its raw mask can also contain a pentagon base or arrow shaft and
    # must never be subtracted from the symbol.  Rebuild the grid exclusively
    # from the accepted projection centers before removing it.
    grid = np.zeros_like(ink)
    for y in horizontal_lines:
        cv2.line(grid, (0, y), (gray.shape[1] - 1, y), 255, 3)
    for x in vertical_lines:
        cv2.line(grid, (x, 0), (x, gray.shape[0] - 1), 255, 3)
    residual = cv2.bitwise_and(ink, cv2.bitwise_not(grid))
    grouped = cv2.dilate(residual, cv2.getStructuringElement(cv2.MORPH_RECT, (17, 7)))
    count, labels, _, _ = cv2.connectedComponentsWithStats((grouped > 0).astype(np.uint8), 8)
    symbols: list[dict[str, Any]] = []
    if symbol_root is not None:
        symbol_root.mkdir(parents=True, exist_ok=True)
    for component in range(1, count):
        ys, xs = np.where((labels == component) & (residual > 0))
        if len(xs) < 20:
            continue
        left, right, top, bottom = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
        center_x, center_y = float(xs.mean()), float(ys.mean())
        row, column = cell_for(center_y, horizontal_lines), cell_for(center_x, vertical_lines)
        if row is None or column is None:
            errors.append("symbol_cell_unresolved")
        canvas = np.full((64, 64), 255, dtype=np.uint8)
        symbol_mask = residual[top : bottom + 1, left : right + 1]
        scale = min(48 / max(1, symbol_mask.shape[1]), 48 / max(1, symbol_mask.shape[0]))
        resized = cv2.resize(symbol_mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        rendered = 255 - resized
        y0, x0 = (64 - rendered.shape[0]) // 2, (64 - rendered.shape[1]) // 2
        canvas[y0 : y0 + rendered.shape[0], x0 : x0 + rendered.shape[1]] = rendered
        symbol_id = f"p{position}s{len(symbols) + 1}"
        symbol_path = symbol_root / f"{symbol_id}.png" if symbol_root is not None else None
        if symbol_path is not None:
            Image.fromarray(canvas).save(symbol_path)
        symbol = {
            "id": symbol_id, "p": position, "cell": [row, column], "inkPixels": int(len(xs)),
            "bbox": [left, top, right - left + 1, bottom - top + 1],
        }
        if symbol_path is not None:
            symbol["image"] = str(symbol_path)
        symbols.append(symbol)
    symbols.sort(key=lambda item: (item["cell"][0] or 99, item["cell"][1] or 99))
    if len(symbols) != 2:
        errors.append(f"symbol_count:{len(symbols)}")
    return symbols, {"horizontalLinePixels": horizontal_lines, "verticalLinePixels": vertical_lines}, errors


def grid_symbol_geometry(crop_path: Path, symbol_root: Path, position: int) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    gray = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return [], {}, ["crop_unreadable"]
    return grid_symbol_geometry_array(gray, symbol_root, position)


def label_grid_symbols(symbols: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    images = [(f"id={item['id']}, panel={item['p']}, deterministic_cell={item['cell']}", item["image"]) for item in symbols]
    ids = [item["id"] for item in symbols]
    model_prompt = f"""这些图片是从 4x4 网格中确定性分离出的单个空心符号，id 顺序={ids}。只识别符号语义，不修改 cell，不输出坐标。
k 只能是 star|triangle|pentagon|arrow|letter|number|hanzi|other；ori 只能是 up|down|left|right|none|unknown。星和五边形 ori=none；三角形和箭头必须给方向。
只输出一行 JSON：{{"symbols":[{{"id":"p1s1","k":"star","ori":"none","fill":"hollow","txt":null}}],"uncertain":[]}}"""
    try:
        result = json_candidate(model_prompt, images, max_tokens=512, timeout=180)
        candidate = result["candidate"]
    except Exception as exc:
        return {}, {}, [f"{type(exc).__name__}: {exc}"]
    rows = candidate.get("symbols") if isinstance(candidate, dict) else None
    errors: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        errors.append("symbols_not_list")
    else:
        for row in rows:
            if not isinstance(row, dict) or row.get("id") not in ids or row.get("id") in by_id:
                errors.append("symbol_id")
                continue
            if row.get("k") not in {"star", "triangle", "pentagon", "arrow", "letter", "number", "hanzi", "other"}:
                errors.append(f"{row.get('id')}:kind")
            if row.get("ori") not in {"up", "down", "left", "right", "none", "unknown"}:
                errors.append(f"{row.get('id')}:orientation")
            by_id[row["id"]] = row
    if set(by_id) != set(ids):
        errors.append("symbol_id_coverage")
    return by_id, {"candidate": candidate, "usage": result.get("usage")}, sorted(set(errors))


def grid_panels(job: dict[str, Any], asset_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], list[dict[str, Any]]]:
    roles = [str(role) for role in job["layoutCandidate"]["panelRoles"]]
    all_geometry: list[dict[str, Any]] = []
    all_symbols: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, role in enumerate(roles):
        position = index + 1
        if role == "missing":
            continue
        crop_path = asset_root / "panel-crops" / f"panel-{position:02d}.png"
        symbols, grid_evidence, panel_errors = grid_symbol_geometry(crop_path, asset_root / "symbol-crops", position)
        all_symbols.extend(symbols)
        all_geometry.append({"p": position, **grid_evidence, "symbols": [{key: item[key] for key in ("id", "cell", "inkPixels", "bbox")} for item in symbols]})
        errors.extend(f"p{position}:{error}" for error in panel_errors)
    labels, model_evidence, model_errors = label_grid_symbols(all_symbols) if not errors else ({}, {}, ["geometry_blocked_model"])
    errors.extend(model_errors)
    panels: list[dict[str, Any]] = []
    if not errors:
        by_panel: dict[int, list[dict[str, Any]]] = {}
        for geometry in all_symbols:
            semantic = labels[geometry["id"]]
            by_panel.setdefault(int(geometry["p"]), []).append({
                "kind": semantic["k"], "cell": geometry["cell"], "orientation": semantic["ori"],
                "fill": semantic.get("fill", "hollow"), "text": semantic.get("txt"),
            })
        for index, role in enumerate(roles):
            position = index + 1
            if role == "missing":
                panels.append({"p": position, "role": role, "opt": None, "frame": {"kind": "grid", "rows": 4, "columns": 4}, "symbols": [], "facts": ["missing matrix cell"]})
                continue
            symbols = sorted(by_panel.get(position, []), key=lambda item: tuple(item["cell"]))
            panels.append({
                "p": position, "role": role, "opt": option_label(roles, index),
                "frame": {"kind": "grid", "rows": 4, "columns": 4}, "symbols": symbols,
                "facts": ["exact 4x4 grid", "exactly two hollow semantic symbols with deterministic cells"],
            })
    return panels, {"method": "cv_grid_projection_and_residual_components_v1", "panels": all_geometry, "modelSemanticLabels": model_evidence}, errors, [model_evidence.get("usage", {})]


def relation_candidate(panels: list[dict[str, Any]], layout_type: str) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    text = json.dumps(panels, ensure_ascii=False, separators=(",", ":"))
    prompt = f"""下面是精确结构化面板，layout={layout_type}。只总结题干之间可观察的移动、旋转、方向、数量或类比关系；不解题、不选答案。panels={text}
只输出一行 JSON：{{"patterns":[{{"type":"rotation|reflection|translation|count_delta|permutation|direction_change|analogy_mapping|unknown","from":[1],"to":[2],"subjects":[],"fact":"准确短句","confidence":0.0}}],"uncertain":[]}}"""
    try:
        result = json_candidate(prompt, [], max_tokens=512, timeout=180)
        candidate = result["candidate"]
    except Exception as exc:
        return {}, [f"{type(exc).__name__}: {exc}"], {}
    errors = []
    if not isinstance(candidate.get("patterns"), list):
        errors.append("patterns")
    if not isinstance(candidate.get("uncertain"), list):
        errors.append("uncertain")
    if any(key in candidate for key in ("answer", "correct_option", "solution")):
        errors.append("answer_leak")
    return candidate, errors, result.get("usage") or {}


def solver_document(job: dict[str, Any], panels: list[dict[str, Any]], relations: dict[str, Any]) -> dict[str, Any]:
    layout = job["layoutCandidate"]
    deterministic_relations: list[dict[str, Any]] = []
    stem_signatures = {
        panel.get("canonicalSignature"): panel.get("p")
        for panel in panels
        if panel.get("role") == "stem" and panel.get("canonicalSignature")
    }
    for panel in panels:
        signature = panel.get("canonicalSignature")
        if panel.get("role") == "option" and signature in stem_signatures:
            deterministic_relations.append({
                "type": "exact_panel_match",
                "stemPanel": stem_signatures[signature],
                "optionPanel": panel.get("p"),
                "option": panel.get("opt"),
                "basis": "canonical region/right-angle/hypotenuse/fill signature",
            })
    return {
        "schemaVersion": "graphic-reasoning-ai-semantic-simple-regular-v1", "assetId": job["assetId"],
        "source": {"sha256": job["sourceSha256"], "imagePaths": job["sourcePaths"], "questionReferences": [{key: ref.get(key) for key in ("questionId", "role", "field", "knowledgePoint", "sourceJsonlLine")} for ref in job["questionReferences"]]},
        "presentation": {"mode": "original_image", "imageAuthority": "source_sha256", "vectorRequired": False},
        "layout": {key: layout.get(key) for key in ("layoutType", "panelCount", "optionCount", "panelRoles", "source")},
        "visualGrammar": "regular_geometry", "regularSubtype": job.get("regularSubtypeCandidate"),
        "geometryAuthority": "deterministic_cv", "semanticLabelAuthority": "vlm_candidate",
        "panels": panels,
        "deterministicRelations": deterministic_relations,
        "crossPanelPatterns": relations.get("patterns", []),
        "uncertainties": relations.get("uncertain", []),
        "vectorEnhancement": {"blocking": False, "status": "optional"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=HERE / "queue.jsonl")
    parser.add_argument("--output-root", type=Path, default=HERE / "model-benchmarks" / MODEL)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--asset", action="append", default=[])
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    selected = set(args.asset)
    jobs = [job for job in read_jsonl(args.queue) if not selected or job["assetId"] in selected][: max(0, args.limit)]
    run_rows: list[dict[str, Any]] = []
    for job in jobs:
        asset_root = output_root / "assets" / job["assetId"]
        subtype = job.get("regularSubtypeCandidate")
        if subtype not in SUPPORTED:
            clear_stale_semantic_outputs(asset_root)
            acceptance = {"assetId": job["assetId"], "status": "repair_pending", "qualityStatus": "unsupported_by_simple_regular_v1", "repairReasons": [f"subtype_pending:{subtype}"]}
            write_json(asset_root / "acceptance.json", acceptance)
            run_rows.append({"assetId": job["assetId"], "subtype": subtype, "status": "repair_pending", "qualityStatus": acceptance["qualityStatus"]})
            continue
        write_panel_crops(job, asset_root)
        if subtype == "closed_polygon_composition":
            panels, geometry_evidence, errors = triangle_panels(job)
            usage: list[dict[str, Any]] = []
        else:
            panels, geometry_evidence, errors, usage = grid_panels(job, asset_root)
        relation = {}
        relation_errors: list[str] = []
        if not errors:
            relation, relation_errors, relation_usage = relation_candidate(panels, job["layoutCandidate"]["layoutType"])
            usage.append(relation_usage)
        else:
            relation_errors = ["geometry_or_semantic_labels_incomplete"]
        write_json(asset_root / "geometry-evidence.json", geometry_evidence)
        write_json(asset_root / "raw-relations.json", relation)
        source_hash_ok = hashlib.sha256(Path(job["sourcePaths"][0]).read_bytes()).hexdigest() == job["sourceSha256"]
        schema_complete = bool(panels) and not errors and not relation_errors
        if schema_complete:
            write_json(asset_root / "solver.json", solver_document(job, panels, relation))
            write_json(asset_root / "evaluation.json", {
                "schemaVersion": "graphic-reasoning-semantic-evaluation-v1", "assetId": job["assetId"], "solverInputExcludesAnswer": True,
                "questionAnswers": [{"questionId": ref.get("questionId"), "answerLabel": ref.get("answerLabel"), "optionLabels": ref.get("optionLabels", [])} for ref in job["questionReferences"]],
            })
        acceptance = {
            "schemaVersion": "graphic-reasoning-simple-regular-acceptance-v1", "assetId": job["assetId"], "subtype": subtype,
            "sourceSha256": job["sourceSha256"], "sourceHash": "pass" if source_hash_ok else "fail",
            "geometryGate": "pass" if not errors else "fail", "semanticLabelGate": "pass" if not errors else "fail",
            "relationGate": "pass" if not relation_errors else "fail", "errors": errors, "relationErrors": relation_errors,
            "solverInputExcludesAnswer": True, "qualityStatus": "fact_review_pending" if schema_complete else "blocked",
            "repairReasons": ["representative_visible_fact_review_pending"] if schema_complete else ["geometry_or_semantic_failure"],
            "status": "repair_pending", "usage": usage,
        }
        write_json(asset_root / "acceptance.json", acceptance)
        row = {"assetId": job["assetId"], "subtype": subtype, "status": "repair_pending", "qualityStatus": acceptance["qualityStatus"], "errors": errors + relation_errors}
        run_rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    state = {
        "schemaVersion": "graphic-reasoning-simple-regular-run-state-v1", "observedAt": datetime.now(timezone.utc).isoformat(),
        "model": MODEL, "requested": len(jobs), "supported": sum(row["subtype"] in SUPPORTED for row in run_rows),
        "qualityAccepted": 0, "repairPending": len(run_rows), "rows": run_rows,
    }
    write_json(output_root / "run-state.json", state)
    print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
