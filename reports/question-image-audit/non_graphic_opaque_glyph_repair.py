#!/usr/bin/env python3
"""Create exact source-glyph references for the final unstructured glyph asset."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import cv2
import jsonschema

import non_graphic_adjudication as adjudication
import non_graphic_pipeline as pipeline


ROOT = Path(__file__).resolve().parent
ASSET_ID = "qi_eeb89fa538ab6bbb6e50b235"


def main() -> None:
    row = next(value for value in pipeline.load_source_rows() if value["assetId"] == ASSET_ID)
    route_path = pipeline.ROUTE_RESULTS / f"{ASSET_ID}.json"
    old_route = json.loads(route_path.read_text(encoding="utf-8"))
    gray = cv2.imread(row["localPath"], cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RuntimeError("source image unreadable")
    height, width = gray.shape
    ink = gray < 240
    projection = ink.sum(axis=0)
    runs = []
    start = None
    for x, value in enumerate(projection):
        if value and start is None:
            start = x
        if start is not None and (not value or x == width - 1):
            end = x if not value else x + 1
            runs.append((start, end))
            start = None
    if len(runs) != 20:
        raise RuntimeError(f"expected 20 glyph projection runs, found {len(runs)}")
    gaps = [runs[index + 1][0] - runs[index][1] for index in range(len(runs) - 1)]
    split = gaps.index(max(gaps)) + 1
    if split != 10:
        raise RuntimeError(f"expected 10+10 visual groups, split={split}")

    cells = []
    rows = [[], []]
    token_records = []
    for index, (left, right) in enumerate(runs):
        row_index = 0 if index < split else 1
        column = index if row_index == 0 else index - split
        token_id = f"source_glyph_r{row_index}c{column}"
        crop = gray[:, left:right]
        digest = hashlib.sha256(f"{crop.shape[0]}x{crop.shape[1]}:".encode() + crop.tobytes()).hexdigest()
        rows[row_index].append(token_id)
        token_records.append({
            "token": token_id, "row": row_index, "column": column,
            "unicode": None, "sourceGlyphSha256": digest,
            "interpretation": "opaque_source_raster_glyph; no Unicode normalization or substitution",
        })
        cells.append({
            "row": row_index, "column": column, "token": token_id,
            "pixelBox": [left, 0, right, height],
            "normalizedBox": [round(left / width, 6), 0.0, round(right / width, 6), 1.0],
            "sourceGlyphSha256": digest, "method": "binary_column_projection_source_crop_v8",
        })

    _rgb, _gray, metrics = pipeline.image_cv(Path(row["localPath"]))
    layer = {
        "schema_version": "perceptual-token-grid-v1", "reading_order": "row_major",
        "rows": rows, "token_encoding": "opaque-source-glyph-reference-v1",
        "source_tokens": token_records,
        "comparison_groups": [
            {"id": "g1", "cells": [{"row": 0, "column": column} for column in range(10)], "rule": "第一组十个原始图形 token"},
            {"id": "g2", "cells": [{"row": 1, "column": column} for column in range(10)], "rule": "第二组十个原始图形 token"},
        ],
        "ambiguous_cells": [
            {"row": record["row"], "column": record["column"], "candidates": [record["token"]], "reason": "源字形可精确引用，但无法可靠映射为唯一 Unicode 字符"}
            for record in token_records
        ],
        "visible_instructions": [], "question_role": "比较两组原始图形 token 中的重复图形",
        "confidence": 1.0, "uncertainties": ["20 个源字形均已精确分割并哈希；Unicode 名称未可靠确定"],
    }
    cv = {
        "method": "binary_column_projection_source_crop_v8", "threshold": 240,
        "detectedGlyphCount": 20, "groupSplit": [10, 10], "cells": cells,
        "coordinateAuthority": "deterministic_opencv",
        "opaqueGlyphEvidence": "source crop hashes and original raster are authoritative",
    }
    structured = {
        "schemaVersion": "non-graphic-question-visual-v1", "assetId": ASSET_ID,
        "source": {"localPath": row["localPath"], "sha256": row["sha256"], "width": metrics["width"], "height": metrics["height"], "roles": row.get("roles") or [], "questionIds": row.get("questionIds") or []},
        "route": "perceptual_grid", "subtype": "opaque_source_glyph_comparison", "status": "needs_review",
        "reasoningLayer": layer,
        "renderLayer": {"kind": "token_grid_with_source_glyphs", "preserveSource": True, "cells": cells, "sourceGlyphPolicy": "never_normalize_or_replace"},
        "verification": {"sourceHashVerified": True, "semanticModel": "deterministic_cv_repair_after_qwen_failure", "semanticModelNoCoordinates": True, "coordinateAuthority": "deterministic_cv_only", "cvMetrics": metrics, "cvChecks": cv, "validationErrors": [], "reviewReasons": ["opaque_glyph_unicode_unresolved"], "modelElapsedSeconds": 0},
    }
    result = {
        "assetId": ASSET_ID, "sha256": row["sha256"], "formalRoute": "perceptual_grid",
        "subtype": "opaque_source_glyph_comparison", "status": "needs_review", "confidence": 1.0,
        "reviewReasons": ["opaque_glyph_unicode_unresolved"],
        "structuredPath": str((pipeline.STRUCTURED / f"{ASSET_ID}.json").relative_to(ROOT)), "modelElapsedSeconds": 0,
    }
    route = copy.deepcopy(old_route)
    route.update({
        "formalRoute": "perceptual_grid", "subtype": "opaque_source_glyph_comparison",
        "confidence": 1.0, "routingState": "routed", "validationErrors": [],
        "uncertainties": ["Unicode 名称未可靠确定，但 20 个源字形已精确分割和哈希"],
        "visibleSummary": "两组横向排列的图形 token，每组 10 个", "questionRole": "比较两组图形中的重复 token",
        "reason": "知觉速度图形比较；使用源裁剪引用以避免错误 Unicode 归一化。",
    })
    jsonschema.validate(structured, json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8")))
    pipeline.atomic_json(pipeline.STRUCTURED / f"{ASSET_ID}.json", structured)
    pipeline.atomic_json(pipeline.SEMANTIC_RESULTS / f"{ASSET_ID}.json", result)
    pipeline.atomic_json(route_path, route)
    adjudication.rebuild_routing_manifest()
    report = {"schemaVersion": "opaque-glyph-repair-v8", "assetId": ASSET_ID, "status": "needs_review", "detectedGlyphCount": 20, "groupSplit": [10, 10], "sourceHashVerified": pipeline.sha256_file(Path(row["localPath"])) == row["sha256"], "reviewReason": "opaque_glyph_unicode_unresolved"}
    pipeline.atomic_json(ROOT / "non-graphic-work/opaque-glyph-repair-v8.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
