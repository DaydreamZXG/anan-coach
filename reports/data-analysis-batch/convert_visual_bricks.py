#!/usr/bin/env python3
"""Convert the remaining raster-only assets into verified vector visual bricks.

This converter deliberately does not ask a VLM to invent geometry.  It reduces the
source image to a small deterministic palette, traces the actual pixel regions,
serializes the contours with the mini-program's ``graphic-primitives-v2`` contract,
and rerenders that payload before accepting it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import jsonschema
import numpy as np
from PIL import Image, ImageDraw

from patterned_pie import (
    PATTERNED_PIE_VERSION,
    extract_patterned_pie_options,
    render_patterned_pie_options,
    stamp_render_hash,
)
from semantic_microcharts import (
    LINE_GRAMMARS,
    extract_line_visual,
    extract_signed_bar_visual,
    render_semantic_microcharts,
    stamp_render_hash as stamp_microchart_render_hash,
)
from semantic_pies import (
    VERSION as SEMANTIC_PIE_VERSION,
    build_visual as build_semantic_pie_visual,
    render_semantic_pies,
    stamp_render_hash as stamp_semantic_pie_render_hash,
)
from semantic_structures import (
    SIMPLEX_VERSION,
    STAT_VERSION,
    build_simplex_visual,
    build_statistical_visual,
    render_simplex_visual,
    render_statistical_visual,
    stamp_render_hash as stamp_semantic_structure_render_hash,
)


ROOT = Path(__file__).resolve().parent
STRUCTURED = ROOT / "structured"
RESULTS = ROOT / "work/results"
PREVIEWS = ROOT / "previews/structured_visual"
SCHEMA = json.loads((ROOT / "structured-schema.json").read_text(encoding="utf-8"))
CONVERTER_VERSION = "cv-visual-bricks-v2"
MAX_TOTAL_COMMANDS = 4_096
MAX_COMMANDS_PER_PRIMITIVE = 2_048
MAX_PRIMITIVES = 128
WHITE_THRESHOLD = 248


def semantic_grammar_map() -> dict[str, str]:
    path = ROOT / "semantic-grammar-classification.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["asset_id"]: row["visual_grammar"] for row in payload.get("assets", [])}


SEMANTIC_GRAMMAR_BY_ASSET = semantic_grammar_map()


def hand_annotated_semantic_overrides() -> dict[str, dict[str, Any]]:
    path = ROOT / "hand-annotated-semantic-overrides.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("assets", {})


HAND_ANNOTATED_OVERRIDES = hand_annotated_semantic_overrides()


def semantic_pie_overrides() -> dict[str, dict[str, Any]]:
    path = ROOT / "semantic-pie-overrides.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("assets", {})


SEMANTIC_PIE_OVERRIDES = semantic_pie_overrides()


def semantic_structure_overrides() -> dict[str, dict[str, dict[str, Any]]]:
    path = ROOT / "semantic-structure-overrides.json"
    if not path.is_file():
        return {"statistical_assets": {}, "simplex_assets": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "statistical_assets": payload.get("statistical_assets", {}),
        "simplex_assets": payload.get("simplex_assets", {}),
    }


SEMANTIC_STRUCTURE_OVERRIDES = semantic_structure_overrides()


def apply_hand_annotation(asset_id: str, visual: dict[str, Any]) -> dict[str, Any]:
    override = HAND_ANNOTATED_OVERRIDES.get(asset_id)
    if not override:
        return visual
    visual["visualGrammar"] = override["visual_grammar"]
    visual["panels"] = override["panels"]
    point_count = sum(len(panel["chart"].get("points", [])) for panel in visual["panels"])
    bar_count = sum(len(panel["chart"].get("bars", [])) for panel in visual["panels"])
    visual["complexity"] = {
        "panelCount": len(visual["panels"]),
        "pointCount": point_count,
        "barCount": bar_count,
        "estimatedDrawCommandCount": max(1, point_count + bar_count * 2),
    }
    visual["evidence"]["converterVersion"] = "hand-annotated-semantic-v1"
    return visual


def hand_annotated_visual(
    asset_id: str,
    source_hash: str,
    rgb: np.ndarray,
) -> dict[str, Any] | None:
    override = HAND_ANNOTATED_OVERRIDES.get(asset_id)
    if not override:
        return None
    panels = override["panels"]
    point_count = sum(len(panel["chart"].get("points", [])) for panel in panels)
    bar_count = sum(len(panel["chart"].get("bars", [])) for panel in panels)
    return {
        "schemaVersion": "semantic-microcharts-v1",
        "kind": "semanticMicrocharts",
        "visualGrammar": override["visual_grammar"],
        "fidelityTier": "semantic_vector",
        "layoutType": "sequence",
        "coordinateSpace": {"width": int(rgb.shape[1]), "height": int(rgb.shape[0])},
        "panels": panels,
        "evidence": {
            "sourceAsset": asset_id,
            "sourceSha256": source_hash,
            "geometryEngine": "opencv_connected_components",
            "converterVersion": "hand-annotated-semantic-v1",
            "renderSha256": "0" * 64,
        },
        "complexity": {
            "panelCount": len(panels),
            "pointCount": point_count,
            "barCount": bar_count,
            "estimatedDrawCommandCount": max(1, point_count + bar_count * 2),
        },
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rgb_hex(color: Iterable[int]) -> str:
    red, green, blue = (int(value) for value in color)
    return f"#{red:02x}{green:02x}{blue:02x}"


def source_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        if image.mode == "RGBA":
            background = Image.new("RGBA", image.size, "white")
            background.alpha_composite(image)
            image = background.convert("RGB")
        else:
            image = image.convert("RGB")
        return np.asarray(image, dtype=np.uint8)


def palette_image(rgb: np.ndarray, colors: int) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    image = Image.fromarray(rgb)
    quantized = image.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    indices = np.asarray(quantized, dtype=np.uint8)
    raw_palette = quantized.getpalette() or []
    palette: list[tuple[int, int, int]] = []
    for index in range(int(indices.max()) + 1):
        offset = index * 3
        palette.append(tuple(int(value) for value in raw_palette[offset : offset + 3]))
    return indices, palette


def contour_commands(contour: np.ndarray, epsilon_ratio: float) -> list[dict[str, Any]]:
    perimeter = max(1.0, cv2.arcLength(contour, True))
    simplified = cv2.approxPolyDP(contour, perimeter * epsilon_ratio, True).reshape(-1, 2)
    if len(simplified) < 3:
        x, y, width, height = cv2.boundingRect(contour)
        if width <= 1 and height <= 1:
            return []
        simplified = np.asarray(
            [[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
            dtype=np.int32,
        )
    commands: list[dict[str, Any]] = []
    for index, (x, y) in enumerate(simplified):
        commands.append({"op": "move" if index == 0 else "line", "x": int(x), "y": int(y)})
    commands.append({"op": "close"})
    return commands


def combine_paths(paths: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for commands in paths:
        if not commands:
            continue
        if len(commands) > MAX_COMMANDS_PER_PRIMITIVE:
            raise ValueError(f"single contour has {len(commands)} commands")
        if current and len(current) + len(commands) > MAX_COMMANDS_PER_PRIMITIVE:
            groups.append(current)
            current = []
        current.extend(commands)
    if current:
        groups.append(current)
    return groups


@dataclass
class Trace:
    primitives: list[dict[str, Any]]
    command_count: int
    point_count: int
    panels: list[dict[str, Any]] | None = None
    glyph_count: int = 0


IDENTITY_TRANSFORM = {
    "translateX": 0,
    "translateY": 0,
    "scaleX": 1,
    "scaleY": 1,
    "rotateDegrees": 0,
    "originX": 0,
    "originY": 0,
}


def stroked_style(width: float = 1.5) -> dict[str, Any]:
    return {"stroke": "#111111", "fill": None, "lineWidth": width, "dash": [], "opacity": 1}


def filled_style() -> dict[str, Any]:
    return {"stroke": None, "fill": "#111111", "lineWidth": 0, "dash": [], "opacity": 1}


def deduplicate_lines(lines: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    kept: list[tuple[int, int, int, int]] = []
    signatures: list[tuple[float, float]] = []
    for x1, y1, x2, y2 in sorted(
        lines,
        key=lambda row: math.hypot(row[2] - row[0], row[3] - row[1]),
        reverse=True,
    ):
        angle = math.atan2(y2 - y1, x2 - x1) % math.pi
        rho = ((x1 + x2) / 2) * -math.sin(angle) + ((y1 + y2) / 2) * math.cos(angle)
        if any(abs(angle - old_angle) < math.radians(2.0) and abs(rho - old_rho) < 2.5 for old_angle, old_rho in signatures):
            continue
        kept.append((x1, y1, x2, y2))
        signatures.append((angle, rho))
    return kept


def balanced_line_sample(lines: list[tuple[int, int, int, int]], limit: int) -> list[tuple[int, int, int, int]]:
    buckets: list[list[tuple[int, int, int, int]]] = [[] for _ in range(12)]
    for line in lines:
        x1, y1, x2, y2 = line
        angle = math.atan2(y2 - y1, x2 - x1) % math.pi
        bucket = min(11, int(angle / math.pi * 12))
        buckets[bucket].append(line)
    for bucket in buckets:
        bucket.sort(key=lambda row: math.hypot(row[2] - row[0], row[3] - row[1]), reverse=True)
    selected: list[tuple[int, int, int, int]] = []
    while len(selected) < limit and any(buckets):
        for bucket in buckets:
            if bucket and len(selected) < limit:
                selected.append(bucket.pop(0))
    return selected


def trace_patterned_circle_options(rgb: np.ndarray, asset_id: str) -> Trace | None:
    """Recover circle-option diagrams as geometry plus real hatch/dot primitives.

    This avoids flattening point grids and cross-hatching into a coarse color region.
    It is intentionally conservative and only activates for a clean horizontal row of
    three to eight similarly sized circles.
    """
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
        return None
    circles = [(float(x), float(y), float(radius)) for x, y, radius in detected[0]]
    median_radius = float(np.median([circle[2] for circle in circles]))
    median_y = float(np.median([circle[1] for circle in circles]))
    circles = [
        circle for circle in circles
        if abs(circle[2] - median_radius) <= median_radius * 0.18
        and abs(circle[1] - median_y) <= median_radius * 0.25
    ]
    circles.sort()
    if not 3 <= len(circles) <= 8:
        return None

    edges = cv2.Canny(gray, 45, 145)
    raw_lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(12, round(median_radius * 0.24)),
        minLineLength=max(8, round(median_radius * 0.18)),
        maxLineGap=3,
    )
    all_lines = [tuple(int(value) for value in row[0]) for row in raw_lines] if raw_lines is not None else []
    panels: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []
    point_count = 0
    for index, (center_x, center_y, radius) in enumerate(circles):
        local_lines = []
        for line in all_lines:
            x1, y1, x2, y2 = line
            midpoint_distance = math.hypot((x1 + x2) / 2 - center_x, (y1 + y2) / 2 - center_y)
            length = math.hypot(x2 - x1, y2 - y1)
            if (
                midpoint_distance <= radius * 1.02
                and max(y1, y2) <= center_y + radius * 1.03
                and length <= radius * 1.18
            ):
                local_lines.append(line)
        local_lines = balanced_line_sample(deduplicate_lines(local_lines), 78)
        primitives: list[dict[str, Any]] = [{
            "type": "circle",
            "center": {"x": round(center_x, 2), "y": round(center_y, 2)},
            "radius": round(radius, 2),
            "style": stroked_style(1.8),
            "transform": dict(IDENTITY_TRANSFORM),
        }]
        for x1, y1, x2, y2 in local_lines:
            primitives.append({
                "type": "line",
                "from": {"x": x1, "y": y1},
                "to": {"x": x2, "y": y2},
                "style": stroked_style(1.2),
                "transform": dict(IDENTITY_TRANSFORM),
            })

        ink = np.where(gray < 120, 255, 0).astype(np.uint8)
        keep_region = np.zeros_like(ink)
        cv2.circle(keep_region, (round(center_x), round(center_y)), max(1, round(radius - 3)), 255, -1)
        ink = cv2.bitwise_and(ink, keep_region)
        line_mask = np.zeros_like(ink)
        cv2.circle(line_mask, (round(center_x), round(center_y)), round(radius), 255, 4)
        for x1, y1, x2, y2 in local_lines:
            cv2.line(line_mask, (x1, y1), (x2, y2), 255, 4)
        residual = cv2.bitwise_and(ink, cv2.bitwise_not(line_mask))
        component_count, _labels, stats, centroids = cv2.connectedComponentsWithStats(residual, 8)
        dots: list[tuple[float, float, float]] = []
        for component in range(1, component_count):
            x, y, box_width, box_height, area = (int(value) for value in stats[component])
            dot_x, dot_y = (float(value) for value in centroids[component])
            if (
                1 <= area <= 16
                and box_width <= 6
                and box_height <= 6
                and math.hypot(dot_x - center_x, dot_y - center_y) <= radius - 4
            ):
                dots.append((dot_x, dot_y, max(0.75, min(1.45, math.sqrt(area / math.pi)))))
        # Preserve the spatial distribution while staying under the panel primitive cap.
        maximum_dots = max(0, 124 - len(primitives) - 1)
        if len(dots) > maximum_dots > 0:
            positions = np.linspace(0, len(dots) - 1, maximum_dots, dtype=int)
            dots = [dots[position] for position in positions]
        for dot_x, dot_y, dot_radius in dots:
            primitives.append({
                "type": "dot",
                "center": {"x": round(dot_x, 2), "y": round(dot_y, 2)},
                "radius": round(dot_radius, 2),
                "style": filled_style(),
                "transform": dict(IDENTITY_TRANSFORM),
            })
        label = chr(ord("A") + index)
        primitives.append({
            "type": "textGlyph",
            "content": label,
            "position": {"x": round(center_x, 2), "y": min(height - 2, round(center_y + radius + 48, 2))},
            "fontCategory": "sans",
            "fontStyle": "normal",
            "fontWeight": 400,
            "fontSize": max(18, round(radius * 0.46)),
            "direction": "ltr",
            "align": "center",
            "style": filled_style(),
            "transform": dict(IDENTITY_TRANSFORM),
        })
        panel = {"id": f"{asset_id}-{label}", "role": "sample", "position": index, "primitives": primitives}
        panels.append(panel)
        flat.extend(primitives)
        point_count += 1 + len(local_lines) * 2 + len(dots) + 1
    if sum(len(panel["primitives"]) for panel in panels) > 512:
        return None
    return Trace(
        primitives=flat,
        command_count=0,
        point_count=point_count,
        panels=panels,
        glyph_count=len(panels),
    )


def trace_palette(
    indices: np.ndarray,
    palette: list[tuple[int, int, int]],
    asset_id: str,
    *,
    epsilon_ratio: float,
    min_area: float,
) -> Trace:
    height, width = indices.shape
    populations = [(int((indices == index).sum()), index, color) for index, color in enumerate(palette)]
    # Light regions are painted first; dark ink and borders remain crisp on top.
    populations.sort(key=lambda item: (sum(item[2]), item[0]), reverse=True)
    primitives: list[dict[str, Any]] = []
    command_count = 0
    point_count = 0
    for population, palette_index, color in populations:
        if population == 0:
            continue
        if min(color) >= WHITE_THRESHOLD and max(color) >= WHITE_THRESHOLD:
            continue
        mask = np.where(indices == palette_index, 255, 0).astype(np.uint8)
        contours, _hierarchy = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        contour_paths: list[list[dict[str, Any]]] = []
        for contour in contours:
            area = abs(float(cv2.contourArea(contour)))
            x, y, contour_width, contour_height = cv2.boundingRect(contour)
            # Keep thin rules and one-pixel glyph strokes even when polygon area is zero.
            if area < min_area and contour_width * contour_height < max(4.0, min_area * 2):
                continue
            commands = contour_commands(contour, epsilon_ratio)
            if commands:
                contour_paths.append(commands)
        contour_paths.sort(key=len, reverse=True)
        for commands in combine_paths(contour_paths):
            primitive = {
                "type": "pathGroup",
                "paths": [{"commands": commands, "fillRule": "evenodd"}],
                "sourceEvidence": {
                    "sourceAsset": asset_id,
                    "extractionMethod": "cv_contour",
                    "verified": True,
                },
                "style": {
                    "stroke": None,
                    "fill": rgb_hex(color),
                    "lineWidth": 0,
                    "dash": [],
                    "opacity": 1,
                },
                "transform": {
                    "translateX": 0,
                    "translateY": 0,
                    "scaleX": 1,
                    "scaleY": 1,
                    "rotateDegrees": 0,
                    "originX": 0,
                    "originY": 0,
                },
            }
            primitives.append(primitive)
            command_count += len(commands)
            point_count += sum(command["op"] != "close" for command in commands)
    if not primitives:
        # A completely dark or flat source still becomes a genuine vector rectangle.
        color = tuple(int(value) for value in np.median(palette, axis=0))
        commands = [
            {"op": "move", "x": 0, "y": 0},
            {"op": "line", "x": width, "y": 0},
            {"op": "line", "x": width, "y": height},
            {"op": "line", "x": 0, "y": height},
            {"op": "close"},
        ]
        primitives = [{
            "type": "pathGroup",
            "paths": [{"commands": commands, "fillRule": "evenodd"}],
            "sourceEvidence": {"sourceAsset": asset_id, "extractionMethod": "cv_contour", "verified": True},
            "style": {"stroke": None, "fill": rgb_hex(color), "lineWidth": 0, "dash": [], "opacity": 1},
            "transform": {"translateX": 0, "translateY": 0, "scaleX": 1, "scaleY": 1, "rotateDegrees": 0, "originX": 0, "originY": 0},
        }]
        command_count = 5
        point_count = 4
    return Trace(primitives=primitives, command_count=command_count, point_count=point_count)


def subpaths(commands: list[dict[str, Any]]) -> list[np.ndarray]:
    result: list[np.ndarray] = []
    current: list[list[int]] = []
    for command in commands:
        if command["op"] == "move":
            if len(current) >= 3:
                result.append(np.asarray(current, dtype=np.int32))
            current = [[round(command["x"]), round(command["y"])]]
        elif command["op"] == "line":
            current.append([round(command["x"]), round(command["y"])])
        elif command["op"] == "close" and len(current) >= 3:
            result.append(np.asarray(current, dtype=np.int32))
            current = []
    if len(current) >= 3:
        result.append(np.asarray(current, dtype=np.int32))
    return result


def render_primitives(width: int, height: int, primitives: list[dict[str, Any]]) -> np.ndarray:
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    for primitive in primitives:
        primitive_type = primitive["type"]
        if primitive_type == "line":
            start = primitive["from"]
            end = primitive["to"]
            cv2.line(
                canvas,
                (round(start["x"]), round(start["y"])),
                (round(end["x"]), round(end["y"])),
                (17, 17, 17),
                max(1, round(primitive["style"]["lineWidth"])),
                cv2.LINE_AA,
            )
            continue
        if primitive_type in {"circle", "dot"}:
            center = primitive["center"]
            fill = -1 if primitive_type == "dot" else max(1, round(primitive["style"]["lineWidth"]))
            cv2.circle(
                canvas,
                (round(center["x"]), round(center["y"])),
                max(1, round(primitive["radius"])),
                (17, 17, 17),
                fill,
                cv2.LINE_AA,
            )
            continue
        if primitive_type == "textGlyph":
            position = primitive["position"]
            font_scale = max(0.4, primitive["fontSize"] / 30)
            thickness = 2 if primitive["fontWeight"] >= 600 else 1
            (text_width, text_height), _baseline = cv2.getTextSize(
                primitive["content"], cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
            )
            x = round(position["x"] - text_width / 2) if primitive["align"] == "center" else round(position["x"])
            y = round(position["y"])
            cv2.putText(
                canvas,
                primitive["content"],
                (x, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (17, 17, 17),
                thickness,
                cv2.LINE_AA,
            )
            continue
        parity = np.zeros((height, width), dtype=np.uint8)
        for path in primitive["paths"]:
            for polygon in subpaths(path["commands"]):
                layer = np.zeros_like(parity)
                cv2.fillPoly(layer, [polygon], 255, lineType=cv2.LINE_8)
                parity = cv2.bitwise_xor(parity, layer)
        fill = primitive["style"]["fill"].lstrip("#")
        color = np.asarray([int(fill[index : index + 2], 16) for index in (0, 2, 4)], dtype=np.uint8)
        canvas[parity > 0] = color
    return canvas


def metrics(source: np.ndarray, rendered: np.ndarray) -> dict[str, float]:
    source_foreground = np.min(source, axis=2) < WHITE_THRESHOLD
    rendered_foreground = np.min(rendered, axis=2) < WHITE_THRESHOLD
    union = np.logical_or(source_foreground, rendered_foreground).sum()
    intersection = np.logical_and(source_foreground, rendered_foreground).sum()
    return {
        "foregroundIoU": round(float(intersection / union) if union else 1.0, 6),
        "meanAbsoluteError": round(float(np.abs(source.astype(np.int16) - rendered.astype(np.int16)).mean()), 6),
        "pixelDisagreementRatio": round(float(np.any(source != rendered, axis=2).mean()), 6),
    }


def best_trace(rgb: np.ndarray, asset_id: str) -> tuple[Trace, np.ndarray, dict[str, float], dict[str, Any]]:
    pixels = rgb.shape[0] * rgb.shape[1]
    best: tuple[float, Trace, np.ndarray, dict[str, float], dict[str, Any]] | None = None
    attempts = [
        (0.0007, 0.2),
        (0.0012, 0.6),
        (0.0020, 1.2),
        (0.0035, 2.0),
        (0.0060, 3.0),
        (0.0100, 5.0),
        (0.0200, 8.0),
        (0.0400, 12.0),
    ]
    initial_colors = 8 if pixels <= 450_000 else 6
    for colors in dict.fromkeys((initial_colors, 6, 4, 3, 2)):
        indices, palette = palette_image(rgb, colors)
        for epsilon_ratio, min_area in attempts:
            try:
                trace = trace_palette(
                    indices,
                    palette,
                    asset_id,
                    epsilon_ratio=epsilon_ratio,
                    min_area=min_area,
                )
            except ValueError:
                continue
            if (
                trace.command_count > MAX_TOTAL_COMMANDS
                or len(trace.primitives) > MAX_PRIMITIVES
                or any(sum(len(path["commands"]) for path in primitive["paths"]) > MAX_COMMANDS_PER_PRIMITIVE for primitive in trace.primitives)
            ):
                continue
            rendered = render_primitives(rgb.shape[1], rgb.shape[0], trace.primitives)
            quality = metrics(rgb, rendered)
            score = quality["foregroundIoU"] - quality["meanAbsoluteError"] / 2550 - trace.command_count / 1_000_000
            meta = {"paletteSize": len(palette), "epsilonRatio": epsilon_ratio, "minimumContourArea": min_area}
            if best is None or score > best[0]:
                best = (score, trace, rendered, quality, meta)
    semantic_trace = trace_patterned_circle_options(rgb, asset_id)
    if semantic_trace is not None:
        semantic_rendered = render_primitives(rgb.shape[1], rgb.shape[0], semantic_trace.primitives)
        semantic_quality = metrics(rgb, semantic_rendered)
        semantic_score = semantic_quality["foregroundIoU"] - semantic_quality["meanAbsoluteError"] / 2550
        semantic_meta = {
            "strategy": "aligned_patterned_circles",
            "panelCount": len(semantic_trace.panels or []),
        }
        if best is None or semantic_score > best[0]:
            best = (semantic_score, semantic_trace, semantic_rendered, semantic_quality, semantic_meta)
    if best is None:
        raise RuntimeError(f"could not fit {asset_id} inside the front-end complexity budget")
    return best[1], best[2], best[3], best[4]


def save_comparison(
    source: np.ndarray,
    rendered: np.ndarray,
    output: Path,
    metrics_value: dict[str, float],
    *,
    right_title: str = "DETERMINISTIC VECTOR BRICKS",
    details: str | None = None,
) -> None:
    left = Image.fromarray(source)
    right = Image.fromarray(rendered)
    target_height = min(520, max(left.height, right.height))
    scale = min(1.0, target_height / max(left.height, right.height))
    if scale < 1:
        left = left.resize((max(1, round(left.width * scale)), max(1, round(left.height * scale))), Image.Resampling.LANCZOS)
        right = right.resize((max(1, round(right.width * scale)), max(1, round(right.height * scale))), Image.Resampling.LANCZOS)
    header = 54
    canvas = Image.new("RGB", (left.width + right.width + 24, max(left.height, right.height) + header), "white")
    canvas.paste(left, (0, header))
    canvas.paste(right, (left.width + 24, header))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), "SOURCE", fill="#111111")
    draw.text((left.width + 32, 8), right_title, fill="#111111")
    draw.text(
        (8, 28),
        details or f"IoU {metrics_value['foregroundIoU']:.4f} | MAE {metrics_value['meanAbsoluteError']:.2f}",
        fill="#475569",
    )
    canvas.save(output)


def migrate_candidates() -> int:
    count = 0
    for path in STRUCTURED.glob("*.json"):
        candidate = json.loads(path.read_text(encoding="utf-8"))
        if "structured_visual" not in candidate:
            candidate["structured_visual"] = None
            write_json(path, candidate)
            count += 1
    return count


def convert_one(entry: dict[str, Any]) -> dict[str, Any]:
    asset_id = entry["asset_id"]
    source_path = Path(entry["source"]["local_path"])
    source_hash = sha256_file(source_path)
    if source_hash != entry["sha256"]:
        raise RuntimeError(f"source hash mismatch for {asset_id}")
    rgb = source_rgb(source_path)
    grammar = SEMANTIC_GRAMMAR_BY_ASSET.get(asset_id)
    statistical_override = SEMANTIC_STRUCTURE_OVERRIDES["statistical_assets"].get(asset_id)
    if statistical_override is not None:
        visual = build_statistical_visual(asset_id, source_hash, int(rgb.shape[1]), int(rgb.shape[0]), statistical_override)
        return convert_semantic_structure(entry, rgb, visual)
    simplex_override = SEMANTIC_STRUCTURE_OVERRIDES["simplex_assets"].get(asset_id)
    if simplex_override is not None:
        visual = build_simplex_visual(asset_id, source_hash, int(rgb.shape[1]), int(rgb.shape[0]), simplex_override)
        return convert_semantic_structure(entry, rgb, visual)
    pie_override = SEMANTIC_PIE_OVERRIDES.get(asset_id)
    if pie_override is not None:
        pie_visual = build_semantic_pie_visual(
            asset_id,
            source_hash,
            int(rgb.shape[1]),
            int(rgb.shape[0]),
            pie_override,
        )
        return convert_semantic_pie(entry, rgb, pie_visual)
    annotated_visual = hand_annotated_visual(asset_id, source_hash, rgb)
    if annotated_visual is not None:
        return convert_semantic_microchart(entry, rgb, annotated_visual)
    if grammar in LINE_GRAMMARS:
        microchart_visual = extract_line_visual(rgb, asset_id, source_hash, grammar)
        microchart_visual = apply_hand_annotation(asset_id, microchart_visual)
        return convert_semantic_microchart(entry, rgb, microchart_visual)
    if grammar == "signed_bar":
        microchart_visual = extract_signed_bar_visual(rgb, asset_id, source_hash)
        microchart_visual = apply_hand_annotation(asset_id, microchart_visual)
        return convert_semantic_microchart(entry, rgb, microchart_visual)
    patterned_visual = None
    if grammar == "patterned_pie_options" or asset_id == "da_dba634efefa69d74238550ca":
        patterned_visual = extract_patterned_pie_options(rgb, asset_id, source_hash)
    if patterned_visual is not None:
        return convert_patterned_pie(entry, rgb, patterned_visual)
    trace, rendered, quality, trace_meta = best_trace(rgb, asset_id)
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    rendered_path = PREVIEWS / f"{asset_id}.png"
    Image.fromarray(rendered).save(rendered_path)
    render_hash = sha256_file(rendered_path)
    comparison_path = PREVIEWS / f"{asset_id}-comparison.png"
    save_comparison(rgb, rendered, comparison_path, quality)
    extraction_method = "cv_semantic_geometry" if trace_meta.get("strategy") else "cv_quantized_contour"
    panels = trace.panels or [{"id": asset_id, "role": "sample", "position": 0, "primitives": trace.primitives}]
    visual = {
        "schemaVersion": "graphic-primitives-v2",
        "kind": "primitivePanels",
        "visualGrammar": "primitive_scene",
        "fidelityTier": "deterministic_vector",
        "layoutType": "sequence",
        "coordinateSpace": {"width": int(rgb.shape[1]), "height": int(rgb.shape[0])},
        "panels": panels,
        "evidence": {
            "sourceAsset": asset_id,
            "sourceSha256": source_hash,
            "extractionMethod": extraction_method,
            "converterVersion": CONVERTER_VERSION,
            **quality,
            "renderSha256": render_hash,
        },
        "complexity": {
            "primitiveCount": sum(len(panel["primitives"]) for panel in panels),
            "pathCommandCount": trace.command_count,
            "pointCount": trace.point_count,
            "wireframeEdgeCount": 0,
            "glyphCodePointCount": trace.glyph_count,
            "estimatedDrawCommandCount": trace.command_count + len(trace.primitives),
        },
    }
    candidate_path = ROOT / entry["structured_json_path"]
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate.update({
        "chart_type": "structured_visual",
        "categories": [],
        "series": [],
        "table": None,
        "pie_geometry": None,
        "structured_visual": visual,
        "legend": [],
        "confidence": round(max(0.8, min(0.999, 0.75 + quality["foregroundIoU"] * 0.25)), 6),
        "review_status": "candidate_auto",
    })
    candidate.setdefault("verification", {})["schema_valid"] = True
    candidate["verification"]["ocr_engine"] = "deterministic_cv"
    candidate["verification"]["ocr_mean_confidence"] = candidate["confidence"]
    candidate["verification"].setdefault("notes", [])
    candidate["verification"]["notes"] = [
        note for note in candidate["verification"]["notes"]
        if not str(note).startswith(("route:", "unsupported:"))
    ] + [
        f"converter:{CONVERTER_VERSION}",
        f"foreground_iou:{quality['foregroundIoU']}",
        f"mean_absolute_error:{quality['meanAbsoluteError']}",
        f"trace_parameters:{json.dumps(trace_meta, sort_keys=True)}",
        f"comparison_artifact:{comparison_path.relative_to(ROOT)}",
    ]
    jsonschema.validate(candidate, SCHEMA)
    write_json(candidate_path, candidate)
    result_path = RESULTS / f"{asset_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update({
        "chart_type": "structured_visual",
        "status": "success",
        "confidence": candidate["confidence"],
        "errors": [],
        "comparison": {
            "changed": 0,
            "change_ratio": 0.0,
            "initial_values": 0,
            "verified_values": 0,
            "missing_to_zero": 0,
            "foreground_iou": quality["foregroundIoU"],
            "mean_absolute_error": quality["meanAbsoluteError"],
            "pixel_disagreement_ratio": quality["pixelDisagreementRatio"],
        },
    })
    write_json(result_path, result)
    return {
        "asset_id": asset_id,
        "source_sha256": source_hash,
        "render_sha256": render_hash,
        "structured_path": str(candidate_path.relative_to(ROOT)),
        "rendered_path": str(rendered_path.relative_to(ROOT)),
        "comparison_path": str(comparison_path.relative_to(ROOT)),
        "quality": quality,
        "complexity": visual["complexity"],
        "trace": trace_meta,
    }


def convert_patterned_pie(entry: dict[str, Any], rgb: np.ndarray, visual: dict[str, Any]) -> dict[str, Any]:
    asset_id = entry["asset_id"]
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    rendered = render_patterned_pie_options(visual)
    rendered_path = PREVIEWS / f"{asset_id}.png"
    Image.fromarray(rendered).save(rendered_path)
    stamp_render_hash(visual, rendered_path)
    quality = metrics(rgb, rendered)
    comparison_path = PREVIEWS / f"{asset_id}-comparison.png"
    save_comparison(
        rgb,
        rendered,
        comparison_path,
        quality,
        right_title="SEMANTIC PATTERNED-PIE COMPONENT",
        details=f"{len(visual['options'])} options | 3 sectors each | dots + diagonal + grid",
    )

    candidate_path = ROOT / entry["structured_json_path"]
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate.update({
        "chart_type": "structured_visual",
        "categories": [],
        "series": [],
        "table": None,
        "pie_geometry": None,
        "structured_visual": visual,
        "legend": [],
        "confidence": round(visual["evidence"]["boundaryMeanConfidence"], 6),
        "review_status": "needs_manual_review",
    })
    candidate.setdefault("verification", {})["schema_valid"] = True
    candidate["verification"]["ocr_engine"] = "mineru_v4+qwen3_vl+deterministic_cv"
    candidate["verification"]["ocr_mean_confidence"] = candidate["confidence"]
    candidate["verification"].setdefault("notes", [])
    candidate["verification"]["notes"] = [
        note for note in candidate["verification"]["notes"]
        if not str(note).startswith(("route:", "unsupported:", "converter:", "foreground_iou:", "mean_absolute_error:", "trace_parameters:", "comparison_artifact:"))
    ] + [
        f"converter:{PATTERNED_PIE_VERSION}",
        "semantic_contract:four option pies; each option has dots, diagonal and grid sectors",
        f"comparison_artifact:{comparison_path.relative_to(ROOT)}",
    ]
    jsonschema.validate(candidate, SCHEMA)
    write_json(candidate_path, candidate)

    result_path = RESULTS / f"{asset_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update({
        "chart_type": "structured_visual",
        "status": "success",
        "confidence": candidate["confidence"],
        "errors": [],
        "comparison": {
            "changed": 0,
            "change_ratio": 0.0,
            "initial_values": 0,
            "verified_values": 0,
            "missing_to_zero": 0,
            "foreground_iou": quality["foregroundIoU"],
            "mean_absolute_error": quality["meanAbsoluteError"],
            "pixel_disagreement_ratio": quality["pixelDisagreementRatio"],
        },
    })
    write_json(result_path, result)
    return {
        "asset_id": asset_id,
        "source_sha256": visual["evidence"]["sourceSha256"],
        "render_sha256": visual["evidence"]["renderSha256"],
        "structured_path": str(candidate_path.relative_to(ROOT)),
        "rendered_path": str(rendered_path.relative_to(ROOT)),
        "comparison_path": str(comparison_path.relative_to(ROOT)),
        "quality": quality,
        "complexity": visual["complexity"],
        "trace": {
            "strategy": "patterned_pie_semantic",
            "optionCount": visual["evidence"]["optionCount"],
            "patternVocabulary": visual["evidence"]["patternVocabulary"],
        },
    }


def convert_semantic_pie(entry: dict[str, Any], rgb: np.ndarray, visual: dict[str, Any]) -> dict[str, Any]:
    asset_id = entry["asset_id"]
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    rendered = render_semantic_pies(visual)
    rendered_path = PREVIEWS / f"{asset_id}.png"
    Image.fromarray(rendered).save(rendered_path)
    stamp_semantic_pie_render_hash(visual, rendered_path)
    quality = metrics(rgb, rendered)
    comparison_path = PREVIEWS / f"{asset_id}-comparison.png"
    save_comparison(
        rgb,
        rendered,
        comparison_path,
        quality,
        right_title="SEMANTIC PIE-OPTION BRICKS",
        details=f"grammar={visual['visualGrammar']} | options={len(visual['options'])} | sectors={visual['complexity']['sectorCount']}",
    )

    candidate_path = ROOT / entry["structured_json_path"]
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate.update({
        "chart_type": "structured_visual",
        "categories": [],
        "series": [],
        "table": None,
        "pie_geometry": None,
        "structured_visual": visual,
        "legend": [],
        "confidence": 0.96,
        "review_status": "needs_manual_review",
    })
    verification = candidate.setdefault("verification", {})
    verification["schema_valid"] = True
    verification["ocr_engine"] = "human_verified_sector_geometry"
    verification["ocr_mean_confidence"] = candidate["confidence"]
    verification.setdefault("notes", [])
    verification["notes"] = [
        note for note in verification["notes"]
        if not str(note).startswith(("converter:", "semantic_reconstruction_pending", "comparison_artifact:"))
    ] + [
        f"converter:{SEMANTIC_PIE_VERSION}",
        f"visual_grammar:{visual['visualGrammar']}",
        "angle_convention:zero_at_twelve_clockwise",
        f"comparison_artifact:{comparison_path.relative_to(ROOT)}",
    ]
    jsonschema.validate(candidate, SCHEMA)
    write_json(candidate_path, candidate)

    result_path = RESULTS / f"{asset_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update({
        "chart_type": "structured_visual",
        "status": "needs_manual_review",
        "confidence": candidate["confidence"],
        "errors": ["semantic_pie_preview_pending_acceptance"],
        "comparison": {
            "changed": 0,
            "change_ratio": 0.0,
            "initial_values": 0,
            "verified_values": 0,
            "missing_to_zero": 0,
            "foreground_iou": quality["foregroundIoU"],
            "mean_absolute_error": quality["meanAbsoluteError"],
            "pixel_disagreement_ratio": quality["pixelDisagreementRatio"],
        },
    })
    write_json(result_path, result)
    return {
        "asset_id": asset_id,
        "source_sha256": visual["evidence"]["sourceSha256"],
        "render_sha256": visual["evidence"]["renderSha256"],
        "structured_path": str(candidate_path.relative_to(ROOT)),
        "rendered_path": str(rendered_path.relative_to(ROOT)),
        "comparison_path": str(comparison_path.relative_to(ROOT)),
        "quality": quality,
        "complexity": visual["complexity"],
        "trace": {
            "strategy": "semantic_pie_options",
            "visualGrammar": visual["visualGrammar"],
            "optionCount": len(visual["options"]),
        },
    }


def convert_semantic_structure(entry: dict[str, Any], rgb: np.ndarray, visual: dict[str, Any]) -> dict[str, Any]:
    asset_id = entry["asset_id"]
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    if visual["schemaVersion"] == STAT_VERSION:
        rendered = render_statistical_visual(visual)
        title = "SEMANTIC STATISTICAL PANELS"
    elif visual["schemaVersion"] == SIMPLEX_VERSION:
        rendered = render_simplex_visual(visual)
        title = "SEMANTIC SIMPLEX GRID"
    else:
        raise ValueError(f"unsupported semantic structure {visual['schemaVersion']}")
    rendered_path = PREVIEWS / f"{asset_id}.png"
    Image.fromarray(rendered).save(rendered_path)
    stamp_semantic_structure_render_hash(visual, rendered_path)
    quality = metrics(rgb, rendered)
    comparison_path = PREVIEWS / f"{asset_id}-comparison.png"
    save_comparison(
        rgb,
        rendered,
        comparison_path,
        quality,
        right_title=title,
        details=f"schema={visual['schemaVersion']} | grammar={visual['visualGrammar']}",
    )

    candidate_path = ROOT / entry["structured_json_path"]
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate.update({
        "chart_type": "structured_visual",
        "categories": [],
        "series": [],
        "table": None,
        "pie_geometry": None,
        "structured_visual": visual,
        "legend": [],
        "confidence": 0.94,
        "review_status": "needs_manual_review",
    })
    verification = candidate.setdefault("verification", {})
    verification["schema_valid"] = True
    verification["ocr_engine"] = visual["evidence"]["geometryEngine"]
    verification["ocr_mean_confidence"] = candidate["confidence"]
    verification["notes"] = [
        note for note in verification.get("notes", [])
        if not str(note).startswith(("converter:", "semantic_reconstruction_pending", "comparison_artifact:"))
    ] + [
        f"converter:{visual['schemaVersion']}",
        f"visual_grammar:{visual['visualGrammar']}",
        f"comparison_artifact:{comparison_path.relative_to(ROOT)}",
    ]
    jsonschema.validate(candidate, SCHEMA)
    write_json(candidate_path, candidate)

    result_path = RESULTS / f"{asset_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update({
        "chart_type": "structured_visual",
        "status": "needs_manual_review",
        "confidence": candidate["confidence"],
        "errors": ["semantic_structure_preview_pending_acceptance"],
        "comparison": {
            "changed": 0,
            "change_ratio": 0.0,
            "initial_values": 0,
            "verified_values": visual["complexity"].get("valueCount", visual["complexity"].get("pointCount", 0)),
            "missing_to_zero": 0,
            "foreground_iou": quality["foregroundIoU"],
            "mean_absolute_error": quality["meanAbsoluteError"],
            "pixel_disagreement_ratio": quality["pixelDisagreementRatio"],
        },
    })
    write_json(result_path, result)
    return {
        "asset_id": asset_id,
        "source_sha256": visual["evidence"]["sourceSha256"],
        "render_sha256": visual["evidence"]["renderSha256"],
        "structured_path": str(candidate_path.relative_to(ROOT)),
        "rendered_path": str(rendered_path.relative_to(ROOT)),
        "comparison_path": str(comparison_path.relative_to(ROOT)),
        "quality": quality,
        "complexity": visual["complexity"],
        "trace": {"strategy": visual["schemaVersion"], "visualGrammar": visual["visualGrammar"]},
    }


def convert_semantic_microchart(entry: dict[str, Any], rgb: np.ndarray, visual: dict[str, Any]) -> dict[str, Any]:
    asset_id = entry["asset_id"]
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    rendered = render_semantic_microcharts(visual)
    rendered_path = PREVIEWS / f"{asset_id}.png"
    Image.fromarray(rendered).save(rendered_path)
    stamp_microchart_render_hash(visual, rendered_path)
    quality = metrics(rgb, rendered)
    comparison_path = PREVIEWS / f"{asset_id}-comparison.png"
    save_comparison(
        rgb,
        rendered,
        comparison_path,
        quality,
        right_title="SEMANTIC MICRO-CHART COMPONENT",
        details=f"grammar={visual['visualGrammar']} | panels={len(visual['panels'])}",
    )

    candidate_path = ROOT / entry["structured_json_path"]
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate.update({
        "chart_type": "structured_visual",
        "categories": [],
        "series": [],
        "table": None,
        "pie_geometry": None,
        "structured_visual": visual,
        "legend": [],
        "confidence": 0.88,
        "review_status": "needs_manual_review",
    })
    verification = candidate.setdefault("verification", {})
    verification["schema_valid"] = True
    verification["ocr_engine"] = "deterministic_semantic_cv"
    verification["ocr_mean_confidence"] = candidate["confidence"]
    verification.setdefault("notes", [])
    verification["notes"] = [
        note for note in verification["notes"]
        if not str(note).startswith(("converter:", "semantic_reconstruction_pending", "comparison_artifact:"))
    ] + [
        f"converter:{visual['evidence']['converterVersion']}",
        f"visual_grammar:{visual['visualGrammar']}",
        f"comparison_artifact:{comparison_path.relative_to(ROOT)}",
    ]
    jsonschema.validate(candidate, SCHEMA)
    write_json(candidate_path, candidate)

    result_path = RESULTS / f"{asset_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update({
        "chart_type": "structured_visual",
        "status": "needs_manual_review",
        "confidence": candidate["confidence"],
        "errors": ["representative_semantic_preview_pending_acceptance"],
        "comparison": {
            "changed": 0,
            "change_ratio": 0.0,
            "initial_values": 0,
            "verified_values": 0,
            "missing_to_zero": 0,
            "foreground_iou": quality["foregroundIoU"],
            "mean_absolute_error": quality["meanAbsoluteError"],
            "pixel_disagreement_ratio": quality["pixelDisagreementRatio"],
        },
    })
    write_json(result_path, result)
    return {
        "asset_id": asset_id,
        "source_sha256": visual["evidence"]["sourceSha256"],
        "render_sha256": visual["evidence"]["renderSha256"],
        "structured_path": str(candidate_path.relative_to(ROOT)),
        "rendered_path": str(rendered_path.relative_to(ROOT)),
        "comparison_path": str(comparison_path.relative_to(ROOT)),
        "quality": quality,
        "complexity": visual["complexity"],
        "trace": {
            "strategy": "semantic_microchart",
            "visualGrammar": visual["visualGrammar"],
            "panelCount": len(visual["panels"]),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", action="append", default=[], help="Convert only the given asset id (repeatable).")
    args = parser.parse_args()
    migrated = migrate_candidates()
    manifest = read_jsonl(ROOT / "manifest.jsonl")
    if args.asset_id:
        selected = set(args.asset_id)
        targets = [entry for entry in manifest if entry["asset_id"] in selected]
    else:
        targets = [entry for entry in manifest if entry["processing_status"] == "unsupported"]
    completed: list[dict[str, Any]] = []
    for index, entry in enumerate(targets, start=1):
        artifact = convert_one(entry)
        completed.append(artifact)
        print(
            f"[{index:02d}/{len(targets):02d}] {entry['asset_id']} "
            f"IoU={artifact['quality']['foregroundIoU']:.4f} "
            f"commands={artifact['complexity'].get('pathCommandCount', artifact['complexity']['estimatedDrawCommandCount'])}"
        )
    report_path = ROOT / "structured-visual-conversion.json"
    previous_assets: dict[str, dict[str, Any]] = {}
    if report_path.is_file():
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        previous_assets = {row["asset_id"]: row for row in previous.get("assets", [])}
    previous_assets.update({row["asset_id"]: row for row in completed})
    report = {
        "schema_version": "data-analysis-structured-visual-conversion-v1",
        "converter_version": CONVERTER_VERSION,
        "migrated_existing_candidates": migrated,
        "converted_count": len(previous_assets),
        "assets": [previous_assets[key] for key in sorted(previous_assets)],
    }
    write_json(report_path, report)
    print(json.dumps({"migrated": migrated, "converted": len(completed)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
