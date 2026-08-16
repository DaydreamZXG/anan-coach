#!/usr/bin/env python3
"""Deterministic renderers for hand-verified statistical panels and simplex grids."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


STAT_VERSION = "semantic-statistical-panels-v1"
SIMPLEX_VERSION = "semantic-simplex-grids-v1"
FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def build_statistical_visual(asset_id: str, source_sha256: str, width: int, height: int, override: dict[str, Any]) -> dict[str, Any]:
    panels = override["panels"]
    return {
        "schemaVersion": STAT_VERSION,
        "kind": "semanticStatisticalPanels",
        "visualGrammar": "statistical_chart_panels",
        "fidelityTier": "semantic_vector",
        "layoutType": "vertical",
        "coordinateSpace": {"width": width, "height": height},
        "panels": panels,
        "evidence": {
            "sourceAsset": asset_id,
            "sourceSha256": source_sha256,
            "geometryEngine": "human_verified_chart_transcription",
            "converterVersion": STAT_VERSION,
            "renderSha256": "0" * 64,
        },
        "complexity": {
            "panelCount": len(panels),
            "categoryCount": sum(len(panel["categories"]) for panel in panels),
            "seriesCount": sum(len(panel["series"]) for panel in panels),
            "valueCount": sum(len(series["values"]) for panel in panels for series in panel["series"]),
            "estimatedDrawCommandCount": sum(len(series["values"]) * 3 for panel in panels for series in panel["series"]),
        },
    }


def render_statistical_visual(visual: dict[str, Any]) -> np.ndarray:
    width = int(visual["coordinateSpace"]["width"])
    height = int(visual["coordinateSpace"]["height"])
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    panels = visual["panels"]
    gap = 24
    panel_height = max(120, (height - gap * (len(panels) - 1)) // len(panels))
    for panel_index, panel in enumerate(panels):
        top = panel_index * (panel_height + gap)
        title_font = _font(max(12, width // 55))
        label_font = _font(max(9, width // 85))
        draw.text((width * 0.23, top + 4), panel["title"], fill="#111111", font=title_font)
        chart_left, chart_right = int(width * 0.09), int(width * 0.96)
        chart_top, chart_bottom = top + 45, min(height - 18, top + panel_height - 48)
        draw.line((chart_left, chart_top, chart_left, chart_bottom), fill="#666666", width=1)
        draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill="#666666", width=1)
        values = [float(value) for series in panel["series"] for value in series["values"] if value is not None]
        y_min = float(panel["yAxis"].get("min") if panel["yAxis"].get("min") is not None else min(0.0, min(values, default=0.0)))
        y_max = float(panel["yAxis"].get("max") if panel["yAxis"].get("max") is not None else max(values, default=1.0) * 1.12)
        categories = panel["categories"]
        group_width = (chart_right - chart_left) / max(1, len(categories))
        bar_series = [series for series in panel["series"] if series["render_as"] == "bar"]
        for series_index, series in enumerate(bar_series):
            bar_width = group_width * 0.62 / max(1, len(bar_series))
            for index, raw_value in enumerate(series["values"]):
                if raw_value is None:
                    continue
                value = float(raw_value)
                x1 = chart_left + index * group_width + group_width * 0.19 + series_index * bar_width
                x2 = x1 + bar_width * 0.88
                y = chart_bottom - (value - y_min) / max(1e-9, y_max - y_min) * (chart_bottom - chart_top)
                draw.rectangle((x1, y, x2, chart_bottom), fill="#6b6b6b")
                value_text = f"{value:g}"
                box = draw.textbbox((0, 0), value_text, font=label_font)
                draw.text(((x1 + x2 - (box[2] - box[0])) / 2, y - (box[3] - box[1]) - 2), value_text, fill="#111111", font=label_font)
        for index, category in enumerate(categories):
            x = chart_left + (index + 0.5) * group_width
            box = draw.textbbox((0, 0), category, font=label_font)
            draw.text((x - (box[2] - box[0]) / 2, chart_bottom + 5), category, fill="#222222", font=label_font)
    return np.asarray(image, dtype=np.uint8)


def build_simplex_visual(asset_id: str, source_sha256: str, width: int, height: int, override: dict[str, Any]) -> dict[str, Any]:
    styling = override.get("styling", {
        "outerLineWidth": 2,
        "gridLineWidth": 1,
        "pointRadius": max(3, width // 145),
        "axisFontSize": max(12, width // 42),
        "pointFontSize": max(12, width // 38),
        "captionFontSize": max(12, width // 42),
    })
    return {
        "schemaVersion": SIMPLEX_VERSION,
        "kind": "semanticSimplexGrid",
        "visualGrammar": override["visual_grammar"],
        "fidelityTier": "semantic_vector",
        "layoutType": "single",
        "coordinateSpace": {"width": width, "height": height},
        "shape": override["shape"],
        "divisions": override["divisions"],
        "vertices": override["vertices"],
        "axes": override["axes"],
        "points": override["points"],
        "caption": override.get("caption"),
        "styling": styling,
        "evidence": {
            "sourceAsset": asset_id,
            "sourceSha256": source_sha256,
            "geometryEngine": "human_verified_simplex_geometry",
            "converterVersion": SIMPLEX_VERSION,
            "renderSha256": "0" * 64,
        },
        "complexity": {
            "vertexCount": len(override["vertices"]),
            "pointCount": len(override["points"]),
            "gridLineCount": override["divisions"] * (3 if override["shape"] == "triangle" else 6),
            "estimatedDrawCommandCount": override["divisions"] * 8 + len(override["points"]) * 3,
        },
    }


def _lerp(a: dict[str, float], b: dict[str, float], t: float) -> tuple[float, float]:
    return (a["x"] + (b["x"] - a["x"]) * t, a["y"] + (b["y"] - a["y"]) * t)


def _triangle_grid(
    draw: ImageDraw.ImageDraw,
    top: dict[str, float],
    left: dict[str, float],
    right: dict[str, float],
    divisions: int,
    outer_width: int,
    grid_width: int,
) -> None:
    draw.line((*_lerp(top, top, 0), *_lerp(left, left, 0), *_lerp(right, right, 0), *_lerp(top, top, 0)), fill=0, width=outer_width)
    for index in range(1, divisions):
        t = index / divisions
        draw.line((*_lerp(top, left, t), *_lerp(top, right, t)), fill=0, width=grid_width)
        draw.line((*_lerp(left, top, t), *_lerp(left, right, t)), fill=0, width=grid_width)
        draw.line((*_lerp(right, top, t), *_lerp(right, left, t)), fill=0, width=grid_width)


def render_simplex_visual(visual: dict[str, Any]) -> np.ndarray:
    width = int(visual["coordinateSpace"]["width"])
    height = int(visual["coordinateSpace"]["height"])
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    vertices = visual["vertices"]
    styling = visual.get("styling", {})
    outer_width = int(styling.get("outerLineWidth", 2))
    grid_width = int(styling.get("gridLineWidth", 1))
    if visual["shape"] == "triangle":
        _triangle_grid(draw, vertices[0], vertices[1], vertices[2], visual["divisions"], outer_width, grid_width)
    else:
        _triangle_grid(draw, vertices[0], vertices[3], vertices[1], visual["divisions"], outer_width, grid_width)
        _triangle_grid(draw, vertices[2], vertices[3], vertices[1], visual["divisions"], outer_width, grid_width)
    label_font = _font(int(styling.get("axisFontSize", max(12, width // 42))))
    point_font = _font(int(styling.get("pointFontSize", max(12, width // 38))))
    for axis in visual["axes"]:
        draw.text((axis["labelPosition"]["x"], axis["labelPosition"]["y"]), axis["label"], fill="#111111", font=label_font, anchor="mm")
    point_radius = int(styling.get("pointRadius", max(3, width // 145)))
    for point in visual["points"]:
        x, y = point["position"]["x"], point["position"]["y"]
        draw.ellipse((x - point_radius, y - point_radius, x + point_radius, y + point_radius), fill=0)
        offset = point.get("labelOffset", {"x": 0, "y": point_radius * 3})
        draw.text((x + offset["x"], y + offset["y"]), point["label"], fill="#111111", font=point_font, anchor="mm")
    if visual.get("caption"):
        caption_font = _font(int(styling.get("captionFontSize", max(12, width // 42))))
        draw.text((width / 2, height - 12), visual["caption"], fill="#111111", font=caption_font, anchor="ms")
    return np.asarray(image, dtype=np.uint8)


def stamp_render_hash(visual: dict[str, Any], rendered_path: Path) -> None:
    visual["evidence"]["renderSha256"] = hashlib.sha256(rendered_path.read_bytes()).hexdigest()
