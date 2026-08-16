#!/usr/bin/env python3
"""Semantic extraction and deterministic rendering for patterned pie options.

These figures are not arbitrary line art.  They are multiple-choice pie charts
whose slices use a small, stable texture vocabulary.  The extractor therefore
owns only geometry (circle centres and radial boundaries); texture meaning is a
compact semantic contract that the mini-program can render consistently.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


PATTERNED_PIE_VERSION = "patterned-pie-semantic-v1"
PATTERNS = ("dots", "diagonal", "grid")


def _radial_ink_score(gray: np.ndarray, center_x: float, center_y: float, radius: float, degrees: float) -> float:
    """Return the share of dark pixels along an inner radius.

    A real slice boundary stays dark for almost the full radius.  Hatch/grid
    strokes intersect a ray only occasionally, so their score is much lower.
    """

    theta = math.radians(degrees)
    height, width = gray.shape
    dark = 0
    total = 0
    for distance in np.linspace(radius * 0.08, radius * 0.91, 80):
        x = round(center_x + distance * math.sin(theta))
        y = round(center_y - distance * math.cos(theta))
        if not (0 <= x < width and 0 <= y < height):
            continue
        total += 1
        dark += int(gray[y, x] < 90)
    return dark / total if total else 0.0


def _pick_boundary_angles(gray: np.ndarray, center_x: float, center_y: float, radius: float) -> tuple[float, float, float]:
    scores = [
        (_radial_ink_score(gray, center_x, center_y, radius, degrees / 2), degrees / 2)
        for degrees in range(720)
    ]
    top_score, top = max(
        (row for row in scores if row[1] >= 350 or row[1] <= 10),
        key=lambda row: row[0],
    )
    candidates: list[tuple[float, float]] = []
    for score, degrees in sorted(
        (row for row in scores if 200 <= row[1] <= 349.5),
        reverse=True,
    ):
        if score < 0.65:
            break
        if all(abs(degrees - previous) >= 8 for _old_score, previous in candidates):
            candidates.append((score, degrees))
        if len(candidates) == 2:
            break
    if top_score < 0.65 or len(candidates) != 2:
        raise ValueError("expected three continuous radial boundaries")
    first, second = sorted(degrees for _score, degrees in candidates)
    # Snap the nearly vertical boundary to the semantic convention.  The source
    # scan can shift it by half a degree because of anti-aliasing.
    return 0.0, round(first, 1), round(second, 1)


def _detect_aligned_circles(rgb: np.ndarray) -> list[tuple[float, float, float]]:
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    detected = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(40, width // 7),
        param1=100,
        param2=45,
        minRadius=max(12, min(height, width) // 12),
        maxRadius=max(24, min(height, width) // 2),
    )
    if detected is None:
        raise ValueError("no option circles detected")
    circles = [(float(x), float(y), float(radius)) for x, y, radius in detected[0]]
    median_radius = float(np.median([circle[2] for circle in circles]))
    median_y = float(np.median([circle[1] for circle in circles]))
    circles = [
        circle
        for circle in circles
        if abs(circle[2] - median_radius) <= median_radius * 0.18
        and abs(circle[1] - median_y) <= median_radius * 0.25
    ]
    circles.sort(key=lambda circle: circle[0])
    if not 3 <= len(circles) <= 8:
        raise ValueError(f"expected 3-8 aligned options, got {len(circles)}")
    return circles


def extract_patterned_pie_options(
    rgb: np.ndarray,
    asset_id: str,
    source_sha256: str,
    *,
    layout_engine: str = "mineru_v4_image_block",
    semantic_engine: str = "qwen3-vl-30b-a3b",
) -> dict[str, Any]:
    """Extract a compact semantic payload from an aligned option row."""

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    circles = _detect_aligned_circles(rgb)
    labels = [chr(ord("A") + index) for index in range(len(circles))]
    options: list[dict[str, Any]] = []
    boundary_scores: list[float] = []
    for position, ((center_x, center_y, radius), label) in enumerate(zip(circles, labels)):
        top, diagonal_start, grid_start = _pick_boundary_angles(gray, center_x, center_y, radius)
        boundary_scores.extend(
            _radial_ink_score(gray, center_x, center_y, radius, angle)
            for angle in (top, diagonal_start, grid_start)
        )
        options.append(
            {
                "id": f"{asset_id}-{label}",
                "label": label,
                "position": position,
                "center": {"x": round(center_x, 1), "y": round(center_y, 1)},
                "radius": round(radius, 1),
                "boundariesDegrees": [top, diagonal_start, grid_start],
                "sectors": [
                    {"pattern": "dots", "startDegrees": top, "endDegrees": diagonal_start},
                    {"pattern": "diagonal", "startDegrees": diagonal_start, "endDegrees": grid_start},
                    {"pattern": "grid", "startDegrees": grid_start, "endDegrees": 360.0},
                ],
            }
        )
    return {
        "schemaVersion": "patterned-pie-options-v1",
        "kind": "patternedPieOptions",
        "visualGrammar": "patterned_pie",
        "fidelityTier": "semantic_vector",
        "layoutType": "sequence",
        "coordinateSpace": {"width": int(rgb.shape[1]), "height": int(rgb.shape[0])},
        "options": options,
        "textureVocabulary": {
            "dots": {"spacing": 6.0, "radius": 0.45},
            "grid": {"spacing": 7.0, "lineWidth": 1.0},
            "diagonal": {"spacing": 5.0, "lineWidth": 1.0, "angleDegrees": 45.0},
        },
        "evidence": {
            "sourceAsset": asset_id,
            "sourceSha256": source_sha256,
            "layoutEngine": layout_engine,
            "semanticEngine": semantic_engine,
            "geometryEngine": "opencv_hough_radial_scan",
            "converterVersion": PATTERNED_PIE_VERSION,
            "optionCount": len(options),
            "sectorsPerOption": 3,
            "patternVocabulary": list(PATTERNS),
            "boundaryMeanConfidence": round(float(np.mean(boundary_scores)), 6),
            "renderSha256": "0" * 64,
        },
        "complexity": {
            "optionCount": len(options),
            "sectorCount": len(options) * 3,
            "patternDefinitionCount": 3,
            "estimatedDrawCommandCount": len(options) * 7 + 3,
        },
    }


def _sector_polygon(center_x: float, center_y: float, radius: float, start: float, end: float) -> list[tuple[float, float]]:
    steps = max(2, math.ceil((end - start) / 1.0))
    points: list[tuple[float, float]] = [(center_x, center_y)]
    for degrees in np.linspace(start, end, steps + 1):
        theta = math.radians(float(degrees))
        points.append((center_x + radius * math.sin(theta), center_y - radius * math.cos(theta)))
    return points


def _pattern_layer(size: tuple[int, int], pattern: str, vocabulary: dict[str, Any]) -> Image.Image:
    width, height = size
    layer = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(layer)
    definition = vocabulary[pattern]
    spacing = max(3, round(definition["spacing"]))
    if pattern == "dots":
        for y in range(spacing // 2, height, spacing):
            for x in range(spacing // 2, width, spacing):
                draw.point((x, y), fill="#111111")
    elif pattern == "grid":
        for x in range(0, width + spacing, spacing):
            draw.line((x, 0, x, height), fill="#111111", width=1)
        for y in range(0, height + spacing, spacing):
            draw.line((0, y, width, y), fill="#111111", width=1)
    elif pattern == "diagonal":
        for offset in range(-height, width + height, spacing):
            draw.line((offset, 0, offset + height, height), fill="#111111", width=1)
    else:
        raise ValueError(f"unknown pattern {pattern}")
    return layer


def render_patterned_pie_options(visual: dict[str, Any]) -> np.ndarray:
    width = int(visual["coordinateSpace"]["width"])
    height = int(visual["coordinateSpace"]["height"])
    canvas = Image.new("RGB", (width, height), "white")
    vocabulary = visual["textureVocabulary"]
    pattern_layers = {
        pattern: _pattern_layer((width, height), pattern, vocabulary)
        for pattern in PATTERNS
    }
    draw = ImageDraw.Draw(canvas)
    for option in visual["options"]:
        center_x = float(option["center"]["x"])
        center_y = float(option["center"]["y"])
        radius = float(option["radius"])
        for sector in option["sectors"]:
            mask = Image.new("L", (width, height), 0)
            ImageDraw.Draw(mask).polygon(
                _sector_polygon(
                    center_x,
                    center_y,
                    radius - 1,
                    float(sector["startDegrees"]),
                    float(sector["endDegrees"]),
                ),
                fill=255,
            )
            canvas.paste(pattern_layers[sector["pattern"]], (0, 0), mask)
        box = (center_x - radius, center_y - radius, center_x + radius, center_y + radius)
        draw.ellipse(box, outline="#111111", width=2)
        for angle in option["boundariesDegrees"]:
            theta = math.radians(float(angle))
            draw.line(
                (
                    center_x,
                    center_y,
                    center_x + radius * math.sin(theta),
                    center_y - radius * math.cos(theta),
                ),
                fill="#111111",
                width=2,
            )
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 40)
        except OSError:
            font = ImageFont.load_default()
        label_y = min(height - 8, center_y + radius + 28)
        draw.text((center_x, label_y), option["label"], fill="#111111", font=font, anchor="mm")
    return np.asarray(canvas, dtype=np.uint8)


def stamp_render_hash(visual: dict[str, Any], rendered_path: Path) -> None:
    digest = hashlib.sha256(rendered_path.read_bytes()).hexdigest()
    visual["evidence"]["renderSha256"] = digest
