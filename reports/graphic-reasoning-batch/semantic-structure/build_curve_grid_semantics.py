#!/usr/bin/env python3
"""Focused semantic extractor for square panels mixing exact lines and curves."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from qwen_client import json_candidate  # noqa: E402


ORIENTATIONS = {"horizontal", "vertical", "diagonal_up", "diagonal_down"}
CURVE_KINDS = {"quarter_arc", "semicircle", "u_curve", "s_curve", "other_smooth_curve"}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_job(queue: Path, asset_id: str) -> dict[str, Any]:
    for line in queue.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("assetId") == asset_id:
            return row
    raise KeyError(asset_id)


def validate_candidate(candidate: dict[str, Any], ids: list[str]) -> list[str]:
    errors: list[str] = []
    rows = candidate.get("panels")
    if not isinstance(rows, list):
        return ["panels_not_list"]
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in ids or row.get("id") in by_id:
            errors.append("panel_id")
            continue
        by_id[row["id"]] = row
        if row.get("frame") != "hollow_square":
            errors.append(f"{row.get('id')}:frame")
        if row.get("fill") != "outline_only":
            errors.append(f"{row.get('id')}:fill")
        straight = row.get("straightSegments")
        curves = row.get("curves")
        if not isinstance(straight, list) or any(item not in ORIENTATIONS for item in straight):
            errors.append(f"{row.get('id')}:straightSegments")
        if not isinstance(curves, list) or any(not isinstance(item, dict) or item.get("kind") not in CURVE_KINDS for item in curves):
            errors.append(f"{row.get('id')}:curves")
        for forbidden in ("x", "y", "bbox", "points", "path", "svg", "answer", "correctOption"):
            if forbidden in row:
                errors.append(f"{row.get('id')}:forbidden:{forbidden}")
    if set(by_id) != set(ids):
        errors.append("panel_coverage")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    job = read_job(args.queue, args.asset)
    layout = job["layoutCandidate"]
    roles = layout["panelRoles"]
    boxes = layout["panelBoxes"]
    asset_root = args.output_root.resolve() / "assets" / args.asset
    crop_root = asset_root / "panel-crops"
    crop_root.mkdir(parents=True, exist_ok=True)
    images: list[tuple[str, Path]] = []
    visible_ids: list[str] = []
    with Image.open(args.source) as source:
        source = source.convert("L")
        for index, (role, box) in enumerate(zip(roles, boxes), 1):
            if role == "missing" or box is None:
                continue
            x, y, width, height = (int(box[key]) for key in ("x", "y", "width", "height"))
            crop = source.crop((x, y, x + width, y + height)).resize((512, 512), Image.Resampling.NEAREST)
            crop_path = crop_root / f"panel-{index:02d}.png"
            crop.save(crop_path)
            panel_id = f"p{index}"
            visible_ids.append(panel_id)
            images.append((f"id={panel_id}, role={role}", crop_path))
    prompt = f"""逐张读取这些放大的黑白图形推理面板，id 顺序={visible_ids}。目标是让另一个 AI 解题，不是重绘。
必须严格区分直线与光滑曲线：
- straightSegments：只列内部直线中心线，每条一次，不含正方形外框；取值 horizontal|vertical|diagonal_up|diagonal_down。
- curves：每条连续光滑曲线一次；kind 只能 quarter_arc|semicircle|u_curve|s_curve|other_smooth_curve；给 opening=up|down|left|right|none。
- rightAngles：内部直线形成的明确直角数量；curveLineIntersections：曲线与内部直线交点数量。
- partitionFacts：简短列出格线、折线、相切/相交和相对位置；禁止坐标。
所有图都是空心线稿，不得把闭合空白区域写成实心。
只输出一行 JSON：{{"panels":[{{"id":"p1","frame":"hollow_square","fill":"outline_only","straightSegments":["horizontal"],"curves":[{{"kind":"quarter_arc","opening":"top"}}],"rightAngles":0,"curveLineIntersections":0,"partitionFacts":[]}}],"uncertain":[]}}"""
    result = json_candidate(prompt, images, max_tokens=3000, timeout=300)
    candidate = result["candidate"]
    errors = validate_candidate(candidate, visible_ids)
    by_id = {row["id"]: row for row in candidate.get("panels", []) if isinstance(row, dict) and row.get("id") in visible_ids}
    panels: list[dict[str, Any]] = []
    option_index = 0
    for index, role in enumerate(roles, 1):
        if role == "option":
            option_index += 1
        label = chr(ord("A") + option_index - 1) if role == "option" else None
        if role == "missing":
            panels.append({"p": index, "role": role, "opt": None, "frame": "missing", "straightSegments": [], "curves": []})
            continue
        row = dict(by_id.get(f"p{index}", {}))
        row.pop("id", None)
        row.update({"p": index, "role": role, "opt": label})
        panels.append(row)
    relation = {"patterns": [], "uncertain": []}
    relation_usage = None
    if not errors:
        relation_prompt = "下面是逐面板可见事实。只总结五个题干面板中直线、曲线、格线和交点的可观察变化，不选答案；无法确认就写 unknown，禁止臆造整体旋转。只输出 JSON：{\"patterns\":[{\"type\":\"line_change|curve_change|partition_change|intersection_change|unknown\",\"from\":[1],\"to\":[2],\"fact\":\"短句\",\"confidence\":0.0}],\"uncertain\":[]}。panels=" + json.dumps([panel for panel in panels if panel["role"] == "stem"], ensure_ascii=False, separators=(",", ":"))
        relation_result = json_candidate(relation_prompt, [], max_tokens=800, timeout=180)
        relation = relation_result["candidate"]
        relation_usage = relation_result.get("usage")
    solver = {
        "schemaVersion": "graphic-reasoning-ai-semantic-line-curve-grid-v1",
        "assetId": args.asset,
        "source": {"sha256": job["sourceSha256"], "imagePaths": job["sourcePaths"], "questionReferences": [{key: ref.get(key) for key in ("questionId", "role", "field", "knowledgePoint", "sourceJsonlLine")} for ref in job["questionReferences"]]},
        "presentation": {"mode": "original_image", "imageAuthority": "source_sha256", "vectorRequired": False},
        "layout": {key: layout.get(key) for key in ("layoutType", "panelCount", "optionCount", "panelRoles", "source")},
        "visualGrammar": "line_curve_partition",
        "geometryAuthority": "original_image_with_vlm_semantic_candidate",
        "panels": panels,
        "crossPanelPatterns": relation.get("patterns", []),
        "uncertainties": (candidate.get("uncertain") or []) + (relation.get("uncertain") or []),
        "vectorEnhancement": {"blocking": False, "status": "optional"},
    }
    write_json(asset_root / "raw-panel-semantics.json", {"candidate": candidate, "usage": result.get("usage")})
    write_json(asset_root / "raw-relations.json", relation)
    if not errors:
        write_json(asset_root / "solver.json", solver)
        write_json(asset_root / "evaluation.json", {"assetId": args.asset, "solverInputExcludesAnswer": True, "questionAnswers": [{"questionId": ref.get("questionId"), "answerLabel": ref.get("answerLabel"), "optionLabels": ref.get("optionLabels", [])} for ref in job["questionReferences"]]})
    source_hash_ok = hashlib.sha256(args.source.read_bytes()).hexdigest() == job["sourceSha256"]
    acceptance = {
        "schemaVersion": "graphic-reasoning-line-curve-grid-acceptance-v1",
        "assetId": args.asset,
        "status": "repair_pending",
        "qualityStatus": "visible_fact_review_pending" if not errors and source_hash_ok else "blocked",
        "sourceHash": "pass" if source_hash_ok else "fail",
        "semanticCandidateGate": "pass" if not errors else "fail",
        "geometryGate": "manual_visible_fact_review_required",
        "solverInputExcludesAnswer": True,
        "errors": errors,
        "repairReasons": ["straight_curve_and_right_angle_visible_fact_review_pending"] if not errors else ["semantic_schema_failure"],
        "usage": {"panels": result.get("usage"), "relations": relation_usage},
    }
    write_json(asset_root / "acceptance.json", acceptance)
    print(json.dumps(acceptance, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    raise SystemExit(main())
