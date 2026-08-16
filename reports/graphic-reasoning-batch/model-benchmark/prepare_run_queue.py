#!/usr/bin/env python3
"""Prepare resumable specialized-model jobs without invoking a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
FIRST_SAMPLE_ORDER = [
    "gr_7b00c758d0ab44e2c464e04f",
    "gr_06e94661142e35ee6757e42d",
    "gr_0751a8530a3a10bda058e654",
    "gr_09a239f1f5c89bec86ff315d",
    "gr_814f04ca63ec75e0fd69a9fb",
]
PROVIDERS = ["OmniSVG1.1_4B", "starvector-1b-im2svg"]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def queue_rows(index_path: Path) -> list[dict[str, Any]]:
    assets = {row["assetId"]: row for row in read_jsonl(index_path)}
    ordered_assets = [asset_id for asset_id in FIRST_SAMPLE_ORDER if asset_id in assets]
    ordered_assets.extend(sorted(set(assets) - set(ordered_assets)))
    rows: list[dict[str, Any]] = []
    priority = 1
    # One representative panel from each grammar is run before the remainder.
    for asset_id in ordered_assets:
        first = next((panel for panel in assets[asset_id]["panels"] if panel["modelPolicy"] == "generate_svg_candidate"), None)
        if first:
            rows.append(job(assets[asset_id], first, priority, "representative_first"))
            priority += 1
    first_keys = {(row["assetId"], row["panelId"]) for row in rows}
    for asset_id in ordered_assets:
        for panel in assets[asset_id]["panels"]:
            if (asset_id, panel["id"]) in first_keys:
                continue
            rows.append(job(assets[asset_id], panel, priority, "remaining_panel"))
            priority += 1
    return rows


def job(asset: dict[str, Any], panel: dict[str, Any], priority: int, stage: str) -> dict[str, Any]:
    input_hash = str(panel["modelInputSha256"])
    key = hashlib.sha256(f"{asset['assetId']}\0{panel['id']}\0{input_hash}\0candidate-parser-v1".encode()).hexdigest()
    skip = panel["modelPolicy"] != "generate_svg_candidate"
    return {
        "schemaVersion": "graphic-vector-model-job-v1",
        "checkpointKey": key,
        "priority": priority,
        "stage": stage,
        "assetId": asset["assetId"],
        "panelId": panel["id"],
        "role": panel["role"],
        "regularSubtype": asset["regularSubtype"],
        "sourceSha256": asset["sourceSha256"],
        "modelInput": panel["modelInput"],
        "modelInputSha256": input_hash,
        "providerOrder": PROVIDERS,
        "attempts": [],
        "status": "skipped_missing_marker" if skip else "blocked_waiting_gpu_ready",
        "resumeRule": "reuse only when checkpointKey, provider, raw SVG hash, parser version, and all evidence file hashes match",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=HERE / "inputs/index.jsonl")
    parser.add_argument("--output", type=Path, default=HERE / "runtime/run-queue.jsonl")
    args = parser.parse_args()
    rows = queue_rows(args.index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    waiting = sum(row["status"] == "blocked_waiting_gpu_ready" for row in rows)
    skipped = sum(row["status"] == "skipped_missing_marker" for row in rows)
    state = {
        "schemaVersion": "graphic-vector-checkpoint-state-v1",
        "status": "blocked_waiting_gpu_ready",
        "modelAccessAllowed": False,
        "totalJobs": len(rows),
        "waitingJobs": waiting,
        "skippedMissingMarkerJobs": skipped,
        "completedJobs": 0,
        "failedJobs": 0,
        "providerOrder": PROVIDERS,
        "firstFiveJobs": [{"assetId": row["assetId"], "panelId": row["panelId"], "regularSubtype": row["regularSubtype"]} for row in rows[:5]],
        "finalAcceptedAssets": 0,
    }
    (args.output.parent / "checkpoint-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
