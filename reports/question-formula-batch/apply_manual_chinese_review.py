#!/usr/bin/env python3
"""Record a completed human review of Chinese formula candidates.

The RapidOCR report is used only as the asset inventory and grouping aid. Every
asset in the report must have been compared with its source image before this
script is run. Confirmed expression fixes are applied separately through the
manual override registry so the original review inventory stays reproducible.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import jsonschema

from common import latex_braces_balanced, write_json


ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "formula-result.schema.json").read_text(encoding="utf-8"))


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-report", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = arguments()
    existing_summary = None
    if args.summary.exists():
        existing_summary = json.loads(args.summary.read_text(encoding="utf-8"))
    report = json.loads(args.review_report.read_text(encoding="utf-8"))
    rows = list(report.get("accepted") or []) + list(report.get("rejected") or [])
    asset_ids = [row["asset_id"] for row in rows]
    if len(asset_ids) != args.expected_count:
        raise SystemExit(
            f"review inventory mismatch: expected={args.expected_count} actual={len(asset_ids)}"
        )
    duplicates = [asset_id for asset_id, count in Counter(asset_ids).items() if count > 1]
    if duplicates:
        raise SystemExit(f"duplicate review assets: {duplicates[:10]}")

    prior_statuses: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    missing: list[str] = []
    for asset_id in asset_ids:
        result_path = args.results / f"{asset_id}.json"
        if not result_path.exists():
            missing.append(asset_id)
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        prior_statuses[str(result.get("status"))] += 1
        providers[str(result.get("provider"))] += 1
        expression = result["blocks"][0]["expression"].strip()
        if args.apply:
            result["status"] = "verified_override"
            result["provider"] = "manual_verified"
            result["provider_request_id"] = None
            result["confidence"] = 1.0
            result["verification"]["latex_nonempty"] = bool(expression)
            result["verification"]["braces_balanced"] = latex_braces_balanced(expression)
            result["verification"]["note"] = (
                "中文公式原图人工逐张复核（2026-08-11）；"
                "RapidOCR 仅用于联系表分组，不参与最终正确性判定"
            )
            jsonschema.validate(result, SCHEMA)
            write_json(result_path, result)

    if missing:
        raise SystemExit(f"missing result files: {missing[:10]} (total={len(missing)})")

    preserved_prior = (
        existing_summary
        if existing_summary
        and existing_summary.get("applied") is True
        and prior_statuses == {"verified_override": len(asset_ids)}
        and providers == {"manual_verified": len(asset_ids)}
        else None
    )
    summary = {
        "schema_version": "formula-manual-chinese-review-summary-v1",
        "reviewed_count": len(asset_ids),
        "applied": bool(args.apply),
        "prior_status_counts": (
            preserved_prior["prior_status_counts"]
            if preserved_prior
            else dict(prior_statuses)
        ),
        "prior_provider_counts": (
            preserved_prior["prior_provider_counts"]
            if preserved_prior
            else dict(providers)
        ),
        "review_inventory": str(args.review_report),
        "decision": "source_image_human_verified",
        "reviewed_at": "2026-08-11",
    }
    write_json(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
