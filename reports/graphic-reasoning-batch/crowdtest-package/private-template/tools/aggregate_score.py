#!/usr/bin/env python3
"""Combine automatic, manual, and blind-solver evidence into a 0-10 suggestion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--manual", type=Path, required=True)
    parser.add_argument("--deepseek", type=Path, required=True)
    parser.add_argument("--honesty", type=float, default=0.5, help="Human score from 0 to 0.5")
    parser.add_argument("--out", type=Path, default=Path("aggregate-score-report.json"))
    args = parser.parse_args()
    if not 0 <= args.honesty <= 0.5:
        parser.error("--honesty must be between 0 and 0.5")

    validation = read(args.validation)
    manual = read(args.manual)
    deepseek = read(args.deepseek)
    expected = max(1, int(validation.get("expectedAssetCount", 0)))
    coverage = min(1.0, max(0.0, float(validation.get("manifestAssetCount", 0)) / expected))
    valid_rate = min(1.0, max(0.0, float(validation.get("validAssetCount", 0)) / expected))
    coverage_score = 2.0 * coverage
    contract_score = 1.5 * valid_rate
    manual_score = min(4.0, max(0.0, float(manual.get("score", 0))))

    accuracy = min(1.0, max(0.0, float(deepseek.get("accuracy", 0))))
    grammar_accuracies = [float(row.get("accuracy", 0)) for row in deepseek.get("byVisualGrammar", {}).values() if row.get("expected", 0)]
    macro_accuracy = sum(grammar_accuracies) / len(grammar_accuracies) if grammar_accuracies else 0.0
    deepseek_score = 1.6 * min(1.0, accuracy / 0.70) + 0.4 * min(1.0, macro_accuracy / 0.60)

    red_flags = []
    if coverage < 0.90:
        red_flags.append("coverage_below_90_percent")
    if validation.get("globalErrors"):
        red_flags.append("validation_global_errors")
    if manual.get("criticalErrorCount", 0) > 21:
        red_flags.append("critical_error_rate_above_10_percent")
    raw_score = coverage_score + contract_score + manual_score + deepseek_score + args.honesty
    suggested = min(2.0, raw_score) if red_flags else raw_score
    report = {
        "schemaVersion": "graphic-reasoning-aggregate-score-v1",
        "suggestedScoreOutOf10": round(suggested, 2),
        "rawScoreOutOf10": round(raw_score, 2),
        "components": {
            "coverageAndTraceability": round(coverage_score, 3),
            "contractAndAnswerIsolation": round(contract_score, 3),
            "visibleFactAccuracy": round(manual_score, 3),
            "downstreamReasoningUsefulness": round(deepseek_score, 3),
            "honestyAndReviewability": round(args.honesty, 3),
        },
        "redFlags": red_flags,
        "note": "This is evidence aggregation, not an automatic final grade. The human reviewer remains responsible for the crowdtest score.",
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
