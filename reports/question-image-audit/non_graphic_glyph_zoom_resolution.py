#!/usr/bin/env python3
"""Pixel-preserving zoom consensus for residual perceptual-token images."""

from __future__ import annotations

import copy
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import jsonschema
from PIL import Image, ImageOps

import non_graphic_pipeline as pipeline
import non_graphic_reconcile as reconcile


ROOT = Path(__file__).resolve().parent
UPSCALED = ROOT / "non-graphic-work/glyph-upscaled-v7"
CACHE = ROOT / "cache/qwen-glyph-zoom-consensus-v7"
RESULTS = ROOT / "non-graphic-work/glyph-zoom-results-v7"
SUMMARY = ROOT / "non-graphic-work/glyph-zoom-summary-v7.json"


def upscaled_image(row: dict[str, Any]) -> Path:
    output = UPSCALED / f"{row['assetId']}.png"
    if output.is_file():
        return output
    with Image.open(row["localPath"]) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        scale = max(1, min(16, 4096 // max(1, image.width)))
        if image.height * scale < 256:
            scale = min(16, max(scale, (256 + image.height - 1) // image.height))
        enlarged = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    output.parent.mkdir(parents=True, exist_ok=True)
    enlarged.save(output, format="PNG")
    return output


def prompt(row: dict[str, Any], layer: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> str:
    current = {"reading_order": layer.get("reading_order"), "rows": layer.get("rows")}
    context = pipeline.contexts_for(row, contexts)
    return f"""你是知觉速度题的最终逐字复核员。图片由原图使用最近邻整数倍放大，每个源像素仅复制、没有生成或改写。
只输出完整 JSON，不要 Markdown，不要坐标。
独立逐像素读取所有 token，保留精确 Unicode、大小写、方向、实心/空心、附加线条和标点。
视觉上并列的一整串若是一个比较项，则整串是一个 token；明确逐格分开时才逐字拆分。
只有放大像素仍真正支持两个不同字符时才填写 ambiguous_cells。禁止因“理论上形近”列候选；候选列表重复或只是同一字符的字体差异不算歧义。
返回：{{"reading_order":"row_major或column_major","rows":[["原样token"]],"ambiguous_cells":[{{"row":0,"column":0,"candidates":["不同候选"],"reason":"放大像素中仍不可区分的证据"}}],"confidence":0到1,"verified_evidence":["核对事实"],"reason":"视觉分组依据"}}
当前转录仅用于最终一致性比较，不得盲从：{json.dumps(current, ensure_ascii=False, separators=(',', ':'))}
题目上下文：{json.dumps(context, ensure_ascii=False, separators=(',', ':'))[:4000]}
"""


def one(item: dict[str, Any], rows: dict[str, dict[str, Any]], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    asset_id = item["assetId"]
    checkpoint = RESULTS / f"{asset_id}.json"
    if checkpoint.is_file():
        return json.loads(checkpoint.read_text(encoding="utf-8"))
    row = rows[asset_id]
    result_path = pipeline.SEMANTIC_RESULTS / f"{asset_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    structured = json.loads((ROOT / result["structuredPath"]).read_text(encoding="utf-8"))
    current_rows = structured["reasoningLayer"].get("rows")
    try:
        model = pipeline.qwen_request(prompt(row, structured["reasoningLayer"], contexts), upscaled_image(row), CACHE / f"{asset_id}.json", 6000)
        raw = model["parsed"]
        candidate_rows = raw.get("rows")
        ambiguous = raw.get("ambiguous_cells") if isinstance(raw.get("ambiguous_cells"), list) else []
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        coordinate_errors = pipeline.forbidden_coordinate_keys(raw)
        exact_agreement = candidate_rows == current_rows
        accepted = (
            exact_agreement and not ambiguous and confidence >= 0.98 and not coordinate_errors
            and isinstance(candidate_rows, list) and candidate_rows
            and all(isinstance(value, list) and value for value in candidate_rows)
        )
        evidence = {
            "mode": "nearest_neighbor_zoom_consensus_v7", "scaleSource": "integer_nearest_neighbor",
            "exactAgreementWithCurrent": exact_agreement, "confidence": confidence,
            "ambiguousCells": ambiguous, "coordinateErrors": coordinate_errors,
            "verifiedEvidence": raw.get("verified_evidence") if isinstance(raw.get("verified_evidence"), list) else [],
            "modelElapsedSeconds": model["elapsedSeconds"], "accepted": False,
        }
        if accepted:
            _rgb, gray, _metrics = pipeline.image_cv(Path(row["localPath"]))
            cv, occupancy = reconcile.ragged_cells(gray, candidate_rows)
            evidence.update(occupancy)
            accepted = occupancy["nonemptyCellCoverage"] >= 0.8
        if accepted:
            revised = copy.deepcopy(structured)
            layer = revised["reasoningLayer"]
            original_uncertainties = list(layer.get("uncertainties") or [])
            original_ambiguous = list(layer.get("ambiguous_cells") or [])
            layer["rows"] = candidate_rows
            layer["ambiguous_cells"] = []
            layer["uncertainties"] = []
            layer["confidence"] = confidence
            if original_uncertainties or original_ambiguous:
                layer["superseded_review_notes"] = {
                    "uncertainties": original_uncertainties,
                    "ambiguous_cells": original_ambiguous,
                    "superseded_by": "pixel-preserving enlarged exact consensus",
                }
            revised["renderLayer"]["cells"] = cv["cells"]
            revised["status"] = "success"
            revised["verification"]["cvChecks"] = cv
            evidence["accepted"] = True
            revised["verification"]["cvChecks"]["glyphZoomConsensusV7"] = evidence
            revised["verification"]["validationErrors"] = []
            revised["verification"]["reviewReasons"] = []
            result.update({"status": "success", "confidence": confidence, "reviewReasons": []})
            jsonschema.validate(revised, json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8")))
            pipeline.atomic_json(pipeline.STRUCTURED / f"{asset_id}.json", revised)
            pipeline.atomic_json(result_path, result)
        outcome = {"assetId": asset_id, "status": "success" if accepted else "needs_review", "accepted": accepted, "confidence": confidence, "exactAgreement": exact_agreement, "ambiguousCellCount": len(ambiguous)}
    except Exception as exc:
        outcome = {"assetId": asset_id, "status": "needs_review", "accepted": False, "error": f"{type(exc).__name__}:{exc}"}
    pipeline.atomic_json(checkpoint, outcome)
    return outcome


def main() -> None:
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    items = [row for row in pipeline.read_jsonl(pipeline.FINAL_MANIFEST) if row["status"] == "needs_review" and row["formalRoute"] == "perceptual_grid"]
    rows = {row["assetId"]: row for row in pipeline.load_source_rows()}
    contexts = pipeline.build_context_cache()
    counts: Counter[str] = Counter()
    outcomes = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(one, item, rows, contexts): item for item in items}
        for index, future in enumerate(as_completed(futures), 1):
            outcome = future.result()
            outcomes.append(outcome)
            counts[outcome["status"]] += 1
            if index % 10 == 0 or index == len(items):
                print(json.dumps({"phase": "glyph_zoom_v7", "completed": index, "scheduled": len(items), "status": dict(counts), "elapsedSeconds": round(time.monotonic() - started, 1)}, ensure_ascii=False), flush=True)
    summary = {"schemaVersion": "glyph-zoom-summary-v7", "scheduled": len(items), "acceptedCount": sum(item["accepted"] for item in outcomes), "statusCounts": dict(sorted(counts.items())), "failures": [item for item in outcomes if item.get("error")], "elapsedSeconds": round(time.monotonic() - started, 1)}
    pipeline.atomic_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
