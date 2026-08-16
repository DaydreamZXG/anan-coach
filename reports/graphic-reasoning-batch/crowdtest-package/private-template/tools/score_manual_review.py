#!/usr/bin/env python3
"""Score the stratified human review sheet."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


WEIGHTS = {
    "layout_panel_option_order": 0.5,
    "line_curve_right_angle": 0.9,
    "text_glyph": 0.4,
    "fill_texture": 0.6,
    "count_direction_position": 0.8,
    "topology_parts_3d": 0.8,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("manual-review-report.json"))
    args = parser.parse_args()
    with args.review.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    dimensions = {}
    total_score = 0.0
    incomplete = []
    critical = []
    for field, weight in WEIGHTS.items():
        applicable = 0
        correct = 0
        for row in rows:
            value = (row.get(field) or "").strip().upper()
            if value in {"NA", "N/A"}:
                continue
            if value not in {"0", "1"}:
                incomplete.append(f'{row.get("assetId")}:{field}')
                continue
            applicable += 1
            correct += int(value)
        rate = correct / applicable if applicable else 0.0
        score = rate * weight
        total_score += score
        dimensions[field] = {"weight": weight, "applicable": applicable, "correct": correct, "rate": round(rate, 6), "score": round(score, 4)}

    for row in rows:
        if (row.get("critical_error") or "").strip() == "1":
            critical.append(row.get("assetId"))
    report = {
        "schemaVersion": "graphic-reasoning-manual-review-v1",
        "reviewedRows": len(rows),
        "maxScore": 4.0,
        "score": round(total_score, 4),
        "dimensions": dimensions,
        "criticalErrorCount": len(critical),
        "criticalErrorAssets": critical,
        "incompleteCells": incomplete,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not incomplete else 1


if __name__ == "__main__":
    raise SystemExit(main())
