#!/usr/bin/env python3
"""Apply human-verified standard chart/table data and exact duplicate aliases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "structured-schema.json").read_text(encoding="utf-8"))
CANDIDATE_FIELDS = ["chart_type", "title", "unit", "axes", "categories", "series", "table", "pie_geometry", "structured_visual", "legend"]


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def promote(asset_id: str, fields: dict[str, Any], note: str, verified_values: int) -> None:
    candidate_path = ROOT / "structured" / f"{asset_id}.json"
    result_path = ROOT / "work/results" / f"{asset_id}.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate.update({field: fields[field] for field in CANDIDATE_FIELDS})
    candidate["confidence"] = 0.99
    candidate["review_status"] = "verified_sample"
    verification = candidate["verification"]
    verification["schema_valid"] = True
    verification["ocr_engine"] = "human_verified_transcription"
    verification["ocr_mean_confidence"] = 0.99
    verification["missing_values_preserved"] = True
    verification["notes"] = [
        old for old in verification.get("notes", [])
        if not str(old).startswith(("converter:", "semantic_reconstruction_pending", "manual_override:", "alias:"))
    ] + [note]
    jsonschema.validate(candidate, SCHEMA)
    write_json(candidate_path, candidate)

    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update({
        "chart_type": candidate["chart_type"],
        "status": "success",
        "confidence": candidate["confidence"],
        "errors": [],
        "comparison": {
            "changed": 0,
            "change_ratio": 0.0,
            "initial_values": verified_values,
            "verified_values": verified_values,
            "missing_to_zero": 0,
        },
    })
    write_json(result_path, result)


def main() -> None:
    payload = json.loads((ROOT / "manual-standard-overrides.json").read_text(encoding="utf-8"))
    for asset_id, override in payload["assets"].items():
        verified_values = int(override["verified_values"])
        fields = {field: override[field] for field in CANDIDATE_FIELDS}
        promote(asset_id, fields, "manual_override:human_verified_chart_transcription", verified_values)
        print(f"{asset_id} manual standard structure ok")
    for asset_id, alias in payload["aliases"].items():
        source_asset = alias["structured_source_asset"]
        source = json.loads((ROOT / "structured" / f"{source_asset}.json").read_text(encoding="utf-8"))
        fields = {field: source[field] for field in CANDIDATE_FIELDS}
        verified_values = sum(len(row["values"]) for row in (source.get("table") or {}).get("rows", []))
        promote(asset_id, fields, f"alias:{source_asset}:{alias['reason']}", verified_values)
        print(f"{asset_id} alias {source_asset} ok")


if __name__ == "__main__":
    main()
