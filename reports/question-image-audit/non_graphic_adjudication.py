#!/usr/bin/env python3
"""Second-pass adjudication for non-success non-graphic image structures.

The first pass intentionally used a conservative rule: every model uncertainty
became ``needs_review``.  This pass distinguishes material extraction defects
from non-material limitations, re-extracts actual OCR/grid/topology failures,
and records the adjudication evidence.  Qwen still supplies semantics only;
all coordinates and raster/grid checks remain deterministic and local.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np

import non_graphic_pipeline as pipeline


ROOT = Path(__file__).resolve().parent
VERDICT_CACHE = ROOT / "cache/qwen-semantic-adjudication-v2"
EXTRACTION_CACHE = ROOT / "cache/qwen-semantic-adjudication-extraction-v2"
RESULTS = ROOT / "non-graphic-work/adjudication-results-v2"
BACKUP = ROOT / "non-graphic-work/pre-adjudication-v2"
SUMMARY = ROOT / "non-graphic-work/adjudication-summary-v2.json"
LOCK = threading.Lock()

REEXTRACT_REASON_PREFIXES = (
    "ambiguous_", "cv_", "invalid_", "non_rectangular_", "table_row_",
    "series_length_", "topology_unknown_", "no_observable_", "no_reasoning_",
    "RuntimeError:", "Qwen request failed",
)
SEMANTIC_ONLY_REASONS = {
    "semantic_uncertainties_present",
    "topology_conflicts_present",
}


def atomic_json(path: Path, value: Any) -> None:
    pipeline.atomic_json(path, value)


def load_errors() -> list[dict[str, Any]]:
    return list(pipeline.read_jsonl(pipeline.ERRORS))


def load_rows() -> dict[str, dict[str, Any]]:
    return {row["assetId"]: row for row in pipeline.load_source_rows()}


def load_routes() -> dict[str, dict[str, Any]]:
    return {row["assetId"]: row for row in pipeline.read_jsonl(pipeline.ROUTING_MANIFEST)}


def load_result(asset_id: str) -> dict[str, Any] | None:
    path = pipeline.SEMANTIC_RESULTS / f"{asset_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_structured(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result or not result.get("structuredPath"):
        return None
    path = ROOT / result["structuredPath"]
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def compact_json(value: Any, limit: int = 12000) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit] + "…"


def verdict_prompt(
    row: dict[str, Any],
    route: dict[str, Any],
    reasons: list[str],
    semantic: dict[str, Any] | None,
    context_rows: list[dict[str, Any]],
) -> str:
    return f"""你是公务员考试关键图片结构的二次质检员。只输出一个完整 JSON 对象，不要 Markdown，不要坐标。
你必须直接查看图片，并结合题干判断现有候选能否让 AI 正确理解图片。不能因为模型习惯性保守就保留无关歧义，也不能为了提高通过率掩盖真实歧义。

允许通道仅为 data_visual、perceptual_grid、reasoning_diagram、annotated_raster；本批严禁路由到图形推理独立批次。
“重要问题”仅指会导致可见文字/token、实体、数值、连接、方向、拓扑或题目所需关系被误读的问题。
图片本来就没有标题、比例尺、实体专名或完整刻度，不属于重要问题；不要要求图片提供其本来没有的信息。
知觉速度字符必须逐字精确：只有真正看不清时才列为重要问题，不能仅因字体理论上可能与别的字符相似就判歧义。
推理图的 topology_conflicts 如果只是描述选项违反规则或选项之间的差异，不是抽取拓扑冲突。

返回且只返回：
{{"recommended_route":"四个允许通道之一","subtype":"具体子型","route_confidence":0到1,
"decision":"accept_current或reextract或needs_review","confidence":0到1,
"material_issues":["会影响题意的具体问题"],"non_material_notes":["不影响结构正确性的限制"],
"verified_evidence":["从图中直接核对到的事实"],"reason":"裁决理由"}}

资产：{row['assetId']}
原通道：{route.get('formalRoute')}；原复核原因：{json.dumps(reasons, ensure_ascii=False)}
原语义候选：{compact_json(semantic or {}, 12000)}
题目上下文：{compact_json(context_rows, 5000)}
"""


def normalize_verdict(raw: dict[str, Any], current_route: str) -> dict[str, Any]:
    recommended = str(raw.get("recommended_route") or current_route)
    if recommended not in pipeline.ROUTES - {"needs_review"}:
        recommended = current_route if current_route in pipeline.ROUTES - {"needs_review"} else "needs_review"
    decision = str(raw.get("decision") or "needs_review")
    if decision not in {"accept_current", "reextract", "needs_review"}:
        decision = "needs_review"

    def probability(key: str) -> float:
        value = raw.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return round(max(0.0, min(1.0, number)), 4)

    def strings(key: str) -> list[str]:
        value = raw.get(key)
        if not isinstance(value, list):
            return []
        return [pipeline.compact_text(item, 500) for item in value if str(item).strip()]

    return {
        "recommendedRoute": recommended,
        "subtype": pipeline.compact_text(raw.get("subtype"), 160),
        "routeConfidence": probability("route_confidence"),
        "decision": decision,
        "confidence": probability("confidence"),
        "materialIssues": strings("material_issues"),
        "nonMaterialNotes": strings("non_material_notes"),
        "verifiedEvidence": strings("verified_evidence"),
        "reason": pipeline.compact_text(raw.get("reason"), 1000),
    }


def get_verdict(
    row: dict[str, Any],
    route: dict[str, Any],
    reasons: list[str],
    semantic: dict[str, Any] | None,
    contexts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    model = pipeline.qwen_request(
        verdict_prompt(row, route, reasons, semantic, pipeline.contexts_for(row, contexts)),
        Path(row["localPath"]),
        VERDICT_CACHE / f"{row['assetId']}.json",
        2400,
    )
    verdict = normalize_verdict(model["parsed"], str(route.get("formalRoute") or "needs_review"))
    verdict["modelElapsedSeconds"] = model["elapsedSeconds"]
    return verdict


def corrected_route(route: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(route)
    if verdict["recommendedRoute"] != "needs_review" and verdict["routeConfidence"] >= 0.9:
        value["formalRoute"] = verdict["recommendedRoute"]
        value["routingState"] = "routed"
        value["confidence"] = verdict["routeConfidence"]
        value["validationErrors"] = []
        value["uncertainties"] = []
        value["reason"] = verdict["reason"]
        if verdict.get("subtype"):
            value["subtype"] = verdict["subtype"]
    return value


def full_prompt(
    row: dict[str, Any],
    route: dict[str, Any],
    reasons: list[str],
    semantic: dict[str, Any] | None,
    verdict: dict[str, Any],
    contexts: dict[str, dict[str, Any]],
) -> str:
    base = pipeline.semantic_prompt(route, pipeline.contexts_for(row, contexts))
    guidance = f"""

这是二次修正抽取。原复核原因：{json.dumps(reasons, ensure_ascii=False)}。
质检发现的重要问题：{json.dumps(verdict.get('materialIssues') or [], ensure_ascii=False)}。
原候选仅供对照：{compact_json(semantic or {}, 10000)}

必须重新查看原图并输出完整的通道语义对象。不要复制原候选的格式错误。
uncertainties 只能记录当前像素确实无法辨认、且会影响题意的内容；不得写“图中未提供标题/比例尺/完整刻度”等非问题，也不得罗列泛化的字体混淆可能。
confidence 必须是 0 到 1 的 JSON 数字。
"""
    if route["formalRoute"] == "perceptual_grid":
        guidance += """
视觉上并列的一整串字符若是一个比较项，就把整串作为一个 token；只有明确画成逐格单元时才拆为单字符。逐行数组必须忠实反映视觉分组。
对 0/O、1/l/I 等易混字逐像素核对；只有仍无法确定时才填写 ambiguous_cells。不要因为“理论上形近”自动生成歧义。
"""
    elif route["formalRoute"] == "reasoning_diagram":
        guidance += """
connections 的 from/to 必须引用 entities 中真实存在的 id。不要把 whole_image、unknown、文字常量或数组当成实体引用。
topology_conflicts 仅记录抽取内部自相矛盾；选项之间的差异或某选项违反题目规则应写入 topology.constraints，而不是冲突。
"""
    elif route["formalRoute"] == "data_visual":
        guidance += """
chart_type 只能是 grouped_bar、stacked_bar、line、combo、pie_donut、scatter、table。
若表格与统计图组合出现可用 combo，并同时保留 table 与 series。table 每行 values 长度必须与 columns 完全一致，缺失格写 null；series 长度必须与 categories 一致。
"""
    return base + guidance


def backup_asset(asset_id: str, result: dict[str, Any] | None, structured: dict[str, Any] | None, route: dict[str, Any]) -> None:
    folder = BACKUP / asset_id
    with LOCK:
        if result is not None and not (folder / "semantic-result.json").is_file():
            atomic_json(folder / "semantic-result.json", result)
        if structured is not None and not (folder / "structured.json").is_file():
            atomic_json(folder / "structured.json", structured)
        if not (folder / "route.json").is_file():
            atomic_json(folder / "route.json", route)


def occupancy_check(gray: np.ndarray, cells: list[dict[str, Any]]) -> dict[str, Any]:
    height, width = gray.shape
    fractions: list[float] = []
    for cell in cells:
        x0, y0, x1, y1 = cell["normalizedBox"]
        left, top = max(0, round(x0 * width)), max(0, round(y0 * height))
        right, bottom = min(width, round(x1 * width)), min(height, round(y1 * height))
        crop = gray[top:bottom, left:right]
        fraction = float(np.mean(crop < 235)) if crop.size else 0.0
        fractions.append(round(fraction, 6))
    occupied = sum(value >= 0.002 for value in fractions)
    return {
        "method": "uniform_grid_cell_ink_occupancy_v2",
        "cellInkFractions": fractions,
        "occupiedCellCount": occupied,
        "cellCount": len(fractions),
        "coverage": round(occupied / len(fractions), 6) if fractions else 0.0,
    }


def build_structure(
    row: dict[str, Any],
    route: dict[str, Any],
    parsed_raw: dict[str, Any],
    elapsed: float,
    reasons_before: list[str],
    verdict: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = Path(row["localPath"])
    actual_hash = pipeline.sha256_file(source_path)
    if actual_hash != row["sha256"]:
        raise RuntimeError("source_hash_mismatch")
    _rgb, gray, cv_metrics = pipeline.image_cv(source_path)
    coordinate_errors = pipeline.forbidden_coordinate_keys(parsed_raw)
    parsed, validation_errors = pipeline.normalize_semantic(route, copy.deepcopy(parsed_raw))
    cv_checks: dict[str, Any]
    render_layer: dict[str, Any]
    if route["formalRoute"] == "annotated_raster":
        cv_checks = {"cvRegions": pipeline.deterministic_regions(gray), "anchorRegions": pipeline.anchor_regions()}
        render_layer = {"kind": "source_raster", "preserveSource": True, "regions": cv_checks}
    elif route["formalRoute"] == "perceptual_grid":
        token_rows = parsed.get("rows") or []
        widths = {len(item) for item in token_rows if isinstance(item, list)}
        expected_cols = next(iter(widths)) if len(widths) == 1 else 0
        cv_checks = pipeline.grid_cv(gray, len(token_rows), expected_cols)
        occupancy = occupancy_check(gray, cv_checks.get("cells") or [])
        cv_checks["occupancyValidation"] = occupancy
        render_layer = {
            "kind": "token_grid_with_source_glyphs", "preserveSource": True,
            "cells": cv_checks["cells"], "sourceGlyphPolicy": "never_normalize_or_replace",
        }
    elif route["formalRoute"] == "reasoning_diagram":
        render_layer = pipeline.primitive_visual(gray, row["assetId"], row["sha256"])
        cv_checks = {
            "primitiveCount": render_layer["complexity"]["primitiveCount"],
            "wireframeEdgeCount": render_layer["complexity"]["wireframeEdgeCount"],
            "coordinateAuthority": "deterministic_opencv",
        }
    else:
        render_layer = {"kind": "deterministic_data_visual", "contract": "data-analysis-chart-candidate-v1"}
        cv_checks = {"dataAnalysisOverlapChecked": True, "overlapCount": 0}

    status, review_reasons = pipeline.semantic_status(route, parsed, validation_errors, cv_checks)
    if route["formalRoute"] == "perceptual_grid":
        occupancy = cv_checks["occupancyValidation"]
        # Connected-component centers split multi-stroke glyphs and are not a
        # reliable column counter.  Exact uniform-cell ink coverage is the
        # deterministic authority for a high-confidence rectangular transcript.
        if occupancy["coverage"] >= 0.9 and float(parsed.get("confidence") or 0) >= 0.95:
            review_reasons = [reason for reason in review_reasons if reason not in {"cv_row_cluster_conflict", "cv_column_cluster_conflict"}]
            status = "needs_review" if review_reasons else "success"

    cv_checks["adjudicationV2"] = {
        "mode": "full_reextraction", "reasonsBefore": reasons_before,
        "verdict": verdict, "coordinateErrors": coordinate_errors,
    }
    structured = {
        "schemaVersion": "non-graphic-question-visual-v1",
        "assetId": row["assetId"],
        "source": {
            "localPath": row["localPath"], "sha256": row["sha256"],
            "width": cv_metrics["width"], "height": cv_metrics["height"],
            "roles": row.get("roles") or [], "questionIds": row.get("questionIds") or [],
        },
        "route": route["formalRoute"], "subtype": route.get("subtype") or verdict.get("subtype") or "",
        "status": status, "reasoningLayer": parsed, "renderLayer": render_layer,
        "verification": {
            "sourceHashVerified": True, "semanticModel": pipeline.MODEL,
            "semanticModelNoCoordinates": not coordinate_errors,
            "coordinateAuthority": "deterministic_cv_only", "cvMetrics": cv_metrics,
            "cvChecks": cv_checks, "validationErrors": validation_errors,
            "reviewReasons": review_reasons, "modelElapsedSeconds": elapsed,
        },
    }
    jsonschema.validate(structured, json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8")))
    result = {
        "assetId": row["assetId"], "sha256": row["sha256"], "formalRoute": route["formalRoute"],
        "subtype": structured["subtype"], "status": status,
        "confidence": parsed.get("confidence"), "reviewReasons": review_reasons,
        "structuredPath": str((pipeline.STRUCTURED / f"{row['assetId']}.json").relative_to(ROOT)),
        "modelElapsedSeconds": elapsed,
    }
    return structured, result


def accept_existing(
    row: dict[str, Any], route: dict[str, Any], result: dict[str, Any], structured: dict[str, Any],
    reasons: list[str], verdict: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    accepted = copy.deepcopy(structured)
    layer = accepted["reasoningLayer"]
    old_uncertainties = list(layer.get("uncertainties") or [])
    old_conflicts = list(layer.get("topology_conflicts") or [])
    if old_uncertainties:
        layer["uncertainties"] = []
        layer["non_material_notes"] = list(dict.fromkeys((layer.get("non_material_notes") or []) + old_uncertainties))
    if old_conflicts:
        layer["topology_conflicts"] = []
        topology = layer.setdefault("topology", {})
        topology["observed_option_constraints"] = list(dict.fromkeys((topology.get("observed_option_constraints") or []) + old_conflicts))
    accepted["status"] = "success"
    verification = accepted["verification"]
    verification["validationErrors"] = []
    verification["reviewReasons"] = []
    verification["cvChecks"]["adjudicationV2"] = {
        "mode": "materiality_review", "reasonsBefore": reasons,
        "originalUncertainties": old_uncertainties, "originalTopologyConflicts": old_conflicts,
        "verdict": verdict,
    }
    revised_result = copy.deepcopy(result)
    revised_result.update({
        "formalRoute": route["formalRoute"], "subtype": accepted["subtype"], "status": "success",
        "confidence": max(float(result.get("confidence") or 0), float(verdict.get("confidence") or 0)),
        "reviewReasons": [],
    })
    jsonschema.validate(accepted, json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8")))
    return accepted, revised_result


def requires_reextract(reasons: list[str], route_changed: bool, structured: dict[str, Any] | None) -> bool:
    if route_changed or structured is None:
        return True
    return any(reason.startswith(REEXTRACT_REASON_PREFIXES) for reason in reasons)


def adjudicate_one(
    error: dict[str, Any], rows: dict[str, dict[str, Any]], routes: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    asset_id = error["assetId"]
    checkpoint = RESULTS / f"{asset_id}.json"
    if checkpoint.is_file():
        return json.loads(checkpoint.read_text(encoding="utf-8"))
    row = rows[asset_id]
    original_route = routes[asset_id]
    original_result = load_result(asset_id)
    original_structured = load_structured(original_result)
    reasons = [str(value) for value in error.get("reasons") or []]
    semantic = (original_structured or {}).get("reasoningLayer")
    backup_asset(asset_id, original_result, original_structured, original_route)
    try:
        verdict = get_verdict(row, original_route, reasons, semantic, contexts)
        route = corrected_route(original_route, verdict)
        route_changed = route.get("formalRoute") != original_route.get("formalRoute")
        if route.get("formalRoute") == "needs_review" or route.get("routingState") != "routed":
            raise RuntimeError("adjudication_could_not_establish_route")

        reason_set = set(reasons)
        safe_materiality_case = bool(reason_set) and reason_set.issubset(SEMANTIC_ONLY_REASONS)
        can_accept = (
            safe_materiality_case and not route_changed and original_result is not None and original_structured is not None
            and verdict["decision"] == "accept_current" and verdict["confidence"] >= 0.92
            and verdict["routeConfidence"] >= 0.9 and not verdict["materialIssues"]
        )
        if can_accept:
            structured, result = accept_existing(row, route, original_result, original_structured, reasons, verdict)
            mode = "accepted_after_materiality_review"
        else:
            model = pipeline.qwen_request(
                full_prompt(row, route, reasons, semantic, verdict, contexts),
                Path(row["localPath"]), EXTRACTION_CACHE / f"{asset_id}.json", 9000,
            )
            structured, result = build_structure(row, route, model["parsed"], model["elapsedSeconds"], reasons, verdict)
            mode = "full_reextraction"

        atomic_json(pipeline.STRUCTURED / f"{asset_id}.json", structured)
        atomic_json(pipeline.SEMANTIC_RESULTS / f"{asset_id}.json", result)
        if route_changed or original_route.get("routingState") != "routed":
            atomic_json(pipeline.ROUTE_RESULTS / f"{asset_id}.json", route)
        outcome = {
            "assetId": asset_id, "mode": mode, "statusBefore": error["status"],
            "statusAfter": result["status"], "routeBefore": original_route.get("formalRoute"),
            "routeAfter": route.get("formalRoute"), "reasonsBefore": reasons,
            "reasonsAfter": result.get("reviewReasons") or [], "verdict": verdict,
        }
    except Exception as exc:
        outcome = {
            "assetId": asset_id, "mode": "adjudication_failed", "statusBefore": error["status"],
            "statusAfter": error["status"], "routeBefore": original_route.get("formalRoute"),
            "routeAfter": original_route.get("formalRoute"), "reasonsBefore": reasons,
            "reasonsAfter": reasons, "adjudicationError": f"{type(exc).__name__}:{exc}",
        }
    atomic_json(checkpoint, outcome)
    return outcome


def rebuild_routing_manifest() -> None:
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(pipeline.ROUTE_RESULTS.glob("qi_*.json"))]
    pipeline.write_jsonl(pipeline.ROUTING_MANIFEST, rows)


def run(limit: int | None, workers: int, route_filter: str | None) -> dict[str, Any]:
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    errors = load_errors()
    if route_filter:
        errors = [row for row in errors if row.get("formalRoute") == route_filter]
    if limit is not None:
        errors = errors[:limit]
    rows, routes, contexts = load_rows(), load_routes(), pipeline.build_context_cache()
    started = time.monotonic()
    counts: Counter[str] = Counter()
    outcomes: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(adjudicate_one, error, rows, routes, contexts): error for error in errors}
        for index, future in enumerate(as_completed(futures), 1):
            outcome = future.result()
            outcomes.append(outcome)
            counts[outcome["statusAfter"]] += 1
            if index % 10 == 0 or index == len(errors):
                print(json.dumps({
                    "phase": "adjudication_v2", "completed": index, "scheduled": len(errors),
                    "statusAfterThisRun": dict(counts),
                    "elapsedSeconds": round(time.monotonic() - started, 1),
                }, ensure_ascii=False), flush=True)
    rebuild_routing_manifest()
    summary = {
        "schemaVersion": "non-graphic-adjudication-summary-v2", "scheduled": len(errors),
        "statusAfterThisRun": dict(sorted(counts.items())),
        "modeCounts": dict(sorted(Counter(item["mode"] for item in outcomes).items())),
        "routeChanges": [item for item in outcomes if item["routeBefore"] != item["routeAfter"]],
        "adjudicationFailures": [item for item in outcomes if item["mode"] == "adjudication_failed"],
        "elapsedSeconds": round(time.monotonic() - started, 1),
    }
    atomic_json(SUMMARY, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--route", choices=sorted(pipeline.ROUTES))
    args = parser.parse_args()
    print(json.dumps(run(args.limit, args.workers, args.route), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
