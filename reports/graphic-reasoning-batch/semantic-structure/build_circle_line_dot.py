#!/usr/bin/env python3
"""Deterministic circle + straight-line + filled-dot semantic extractor."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image
from skimage.morphology import skeletonize


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from qwen_client import json_candidate  # noqa: E402


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


def detect_circle(gray: np.ndarray) -> tuple[float, float, float, list[dict[str, float]]]:
    height, width = gray.shape
    blurred = cv2.GaussianBlur(gray, (5, 5), 1.2)
    found = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1,
        minDist=min(height, width) // 2,
        param1=100,
        param2=24,
        minRadius=int(min(height, width) * 0.30),
        maxRadius=int(min(height, width) * 0.47),
    )
    if found is None:
        raise ValueError("circle_not_found")
    candidates = [
        {"cx": float(x), "cy": float(y), "r": float(radius)}
        for x, y, radius in found[0]
    ]
    candidates.sort(key=lambda item: abs(item["cx"] - width / 2) + abs(item["cy"] - height / 2) + abs(item["r"] - min(height, width) * 0.39))
    selected = candidates[0]
    return selected["cx"], selected["cy"], selected["r"], candidates


def detect_filled_dots(ink: np.ndarray, cx: float, cy: float, radius: float) -> list[dict[str, float]]:
    yy, xx = np.indices(ink.shape)
    inside = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (radius + 4) ** 2
    distance = cv2.distanceTransform(((ink > 0) & inside).astype(np.uint8), cv2.DIST_L2, 5)
    # Filled dots can touch one another or the circle.  Their thick interiors
    # still have three separate distance-transform maxima even when a simple
    # connected-component pass merges two dots.
    maxima = ((distance == cv2.dilate(distance, np.ones((9, 9), np.uint8))) & (distance >= 5.0)).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(maxima, 8)
    dots: list[dict[str, float]] = []
    for component in range(1, count):
        if int(stats[component, cv2.CC_STAT_AREA]) < 1:
            continue
        center_x = float(centroids[component, 0])
        center_y = float(centroids[component, 1])
        dot_radius = float(distance[labels == component].max())
        dots.append({"cx": center_x, "cy": center_y, "r": dot_radius})
    dots.sort(key=lambda item: (item["cy"], item["cx"]))
    return dots


def angular_difference(first: float, second: float) -> float:
    difference = abs(first - second) % 180
    return min(difference, 180 - difference)


def canonical_line(segment: list[int]) -> dict[str, float]:
    x1, y1, x2, y2 = map(float, segment)
    angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180
    normal = math.radians((angle + 90) % 180)
    rho = ((x1 + x2) / 2) * math.cos(normal) + ((y1 + y2) / 2) * math.sin(normal)
    return {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "angle": angle,
        "rho": rho,
        "length": math.hypot(x2 - x1, y2 - y1),
    }


def detect_lines(gray: np.ndarray, cx: float, cy: float, radius: float, dots: list[dict[str, float]]) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    ink = (gray < 180).astype(np.uint8) * 255
    ink[:5, :] = 0
    ink[-5:, :] = 0
    ink[:, :5] = 0
    ink[:, -5:] = 0
    yy, xx = np.indices(gray.shape)
    circle_distance = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    ink[np.abs(circle_distance - radius) <= 4] = 0
    for dot in dots:
        cv2.circle(ink, (round(dot["cx"]), round(dot["cy"])), round(dot["r"] + 4), 0, -1)
    skeleton = skeletonize(ink > 0).astype(np.uint8) * 255
    found = cv2.HoughLinesP(skeleton, 1, np.pi / 720, threshold=8, minLineLength=18, maxLineGap=30)
    raw = [canonical_line(line) for line in ([] if found is None else found[:, 0].tolist())]
    raw.sort(key=lambda item: item["length"], reverse=True)
    merged: list[dict[str, float]] = []
    for candidate in raw:
        if any(angular_difference(candidate["angle"], kept["angle"]) <= 2 and abs(candidate["rho"] - kept["rho"]) <= 2 for kept in merged):
            continue
        midpoint_x = (candidate["x1"] + candidate["x2"]) / 2
        midpoint_y = (candidate["y1"] + candidate["y2"]) / 2
        if math.hypot(midpoint_x - cx, midpoint_y - cy) > radius + 2:
            continue
        merged.append(candidate)
    return merged[:2], raw


def relative_region(x: float, y: float, cx: float, cy: float) -> str:
    dx, dy = x - cx, y - cy
    horizontal = "left" if dx < -3 else "right" if dx > 3 else ""
    vertical = "top" if dy < -3 else "bottom" if dy > 3 else ""
    return "_".join(part for part in (vertical, horizontal) if part) or "center"


def clock_degrees(x: float, y: float, cx: float, cy: float) -> int:
    return int(round(math.degrees(math.atan2(x - cx, cy - y)) % 360))


def line_semantics(line: dict[str, float], cx: float, cy: float, radius: float) -> dict[str, Any]:
    angle = math.radians(line["angle"])
    direction = np.array([math.cos(angle), math.sin(angle)], dtype=float)
    first = np.array([line["x1"], line["y1"]], dtype=float)
    center = np.array([cx, cy], dtype=float)
    nearest = first + direction * float(np.dot(center - first, direction))
    offset = float(np.linalg.norm(nearest - center))
    extent = math.sqrt(max(0.0, radius * radius - offset * offset))
    endpoints = [nearest - direction * extent, nearest + direction * extent]
    clocks = sorted(clock_degrees(float(point[0]), float(point[1]), cx, cy) for point in endpoints)
    return {"kind": "straightChord", "orientationDegrees": round(line["angle"], 1), "boundaryClockDegrees": clocks}


def dot_line_relation(dot: dict[str, float], line: dict[str, float]) -> dict[str, Any]:
    dx = line["x2"] - line["x1"]
    dy = line["y2"] - line["y1"]
    length = max(1e-6, math.hypot(dx, dy))
    signed_distance = (dx * (dot["cy"] - line["y1"]) - dy * (dot["cx"] - line["x1"])) / length
    touches = abs(signed_distance) <= dot["r"] + 2
    return {
        "side": "on" if touches else "positive" if signed_distance > 0 else "negative",
        "touches": touches,
        "centerDistanceInDotRadii": round(abs(signed_distance) / max(1.0, dot["r"]), 2),
    }


def extract_panel(gray: np.ndarray) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    cx, cy, radius, circle_candidates = detect_circle(gray)
    ink = (gray < 180).astype(np.uint8)
    ink[:5, :] = 0
    ink[-5:, :] = 0
    ink[:, :5] = 0
    ink[:, -5:] = 0
    dots = detect_filled_dots(ink, cx, cy, radius)
    lines, raw_lines = detect_lines(gray, cx, cy, radius, dots)
    errors: list[str] = []
    if len(dots) != 3:
        errors.append(f"dot_count:{len(dots)}")
    if len(lines) != 2:
        errors.append(f"line_count:{len(lines)}")
    lines = sorted(lines, key=lambda item: item["angle"])
    dot_semantics = []
    for dot in dots:
        radial = math.hypot(dot["cx"] - cx, dot["cy"] - cy) / radius
        dot_semantics.append({
            "kind": "dot",
            "fill": "solid",
            "region": relative_region(dot["cx"], dot["cy"], cx, cy),
            "clockDegrees": clock_degrees(dot["cx"], dot["cy"], cx, cy),
            "radialZone": "rim" if radial >= 0.72 else "inner",
            "relativeToLines": [dot_line_relation(dot, line) for line in lines],
        })
    panel = {
        "frame": {"kind": "circle", "fill": "hollow", "count": 1},
        "straightLines": [line_semantics(line, cx, cy, radius) for line in lines],
        "dots": dot_semantics,
        "dotTouchCountPerLine": [sum(relation["touches"] for relation in (dot["relativeToLines"][index] for dot in dot_semantics)) for index in range(len(lines))],
        "topology": {"circleLoops": 1, "straightLineCount": len(lines), "solidDotCount": len(dots)},
        "facts": ["one hollow circle", "exactly two straight internal lines", "exactly three solid black dots"],
    }
    evidence = {
        "circle": {"cx": round(cx, 3), "cy": round(cy, 3), "r": round(radius, 3)},
        "circleCandidates": circle_candidates,
        "dots": [{key: round(value, 3) for key, value in dot.items()} for dot in dots],
        "selectedLines": lines,
        "rawHoughLineCount": len(raw_lines),
    }
    return panel, evidence, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--skip-relations", action="store_true")
    args = parser.parse_args()
    job = read_job(args.queue, args.asset)
    layout = job["layoutCandidate"]
    roles = layout["panelRoles"]
    boxes = layout["panelBoxes"]
    asset_root = args.output_root.resolve() / "assets" / args.asset
    crop_root = asset_root / "panel-crops"
    crop_root.mkdir(parents=True, exist_ok=True)
    source_gray = cv2.imread(str(args.source), cv2.IMREAD_GRAYSCALE)
    if source_gray is None:
        raise SystemExit("source unreadable")
    panels: list[dict[str, Any]] = []
    evidence_panels: list[dict[str, Any]] = []
    errors: list[str] = []
    option_index = 0
    for index, (role, box) in enumerate(zip(roles, boxes), 1):
        if role == "option":
            option_index += 1
        label = chr(ord("A") + option_index - 1) if role == "option" else None
        if role == "missing" or box is None:
            panels.append({"p": index, "role": role, "opt": label, "frame": "missing", "straightLines": [], "dots": []})
            continue
        x, y, width, height = (int(box[key]) for key in ("x", "y", "width", "height"))
        crop = source_gray[y : y + height, x : x + width]
        crop_path = crop_root / f"panel-{index:02d}.png"
        Image.fromarray(crop).save(crop_path)
        try:
            panel, evidence, panel_errors = extract_panel(crop)
        except Exception as exc:
            panel, evidence, panel_errors = {}, {}, [f"{type(exc).__name__}:{exc}"]
        panel.update({"p": index, "role": role, "opt": label})
        panels.append(panel)
        evidence_panels.append({"p": index, **evidence})
        errors.extend(f"p{index}:{error}" for error in panel_errors)
    relation = {"patterns": [], "uncertain": []}
    usage = None
    if not errors and not args.skip_relations:
        prompt = "下面是圆形图推题的确定性结构化面板。重点比较每个黑点相对两条直线的 side/touches、圆周钟点位置和两条线的圆周端点。只有五个题干面板全部支持时才能写整体旋转；否则逐项描述可观察变化并标 unknown。不要选答案、不要引用答案。只输出 JSON：{\"patterns\":[{\"type\":\"rotation|translation|permutation|incidence_change|unknown\",\"from\":[1],\"to\":[2],\"fact\":\"短句\",\"confidence\":0.0}],\"uncertain\":[]}。panels=" + json.dumps(panels, ensure_ascii=False, separators=(",", ":"))
        result = json_candidate(prompt, [], max_tokens=700, timeout=180)
        relation = result["candidate"]
        usage = result.get("usage")
    solver = {
        "schemaVersion": "graphic-reasoning-ai-semantic-circle-line-dot-v1",
        "assetId": args.asset,
        "source": {"sha256": job["sourceSha256"], "imagePaths": job["sourcePaths"], "questionReferences": [{key: ref.get(key) for key in ("questionId", "role", "field", "knowledgePoint", "sourceJsonlLine")} for ref in job["questionReferences"]]},
        "presentation": {"mode": "original_image", "imageAuthority": "source_sha256", "vectorRequired": False},
        "layout": {key: layout.get(key) for key in ("layoutType", "panelCount", "optionCount", "panelRoles", "source")},
        "visualGrammar": "regular_geometry",
        "regularSubtype": "circle_line_dot",
        "geometryAuthority": "deterministic_cv",
        "panels": panels,
        "crossPanelPatterns": relation.get("patterns", []),
        "uncertainties": relation.get("uncertain", []),
        "vectorEnhancement": {"blocking": False, "status": "optional"},
    }
    write_json(asset_root / "geometry-evidence.json", {"method": "hough_circle_distance_transform_dot_hough_line_nms_v1", "panels": evidence_panels})
    write_json(asset_root / "raw-relations.json", relation)
    if not errors:
        write_json(asset_root / "solver.json", solver)
        write_json(asset_root / "evaluation.json", {"assetId": args.asset, "solverInputExcludesAnswer": True, "questionAnswers": [{"questionId": ref.get("questionId"), "answerLabel": ref.get("answerLabel"), "optionLabels": ref.get("optionLabels", [])} for ref in job["questionReferences"]]})
    source_hash_ok = hashlib.sha256(args.source.read_bytes()).hexdigest() == job["sourceSha256"]
    acceptance = {
        "schemaVersion": "graphic-reasoning-circle-line-dot-acceptance-v1",
        "assetId": args.asset,
        "status": "repair_pending",
        "qualityStatus": "fact_review_pending" if not errors and source_hash_ok else "blocked",
        "sourceHash": "pass" if source_hash_ok else "fail",
        "geometryGate": "pass" if not errors else "fail",
        "solverInputExcludesAnswer": True,
        "errors": errors,
        "repairReasons": ["representative_visible_fact_review_pending"] if not errors and source_hash_ok else ["geometry_failure"],
        "usage": usage,
    }
    write_json(asset_root / "acceptance.json", acceptance)
    print(json.dumps(acceptance, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    raise SystemExit(main())
