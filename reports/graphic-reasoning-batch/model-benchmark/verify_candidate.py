#!/usr/bin/env python3
"""Deterministically check a typed model candidate against one source panel.

This verifier decides whether model geometry is a usable optimization seed.  It
does not promote model coordinates to final graphic-primitives-v2 geometry.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from candidate_ingest import CANDIDATE_SCHEMA


def _xy(point: dict[str, Any], width: int, height: int) -> np.ndarray:
    return np.array([float(point["x"]) / 1000.0 * width, float(point["y"]) / 1000.0 * height], dtype=np.float64)


def _curve_steps(points: list[np.ndarray]) -> int:
    span = sum(float(np.linalg.norm(right - left)) for left, right in zip(points, points[1:]))
    return max(24, min(512, round(span * 1.5)))


def _arc_angles(start: float, end: float, anticlockwise: bool) -> tuple[float, float]:
    start_radians, end_radians = math.radians(start), math.radians(end)
    if anticlockwise:
        while end_radians > start_radians:
            end_radians -= 2 * math.pi
    else:
        while end_radians < start_radians:
            end_radians += 2 * math.pi
    return start_radians, end_radians


def sample_primitive(primitive: dict[str, Any], width: int, height: int) -> tuple[np.ndarray, bool]:
    primitive_type = primitive.get("type")
    closed = False
    if primitive_type == "line":
        start, end = _xy(primitive["from"], width, height), _xy(primitive["to"], width, height)
        steps = _curve_steps([start, end])
        t = np.linspace(0.0, 1.0, steps)[:, None]
        return start + (end - start) * t, False
    if primitive_type in {"polyline", "polygon", "face"}:
        vertices = [_xy(point, width, height) for point in primitive["points"]]
        closed = primitive_type in {"polygon", "face"}
        if closed:
            vertices.append(vertices[0])
        chunks = []
        for start, end in zip(vertices, vertices[1:]):
            t = np.linspace(0.0, 1.0, _curve_steps([start, end]))[:, None]
            chunks.append(start + (end - start) * t)
        return (np.concatenate(chunks) if chunks else np.empty((0, 2))), closed
    if primitive_type in {"rect", "roundedRect"}:
        box = primitive["box"]
        x = float(box["x"]) / 1000.0 * width
        y = float(box["y"]) / 1000.0 * height
        w = float(box["width"]) / 1000.0 * width
        h = float(box["height"]) / 1000.0 * height
        vertices = [np.array(point, dtype=np.float64) for point in ((x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y))]
        chunks = []
        for start, end in zip(vertices, vertices[1:]):
            t = np.linspace(0.0, 1.0, _curve_steps([start, end]))[:, None]
            chunks.append(start + (end - start) * t)
        return np.concatenate(chunks), True
    if primitive_type == "circle":
        center = _xy(primitive["center"], width, height)
        radius = float(primitive["radius"]) / 1000.0 * (width + height) / 2.0
        angles = np.linspace(0.0, 2 * math.pi, max(48, round(radius * 6)), endpoint=False)
        return center + np.column_stack((np.cos(angles), np.sin(angles))) * radius, True
    if primitive_type in {"ellipse", "arc"}:
        center = _xy(primitive["center"], width, height)
        rx = float(primitive["radiusX"]) / 1000.0 * width
        ry = float(primitive["radiusY"]) / 1000.0 * height
        rotation = math.radians(float(primitive.get("rotationDegrees", 0)))
        if primitive_type == "ellipse":
            angles = np.linspace(0.0, 2 * math.pi, max(48, round(max(rx, ry) * 6)), endpoint=False)
            closed = True
        else:
            start, end = _arc_angles(float(primitive["startDegrees"]), float(primitive["endDegrees"]), bool(primitive["anticlockwise"]))
            angles = np.linspace(start, end, max(24, round(abs(end - start) * max(rx, ry) * 1.5)))
        local = np.column_stack((np.cos(angles) * rx, np.sin(angles) * ry))
        matrix = np.array([[math.cos(rotation), -math.sin(rotation)], [math.sin(rotation), math.cos(rotation)]])
        return center + local @ matrix.T, closed
    if primitive_type == "quadraticBezier":
        start, control, end = (_xy(primitive[key], width, height) for key in ("from", "control", "to"))
        t = np.linspace(0.0, 1.0, _curve_steps([start, control, end]))[:, None]
        return (1 - t) ** 2 * start + 2 * (1 - t) * t * control + t ** 2 * end, False
    if primitive_type == "cubicBezier":
        start, c1, c2, end = (_xy(primitive[key], width, height) for key in ("from", "control1", "control2", "to"))
        t = np.linspace(0.0, 1.0, _curve_steps([start, c1, c2, end]))[:, None]
        return (1 - t) ** 3 * start + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * end, False
    return np.empty((0, 2)), False


def _distance_metrics(points: np.ndarray, distance: np.ndarray) -> dict[str, float] | None:
    if not len(points):
        return None
    rounded = np.rint(points).astype(np.int32)
    rounded[:, 0] = np.clip(rounded[:, 0], 0, distance.shape[1] - 1)
    rounded[:, 1] = np.clip(rounded[:, 1], 0, distance.shape[0] - 1)
    values = distance[rounded[:, 1], rounded[:, 0]]
    return {
        "meanDistancePixels": round(float(np.mean(values)), 4),
        "p95DistancePixels": round(float(np.percentile(values, 95)), 4),
        "maxDistancePixels": round(float(np.max(values)), 4),
    }


def _line_signature(primitive: dict[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    if primitive.get("type") != "line":
        return None
    start = np.array([float(primitive["from"]["x"]), float(primitive["from"]["y"])])
    end = np.array([float(primitive["to"]["x"]), float(primitive["to"]["y"])])
    return (start, end) if tuple(start) <= tuple(end) else (end, start)


def _duplicate_pairs(primitives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    for left_index, left in enumerate(primitives):
        for right_index in range(left_index + 1, len(primitives)):
            right = primitives[right_index]
            left_line, right_line = _line_signature(left), _line_signature(right)
            if left_line and right_line:
                endpoint_distance = (np.linalg.norm(left_line[0] - right_line[0]) + np.linalg.norm(left_line[1] - right_line[1])) / 2
                left_vector, right_vector = left_line[1] - left_line[0], right_line[1] - right_line[0]
                denominator = max(1e-9, np.linalg.norm(left_vector) * np.linalg.norm(right_vector))
                angle = math.degrees(math.acos(max(-1.0, min(1.0, abs(float(np.dot(left_vector, right_vector))) / denominator))))
                if endpoint_distance <= 12 and angle <= 2:
                    duplicates.append({"left": left_index, "right": right_index, "type": "line", "endpointDistance": round(float(endpoint_distance), 3), "angleDifference": round(angle, 3)})
            if left.get("type") == right.get("type") == "circle":
                left_center = np.array([left["center"]["x"], left["center"]["y"]], dtype=float)
                right_center = np.array([right["center"]["x"], right["center"]["y"]], dtype=float)
                if np.linalg.norm(left_center - right_center) <= 12 and abs(float(left["radius"]) - float(right["radius"])) <= 12:
                    duplicates.append({"left": left_index, "right": right_index, "type": "circle"})
    return duplicates


def _filled_interior_mask(primitive: dict[str, Any], width: int, height: int) -> np.ndarray | None:
    mask = np.zeros((height, width), np.uint8)
    primitive_type = primitive.get("type")
    if primitive_type in {"polygon", "face"}:
        points = np.rint([_xy(point, width, height) for point in primitive["points"]]).astype(np.int32)
        cv2.fillPoly(mask, [points], 255)
    elif primitive_type in {"rect", "roundedRect"}:
        box = primitive["box"]
        p1 = _xy({"x": box["x"], "y": box["y"]}, width, height)
        p2 = _xy({"x": box["x"] + box["width"], "y": box["y"] + box["height"]}, width, height)
        cv2.rectangle(mask, tuple(np.rint(p1).astype(int)), tuple(np.rint(p2).astype(int)), 255, -1)
    elif primitive_type == "circle":
        center = tuple(np.rint(_xy(primitive["center"], width, height)).astype(int))
        radius = round(float(primitive["radius"]) / 1000.0 * (width + height) / 2.0)
        cv2.circle(mask, center, radius, 255, -1)
    elif primitive_type == "ellipse":
        center = tuple(np.rint(_xy(primitive["center"], width, height)).astype(int))
        axes = (round(float(primitive["radiusX"]) / 1000.0 * width), round(float(primitive["radiusY"]) / 1000.0 * height))
        cv2.ellipse(mask, center, axes, float(primitive.get("rotationDegrees", 0)), 0, 360, 255, -1)
    else:
        return None
    kernel = np.ones((3, 3), np.uint8)
    return cv2.erode(mask, kernel, iterations=2)


def verify_candidate(candidate: dict[str, Any], source_panel: Path) -> dict[str, Any]:
    if candidate.get("schemaVersion") != CANDIDATE_SCHEMA:
        raise ValueError("candidate_schema_mismatch")
    gray = cv2.imread(str(source_panel), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("source_panel_decode_failed")
    height, width = gray.shape
    ink = gray < 210
    distance = cv2.distanceTransform((~ink).astype(np.uint8), cv2.DIST_L2, 3)
    threshold_mean = max(1.25, min(width, height) * 0.018)
    threshold_p95 = max(2.5, min(width, height) * 0.035)
    primitive_evidence: list[dict[str, Any]] = []
    geometry_failures: list[str] = []
    fill_failures: list[str] = []
    drawable_masks: list[np.ndarray] = []
    for index, primitive in enumerate(candidate.get("primitives", [])):
        points, closed = sample_primitive(primitive, width, height)
        metrics = _distance_metrics(points, distance)
        evidence: dict[str, Any] = {"index": index, "type": primitive.get("type"), "closed": closed, "pixelSupport": metrics}
        if metrics and (metrics["meanDistancePixels"] > threshold_mean or metrics["p95DistancePixels"] > threshold_p95):
            geometry_failures.append(f"primitive_{index}_pixel_support")
        if len(points):
            rendered = np.zeros_like(gray)
            cv2.polylines(rendered, [np.rint(points).astype(np.int32)], closed, 255, 1, cv2.LINE_AA)
            drawable_masks.append(rendered)
        fill = primitive.get("style", {}).get("fill")
        if fill not in (None, "#ffffff") and primitive.get("type") != "textGlyph":
            interior = _filled_interior_mask(primitive, width, height)
            if interior is None or cv2.countNonZero(interior) < 8:
                evidence["fillVerification"] = {"status": "fail", "reason": "unsupported_or_empty_interior"}
                fill_failures.append(f"primitive_{index}_fill_unverified")
            else:
                interior_pixels = interior > 0
                solid_ratio = float((gray[interior_pixels] < 80).mean())
                status = "pass" if solid_ratio >= 0.72 else "fail"
                evidence["fillVerification"] = {"status": status, "solidPixelRatio": round(solid_ratio, 4), "minimum": 0.72}
                if status == "fail":
                    fill_failures.append(f"primitive_{index}_hollow_or_non_solid")
        primitive_evidence.append(evidence)
    duplicates = _duplicate_pairs(candidate.get("primitives", []))
    candidate_mask = np.maximum.reduce(drawable_masks) if drawable_masks else np.zeros_like(gray)
    tolerance = cv2.dilate((candidate_mask > 0).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    source_recall = float((ink & tolerance).sum() / max(1, ink.sum()))
    source_tolerance = cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    candidate_pixels = candidate_mask > 0
    candidate_precision = float((candidate_pixels & source_tolerance).sum() / max(1, candidate_pixels.sum()))
    pixel_gate = "pass" if not geometry_failures and candidate_precision >= 0.86 and source_recall >= 0.72 else "fail"
    duplicate_gate = "pass" if not duplicates else "fail"
    fill_gate = "pass" if not fill_failures else "fail"
    seed_status = "verified_refinement_seed" if pixel_gate == duplicate_gate == fill_gate == "pass" else "repair_pending"
    return {
        "schemaVersion": "graphic-vector-candidate-verification-v1",
        "assetId": candidate["assetId"],
        "panelId": candidate["panelId"],
        "sourcePanel": str(source_panel.resolve()),
        "candidateSvgSha256": candidate.get("evidence", {}).get("candidateSvgSha256"),
        "thresholds": {"meanDistancePixels": round(threshold_mean, 4), "p95DistancePixels": round(threshold_p95, 4), "candidatePrecision": 0.86, "sourceRecall": 0.72, "solidPixelRatio": 0.72},
        "primitiveEvidence": primitive_evidence,
        "duplicateGeometry": duplicates,
        "aggregate": {"candidatePrecision": round(candidate_precision, 4), "sourceRecall": round(source_recall, 4)},
        "gates": {
            "pixelSupport": {"status": pixel_gate, "failures": geometry_failures},
            "duplicateSuppression": {"status": duplicate_gate},
            "solidFillOnly": {"status": fill_gate, "failures": fill_failures},
            "topology": {"status": "pending", "reason": "panel_semantic_topology_comparison_required"},
            "finalAcceptance": {"status": "blocked", "reason": "verified_seed_still_requires_deterministic_coordinate_refinement"},
        },
        "status": seed_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source-panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_candidate(json.loads(args.candidate.read_text(encoding="utf-8")), args.source_panel)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if result["status"] == "verified_refinement_seed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
