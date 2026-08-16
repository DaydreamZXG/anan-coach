#!/usr/bin/env python3
"""Apply deterministic quality gates to Texo candidate results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import write_json


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--katex-validation", type=Path, required=True)
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = arguments()
    validation = json.loads(args.katex_validation.read_text(encoding="utf-8"))
    invalid = {row["asset_id"] for row in validation["katex_invalid"]}
    suspicious = {row["asset_id"] for row in validation["suspicious_repetition"]}
    counts = {"candidate_auto": 0, "needs_manual_review": 0, "failed": 0, "preserved": 0}
    for source in sorted(args.input_dir.glob("*.json")):
        value = json.loads(source.read_text(encoding="utf-8"))
        destination = args.output_dir / source.name
        if destination.exists() and not args.overwrite:
            counts["preserved"] += 1
            continue
        if value.get("provider") != "texo_local" or value.get("status") == "failed":
            counts[value.get("status", "failed")] = counts.get(value.get("status", "failed"), 0) + 1
            write_json(destination, value)
            continue
        reasons: list[str] = []
        confidence = value.get("confidence")
        if not isinstance(confidence, (int, float)) or confidence < args.min_confidence:
            reasons.append("low_confidence")
        asset_id = value["asset_id"]
        if asset_id in invalid:
            reasons.append("katex_invalid")
        if asset_id in suspicious:
            reasons.append("repetition_suspected")
        verification = value.get("verification") or {}
        if not verification.get("source_hash_matches"):
            reasons.append("source_hash_mismatch")
        if not verification.get("latex_nonempty") or not verification.get("braces_balanced"):
            reasons.append("invalid_latex_shape")
        if "最大长度" in str(verification.get("note") or ""):
            reasons.append("generation_truncated")
        value["status"] = "needs_manual_review" if reasons else "candidate_auto"
        suffix = "quality_gates=" + (",".join(reasons) if reasons else "passed")
        value["verification"]["note"] = f"{value['verification']['note']}; {suffix}"
        counts[value["status"]] += 1
        write_json(destination, value)
    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
