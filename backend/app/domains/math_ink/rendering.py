from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from math import hypot, isfinite
from typing import Any, Iterable

from PIL import Image, ImageDraw

from app.domains.math_ink.schemas import RecognizeMathInkCommand


RENDERER_VERSION = "math-ink-renderer@v1"
_CANVAS_SIZE = 512
_PADDING = 36
_MAX_RENDER_BYTES = 128 * 1024


class InvalidInkError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RenderedInk:
    png: bytes
    vector_fingerprint: str
    render_fingerprint: str
    width: int
    height: int
    renderer_version: str = RENDERER_VERSION


def render_math_ink(command: RecognizeMathInkCommand) -> RenderedInk:
    strokes = [
        [
            (float(x) * command.scale_x, float(y) * command.scale_y)
            for x, y in zip(stroke.x, stroke.y, strict=True)
        ]
        for stroke in command.strokes
    ]
    vector_payload = json.dumps(
        {
            "contractVersion": "math-ink-vector@v1",
            "scaleX": command.scale_x,
            "scaleY": command.scale_y,
            "strokes": [
                {
                    "x": list(stroke.x),
                    "y": list(stroke.y),
                    "t": list(stroke.t),
                }
                for stroke in command.strokes
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _render(
        strokes,
        maximum_coordinate=50_000.0,
        vector_payload=vector_payload,
    )


def render_annotation_strokes(strokes: Iterable[dict[str, Any]]) -> RenderedInk | None:
    vectors: list[list[tuple[float, float]]] = []
    for stroke in strokes:
        points = stroke.get("points")
        if not isinstance(points, list):
            continue
        vector: list[tuple[float, float]] = []
        for point in points:
            if not isinstance(point, dict):
                raise InvalidInkError("ink_contract_invalid")
            vector.append((float(point.get("x")), float(point.get("y"))))
        if len(vector) >= 2:
            vectors.append(vector)
    if not vectors:
        return None
    return _render(vectors, maximum_coordinate=1.0)


def _render(
    strokes: list[list[tuple[float, float]]],
    *,
    maximum_coordinate: float,
    vector_payload: bytes | None = None,
) -> RenderedInk:
    if not strokes or sum(len(stroke) for stroke in strokes) > 20_000:
        raise InvalidInkError("ink_size_invalid")
    flat = [point for stroke in strokes for point in stroke]
    if any(
        not isfinite(value) or value < 0 or value > maximum_coordinate
        for point in flat
        for value in point
    ):
        raise InvalidInkError("ink_coordinate_invalid")
    path_length = sum(
        hypot(right[0] - left[0], right[1] - left[1])
        for stroke in strokes
        for left, right in zip(stroke, stroke[1:])
    )
    if path_length <= maximum_coordinate * 0.00005:
        raise InvalidInkError("ink_blank")
    min_x = min(point[0] for point in flat)
    max_x = max(point[0] for point in flat)
    min_y = min(point[1] for point in flat)
    max_y = max(point[1] for point in flat)
    extent_x = max_x - min_x
    extent_y = max_y - min_y
    if max(extent_x, extent_y) <= maximum_coordinate * 0.00005:
        raise InvalidInkError("ink_blank")

    if vector_payload is None:
        vector_payload = "|".join(
            ";".join(f"{x:.8f},{y:.8f}" for x, y in stroke)
            for stroke in strokes
        ).encode("ascii")
    vector_fingerprint = sha256(vector_payload).hexdigest()
    drawable = _CANVAS_SIZE - 2 * _PADDING
    span_x = max(extent_x, maximum_coordinate * 0.0001)
    span_y = max(extent_y, maximum_coordinate * 0.0001)
    scale = min(drawable / span_x, drawable / span_y)
    offset_x = (_CANVAS_SIZE - extent_x * scale) / 2.0
    offset_y = (_CANVAS_SIZE - extent_y * scale) / 2.0

    image = Image.new("RGB", (_CANVAS_SIZE, _CANVAS_SIZE), "white")
    draw = ImageDraw.Draw(image)
    line_width = max(3, round(_CANVAS_SIZE / 160))
    for stroke in strokes:
        rendered = [
            (
                round(offset_x + (x - min_x) * scale, 3),
                round(offset_y + (y - min_y) * scale, 3),
            )
            for x, y in stroke
        ]
        draw.line(rendered, fill="black", width=line_width, joint="curve")

    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    png = output.getvalue()
    if len(png) > _MAX_RENDER_BYTES:
        raise InvalidInkError("ink_render_too_large")
    return RenderedInk(
        png=png,
        vector_fingerprint=vector_fingerprint,
        render_fingerprint=sha256(png).hexdigest(),
        width=_CANVAS_SIZE,
        height=_CANVAS_SIZE,
    )
