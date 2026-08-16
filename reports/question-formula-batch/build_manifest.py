#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from common import (
    AUDIT,
    DATA_ANALYSIS_GRAMMARS,
    ROOT,
    priority_for,
    read_jsonl,
    sha256_file,
    write_json,
    write_jsonl,
)


def explicit_formula_assets() -> dict[str, dict[str, Any]]:
    return {
        row["sha256"]: row
        for row in read_jsonl(AUDIT)
        if row.get("kind") == "formula_tex"
    }


def visual_formula_supplements() -> dict[str, dict[str, Any]]:
    if not DATA_ANALYSIS_GRAMMARS.is_file():
        return {}
    payload = json.loads(DATA_ANALYSIS_GRAMMARS.read_text(encoding="utf-8"))
    supplements: dict[str, dict[str, Any]] = {}
    for row in payload.get("assets", []):
        if row.get("visual_grammar") != "formula_text":
            continue
        path = Path(row["source"])
        digest = sha256_file(path)
        supplements[digest] = {
            "asset_id": f"qi_{digest[:24]}",
            "sha256": digest,
            "kind": "visual_formula_supplement",
            "local_path": str(path),
            "size_bytes": path.stat().st_size,
            "occurrences": [
                {
                    "role": "material",
                    "track": "data_analysis",
                    "source": "semantic-grammar-classification.json",
                    "data_analysis_asset_id": row["asset_id"],
                }
            ],
        }
    return supplements


def main() -> None:
    assets = explicit_formula_assets()
    explicit_count = len(assets)
    supplements = visual_formula_supplements()
    supplements_added = 0
    for digest, row in supplements.items():
        if digest not in assets:
            assets[digest] = row
            supplements_added += 1

    rows: list[dict[str, Any]] = []
    for asset in assets.values():
        occurrences = asset.get("occurrences") or []
        roles = sorted({str(item.get("role") or "") for item in occurrences if item.get("role")})
        tracks = sorted({str(item.get("track") or "") for item in occurrences if item.get("track")})
        rows.append(
            {
                "schema_version": "formula-recognition-task-v1",
                "asset_id": asset["asset_id"],
                "sha256": asset["sha256"],
                "source_path": asset["local_path"],
                "size_bytes": asset.get("size_bytes"),
                "source_kind": asset["kind"],
                "priority": priority_for(occurrences),
                "roles": roles,
                "tracks": tracks,
                "occurrence_count": len(occurrences),
            }
        )
    rows.sort(key=lambda row: (row["priority"] != "question_critical", row["asset_id"]))
    write_jsonl(ROOT / "manifest.jsonl", rows)

    priorities = Counter(row["priority"] for row in rows)
    source_kinds = Counter(row["source_kind"] for row in rows)
    summary = {
        "schema_version": "formula-batch-summary-v1",
        "asset_count": len(rows),
        "explicit_formula_asset_count": explicit_count,
        "visual_formula_supplement_count": supplements_added,
        "priority_counts": dict(priorities),
        "source_kind_counts": dict(source_kinds),
        "processing_order": ["question_critical", "explanation"],
        "provider": "xfyun_formula",
        "output_contract": "formula-asset-v1",
    }
    write_json(ROOT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

