#!/usr/bin/env python3
"""Score answer predictions privately; never distribute this with the public task."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question-key", type=Path, default=Path("question-key-private.jsonl"))
    parser.add_argument("--sample", type=Path, default=Path("deepseek-sample-private.jsonl"))
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("deepseek-score-report.json"))
    args = parser.parse_args()

    key = {row["questionId"]: row for row in load_jsonl(args.question_key)}
    sample = {row["questionId"]: row for row in load_jsonl(args.sample)}
    predictions: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in load_jsonl(args.predictions):
        question_id = row.get("questionId")
        if question_id in predictions:
            duplicates.append(question_id)
        predictions[question_id] = row

    by_grammar: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "answered": 0, "correct": 0, "informationSufficient": 0})
    invalid: list[str] = []
    details: list[dict[str, Any]] = []
    for question_id, sample_row in sample.items():
        grammar = sample_row["visualGrammar"]
        stats = by_grammar[grammar]
        stats["expected"] += 1
        prediction = predictions.get(question_id)
        answer = prediction.get("answer") if prediction else None
        valid = isinstance(answer, str) and answer in "ABCDEFGH"
        if valid:
            stats["answered"] += 1
        else:
            invalid.append(question_id)
        correct = valid and answer == key[question_id]["officialAnswer"]
        if correct:
            stats["correct"] += 1
        sufficient = bool(prediction and prediction.get("informationSufficient") is True)
        if sufficient:
            stats["informationSufficient"] += 1
        details.append({"questionId": question_id, "visualGrammar": grammar, "answered": valid, "correct": correct, "informationSufficient": sufficient})

    total_expected = len(sample)
    total_answered = sum(v["answered"] for v in by_grammar.values())
    total_correct = sum(v["correct"] for v in by_grammar.values())
    total_sufficient = sum(v["informationSufficient"] for v in by_grammar.values())
    grammar_report = {}
    for grammar, stats in sorted(by_grammar.items()):
        grammar_report[grammar] = {
            **stats,
            "coverageRate": ratio(stats["answered"], stats["expected"]),
            "accuracy": ratio(stats["correct"], stats["expected"]),
            "accuracyOnAnswered": ratio(stats["correct"], stats["answered"]),
            "informationSufficientRate": ratio(stats["informationSufficient"], stats["expected"]),
        }
    report = {
        "schemaVersion": "deepseek-json-only-score-v1",
        "expected": total_expected,
        "answered": total_answered,
        "correct": total_correct,
        "coverageRate": ratio(total_answered, total_expected),
        "accuracy": ratio(total_correct, total_expected),
        "accuracyOnAnswered": ratio(total_correct, total_answered),
        "informationSufficientRate": ratio(total_sufficient, total_expected),
        "duplicateQuestionIds": sorted(set(duplicates)),
        "invalidOrMissingQuestionIds": invalid,
        "byVisualGrammar": grammar_report,
        "details": details,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["expected", "answered", "correct", "coverageRate", "accuracy", "accuracyOnAnswered", "informationSufficientRate"]}, ensure_ascii=False, indent=2))
    return 0 if total_answered == total_expected and not duplicates else 1


if __name__ == "__main__":
    raise SystemExit(main())
