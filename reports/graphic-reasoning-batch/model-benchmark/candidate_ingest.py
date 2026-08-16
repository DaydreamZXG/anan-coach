#!/usr/bin/env python3
"""Parse an image-to-SVG model result into a typed, non-authoritative candidate.

The output of this module is deliberately not a final graphic-primitives-v2
scene.  It is an intermediate proposal that must still be fitted to source
pixels and checked against deterministic panel/topology evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CANDIDATE_SCHEMA = "graphic-vector-model-candidate-v1"
MAX_SVG_BYTES = 1_048_576
MAX_XML_NODES = 4_096
MAX_PRIMITIVES = 128
FORBIDDEN_ELEMENTS = {
    "animation",
    "audio",
    "embed",
    "filter",
    "foreignObject",
    "iframe",
    "image",
    "mask",
    "object",
    "script",
    "style",
    "use",
    "video",
}
IGNORED_ELEMENTS = {"desc", "metadata", "title"}
CONTAINER_ELEMENTS = {"svg", "g"}
DRAWABLE_ELEMENTS = {
    "circle",
    "ellipse",
    "line",
    "path",
    "polygon",
    "polyline",
    "rect",
    "text",
}
NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
PATH_TOKEN_RE = re.compile(r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
PATH_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}


class CandidateError(ValueError):
    pass


@dataclass(frozen=True)
class ViewBox:
    x: float
    y: float
    width: float
    height: float

    def point(self, x: float, y: float) -> dict[str, float]:
        return {
            "x": round((x - self.x) / self.width * 1000.0, 3),
            "y": round((y - self.y) / self.height * 1000.0, 3),
        }

    def dx(self, value: float) -> float:
        return round(value / self.width * 1000.0, 3)

    def dy(self, value: float) -> float:
        return round(value / self.height * 1000.0, 3)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number(value: str | None, field: str, default: float | None = None) -> float:
    if value is None:
        if default is not None:
            return default
        raise CandidateError(f"missing_{field}")
    match = NUMBER_RE.fullmatch(value.strip().removesuffix("px"))
    if not match:
        raise CandidateError(f"invalid_{field}")
    result = float(match.group(0))
    if not math.isfinite(result):
        raise CandidateError(f"non_finite_{field}")
    return result


def _view_box(root: ET.Element) -> ViewBox:
    raw = root.get("viewBox")
    if raw:
        values = [float(value) for value in NUMBER_RE.findall(raw)]
        if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
            raise CandidateError("invalid_viewBox")
        return ViewBox(*values)
    width = _number(root.get("width"), "width")
    height = _number(root.get("height"), "height")
    if width <= 0 or height <= 0:
        raise CandidateError("invalid_dimensions")
    return ViewBox(0.0, 0.0, width, height)


def _identity_transform() -> dict[str, float]:
    return {
        "translateX": 0,
        "translateY": 0,
        "scaleX": 1,
        "scaleY": 1,
        "rotateDegrees": 0,
        "originX": 0,
        "originY": 0,
    }


def _color(value: str | None, default: str | None) -> str | None:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized == "none":
        return None
    aliases = {"black": "#111111", "white": "#ffffff", "transparent": None}
    if normalized in aliases:
        return aliases[normalized]
    if re.fullmatch(r"#[0-9a-f]{3,8}", normalized) or re.fullmatch(r"rgba?\([\d\s.,%]+\)", normalized):
        return normalized
    raise CandidateError("unsupported_color")


def _style_map(element: ET.Element, inherited: dict[str, str] | None = None) -> dict[str, str]:
    result = dict(inherited or {})
    raw = element.get("style")
    if raw:
        for declaration in raw.split(";"):
            if ":" in declaration:
                key, value = declaration.split(":", 1)
                result[key.strip()] = value.strip()
    for key in ("stroke", "fill", "stroke-width", "stroke-dasharray", "opacity", "fill-opacity", "stroke-opacity"):
        if element.get(key) is not None:
            result[key] = str(element.get(key))
    return result


def _primitive_style(style: dict[str, str], view_box: ViewBox, *, text: bool = False) -> dict[str, Any]:
    stroke = _color(style.get("stroke"), None)
    fill = _color(style.get("fill"), "#111111" if text else "#111111")
    line_width = _number(style.get("stroke-width"), "stroke_width", 1.0)
    dash: list[float] = []
    if style.get("stroke-dasharray") not in (None, "", "none"):
        dash = [round(float(value), 3) for value in NUMBER_RE.findall(style["stroke-dasharray"])]
    opacity = _number(style.get("opacity"), "opacity", 1.0)
    if not 0 <= opacity <= 1:
        raise CandidateError("opacity_out_of_range")
    return {
        "stroke": stroke,
        "fill": fill,
        "lineWidth": round(line_width / ((view_box.width + view_box.height) / 2.0) * 1000.0, 3),
        "dash": dash,
        "opacity": round(opacity, 4),
    }


def _primitive(primitive_type: str, style: dict[str, Any], **geometry: Any) -> dict[str, Any]:
    return {"type": primitive_type, **geometry, "style": style, "transform": _identity_transform()}


def _points(raw: str | None) -> list[tuple[float, float]]:
    values = [float(value) for value in NUMBER_RE.findall(raw or "")]
    if len(values) < 4 or len(values) % 2:
        raise CandidateError("invalid_points")
    return list(zip(values[::2], values[1::2]))


def _path_tokens(raw: str) -> list[str]:
    tokens = PATH_TOKEN_RE.findall(raw)
    residue = PATH_TOKEN_RE.sub("", raw).replace(",", "").strip()
    if residue:
        raise CandidateError("invalid_path_token")
    if not tokens:
        raise CandidateError("empty_path")
    return tokens


def _arc_center(
    start: tuple[float, float],
    end: tuple[float, float],
    rx: float,
    ry: float,
    rotation: float,
    large_arc: bool,
    sweep: bool,
) -> dict[str, float | bool] | None:
    if rx == 0 or ry == 0 or start == end:
        return None
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation % 360.0)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx = (start[0] - end[0]) / 2.0
    dy = (start[1] - end[1]) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy
    scale = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if scale > 1:
        factor = math.sqrt(scale)
        rx *= factor
        ry *= factor
    numerator = max(0.0, rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p)
    denominator = max(1e-12, rx * rx * y1p * y1p + ry * ry * x1p * x1p)
    coefficient = (-1.0 if large_arc == sweep else 1.0) * math.sqrt(numerator / denominator)
    cxp = coefficient * (rx * y1p / ry)
    cyp = coefficient * (-ry * x1p / rx)
    cx = cos_phi * cxp - sin_phi * cyp + (start[0] + end[0]) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (start[1] + end[1]) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        length = max(1e-12, math.hypot(ux, uy) * math.hypot(vx, vy))
        value = math.acos(max(-1.0, min(1.0, dot / length)))
        return -value if ux * vy - uy * vx < 0 else value

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    start_angle = angle(1.0, 0.0, ux, uy)
    delta = angle(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    return {
        "cx": cx,
        "cy": cy,
        "rx": rx,
        "ry": ry,
        "start": math.degrees(start_angle),
        "end": math.degrees(start_angle + delta),
        "anticlockwise": not sweep,
    }


def _path_primitives(raw: str, style: dict[str, Any], view_box: ViewBox) -> tuple[list[dict[str, Any]], list[str]]:
    tokens = _path_tokens(raw)
    primitives: list[dict[str, Any]] = []
    warnings: list[str] = []
    index = 0
    command: str | None = None
    current = (0.0, 0.0)
    subpath_start = current
    previous_control: tuple[float, float] | None = None
    previous_curve_command: str | None = None

    def absolute_pair(x: float, y: float, relative: bool) -> tuple[float, float]:
        return (current[0] + x, current[1] + y) if relative else (x, y)

    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
            if command.upper() == "Z":
                if current != subpath_start:
                    primitives.append(_primitive("line", style, **{"from": view_box.point(*current), "to": view_box.point(*subpath_start)}))
                current = subpath_start
                previous_control = None
                previous_curve_command = None
                command = None
                continue
        if command is None:
            raise CandidateError("path_command_required")
        upper = command.upper()
        arity = PATH_ARITY[upper]
        if index + arity > len(tokens) or any(token.isalpha() for token in tokens[index:index + arity]):
            raise CandidateError("path_parameter_count")
        values = [float(value) for value in tokens[index:index + arity]]
        index += arity
        relative = command.islower()

        if upper == "M":
            current = absolute_pair(values[0], values[1], relative)
            subpath_start = current
            command = "l" if relative else "L"
            previous_control = None
            previous_curve_command = None
            continue
        start = current
        if upper == "L":
            current = absolute_pair(values[0], values[1], relative)
            primitives.append(_primitive("line", style, **{"from": view_box.point(*start), "to": view_box.point(*current)}))
        elif upper == "H":
            current = (current[0] + values[0], current[1]) if relative else (values[0], current[1])
            primitives.append(_primitive("line", style, **{"from": view_box.point(*start), "to": view_box.point(*current)}))
        elif upper == "V":
            current = (current[0], current[1] + values[0]) if relative else (current[0], values[0])
            primitives.append(_primitive("line", style, **{"from": view_box.point(*start), "to": view_box.point(*current)}))
        elif upper == "C":
            c1 = absolute_pair(values[0], values[1], relative)
            c2 = absolute_pair(values[2], values[3], relative)
            current = absolute_pair(values[4], values[5], relative)
            primitives.append(_primitive("cubicBezier", style, **{
                "from": view_box.point(*start), "control1": view_box.point(*c1),
                "control2": view_box.point(*c2), "to": view_box.point(*current),
            }))
            previous_control = c2
        elif upper == "S":
            c1 = (
                (2 * start[0] - previous_control[0], 2 * start[1] - previous_control[1])
                if previous_curve_command in {"C", "S"} and previous_control else start
            )
            c2 = absolute_pair(values[0], values[1], relative)
            current = absolute_pair(values[2], values[3], relative)
            primitives.append(_primitive("cubicBezier", style, **{
                "from": view_box.point(*start), "control1": view_box.point(*c1),
                "control2": view_box.point(*c2), "to": view_box.point(*current),
            }))
            previous_control = c2
        elif upper == "Q":
            control = absolute_pair(values[0], values[1], relative)
            current = absolute_pair(values[2], values[3], relative)
            primitives.append(_primitive("quadraticBezier", style, **{
                "from": view_box.point(*start), "control": view_box.point(*control), "to": view_box.point(*current),
            }))
            previous_control = control
        elif upper == "T":
            control = (
                (2 * start[0] - previous_control[0], 2 * start[1] - previous_control[1])
                if previous_curve_command in {"Q", "T"} and previous_control else start
            )
            current = absolute_pair(values[0], values[1], relative)
            primitives.append(_primitive("quadraticBezier", style, **{
                "from": view_box.point(*start), "control": view_box.point(*control), "to": view_box.point(*current),
            }))
            previous_control = control
        elif upper == "A":
            current = absolute_pair(values[5], values[6], relative)
            arc = _arc_center(start, current, values[0], values[1], values[2], bool(values[3]), bool(values[4]))
            if arc is None:
                primitives.append(_primitive("line", style, **{"from": view_box.point(*start), "to": view_box.point(*current)}))
            else:
                primitives.append(_primitive("arc", style, center=view_box.point(float(arc["cx"]), float(arc["cy"])),
                    radiusX=view_box.dx(float(arc["rx"])), radiusY=view_box.dy(float(arc["ry"])),
                    rotationDegrees=round(values[2], 3), startDegrees=round(float(arc["start"]), 3),
                    endDegrees=round(float(arc["end"]), 3), anticlockwise=bool(arc["anticlockwise"])))
            previous_control = None
        previous_curve_command = upper
        if upper not in {"C", "S", "Q", "T"}:
            previous_control = None
    if style.get("fill") is not None:
        warnings.append("path_fill_is_model_candidate_only")
    return primitives, warnings


def _element_primitives(element: ET.Element, inherited_style: dict[str, str], view_box: ViewBox) -> tuple[list[dict[str, Any]], list[str]]:
    tag = _local_name(element.tag)
    raw_style = _style_map(element, inherited_style)
    style = _primitive_style(raw_style, view_box, text=tag == "text")
    warnings: list[str] = []
    if tag == "line":
        return [_primitive("line", style, **{
            "from": view_box.point(_number(element.get("x1"), "x1", 0), _number(element.get("y1"), "y1", 0)),
            "to": view_box.point(_number(element.get("x2"), "x2", 0), _number(element.get("y2"), "y2", 0)),
        })], warnings
    if tag == "rect":
        x, y = _number(element.get("x"), "x", 0), _number(element.get("y"), "y", 0)
        width, height = _number(element.get("width"), "rect_width"), _number(element.get("height"), "rect_height")
        if width <= 0 or height <= 0:
            raise CandidateError("invalid_rect")
        box = {**view_box.point(x, y), "width": view_box.dx(width), "height": view_box.dy(height)}
        radius = max(_number(element.get("rx"), "rx", 0), _number(element.get("ry"), "ry", 0))
        if radius > 0:
            return [_primitive("roundedRect", style, box=box, radius=max(view_box.dx(radius), view_box.dy(radius)))], warnings
        return [_primitive("rect", style, box=box)], warnings
    if tag == "circle":
        radius = _number(element.get("r"), "radius")
        if radius <= 0:
            raise CandidateError("invalid_circle")
        return [_primitive("circle", style, center=view_box.point(_number(element.get("cx"), "cx", 0), _number(element.get("cy"), "cy", 0)), radius=round((view_box.dx(radius) + view_box.dy(radius)) / 2, 3))], warnings
    if tag == "ellipse":
        rx, ry = _number(element.get("rx"), "rx"), _number(element.get("ry"), "ry")
        if rx <= 0 or ry <= 0:
            raise CandidateError("invalid_ellipse")
        return [_primitive("ellipse", style, center=view_box.point(_number(element.get("cx"), "cx", 0), _number(element.get("cy"), "cy", 0)), radiusX=view_box.dx(rx), radiusY=view_box.dy(ry), rotationDegrees=0)], warnings
    if tag in {"polyline", "polygon"}:
        points = [view_box.point(x, y) for x, y in _points(element.get("points"))]
        return [_primitive(tag, style, points=points)], warnings
    if tag == "path":
        return _path_primitives(element.get("d") or "", style, view_box)
    if tag == "text":
        content = "".join(element.itertext()).strip()
        if not content or len(content) > 16:
            raise CandidateError("invalid_text_content")
        anchor = element.get("text-anchor", "start")
        align = {"start": "left", "middle": "center", "end": "right"}.get(anchor, "left")
        font_family = (element.get("font-family") or "sans").lower()
        font_category = "monospace" if "mono" in font_family else "serif" if "serif" in font_family else "symbol" if "symbol" in font_family else "sans"
        weight = int(round(_number(element.get("font-weight"), "font_weight", 400) / 100) * 100)
        weight = min((400, 500, 600, 700), key=lambda value: abs(value - weight))
        return [_primitive("textGlyph", style, content=content,
            position=view_box.point(_number(element.get("x"), "text_x", 0), _number(element.get("y"), "text_y", 0)),
            fontCategory=font_category, fontStyle="italic" if element.get("font-style") == "italic" else "normal",
            fontWeight=weight, fontSize=view_box.dy(_number(element.get("font-size"), "font_size", 16)),
            direction="vertical" if element.get("writing-mode", "").startswith("vertical") else "ltr", align=align)], warnings
    raise CandidateError(f"unsupported_element_{tag}")


def _families(primitives: Iterable[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for primitive in primitives:
        primitive_type = primitive["type"]
        if primitive_type in {"line", "polyline"}:
            result.add("line")
        if primitive_type in {"quadraticBezier", "cubicBezier", "arc"}:
            result.add("curve")
        if primitive_type in {"circle", "ellipse", "arc"}:
            result.add("circle_arc")
        if primitive_type in {"polygon", "rect", "roundedRect"}:
            result.add("polygon")
        if primitive_type == "textGlyph":
            result.add("text")
        if primitive_type not in {"line", "polyline", "textGlyph"} and primitive.get("style", {}).get("fill") not in (None, "#ffffff"):
            result.add("fill")
    return result


def ingest_svg(
    svg_path: Path,
    *,
    asset_id: str,
    panel_id: str,
    source_sha256: str,
    provider: str,
    model: str,
    semantics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = svg_path.read_bytes()
    if len(raw) > MAX_SVG_BYTES:
        raise CandidateError("svg_size_limit")
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise CandidateError("invalid_source_sha256")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise CandidateError(f"invalid_xml:{error}") from error
    if _local_name(root.tag) != "svg":
        raise CandidateError("root_not_svg")
    nodes = list(root.iter())
    if len(nodes) > MAX_XML_NODES:
        raise CandidateError("xml_node_limit")
    view_box = _view_box(root)
    primitives: list[dict[str, Any]] = []
    issues: list[str] = []
    forbidden: list[str] = []

    def visit(element: ET.Element, inherited_style: dict[str, str] | None = None) -> None:
        tag = _local_name(element.tag)
        if tag in FORBIDDEN_ELEMENTS:
            forbidden.append(tag)
            return
        if element.get("transform"):
            issues.append(f"unflattened_transform:{tag}")
            return
        if tag in IGNORED_ELEMENTS:
            return
        style = _style_map(element, inherited_style)
        if tag in CONTAINER_ELEMENTS:
            for child in list(element):
                visit(child, style)
            return
        if tag not in DRAWABLE_ELEMENTS:
            issues.append(f"unsupported_element:{tag}")
            return
        try:
            items, warnings = _element_primitives(element, style, view_box)
            primitives.extend(items)
            issues.extend(warnings)
        except CandidateError as error:
            issues.append(str(error))

    visit(root)
    if len(primitives) > MAX_PRIMITIVES:
        issues.append("primitive_limit_exceeded")
    families = _families(primitives)
    expected = semantics or {}
    for family in expected.get("expectedPrimitiveFamilies", []):
        if family not in families:
            issues.append(f"missing_expected_family:{family}")
    contains_text = expected.get("containsText")
    if contains_text is True and "text" not in families:
        issues.append("expected_textGlyph_missing")
    if contains_text is False and "text" in families:
        issues.append("unexpected_textGlyph")
    contains_fill = expected.get("containsFilledRegions")
    if contains_fill is False and "fill" in families:
        issues.append("unexpected_filled_region")
    if contains_fill is True and "fill" not in families:
        issues.append("expected_filled_region_missing")
    if "fill" in families:
        issues.append("fill_requires_deterministic_pixel_verification")

    hard_issues = [issue for issue in issues if not issue.endswith("model_candidate_only") and issue != "fill_requires_deterministic_pixel_verification"]
    security_status = "fail" if forbidden else "pass"
    typed_status = "pass" if primitives and not any(issue.startswith(("unsupported_element", "invalid_", "path_", "unflattened_transform", "primitive_limit")) for issue in issues) else "fail"
    semantic_status = "pass" if not any(issue.startswith(("missing_expected", "expected_", "unexpected_")) for issue in issues) else "fail"
    candidate_status = "candidate_ready_for_refinement" if security_status == typed_status == semantic_status == "pass" and not hard_issues else "candidate_rejected"
    return {
        "schemaVersion": CANDIDATE_SCHEMA,
        "assetId": asset_id,
        "panelId": panel_id,
        "sourceSha256": source_sha256,
        "provider": provider,
        "model": model,
        "candidateRole": "non_authoritative_geometry_proposal",
        "coordinateSpace": {"width": 1000, "height": 1000},
        "sourceViewBox": {"x": view_box.x, "y": view_box.y, "width": view_box.width, "height": view_box.height},
        "primitives": primitives,
        "evidence": {
            "candidateSvg": str(svg_path.resolve()),
            "candidateSvgSha256": hashlib.sha256(raw).hexdigest(),
            "sourceSemantics": expected,
            "detectedPrimitiveFamilies": sorted(families),
            "issues": sorted(set(issues)),
            "forbiddenElements": sorted(set(forbidden)),
            "geometryAuthority": "deterministic_cv_pixel_refinement_required",
            "layoutAuthority": "deterministic_panel_inventory_only",
            "noRasterFallback": True,
        },
        "gates": {
            "security": {"status": security_status},
            "typedPrimitive": {"status": typed_status},
            "semanticExpectation": {"status": semantic_status},
            "deterministicPixelFit": {"status": "pending"},
            "topology": {"status": "pending"},
            "frontendContract": {"status": "pending"},
            "finalAcceptance": {"status": "blocked", "reason": "model_candidate_is_never_final_geometry"},
        },
        "status": candidate_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--svg", type=Path, required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--panel-id", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--semantics", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    semantics = json.loads(args.semantics.read_text(encoding="utf-8")) if args.semantics else None
    result = ingest_svg(args.svg, asset_id=args.asset_id, panel_id=args.panel_id,
        source_sha256=args.source_sha256, provider=args.provider, model=args.model, semantics=semantics)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if result["status"] == "candidate_ready_for_refinement" else 2


if __name__ == "__main__":
    raise SystemExit(main())
