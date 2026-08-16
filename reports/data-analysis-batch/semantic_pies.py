#!/usr/bin/env python3
"""Semantic pie-option bricks and a deterministic reference renderer.

Angles use the product convention: 0 degrees is twelve o'clock and values grow
clockwise.  The payload stores sectors, not traced pixels, so the mini-program can
render the same chart responsively on phones and tablets.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


VERSION = "semantic-pie-options-v1"
STYLES = {
    "white",
    "black",
    "gray_dark",
    "gray",
    "gray_light",
    "diagonal",
    "horizontal",
    "grid",
    "dots",
}


def build_visual(
    asset_id: str,
    source_sha256: str,
    width: int,
    height: int,
    override: dict[str, Any],
) -> dict[str, Any]:
    options = override["options"]
    sector_count = 0
    for index, option in enumerate(options):
        if option["position"] != index:
            raise ValueError(f"{asset_id}: option positions must be contiguous")
        if option["radius"] <= 0:
            raise ValueError(f"{asset_id}: radius must be positive")
        sectors = option["sectors"]
        if not sectors:
            raise ValueError(f"{asset_id}: every option needs at least one sector")
        sector_count += len(sectors)
        for sector in sectors:
            if sector["style"] not in STYLES:
                raise ValueError(f"{asset_id}: unknown pie style {sector['style']}")
            if not 0 <= sector["startDegrees"] <= 360:
                raise ValueError(f"{asset_id}: sector start out of range")
            if not 0 < sector["sweepDegrees"] <= 360:
                raise ValueError(f"{asset_id}: sector sweep out of range")
        boundaries = option.get("boundariesDegrees")
        if boundaries is not None:
            if not 1 <= len(boundaries) <= 8:
                raise ValueError(f"{asset_id}: explicit pie boundaries must contain 1-8 angles")
            if any(not 0 <= float(angle) <= 360 for angle in boundaries):
                raise ValueError(f"{asset_id}: explicit pie boundary out of range")
    return {
        "schemaVersion": VERSION,
        "kind": "semanticPieOptions",
        "visualGrammar": override["visual_grammar"],
        "fidelityTier": "semantic_vector",
        "layoutType": "sequence",
        "coordinateSpace": {"width": width, "height": height},
        "options": options,
        "styleVocabulary": sorted({sector["style"] for option in options for sector in option["sectors"]}),
        "evidence": {
            "sourceAsset": asset_id,
            "sourceSha256": source_sha256,
            "geometryEngine": "human_verified_sector_geometry",
            "converterVersion": VERSION,
            "renderSha256": "0" * 64,
        },
        "complexity": {
            "optionCount": len(options),
            "sectorCount": sector_count,
            "estimatedDrawCommandCount": len(options) * 2
            + sector_count * 3
            + sum(len(option.get("boundariesDegrees", [])) for option in options),
        },
    }


def _sector_mask(size: tuple[int, int], box: tuple[float, float, float, float], start: float, sweep: float) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    # Pillow starts at three o'clock; the payload starts at twelve o'clock.
    draw.pieslice(box, start=start - 90, end=start + sweep - 90, fill=255)
    return mask


def _pattern_layer(size: tuple[int, int], style: str, radius: float) -> Image.Image:
    colors = {
        "white": 255,
        "black": 0,
        "gray_dark": 82,
        "gray": 150,
        "gray_light": 205,
    }
    if style in colors:
        return Image.new("L", size, colors[style])
    layer = Image.new("L", size, 255)
    draw = ImageDraw.Draw(layer)
    spacing = max(4, int(round(radius / 10)))
    line_width = max(1, int(round(radius / 45)))
    width, height = size
    if style in {"diagonal", "grid"}:
        for offset in range(-height, width + height, spacing):
            draw.line((offset, height, offset + height, 0), fill=0, width=line_width)
    if style in {"horizontal", "grid"}:
        for y in range(0, height + 1, spacing):
            draw.line((0, y, width, y), fill=90 if style == "horizontal" else 0, width=line_width)
    if style == "dots":
        dot_radius = max(1, int(round(radius / 55)))
        for y in range(spacing // 2, height, spacing):
            for x in range(spacing // 2, width, spacing):
                draw.ellipse((x - dot_radius, y - dot_radius, x + dot_radius, y + dot_radius), fill=95)
    return layer


def render_semantic_pies(visual: dict[str, Any]) -> np.ndarray:
    width = int(visual["coordinateSpace"]["width"])
    height = int(visual["coordinateSpace"]["height"])
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    for option in visual["options"]:
        cx = float(option["center"]["x"])
        cy = float(option["center"]["y"])
        radius = float(option["radius"])
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        frame = option.get("frame")
        if frame:
            draw.rectangle(
                (frame["x"], frame["y"], frame["x"] + frame["width"], frame["y"] + frame["height"]),
                outline=0,
                width=max(1, int(round(radius / 35))),
            )
        draw.ellipse(box, fill=255, outline=0, width=max(1, int(round(radius / 35))))
        explicit_boundaries = option.get("boundariesDegrees")
        boundaries: set[float] = set(float(angle) % 360 for angle in explicit_boundaries or [])
        for sector in option["sectors"]:
            start = float(sector["startDegrees"])
            sweep = float(sector["sweepDegrees"])
            if explicit_boundaries is None:
                boundaries.add(start % 360)
                boundaries.add((start + sweep) % 360)
            mask = _sector_mask((width, height), box, start, sweep)
            canvas.paste(_pattern_layer((width, height), sector["style"], radius), (0, 0), mask)
        draw = ImageDraw.Draw(canvas)
        radial_width = max(1, int(round(radius / 35)))
        for angle in sorted(boundaries):
            radians = math.radians(angle)
            end_x = cx + math.sin(radians) * radius
            end_y = cy - math.cos(radians) * radius
            draw.line((cx, cy, end_x, end_y), fill=0, width=radial_width)
        draw.ellipse(box, outline=0, width=radial_width)

        label_y = float(option.get("labelY", cy + radius + max(12, radius * 0.3)))
        label = option["label"]
        label_box = draw.textbbox((0, 0), label, font=font)
        label_width = label_box[2] - label_box[0]
        draw.text((cx - label_width / 2, label_y), label, fill=0, font=font)
    return np.asarray(canvas.convert("RGB"), dtype=np.uint8)


def stamp_render_hash(visual: dict[str, Any], rendered_path: Path) -> None:
    visual["evidence"]["renderSha256"] = hashlib.sha256(rendered_path.read_bytes()).hexdigest()
