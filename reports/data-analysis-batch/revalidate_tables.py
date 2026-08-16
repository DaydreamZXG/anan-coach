#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pipeline


ROOT = Path(__file__).resolve().parent
STRUCTURED = ROOT / "structured"
RESULTS = ROOT / "work/results"
CACHE = ROOT / "cache/qwen"
QUEUE = ROOT / "work/table-semantic-repair-queue.jsonl"
SUMMARY = ROOT / "work/table-semantic-audit-summary.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def backfill_table_metadata(candidate: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    table = candidate.get("table")
    if not isinstance(table, dict):
        return notes
    table.setdefault("row_header_label", None)
    table.setdefault("corner_header", None)
    table.setdefault("column_groups", [])
    table.setdefault("missing_cells", [])
    for row in table.get("rows") or []:
        row.setdefault("unit", None)

    cache_path = CACHE / f"{candidate['asset_id']}.whole.json"
    if not cache_path.is_file():
        return notes
    raw = read_json(cache_path).get("parsed") or {}
    raw_table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
    raw_columns = [str(value).strip() for value in (raw_table.get("columns") or [])]
    if not table.get("row_header_label") and raw_columns and len(raw_columns) == len(table.get("columns") or []) + 1:
        table["row_header_label"] = raw_columns[0] or None
    series_units = {
        str(item.get("label") or "").strip(): str(item.get("unit") or "").strip() or None
        for item in (raw.get("series") or []) if isinstance(item, dict)
    }
    for row in table.get("rows") or []:
        if not row.get("unit"):
            row["unit"] = series_units.get(str(row.get("label") or "").strip())
    pipeline.apply_explicit_table_missing_values(table, raw.get("explicit_missing_values"), notes)
    return notes


def main() -> int:
    selection = read_json(ROOT / "work/full-selection.json")
    selected = set(selection.get("asset_ids") or [])
    counts = Counter()
    error_counts = Counter()
    queue: list[dict[str, Any]] = []
    for path in sorted(STRUCTURED.glob("*.json")):
        candidate = read_json(path)
        if candidate.get("asset_id") not in selected or candidate.get("chart_type") != "table":
            continue
        counts["tables_scanned"] += 1
        metadata_notes = backfill_table_metadata(candidate)
        if metadata_notes:
            candidate_notes = candidate.setdefault("verification", {}).setdefault("notes", [])
            candidate_notes.extend(note for note in metadata_notes if note not in candidate_notes)
            counts["metadata_backfilled"] += 1
        result_path = RESULTS / path.name
        result = read_json(result_path) if result_path.is_file() else {}
        previous_status = result.get("status")
        errors = pipeline.deterministic_errors(candidate, result.get("comparison") or {})
        for error in errors:
            error_counts[error.split(":", 1)[0]] += 1
        if errors:
            counts["requires_structure_repair"] += 1
            if result.get("status") == "success":
                counts["false_success_removed"] += 1
            result["status"] = "needs_manual_review"
            result["errors"] = sorted(set((result.get("errors") or []) + errors))
            result["auto_repair"] = {
                "required": True,
                "owner": "table_topology_model",
                "human_review_required": False,
            }
            candidate["review_status"] = "needs_manual_review"
            candidate_notes = candidate.setdefault("verification", {}).setdefault("notes", [])
            candidate_notes.extend(
                note for note in (f"validation_error:{error}" for error in errors)
                if note not in candidate_notes
            )
            queue.append({
                "asset_id": candidate["asset_id"],
                "errors": errors,
                "source_local_path": candidate.get("source", {}).get("local_path"),
                "previous_status": previous_status,
            })
        pipeline.atomic_json(path, candidate)
        if result:
            pipeline.atomic_json(result_path, result)

    with QUEUE.open("w", encoding="utf-8") as handle:
        for item in queue:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "counts": dict(counts),
        "error_counts": dict(error_counts),
        "queue_path": str(QUEUE.relative_to(ROOT)),
    }
    pipeline.atomic_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
