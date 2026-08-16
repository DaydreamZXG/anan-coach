#!/usr/bin/env python3
"""Promote explanation-only candidates after an independent Chinese OCR match."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from decimal import Decimal, InvalidOperation

from common import write_json


CJK = re.compile(r"[\u3400-\u9fff]")
NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--qwen-confidence", type=float, default=0.9)
    parser.add_argument("--ocr-confidence", type=float, default=0.9)
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def cjk_counter(value: str) -> Counter[str]:
    return Counter(CJK.findall(value))


def number_counter(value: str) -> Counter[str]:
    values = []
    for raw in NUMBER.findall(value):
        try:
            normalized = format(Decimal(raw).normalize(), "f")
        except InvalidOperation:
            normalized = raw
        values.append(normalized)
    return Counter(values)


def main() -> None:
    args = arguments()
    accepted = []
    rejected = Counter()
    rejected_items = []
    checked = 0
    for line in args.ocr.read_text(encoding="utf-8").splitlines():
        ocr = json.loads(line)
        result_path = args.results / f"{ocr['asset_id']}.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        checked += 1
        expression = result["blocks"][0]["expression"]
        expected = cjk_counter(expression)
        expected_numbers = number_counter(expression)
        observations = ocr.get("observations") or []
        observed_text = "".join(str(item.get("text") or "") for item in observations)
        observed = cjk_counter(observed_text)
        observed_numbers = number_counter(observed_text)
        confidences = [
            float(item.get("confidence") or 0)
            for item in observations
            if cjk_counter(str(item.get("text") or ""))
        ]
        reason = None
        if result.get("priority") != "explanation":
            reason = "not_explanation"
        elif result.get("status") != "needs_manual_review":
            reason = "not_pending"
        elif not expected:
            reason = "candidate_has_no_cjk"
        elif not observed:
            reason = "ocr_has_no_cjk"
        elif expected != observed:
            reason = "cjk_mismatch"
        elif expected_numbers != observed_numbers:
            reason = "number_mismatch"
        elif not isinstance(result.get("confidence"), (int, float)) or result["confidence"] < args.qwen_confidence:
            reason = "qwen_confidence_low"
        elif not confidences or min(confidences) < args.ocr_confidence:
            reason = "ocr_confidence_low"
        if reason:
            rejected[reason] += 1
            if args.include_rejected:
                rejected_items.append(
                    {
                        "asset_id": result["asset_id"],
                        "expression": expression,
                        "qwen_confidence": result.get("confidence"),
                        "ocr_text": observed_text,
                        "ocr_min_confidence": min(confidences) if confidences else 0.0,
                        "reason": reason,
                    }
                )
            continue
        accepted.append(
            {
                "asset_id": result["asset_id"],
                "expression": expression,
                "qwen_confidence": result["confidence"],
                "ocr_text": observed_text,
                "ocr_min_confidence": min(confidences),
            }
        )
        if args.apply:
            result["status"] = "candidate_auto"
            result["verification"]["note"] = (
                result["verification"]["note"]
                + f"；RapidOCR 中文字符独立匹配，min_confidence={min(confidences):.3f}"
            )
            write_json(result_path, result)
    report = {
        "schema_version": "formula-rapidocr-crosscheck-v1",
        "checked_count": checked,
        "accepted_count": len(accepted),
        "rejected_counts": dict(rejected),
        "applied": bool(args.apply),
        "accepted": accepted,
    }
    if args.include_rejected:
        report["rejected"] = rejected_items
    write_json(args.report, report)
    print(json.dumps({k: v for k, v in report.items() if k != "accepted"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
