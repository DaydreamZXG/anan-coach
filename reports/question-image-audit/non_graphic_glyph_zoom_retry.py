#!/usr/bin/env python3
"""Retry only the five malformed-JSON glyph zoom checks with fresh caches."""

from __future__ import annotations

import json
import os
from pathlib import Path

import non_graphic_glyph_zoom_resolution as zoom
import non_graphic_pipeline as pipeline


ROOT = Path(__file__).resolve().parent
IDS = {
    "qi_718b11c22e76d234e2bfadd2",
    "qi_7f5c6db7a342ee072efe04c4",
    "qi_82fc06be9800b21932260fe3",
    "qi_a6cc7032aed50a4bcf48623f",
    "qi_d81cc09c4ccb49959c74d0e1",
}


def main() -> None:
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    zoom.CACHE = ROOT / "cache/qwen-glyph-zoom-consensus-v7-retry"
    zoom.RESULTS = ROOT / "non-graphic-work/glyph-zoom-results-v7-retry"
    rows = {row["assetId"]: row for row in pipeline.load_source_rows()}
    contexts = pipeline.build_context_cache()
    manifest = {row["assetId"]: row for row in pipeline.read_jsonl(pipeline.FINAL_MANIFEST)}
    outcomes = [zoom.one(manifest[asset_id], rows, contexts) for asset_id in sorted(IDS)]
    report = {
        "schemaVersion": "glyph-zoom-retry-summary-v7", "scheduled": len(outcomes),
        "acceptedCount": sum(item.get("accepted") is True for item in outcomes), "outcomes": outcomes,
    }
    pipeline.atomic_json(ROOT / "non-graphic-work/glyph-zoom-retry-summary-v7.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
