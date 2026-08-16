from __future__ import annotations

import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "structured-mobile-production-combo-chart.json"
OUTPUT = ROOT / "redrawn-mobile-production-combo-chart.svg"


def main() -> None:
    chart = json.loads(DATA.read_text(encoding="utf-8"))
    width, height = 700, 361
    left, right, top, bottom = 70, 45, 55, 78
    plot_width = width - left - right
    plot_height = height - top - bottom
    bar_series, line_series = chart["series"]
    left_axis, right_axis = chart["leftAxis"], chart["rightAxis"]

    def y_left(value: float) -> float:
        return top + plot_height * (left_axis["max"] - value) / (left_axis["max"] - left_axis["min"])

    def y_right(value: float) -> float:
        return top + plot_height * (right_axis["max"] - value) / (right_axis["max"] - right_axis["min"])

    category_step = plot_width / len(chart["categories"])
    centers = [left + category_step * (index + 0.5) for index in range(len(chart["categories"]))]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g font-family="Arial, PingFang SC, sans-serif" fill="#333">',
        f'<text x="{width / 2}" y="28" text-anchor="middle" font-size="20">{html.escape(chart["title"])}</text>',
        f'<text x="12" y="52" font-size="15">{html.escape(left_axis["unit"])}</text>',
        f'<text x="680" y="52" font-size="15">{html.escape(right_axis["unit"])}</text>',
        f'<path d="M{left} {top}V{top + plot_height}H{width - right}" fill="none" stroke="#555" stroke-width="1.2"/>',
        f'<path d="M{width - right} {top}V{top + plot_height}" fill="none" stroke="#777" stroke-width="1"/>',
    ]

    for tick in left_axis["ticks"]:
        y = y_left(tick)
        parts.append(f'<text x="58" y="{y + 4:.2f}" text-anchor="end" font-size="12" fill="#666">{tick}</text>')
    for tick in right_axis["ticks"]:
        y = y_right(tick)
        parts.append(f'<text x="{width - right + 10}" y="{y + 4:.2f}" font-size="12" fill="#666">{tick}</text>')

    bar_width = 22
    for center, category, value in zip(centers, chart["categories"], bar_series["values"]):
        y = y_left(value)
        bar_height = top + plot_height - y
        parts.append(f'<rect x="{center - bar_width / 2:.2f}" y="{y:.2f}" width="{bar_width}" height="{bar_height:.2f}" fill="#8e8e8e"/>')
        parts.append(f'<text x="{center:.2f}" y="{y - 8:.2f}" text-anchor="middle" font-size="12" fill="#555">{value}</text>')
        parts.append(f'<text x="{center:.2f}" y="{top + plot_height + 23}" text-anchor="middle" font-size="13">{html.escape(category)}</text>')

    points = [(center, y_right(value)) for center, value in zip(centers, line_series["values"])]
    path = " ".join(("M" if index == 0 else "L") + f"{x:.2f} {y:.2f}" for index, (x, y) in enumerate(points))
    parts.append(f'<path d="{path}" fill="none" stroke="#b4b4b4" stroke-width="4" stroke-linejoin="round"/>')
    for (x, y), value in zip(points, line_series["values"]):
        parts.append(f'<rect x="{x - 4}" y="{y - 4}" width="8" height="8" fill="#b4b4b4"/>')
        parts.append(f'<text x="{x:.2f}" y="{y - 12:.2f}" text-anchor="middle" font-size="13" fill="#555">{value:g}</text>')

    legend_y = height - 14
    parts.extend(
        [
            f'<rect x="205" y="{legend_y - 9}" width="30" height="9" fill="#8e8e8e"/>',
            f'<text x="242" y="{legend_y}" font-size="13">{html.escape(bar_series["name"])}</text>',
            f'<path d="M430 {legend_y - 5}H460" stroke="#b4b4b4" stroke-width="4"/><rect x="441" y="{legend_y - 9}" width="8" height="8" fill="#b4b4b4"/>',
            f'<text x="468" y="{legend_y}" font-size="13">{html.escape(line_series["name"])}</text>',
            "</g>",
            "</svg>",
        ]
    )
    OUTPUT.write_text("\n".join(parts), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
