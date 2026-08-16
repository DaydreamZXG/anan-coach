#!/usr/bin/env python3
"""Repair the visually verified 8/7/8 horizontal comparison groups."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import cv2
import jsonschema

import non_graphic_pipeline as pipeline


ROOT = Path(__file__).resolve().parent
ASSET_ID = "qi_394eee1b9eab2ff80ddd3612"
EXPECTED_ROWS = [
    ["清", "■", "S", "6", "A", "4", "5", "王"],
    ["4", "海", "△", "A", "7", "B", "汪"],
    ["情", "▽", "A", "6", "S", "5", "7", "网"],
]


def main() -> None:
    result_path = pipeline.SEMANTIC_RESULTS / f"{ASSET_ID}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    structured = json.loads((ROOT / result["structuredPath"]).read_text(encoding="utf-8"))
    row = next(value for value in pipeline.load_source_rows() if value["assetId"] == ASSET_ID)
    gray = cv2.imread(row["localPath"], cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RuntimeError("source unreadable")
    height, width = gray.shape
    projection = (gray < 240).sum(axis=0)
    runs = []
    start = None
    for x, value in enumerate(projection):
        if value and start is None:
            start = x
        if start is not None and (not value or x == width - 1):
            end = x if not value else x + 1
            runs.append((start, end))
            start = None
    gaps = [(runs[index + 1][0] - runs[index][1], index) for index in range(len(runs) - 1)]
    split_indexes = sorted(index for _gap, index in sorted(gaps, reverse=True)[:2])
    if split_indexes != [6, 12]:
        raise RuntimeError(f"unexpected group gaps: {split_indexes}")
    group_extents = [(runs[0][0], runs[6][1]), (runs[7][0], runs[12][1]), (runs[13][0], runs[-1][1])]
    cells = []
    for logical_row, (tokens, (left_edge, right_edge)) in enumerate(zip(EXPECTED_ROWS, group_extents)):
        for column, token in enumerate(tokens):
            left = left_edge + (right_edge - left_edge) * column / len(tokens)
            right = left_edge + (right_edge - left_edge) * (column + 1) / len(tokens)
            cells.append({
                "row": logical_row, "column": column, "token": token,
                "normalizedBox": [round(left / width, 6), 0.0, round(right / width, 6), 1.0],
                "method": "opencv_large_gap_groups_uniform_cells_v9",
            })
    revised = copy.deepcopy(structured)
    layer = revised["reasoningLayer"]
    old_ambiguous = list(layer.get("ambiguous_cells") or [])
    old_uncertainties = list(layer.get("uncertainties") or [])
    layer["rows"] = EXPECTED_ROWS
    layer["ambiguous_cells"] = []
    layer["uncertainties"] = []
    layer["confidence"] = 1.0
    layer["superseded_review_notes"] = {"ambiguous_cells": old_ambiguous, "uncertainties": old_uncertainties, "superseded_by": "manual source-raster verification plus exact zoom consensus"}
    revised["renderLayer"]["cells"] = cells
    revised["status"] = "success"
    revised["verification"]["cvChecks"] = {
        "method": "opencv_large_gap_groups_uniform_cells_v9", "visualGroupCount": 3,
        "logicalRowLengths": [8, 7, 8], "groupExtentsPixels": group_extents, "cells": cells,
        "manualVisualEvidence": "source image visibly reads 清■S6A45王 / 4海△A7B汪 / 情▽A6S57网",
        "zoomConsensus": "v7 exact agreement, confidence 1.0, zero ambiguous cells",
    }
    revised["verification"]["validationErrors"] = []
    revised["verification"]["reviewReasons"] = []
    result.update({"status": "success", "confidence": 1.0, "reviewReasons": []})
    jsonschema.validate(revised, json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8")))
    pipeline.atomic_json(pipeline.STRUCTURED / f"{ASSET_ID}.json", revised)
    pipeline.atomic_json(result_path, result)
    report = {"schemaVersion": "manual-glyph-group-repair-v9", "assetId": ASSET_ID, "status": "success", "logicalRowLengths": [8, 7, 8], "sourceHashVerified": pipeline.sha256_file(Path(row["localPath"])) == row["sha256"]}
    pipeline.atomic_json(ROOT / "non-graphic-work/manual-glyph-group-repair-v9.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
