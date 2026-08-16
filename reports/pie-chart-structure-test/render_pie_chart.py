from __future__ import annotations

import html
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "structured-pie-chart.json"
OUTPUT = ROOT / "redrawn-pie-chart.svg"


def point(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return cx + radius * math.sin(angle), cy - radius * math.cos(angle)


def main() -> None:
    chart = json.loads(DATA.read_text(encoding="utf-8"))
    width = height = 320
    cx = cy = 160
    radius = 132
    fills = ("#f7f7f7", "#ffffff", "#f2f2f2", "#fafafa")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g stroke="#151515" stroke-width="3" stroke-linejoin="round">',
    ]
    for index, slice_ in enumerate(chart["slices"]):
        start = float(slice_["startAngle"])
        end = float(slice_["endAngle"])
        sweep = float(slice_["sweepAngle"])
        x1, y1 = point(cx, cy, radius, start)
        x2, y2 = point(cx, cy, radius, end)
        large_arc = 1 if sweep > 180 else 0
        parts.append(
            f'<path d="M {cx} {cy} L {x1:.2f} {y1:.2f} '
            f'A {radius} {radius} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" '
            f'fill="{fills[index % len(fills)]}"/>'
        )
        mid = start + sweep / 2
        label_radius = radius * (0.56 if sweep >= 50 else 0.72)
        lx, ly = point(cx, cy, label_radius, mid)
        parts.append(
            f'<text x="{lx:.2f}" y="{ly + 9:.2f}" text-anchor="middle" '
            'font-family="Arial, PingFang SC, sans-serif" font-size="34" '
            f'font-weight="500" stroke="none" fill="#333333">{html.escape(str(slice_["label"]))}</text>'
        )
    parts.extend(["</g>", "</svg>"])
    OUTPUT.write_text("\n".join(parts), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
