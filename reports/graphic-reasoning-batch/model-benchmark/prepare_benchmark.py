#!/usr/bin/env python3
"""Create deterministic per-panel model inputs without invoking any GPU model."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import cv2


HERE = Path(__file__).resolve().parent
BATCH_ROOT = HERE.parent
sys.path.insert(0, str(BATCH_ROOT))
import pipeline  # noqa: E402


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strip_layout_border(crop):
    cleaned = crop.copy()
    band = max(2, min(5, round(min(cleaned.shape[:2]) * 0.035)))
    cleaned[:band, :] = 255
    cleaned[-band:, :] = 255
    cleaned[:, :band] = 255
    cleaned[:, -band:] = 255
    return cleaned, band


def prepare(manifest_path: Path, output_root: Path, phase: int, selected: set[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in _read_jsonl(manifest_path):
        if row.get("benchmarkStatus") == "route_conflict_quarantined":
            continue
        if int(row.get("phase", 1)) > phase or selected and row["assetId"] not in selected:
            continue
        source = Path(row["sourcePath"])
        source_hash = _sha256(source) if source.is_file() else None
        result: dict[str, Any] = {
            "schemaVersion": "graphic-vector-benchmark-input-v1",
            "assetId": row["assetId"],
            "sourcePath": str(source),
            "sourceSha256": row["sha256"],
            "regularSubtype": row["regularSubtype"],
            "phase": row["phase"],
            "status": "repair_pending",
            "issues": [],
            "panels": [],
        }
        if source_hash != row["sha256"]:
            result["issues"].append("source_sha256_mismatch")
            results.append(result)
            continue
        gray = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            result["issues"].append("source_decode_failed")
            results.append(result)
            continue
        boxes, detection = pipeline.detect_panels(gray)
        layout, roles, option_count = pipeline.infer_layout(boxes, int(gray.shape[1]), int(gray.shape[0]))
        result["deterministicPanelEvidence"] = {
            "layoutType": layout,
            "panelCount": len(boxes),
            "optionCount": option_count,
            "detection": detection,
        }
        if len(boxes) != int(row["expectedPanelCount"]):
            result["issues"].append("panel_count_mismatch")
        if option_count != int(row["expectedOptionCount"]):
            result["issues"].append("option_count_mismatch")
        asset_root = output_root / row["assetId"]
        asset_root.mkdir(parents=True, exist_ok=True)
        for index, (box, role) in enumerate(zip(boxes, roles), start=1):
            crop = gray[box.y:box.y + box.h, box.x:box.x + box.w]
            is_missing = pipeline._is_question_mark_panel(crop)
            cleaned, removed_band = _strip_layout_border(crop)
            panel_role = "missing" if is_missing else role
            panel_path = asset_root / f"panel-{index:02d}.png"
            if not cv2.imwrite(str(panel_path), cleaned):
                result["issues"].append(f"panel_write_failed:{index}")
                continue
            result["panels"].append({
                "id": f"panel-{index}",
                "role": panel_role,
                "sourceBox": box.as_dict(int(gray.shape[1]), int(gray.shape[0])),
                "modelInput": str(panel_path.resolve()),
                "modelInputSha256": _sha256(panel_path),
                "layoutBorderRemovedPixels": removed_band,
                "modelPolicy": "skip_missing_marker" if is_missing else "generate_svg_candidate",
                "semantics": row.get("semantics", {}),
            })
        (asset_root / "panels.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=HERE / "benchmark-manifest.jsonl")
    parser.add_argument("--output-root", type=Path, default=HERE / "inputs")
    parser.add_argument("--phase", type=int, default=1)
    parser.add_argument("--asset", action="append", default=[])
    args = parser.parse_args()
    results = prepare(args.manifest, args.output_root, args.phase, set(args.asset))
    index_path = args.output_root / "index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in results), encoding="utf-8")
    return 0 if results and all(not row["issues"] for row in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
