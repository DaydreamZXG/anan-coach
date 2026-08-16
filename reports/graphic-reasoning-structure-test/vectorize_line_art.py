from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source-cq_09fc9cc57c86e57c553944c2.png"
STRUCTURED = ROOT / "structured-cq_09fc9cc57c86e57c553944c2.json"
REDRAWN = ROOT / "redrawn-cq_09fc9cc57c86e57c553944c2.svg"

ITEMS = (
    ("s1", "sequence", "", 1, (7, 5, 104, 105)),
    ("s2", "sequence", "", 2, (105, 5, 202, 105)),
    ("s3", "sequence", "", 3, (203, 5, 300, 105)),
    ("s4", "sequence", "", 4, (301, 5, 398, 105)),
    ("s5", "sequence", "", 5, (399, 5, 496, 105)),
    ("A", "option", "A", 1, (104, 130, 204, 225)),
    ("B", "option", "B", 2, (204, 130, 302, 225)),
    ("C", "option", "C", 3, (302, 130, 399, 225)),
    ("D", "option", "D", 4, (399, 130, 497, 225)),
)


def contour_path(mask: np.ndarray) -> str:
    contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    commands: list[str] = []
    for contour in contours:
        approximation = cv2.approxPolyDP(contour, 0.22, True)
        points = approximation[:, 0, :]
        if len(points) < 3:
            continue
        first_x, first_y = points[0]
        commands.append(f"M{int(first_x)} {int(first_y)}")
        commands.extend(f"L{int(x)} {int(y)}" for x, y in points[1:])
        commands.append("Z")
    return " ".join(commands)


def main() -> None:
    source = cv2.imread(str(SOURCE), cv2.IMREAD_GRAYSCALE)
    if source is None:
        raise SystemExit(f"cannot read {SOURCE}")

    structured_items: list[dict[str, object]] = []
    svg_groups: list[str] = []
    for item_id, role, label, position, (left, top, right, bottom) in ITEMS:
        crop = source[top:bottom, left:right]
        _, black = cv2.threshold(crop, 160, 255, cv2.THRESH_BINARY_INV)
        path = contour_path(black)
        width = right - left
        height = bottom - top
        structured_items.append(
            {
                "id": item_id,
                "role": role,
                "label": label,
                "position": position,
                "bounds": {"x": left, "y": top, "width": width, "height": height},
                "viewBox": f"0 0 {width} {height}",
                "paths": [{"d": path, "fill": "#000000", "fillRule": "evenodd"}],
            }
        )
        svg_groups.append(
            f'<g id="{item_id}" transform="translate({left} {top})">'
            f'<path d="{path}" fill="#000" fill-rule="evenodd"/></g>'
        )

    payload = {
        "schemaVersion": "graphic-line-art-v1",
        "sourceQuestionId": "cq_09fc9cc57c86e57c553944c2",
        "sourceAnswer": "C",
        "canvas": {"width": 600, "height": 268},
        "items": structured_items,
        "decorations": {
            "missingCell": {"x": 496, "y": 5, "width": 98, "height": 100},
            "optionLabels": ["A", "B", "C", "D"],
        },
    }
    STRUCTURED.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 268" width="600" height="268">
  <rect width="600" height="268" fill="#fff"/>
  {''.join(svg_groups)}
  <rect x="496" y="5" width="98" height="100" fill="none" stroke="#000" stroke-width="2"/>
  <text x="545" y="91" text-anchor="middle" font-family="Georgia,serif" font-size="68" fill="#000">?</text>
  <g text-anchor="middle" font-family="Arial,sans-serif" font-size="27" fill="#000">
    <text x="154" y="261">A</text><text x="253" y="261">B</text>
    <text x="350" y="261">C</text><text x="448" y="261">D</text>
  </g>
</svg>'''
    REDRAWN.write_text(svg, encoding="utf-8")
    print(STRUCTURED)
    print(REDRAWN)


if __name__ == "__main__":
    main()
