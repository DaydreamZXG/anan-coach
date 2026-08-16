#!/usr/bin/env python3
"""Run only line/curve and signed-bar semantic grammars with explicit failures."""
from __future__ import annotations

import json
from pathlib import Path

import convert_visual_bricks as converter
import finalize
from semantic_microcharts import LINE_GRAMMARS


ROOT = Path(__file__).resolve().parent


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    manifest = converter.read_jsonl(ROOT / "manifest.jsonl")
    target_grammars = LINE_GRAMMARS | {"signed_bar"}
    targets = [
        entry for entry in manifest
        if converter.SEMANTIC_GRAMMAR_BY_ASSET.get(entry["asset_id"]) in target_grammars
    ]
    converted = []
    failures = []
    for index, entry in enumerate(targets, 1):
        grammar = converter.SEMANTIC_GRAMMAR_BY_ASSET[entry["asset_id"]]
        try:
            artifact = converter.convert_one(entry)
            converted.append(artifact)
            print(f"[{index:02d}/{len(targets):02d}] {entry['asset_id']} {grammar} ok")
        except Exception as error:
            failures.append({"asset_id": entry["asset_id"], "visual_grammar": grammar, "error": f"{type(error).__name__}: {error}"})
            print(f"[{index:02d}/{len(targets):02d}] {entry['asset_id']} {grammar} FAILED: {error}")
    inventory = finalize.build_outputs()
    report = {
        "schema_version": "data-analysis-semantic-microchart-batch-v1",
        "target_count": len(targets),
        "converted_count": len(converted),
        "failure_count": len(failures),
        "converted": converted,
        "failures": failures,
        "inventory_statuses": inventory["processing_status_counts"],
    }
    write_json(ROOT / "semantic-microchart-batch.json", report)
    print(json.dumps({"converted": len(converted), "failed": len(failures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
