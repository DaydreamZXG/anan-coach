#!/usr/bin/env python3
"""Compact semantic extraction for line sketches and signed bar mini charts."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw


VERSION = "semantic-microcharts-v1"
LINE_GRAMMARS = {
    "line_sequence",
    "line_options",
    "line_with_axis",
    "line_options_with_axis",
    "curve_chart",
    "curve_options",
    "curve_options_with_axis",
}


def _component_rows(gray: np.ndarray) -> list[tuple[int, int, int, int, int, np.ndarray]]:
    binary = (gray < 180).astype(np.uint8)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, 8)
    rows: list[tuple[int, int, int, int, int, np.ndarray]] = []
    for component in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[component])
        if area < 20 or width < 14 or height < 5:
            continue
        rows.append((area, x, y, width, height, labels == component))
    return rows


def _centerline(
    mask: np.ndarray,
    bounds: tuple[int, int, int, int],
    *,
    normalization_bounds: tuple[int, int, int, int] | None = None,
    smooth: bool = False,
) -> list[dict[str, float]]:
    x, y, width, height = bounds
    samples: list[tuple[float, float]] = []
    for column in range(x, x + width):
        ys = np.flatnonzero(mask[:, column])
        if ys.size:
            samples.append((float(column), float(np.median(ys))))
    if len(samples) < 2:
        raise ValueError("line component has fewer than two x samples")
    curve = np.asarray(samples, dtype=np.float32).reshape(-1, 1, 2)
    # Piecewise-linear charts should contain only actual turning geometry.  Smooth
    # curves keep a few more control points, but those are never rendered as data
    # markers (markers are extracted separately below).
    epsilon_ratio = 0.045 if smooth else 0.105
    epsilon = max(2.5, min(width, height) * epsilon_ratio)
    simplified = cv2.approxPolyDP(curve, epsilon, False).reshape(-1, 2)
    # Anti-aliased strokes occasionally add a one-pixel hook at either end.
    if len(simplified) > 3 and math.dist(simplified[0], simplified[1]) < 3:
        simplified = simplified[1:]
    if len(simplified) > 3 and math.dist(simplified[-1], simplified[-2]) < 3:
        simplified = simplified[:-1]
    normal_x, normal_y, normal_width, normal_height = normalization_bounds or bounds
    return [
        {
            "x": round((float(px) - normal_x) / max(1, normal_width - 1), 5),
            "y": round((float(py) - normal_y) / max(1, normal_height - 1), 5),
        }
        for px, py in simplified
    ]


def _marker_shape(contour: np.ndarray) -> str:
    perimeter = cv2.arcLength(contour, True)
    polygon = cv2.approxPolyDP(contour, max(0.8, perimeter * 0.06), True)
    if len(polygon) == 4:
        _x, _y, width, height = cv2.boundingRect(polygon)
        fill_ratio = cv2.contourArea(polygon) / max(1, width * height)
        return "diamond" if fill_ratio < 0.7 else "square"
    return "circle"


def _detect_markers(
    mask: np.ndarray,
    bounds: tuple[int, int, int, int],
) -> list[dict[str, float | str]]:
    x, y, width, height = bounds
    crop = (mask[y : y + height, x : x + width] * 255).astype(np.uint8)
    markers: list[tuple[float, float, str]] = []

    # Hollow circles/squares/diamonds create enclosed holes even when their outer
    # stroke is connected to the line.  This is more reliable than treating every
    # polyline vertex as a marker.
    contours, hierarchy = cv2.findContours(crop, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is not None:
        for index, contour in enumerate(contours):
            if hierarchy[0][index][3] < 0:
                continue
            area = cv2.contourArea(contour)
            marker_x, marker_y, marker_width, marker_height = cv2.boundingRect(contour)
            if not 4 <= area <= 500 or not 3 <= marker_width <= 32 or not 3 <= marker_height <= 32:
                continue
            moment = cv2.moments(contour)
            if moment["m00"]:
                center_x = moment["m10"] / moment["m00"]
                center_y = moment["m01"] / moment["m00"]
            else:
                center_x = marker_x + marker_width / 2
                center_y = marker_y + marker_height / 2
            markers.append((center_x, center_y, _marker_shape(contour)))

    # Filled markers have no hole.  Their centres are local maxima of foreground
    # thickness; ordinary antialiased line bends remain below this threshold.
    distance = cv2.distanceTransform(crop, cv2.DIST_L2, 3)
    maxima = (distance >= 3.0) & (distance == cv2.dilate(distance, np.ones((5, 5), np.uint8)))
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(maxima.astype(np.uint8), 8)
    for component in range(1, count):
        if stats[component, cv2.CC_STAT_AREA] < 1:
            continue
        center_x, center_y = (float(value) for value in centroids[component])
        if any(math.hypot(center_x - old_x, center_y - old_y) <= 5 for old_x, old_y, _shape in markers):
            continue
        patch_x1, patch_y1 = max(0, int(center_x) - 5), max(0, int(center_y) - 5)
        patch_x2, patch_y2 = min(width, int(center_x) + 6), min(height, int(center_y) + 6)
        core = ((distance[patch_y1:patch_y2, patch_x1:patch_x2] >= 2.0) * 255).astype(np.uint8)
        core_contours, _ = cv2.findContours(core, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        shape = "circle"
        if core_contours:
            shape = _marker_shape(max(core_contours, key=cv2.contourArea))
        markers.append((center_x, center_y, shape))

    markers.sort(key=lambda marker: marker[0])
    return [
        {
            "x": round(center_x / max(1, width - 1), 5),
            "y": round(center_y / max(1, height - 1), 5),
            "shape": shape,
        }
        for center_x, center_y, shape in markers
    ]


def _trim_marker_hooks(
    points: list[dict[str, float]],
    width: int,
    height: int,
    markers: list[dict[str, float | str]],
) -> list[dict[str, float]]:
    if not markers or len(points) <= 3:
        return points
    trimmed = list(points)
    first_distance = math.hypot(
        (trimmed[1]["x"] - trimmed[0]["x"]) * width,
        (trimmed[1]["y"] - trimmed[0]["y"]) * height,
    )
    if first_distance <= 10:
        del trimmed[1]
    if len(trimmed) > 3:
        last_distance = math.hypot(
            (trimmed[-1]["x"] - trimmed[-2]["x"]) * width,
            (trimmed[-1]["y"] - trimmed[-2]["y"]) * height,
        )
        if last_distance <= 10:
            del trimmed[-2]
    return trimmed


def _axis_geometry(
    mask: np.ndarray,
    bounds: tuple[int, int, int, int],
) -> dict[str, float | bool] | None:
    x, y, width, height = bounds
    crop = mask[y : y + height, x : x + width]
    row_counts = crop.sum(axis=1)
    column_counts = crop.sum(axis=0)
    axis_y = int(np.argmax(row_counts))
    axis_x = int(np.argmax(column_counts))
    show_x = int(row_counts[axis_y]) >= width * 0.55
    show_y = int(column_counts[axis_x]) >= height * 0.55
    if not (show_x or show_y):
        return None
    return {
        "showX": bool(show_x),
        "showY": bool(show_y),
        "originX": round(axis_x / max(1, width - 1), 5),
        "originY": round(axis_y / max(1, height - 1), 5),
    }


def _axis_aware_panels(
    rows: list[tuple[int, int, int, int, int, np.ndarray]],
    grammar: str,
    image_shape: tuple[int, ...],
) -> list[tuple[tuple[int, int, int, int], np.ndarray, dict[str, float | bool]]] | None:
    height, width = image_shape[:2]
    axis_rows: list[tuple[int, int, int, int, int, np.ndarray, dict[str, float | bool]]] = []
    for row in rows:
        area, x, y, panel_width, panel_height, mask = row
        axis = _axis_geometry(mask, (x, y, panel_width, panel_height))
        if axis and panel_width >= width * 0.2 and panel_height >= height * 0.2:
            axis_rows.append((*row, axis))
    if not axis_rows:
        return None
    option_mode = "options" in grammar
    if option_mode:
        axis_rows.sort(key=lambda row: (round((row[2] + row[4] / 2) / max(20, height / 2)), row[1]))
        if not 2 <= len(axis_rows) <= 8:
            return None
    else:
        axis_rows = [max(axis_rows, key=lambda row: row[0])]

    result: list[tuple[tuple[int, int, int, int], np.ndarray, dict[str, float | bool]]] = []
    for _area, x, y, panel_width, panel_height, axis_mask, axis in axis_rows:
        candidates = []
        for candidate in rows:
            candidate_area, candidate_x, candidate_y, candidate_width, candidate_height, candidate_mask = candidate
            if candidate_mask is axis_mask:
                continue
            center_x = candidate_x + candidate_width / 2
            center_y = candidate_y + candidate_height / 2
            if x <= center_x <= x + panel_width and y <= center_y <= y + panel_height:
                if candidate_width >= panel_width * 0.25 and candidate_height >= 5:
                    candidates.append(candidate)
        if not candidates:
            continue
        curve = max(candidates, key=lambda row: row[0])
        result.append(((x, y, panel_width, panel_height), curve[5], axis))
    if option_mode and len(result) != len(axis_rows):
        return None
    return result or None


def extract_line_visual(rgb: np.ndarray, asset_id: str, source_sha256: str, grammar: str) -> dict[str, Any]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    rows = _component_rows(gray)
    if not rows:
        raise ValueError("no line component found")
    option_mode = "options" in grammar
    axis_panels = _axis_aware_panels(rows, grammar, rgb.shape)
    panels: list[dict[str, Any]] = []
    if axis_panels:
        for position, (panel_bounds, curve_mask, axis) in enumerate(axis_panels):
            curve_rows = [row for row in rows if row[5] is curve_mask]
            if not curve_rows:
                raise ValueError("axis-aware curve component disappeared")
            _area, curve_x, curve_y, curve_width, curve_height, _mask = curve_rows[0]
            points = _centerline(
                curve_mask,
                (curve_x, curve_y, curve_width, curve_height),
                normalization_bounds=panel_bounds,
                smooth="curve" in grammar,
            )
            panels.append({
                "id": f"{asset_id}-{position}",
                "label": chr(ord("A") + position) if option_mode else None,
                "position": position,
                "bounds": {"x": panel_bounds[0], "y": panel_bounds[1], "width": panel_bounds[2], "height": panel_bounds[3]},
                "chart": {
                    "type": "polyline",
                    "points": points,
                    "interpolation": "smooth" if "curve" in grammar else "linear",
                    "strokeStyle": "solid",
                    "strokeWidth": 2,
                    "markers": [],
                    "axis": axis,
                },
            })
        return _visual(rgb, asset_id, source_sha256, grammar, panels)

    if option_mode:
        # The actual option polylines dominate their tiny A/B/C/D labels.
        selected = [row for row in rows if row[3] >= 0.08 * rgb.shape[1]]
        selected.sort(key=lambda row: (round((row[2] + row[4] / 2) / max(20, rgb.shape[0] / 2)), row[1]))
        if not 2 <= len(selected) <= 8:
            raise ValueError(f"expected 2-8 line option panels, got {len(selected)}")
    else:
        selected = [max(rows, key=lambda row: row[0])]
    for position, (_area, x, y, width, height, mask) in enumerate(selected):
        markers = _detect_markers(mask, (x, y, width, height))
        points = _trim_marker_hooks(
            _centerline(mask, (x, y, width, height), smooth="curve" in grammar),
            width,
            height,
            markers,
        )
        panels.append(
            {
                "id": f"{asset_id}-{position}",
                "label": chr(ord("A") + position) if option_mode else None,
                "position": position,
                "bounds": {"x": x, "y": y, "width": width, "height": height},
                "chart": {
                    "type": "polyline",
                    "points": points,
                    "interpolation": "smooth" if "curve" in grammar else "linear",
                    "strokeStyle": "solid",
                    "strokeWidth": 2,
                    "markers": markers,
                    "axis": None,
                },
            }
        )
    return _visual(rgb, asset_id, source_sha256, grammar, panels)


def _baseline_row(gray: np.ndarray) -> int:
    dark = gray < 180
    row_counts = dark.sum(axis=1)
    height = gray.shape[0]
    candidates = [(int(row_counts[y]), y) for y in range(max(1, int(height * 0.15)), min(height - 1, int(height * 0.9)))]
    score, baseline = max(candidates)
    if score < gray.shape[1] * 0.35:
        raise ValueError("no dominant signed-bar baseline")
    return baseline


def extract_signed_bar_visual(rgb: np.ndarray, asset_id: str, source_sha256: str) -> dict[str, Any]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    baseline = _baseline_row(gray)
    dark = gray < 180
    search_width = max(1, int(gray.shape[1] * 0.2))
    column_counts = dark[:, :search_width].sum(axis=0)
    axis_x_candidate = int(np.argmax(column_counts))
    axis_x = axis_x_candidate if int(column_counts[axis_x_candidate]) >= gray.shape[0] * 0.55 else None
    binary = ((gray < 180) * 255).astype(np.uint8)
    contours, _hierarchy = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    bars: list[dict[str, Any]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if width < 8 or height < 8 or area < width * height * 0.65:
            continue
        if width > gray.shape[1] * 0.3 or height > gray.shape[0] * 0.85:
            continue
        above = y + height <= baseline + 3 and baseline - (y + height) <= 5
        below = y >= baseline - 3 and y - baseline <= 5
        if not above and not below:
            continue
        signed_height = baseline - y if above else -(y + height - baseline)
        bars.append({"x": x, "width": width, "signedHeight": signed_height})
    bars.sort(key=lambda row: row["x"])
    if not 2 <= len(bars) <= 12:
        raise ValueError(f"expected 2-12 signed bars, got {len(bars)}")
    maximum = max(abs(row["signedHeight"]) for row in bars)
    normalized = [
        {
            "x": round(row["x"] / max(1, rgb.shape[1] - 1), 5),
            "width": round(row["width"] / max(1, rgb.shape[1] - 1), 5),
            "value": round(row["signedHeight"] / maximum, 5),
        }
        for row in bars
    ]
    baseline_columns = np.flatnonzero(dark[max(0, baseline - 1) : min(gray.shape[0], baseline + 2), :].any(axis=0))
    baseline_start = int(baseline_columns.min()) if baseline_columns.size else 0
    baseline_end = int(baseline_columns.max()) if baseline_columns.size else gray.shape[1] - 1
    panels = [{
        "id": f"{asset_id}-0",
        "label": None,
        "position": 0,
        "bounds": {"x": 0, "y": 0, "width": int(rgb.shape[1]), "height": int(rgb.shape[0])},
        "chart": {
            "type": "signedBar",
            "baseline": round(baseline / max(1, rgb.shape[0] - 1), 5),
            "baselineStart": round(baseline_start / max(1, rgb.shape[1] - 1), 5),
            "baselineEnd": round(baseline_end / max(1, rgb.shape[1] - 1), 5),
            "verticalAxisX": round(axis_x / max(1, rgb.shape[1] - 1), 5) if axis_x is not None else None,
            "bars": normalized,
        },
    }]
    return _visual(rgb, asset_id, source_sha256, "signed_bar", panels)


def _visual(
    rgb: np.ndarray,
    asset_id: str,
    source_sha256: str,
    grammar: str,
    panels: list[dict[str, Any]],
) -> dict[str, Any]:
    point_count = sum(len(panel["chart"].get("points", [])) for panel in panels)
    bar_count = sum(len(panel["chart"].get("bars", [])) for panel in panels)
    return {
        "schemaVersion": "semantic-microcharts-v1",
        "kind": "semanticMicrocharts",
        "visualGrammar": grammar,
        "fidelityTier": "semantic_vector",
        "layoutType": "sequence",
        "coordinateSpace": {"width": int(rgb.shape[1]), "height": int(rgb.shape[0])},
        "panels": panels,
        "evidence": {
            "sourceAsset": asset_id,
            "sourceSha256": source_sha256,
            "geometryEngine": "opencv_connected_components",
            "converterVersion": VERSION,
            "renderSha256": "0" * 64,
        },
        "complexity": {
            "panelCount": len(panels),
            "pointCount": point_count,
            "barCount": bar_count,
            "estimatedDrawCommandCount": max(1, point_count + bar_count * 2),
        },
    }


def render_semantic_microcharts(visual: dict[str, Any]) -> np.ndarray:
    width = int(visual["coordinateSpace"]["width"])
    height = int(visual["coordinateSpace"]["height"])
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for panel in visual["panels"]:
        bounds = panel["bounds"]
        chart = panel["chart"]
        if chart["type"] == "polyline":
            points = [
                (
                    bounds["x"] + point["x"] * max(1, bounds["width"] - 1),
                    bounds["y"] + point["y"] * max(1, bounds["height"] - 1),
                )
                for point in chart["points"]
            ]
            if chart.get("axis"):
                axis = chart["axis"]
                origin_x = bounds["x"] + axis["originX"] * max(1, bounds["width"] - 1)
                origin_y = bounds["y"] + axis["originY"] * max(1, bounds["height"] - 1)
                if axis["showX"]:
                    draw.line((bounds["x"], origin_y, bounds["x"] + bounds["width"], origin_y), fill="#111111", width=1)
                if axis["showY"]:
                    draw.line((origin_x, bounds["y"], origin_x, bounds["y"] + bounds["height"]), fill="#111111", width=1)
            if chart["interpolation"] == "smooth" and len(points) >= 3:
                points = _smooth_points(points)
            draw.line(points, fill="#111111", width=max(2, int(chart.get("strokeWidth", 2))), joint="curve")
            for marker in chart.get("markers", []):
                marker_x = bounds["x"] + marker["x"] * max(1, bounds["width"] - 1)
                marker_y = bounds["y"] + marker["y"] * max(1, bounds["height"] - 1)
                _draw_marker(draw, marker_x, marker_y, str(marker["shape"]))
            if panel["label"]:
                label_x = bounds["x"] + bounds["width"] / 2
                label_y = min(height - 2, bounds["y"] + bounds["height"] + 13)
                draw.text((label_x, label_y), panel["label"], fill="#111111", anchor="mm")
        elif chart["type"] == "signedBar":
            baseline = chart["baseline"] * max(1, height - 1)
            axis_x = chart.get("verticalAxisX")
            if axis_x is not None:
                absolute_axis_x = axis_x * max(1, width - 1)
                draw.line((absolute_axis_x, 0, absolute_axis_x, height), fill="#111111", width=1)
            baseline_start = chart.get("baselineStart", 0.0) * max(1, width - 1)
            baseline_end = chart.get("baselineEnd", 1.0) * max(1, width - 1)
            draw.line((baseline_start, baseline, baseline_end, baseline), fill="#111111", width=1)
            has_positive = any(bar["value"] > 0 for bar in chart["bars"])
            has_negative = any(bar["value"] < 0 for bar in chart["bars"])
            if has_positive and has_negative:
                scale = min(baseline, height - baseline - 1) * 0.92
            elif has_positive:
                scale = baseline * 0.92
            else:
                scale = (height - baseline - 1) * 0.92
            for bar in chart["bars"]:
                x1 = bar["x"] * max(1, width - 1)
                x2 = x1 + bar["width"] * max(1, width - 1)
                value_y = baseline - bar["value"] * scale
                draw.rectangle((x1, min(baseline, value_y), x2, max(baseline, value_y)), outline="#111111", width=1)
    return np.asarray(canvas, dtype=np.uint8)


def _smooth_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Catmull-Rom interpolation for deterministic smooth chart rendering."""
    if len(points) < 3:
        return points
    padded = [points[0], *points, points[-1]]
    rendered: list[tuple[float, float]] = []
    for index in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[index - 1 : index + 3]
        for step in range(12):
            t = step / 12
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t + (2*p0[0] - 5*p1[0] + 4*p2[0] - p3[0]) * t2 + (-p0[0] + 3*p1[0] - 3*p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t + (2*p0[1] - 5*p1[1] + 4*p2[1] - p3[1]) * t2 + (-p0[1] + 3*p1[1] - 3*p2[1] + p3[1]) * t3)
            rendered.append((x, y))
    rendered.append(points[-1])
    return rendered


def _draw_marker(draw: ImageDraw.ImageDraw, x: float, y: float, shape: str) -> None:
    radius = 3
    if shape == "diamond":
        draw.polygon([(x, y-radius-1), (x+radius+1, y), (x, y+radius+1), (x-radius-1, y)], outline="#111111", fill="white")
    elif shape == "square":
        draw.rectangle((x-radius, y-radius, x+radius, y+radius), outline="#111111", fill="white")
    else:
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill="#111111")


def stamp_render_hash(visual: dict[str, Any], rendered_path: Path) -> None:
    visual["evidence"]["renderSha256"] = hashlib.sha256(rendered_path.read_bytes()).hexdigest()
