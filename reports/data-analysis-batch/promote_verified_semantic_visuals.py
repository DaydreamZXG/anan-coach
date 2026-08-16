#!/usr/bin/env python3
"""Promote semantic line/bar/pie previews after direct source/redraw QA."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import jsonschema

import convert_visual_bricks as converter


ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "structured-schema.json").read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def promote(asset_id: str, evidence_note: str) -> None:
    candidate_path = ROOT / "structured" / f"{asset_id}.json"
    result_path = ROOT / "work/results" / f"{asset_id}.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["review_status"] = "verified_sample"
    candidate["confidence"] = max(0.96, float(candidate.get("confidence") or 0))
    verification = candidate["verification"]
    verification["schema_valid"] = True
    verification["notes"] = [
        note for note in verification.get("notes", [])
        if not str(note).startswith(("manual_qa:", "semantic_reconstruction_pending"))
    ] + [evidence_note]
    jsonschema.validate(candidate, SCHEMA)
    write_json(candidate_path, candidate)

    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["status"] = "success"
    result["confidence"] = candidate["confidence"]
    result["errors"] = []
    write_json(result_path, result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote only assets that were explicitly inspected.")
    parser.add_argument("--asset-id", action="append", required=True, help="Verified asset id; repeat for multiple assets.")
    parser.add_argument("--evidence-note", required=True, help="Concrete source/redraw QA evidence attached to every promoted asset.")
    args = parser.parse_args()
    known_assets = (
        set(converter.SEMANTIC_GRAMMAR_BY_ASSET)
        | set(converter.SEMANTIC_PIE_OVERRIDES)
        | set(converter.SEMANTIC_STRUCTURE_OVERRIDES["statistical_assets"])
        | set(converter.SEMANTIC_STRUCTURE_OVERRIDES["simplex_assets"])
    )
    unknown = sorted(set(args.asset_id) - known_assets)
    if unknown:
        parser.error(f"unknown semantic asset ids: {', '.join(unknown)}")
    for asset_id in args.asset_id:
        promote(asset_id, args.evidence_note)
    print(json.dumps({"promoted_assets": args.asset_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
