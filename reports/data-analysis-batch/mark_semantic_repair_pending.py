#!/usr/bin/env python3
"""Withdraw false auto-success claims for non-semantic contour payloads."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import finalize


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "work/results"


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    affected: list[str] = []
    for candidate_path in sorted((ROOT / "structured").glob("*.json")):
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        visual = candidate.get("structured_visual") or {}
        if candidate.get("chart_type") != "structured_visual":
            continue
        if visual.get("schemaVersion") != "graphic-primitives-v2":
            continue
        asset_id = candidate["asset_id"]
        candidate["review_status"] = "needs_manual_review"
        candidate["confidence"] = min(float(candidate.get("confidence") or 0), 0.49)
        verification = candidate.setdefault("verification", {})
        notes = [
            note for note in verification.setdefault("notes", [])
            if note != "semantic_reconstruction_pending"
        ]
        notes.append("semantic_reconstruction_pending")
        verification["notes"] = notes
        write_json(candidate_path, candidate)

        result_path = RESULTS / f"{asset_id}.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["status"] = "needs_manual_review"
        result["confidence"] = candidate["confidence"]
        result["errors"] = ["semantic_visual_grammar_not_classified"]
        write_json(result_path, result)
        affected.append(asset_id)

    # The representative patterned-pie contract has been visually accepted by
    # the product owner in the paired source/redraw comparison.
    accepted_path = ROOT / "structured/da_dba634efefa69d74238550ca.json"
    accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
    accepted["review_status"] = "verified_sample"
    write_json(accepted_path, accepted)

    inventory = finalize.build_outputs()
    write_json(
        ROOT / "semantic-repair-status.json",
        {
            "schema_version": "data-analysis-semantic-repair-status-v1",
            "status": "classification_pending",
            "withdrawn_false_auto_success_count": len(affected),
            "verified_semantic_samples": ["da_dba634efefa69d74238550ca"],
            "pending_asset_ids": affected,
            "inventory_statuses": inventory["processing_status_counts"],
        },
    )
    print(json.dumps({"withdrawn": len(affected), "statuses": inventory["processing_status_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
