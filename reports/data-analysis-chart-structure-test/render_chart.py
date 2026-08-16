from __future__ import annotations

import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "structured-cq_03ea803d569c90b5b17c072c.json"
OUTPUT = ROOT / "redrawn-cq_03ea803d569c90b5b17c072c.svg"


def main() -> None:
    chart = json.loads(DATA.read_text(encoding="utf-8"))
    width, height = 593, 307
    left, right, top, bottom = 47, 5, 40, 53
    plot_width = width - left - right
    plot_height = height - top - bottom
    maximum = chart["yAxis"]["max"]
    step = chart["yAxis"]["step"]
    category_width = plot_width / len(chart["categories"])
    bar_width = 18
    gap = 4
    group_width = len(chart["series"]) * bar_width + (len(chart["series"]) - 1) * gap

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        '<g font-family="Arial, PingFang SC, sans-serif" fill="#111">',
        f'<text x="4" y="19" font-size="14">（{html.escape(chart["unit"])}）</text>',
        f'<text x="296.5" y="18" text-anchor="middle" font-size="18">{html.escape(chart["title"])}</text>',
    ]

    for tick in range(chart["yAxis"]["min"], maximum + 1, step):
        y = top + plot_height - tick / maximum * plot_height
        parts.append(f'<text x="36" y="{y + 4:.2f}" text-anchor="end" font-size="11" fill="#555">{tick}</text>')
    parts.extend(
        [
            f'<path d="M{left} {top}V{top + plot_height}H{width - right}" fill="none" stroke="#111" stroke-width="1"/>',
        ]
    )

    for category_index, category in enumerate(chart["categories"]):
        center = left + (category_index + 0.5) * category_width
        start_x = center - group_width / 2
        parts.append(f'<text x="{center:.2f}" y="{top + plot_height + 20}" text-anchor="middle" font-size="13">{html.escape(category)}</text>')
        for series_index, series in enumerate(chart["series"]):
            value = series["values"][category_index]
            if value is None:
                continue
            x = start_x + series_index * (bar_width + gap)
            bar_height = value / maximum * plot_height
            y = top + plot_height - bar_height
            style = series["style"]
            dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
            parts.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width}" height="{bar_height:.2f}" '
                f'fill="{style["fill"]}" stroke="{style["stroke"]}" stroke-width="1"{dash}/>'
            )
            parts.append(f'<text x="{x + bar_width / 2:.2f}" y="{y - 6:.2f}" text-anchor="middle" font-size="12" fill="#444">{value}</text>')

    legend_y = height - 10
    legend_start = 165
    for index, series in enumerate(chart["series"]):
        x = legend_start + index * 77
        style = series["style"]
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        parts.append(f'<rect x="{x}" y="{legend_y - 8}" width="8" height="8" fill="{style["fill"]}" stroke="{style["stroke"]}"{dash}/>')
        parts.append(f'<text x="{x + 12}" y="{legend_y}" font-size="12">{html.escape(series["label"])}</text>')

    parts.extend(["</g>", "</svg>"])
    OUTPUT.write_text("\n".join(parts), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
