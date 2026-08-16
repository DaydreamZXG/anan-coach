#!/usr/bin/env python3
"""Refine typed model seeds against source pixels with deterministic geometry."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from candidate_ingest import CANDIDATE_SCHEMA
from verify_candidate import _distance_metrics, _xy, sample_primitive


def _norm(point: np.ndarray, width: int, height: int) -> dict[str, float]:
    return {"x": round(float(point[0]) / width * 1000.0, 3), "y": round(float(point[1]) / height * 1000.0, 3)}


def _ink_xy(gray: np.ndarray) -> np.ndarray:
    y, x = np.nonzero(gray < 210)
    return np.column_stack((x, y)).astype(np.float64)


def _segment_neighborhood(points: np.ndarray, start: np.ndarray, end: np.ndarray, radius: float) -> np.ndarray:
    vector = end - start
    length_squared = max(1e-9, float(np.dot(vector, vector)))
    projection = ((points - start) @ vector) / length_squared
    projected = start + np.clip(projection, 0.0, 1.0)[:, None] * vector
    distance = np.linalg.norm(points - projected, axis=1)
    margin = radius / max(1.0, math.sqrt(length_squared))
    return points[(projection >= -margin) & (projection <= 1.0 + margin) & (distance <= radius)]


def _fit_line(primitive: dict[str, Any], gray: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]]:
    height, width = gray.shape
    start, end = _xy(primitive["from"], width, height), _xy(primitive["to"], width, height)
    source_points = _ink_xy(gray)
    radius = max(2.5, min(width, height) * 0.04)
    support = _segment_neighborhood(source_points, start, end, radius)
    if len(support) < max(8, np.linalg.norm(end - start) * 0.25):
        return primitive, {"status": "repair_pending", "reason": "insufficient_line_support", "supportPixels": len(support)}
    fit = cv2.fitLine(support.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).reshape(-1)
    direction = np.array([fit[0], fit[1]], dtype=np.float64)
    center = np.array([fit[2], fit[3]], dtype=np.float64)
    direction /= max(1e-9, np.linalg.norm(direction))
    if np.dot(direction, end - start) < 0:
        direction *= -1
    projections = (support - center) @ direction
    lower, upper = np.percentile(projections, [2, 98])
    candidate_projection = np.array([(start - center) @ direction, (end - center) @ direction])
    lower = max(lower, float(candidate_projection.min() - radius))
    upper = min(upper, float(candidate_projection.max() + radius))
    refined_start, refined_end = center + direction * lower, center + direction * upper
    angle = math.degrees(math.atan2(refined_end[1] - refined_start[1], refined_end[0] - refined_start[0])) % 180
    snap = "none"
    if min(angle, 180 - angle) <= 3.0:
        y = float(np.median(support[:, 1]))
        refined_start[1] = refined_end[1] = y
        snap = "horizontal"
    elif abs(angle - 90) <= 3.0:
        x = float(np.median(support[:, 0]))
        refined_start[0] = refined_end[0] = x
        snap = "vertical"
    result = copy.deepcopy(primitive)
    result["from"], result["to"] = _norm(refined_start, width, height), _norm(refined_end, width, height)
    return result, {"status": "refined", "method": "opencv_huber_line_fit", "supportPixels": len(support), "axisSnap": snap}


def _fit_circle(primitive: dict[str, Any], gray: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]]:
    height, width = gray.shape
    center = _xy(primitive["center"], width, height)
    scale = (width + height) / 2.0
    radius = float(primitive["radius"]) / 1000.0 * scale
    points = _ink_xy(gray)
    radial_error = np.abs(np.linalg.norm(points - center, axis=1) - radius)
    capture = max(2.5, min(width, height) * 0.04)
    support = points[radial_error <= capture]
    if len(support) < max(20, radius * 1.2):
        return primitive, {"status": "repair_pending", "reason": "insufficient_circle_support", "supportPixels": len(support)}
    angles = (np.arctan2(support[:, 1] - center[1], support[:, 0] - center[0]) + 2 * math.pi) % (2 * math.pi)
    covered_bins = len(np.unique(np.floor(angles / (2 * math.pi) * 36).astype(int)))
    if covered_bins < 24:
        return primitive, {"status": "repair_pending", "reason": "circle_angular_coverage_low", "coveredBins": covered_bins}
    matrix = np.column_stack((2 * support[:, 0], 2 * support[:, 1], np.ones(len(support))))
    target = support[:, 0] ** 2 + support[:, 1] ** 2
    cx, cy, constant = np.linalg.lstsq(matrix, target, rcond=None)[0]
    fitted_radius = math.sqrt(max(0.0, constant + cx * cx + cy * cy))
    if np.linalg.norm(np.array([cx, cy]) - center) > capture * 1.5 or abs(fitted_radius - radius) > capture * 1.5:
        return primitive, {"status": "repair_pending", "reason": "circle_fit_drift", "supportPixels": len(support)}
    result = copy.deepcopy(primitive)
    result["center"] = _norm(np.array([cx, cy]), width, height)
    result["radius"] = round(fitted_radius / scale * 1000.0, 3)
    return result, {"status": "refined", "method": "deterministic_algebraic_circle_fit", "supportPixels": len(support), "coveredAngleBins": covered_bins}


def _fit_rect(primitive: dict[str, Any], gray: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]]:
    height, width = gray.shape
    box = primitive["box"]
    x0, y0 = float(box["x"]) / 1000 * width, float(box["y"]) / 1000 * height
    x1 = x0 + float(box["width"]) / 1000 * width
    y1 = y0 + float(box["height"]) / 1000 * height
    points = _ink_xy(gray)
    capture = max(2.5, min(width, height) * 0.04)
    vertical0 = points[(np.abs(points[:, 0] - x0) <= capture) & (points[:, 1] >= y0 - capture) & (points[:, 1] <= y1 + capture)]
    vertical1 = points[(np.abs(points[:, 0] - x1) <= capture) & (points[:, 1] >= y0 - capture) & (points[:, 1] <= y1 + capture)]
    horizontal0 = points[(np.abs(points[:, 1] - y0) <= capture) & (points[:, 0] >= x0 - capture) & (points[:, 0] <= x1 + capture)]
    horizontal1 = points[(np.abs(points[:, 1] - y1) <= capture) & (points[:, 0] >= x0 - capture) & (points[:, 0] <= x1 + capture)]
    supports = [vertical0, vertical1, horizontal0, horizontal1]
    if any(len(support) < 8 for support in supports):
        return primitive, {"status": "repair_pending", "reason": "insufficient_rect_edge_support", "supportPixels": [len(item) for item in supports]}
    rx0, rx1 = float(np.median(vertical0[:, 0])), float(np.median(vertical1[:, 0]))
    ry0, ry1 = float(np.median(horizontal0[:, 1])), float(np.median(horizontal1[:, 1]))
    if rx0 > rx1:
        rx0, rx1 = rx1, rx0
    if ry0 > ry1:
        ry0, ry1 = ry1, ry0
    result = copy.deepcopy(primitive)
    result["box"] = {**_norm(np.array([rx0, ry0]), width, height), "width": round((rx1 - rx0) / width * 1000, 3), "height": round((ry1 - ry0) / height * 1000, 3)}
    return result, {"status": "refined", "method": "median_axis_aligned_edge_fit", "supportPixels": [len(item) for item in supports], "rightAngles": "exact"}


def _bezier_objective(primitive: dict[str, Any], width: int, height: int, distance: np.ndarray, seed: np.ndarray, values: np.ndarray) -> float:
    trial = copy.deepcopy(primitive)
    keys = ("from", "control", "to") if primitive["type"] == "quadraticBezier" else ("from", "control1", "control2", "to")
    for index, key in enumerate(keys):
        trial[key] = _norm(values[index], width, height)
    points, _ = sample_primitive(trial, width, height)
    metrics = _distance_metrics(points, distance)
    if metrics is None:
        return float("inf")
    regularization = float(np.mean((values - seed) ** 2)) * 0.008
    return metrics["meanDistancePixels"] + 0.25 * metrics["p95DistancePixels"] + regularization


def _fit_bezier(primitive: dict[str, Any], gray: np.ndarray, distance: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]]:
    height, width = gray.shape
    keys = ("from", "control", "to") if primitive["type"] == "quadraticBezier" else ("from", "control1", "control2", "to")
    seed = np.array([_xy(primitive[key], width, height) for key in keys], dtype=np.float64)
    values = seed.copy()
    before = _bezier_objective(primitive, width, height, distance, seed, values)
    best = before
    for step in (3.0, 1.5, 0.75):
        changed = True
        rounds = 0
        while changed and rounds < 3:
            changed = False
            rounds += 1
            for point_index in range(len(values)):
                for axis in (0, 1):
                    for direction in (-1.0, 1.0):
                        trial = values.copy()
                        trial[point_index, axis] += direction * step
                        if abs(trial[point_index, axis] - seed[point_index, axis]) > 6.0:
                            continue
                        score = _bezier_objective(primitive, width, height, distance, seed, trial)
                        if score + 1e-6 < best:
                            values, best, changed = trial, score, True
    result = copy.deepcopy(primitive)
    for index, key in enumerate(keys):
        result[key] = _norm(values[index], width, height)
    status = "refined" if math.isfinite(best) and best <= before else "repair_pending"
    return result, {"status": status, "method": "bounded_deterministic_coordinate_descent", "objectiveBefore": round(before, 4), "objectiveAfter": round(best, 4), "maximumSeedDriftPixels": round(float(np.max(np.linalg.norm(values - seed, axis=1))), 4)}


def refine_candidate(candidate: dict[str, Any], source_panel: Path) -> dict[str, Any]:
    if candidate.get("schemaVersion") != CANDIDATE_SCHEMA:
        raise ValueError("candidate_schema_mismatch")
    gray = cv2.imread(str(source_panel), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("source_panel_decode_failed")
    ink = gray < 210
    distance = cv2.distanceTransform((~ink).astype(np.uint8), cv2.DIST_L2, 3)
    refined: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for index, primitive in enumerate(candidate.get("primitives", [])):
        primitive_type = primitive.get("type")
        if primitive_type == "line":
            result, item = _fit_line(primitive, gray)
        elif primitive_type == "circle":
            result, item = _fit_circle(primitive, gray)
        elif primitive_type in {"rect", "roundedRect"}:
            result, item = _fit_rect(primitive, gray)
        elif primitive_type in {"quadraticBezier", "cubicBezier"}:
            result, item = _fit_bezier(primitive, gray, distance)
        else:
            result, item = copy.deepcopy(primitive), {"status": "repair_pending", "reason": f"deterministic_refiner_not_implemented:{primitive_type}"}
        item = {"index": index, "type": primitive_type, **item}
        refined.append(result)
        evidence.append(item)
    pending = [item for item in evidence if item["status"] != "refined"]
    return {
        "schemaVersion": "graphic-vector-deterministic-refinement-v1",
        "assetId": candidate["assetId"],
        "panelId": candidate["panelId"],
        "sourceSha256": candidate["sourceSha256"],
        "sourcePanel": str(source_panel.resolve()),
        "geometrySource": "model_seed_then_deterministic_source_pixel_fit",
        "coordinateSpace": {"width": 1000, "height": 1000},
        "primitives": refined,
        "primitiveEvidence": evidence,
        "gates": {
            "allGeometryDeterministicallyRefined": {"status": "pass" if not pending and bool(refined) else "fail"},
            "topology": {"status": "pending"},
            "frontendContract": {"status": "pending"},
            "finalAcceptance": {"status": "blocked", "reason": "topology_render_and_contract_gates_required"},
        },
        "status": "refined_geometry_ready_for_topology" if not pending and refined else "repair_pending",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source-panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = refine_candidate(json.loads(args.candidate.read_text(encoding="utf-8")), args.source_panel)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if result["status"] == "refined_geometry_ready_for_topology" else 2


if __name__ == "__main__":
    raise SystemExit(main())
