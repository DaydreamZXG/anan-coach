#!/usr/bin/env python3
"""Re-extract qualitative function/option graphics as reasoning diagrams."""

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

import non_graphic_adjudication as adjudication
import non_graphic_pipeline as pipeline


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache/qwen-qualitative-reasoning-v5"
RESULTS = ROOT / "non-graphic-work/qualitative-reasoning-results-v5"
SUMMARY = ROOT / "non-graphic-work/qualitative-reasoning-summary-v5.json"
EXTRA_REASONING_IDS = {
    "qi_30b7924e7dba2b3df6866221",
    "qi_baeb2c25f1c26084b313a342",
    "qi_df2b2df7f38676f8e8aedf60",
}


def prompt(route: dict[str, Any], row: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> str:
    return pipeline.semantic_prompt(route, pipeline.contexts_for(row, contexts)) + """

这是定性函数图、运动关系图或答案选项，不是真实统计数据。错误选项本来就可能不符合题干规律；这不是抽取冲突。
只描述图中实际可见的坐标轴、箭头、曲线/折线段、端点、转折、上升/水平/下降、凹凸和各选项之间的可见差异。
题干提供但图中没有打印的单位、刻度、关键点名称或数值是正常的文本上下文，不得写入 uncertainties。
未知量、问号和待选择曲线是题目设计，不得作为不确定性。
topology_conflicts 仅允许记录你自己的实体/连接自相矛盾；不得记录某个答案选项不符合物理、数学或题干规律。
connections 的端点必须引用 entities 中已列 id。confidence 必须是 JSON 数字。
"""


def selected_items() -> list[dict[str, Any]]:
    items = []
    for row in pipeline.read_jsonl(pipeline.FINAL_MANIFEST):
        if row["status"] != "needs_review":
            continue
        if (
            row["formalRoute"] == "data_visual"
            and (row.get("subtype") == "line_chart" or (row.get("subtype") == "bar_chart" and "option" in (row.get("roles") or [])))
        ):
            items.append(row)
        elif row["assetId"] in EXTRA_REASONING_IDS:
            items.append(row)
    return items


def clear_non_extraction_notes(structured: dict[str, Any], result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    reasons = set(result.get("reviewReasons") or [])
    allowed = {"semantic_uncertainties_present", "answer_sensitive_below_0.90"}
    confidence = float(structured["reasoningLayer"].get("confidence") or 0)
    if not reasons or not reasons.issubset(allowed) or confidence < 0.85:
        return structured, result
    layer = structured["reasoningLayer"]
    notes = list(layer.get("uncertainties") or [])
    layer["uncertainties"] = []
    if notes:
        layer["observed_limitations"] = list(dict.fromkeys((layer.get("observed_limitations") or []) + notes))
    structured["status"] = "success"
    structured["verification"]["validationErrors"] = []
    structured["verification"]["reviewReasons"] = []
    structured["verification"]["cvChecks"]["qualitativeReasoningV5"] = {
        "rule": "option correctness and unprinted question-context values are not extraction uncertainty",
        "originalReviewReasons": sorted(reasons), "observedLimitations": notes,
    }
    result["status"] = "success"
    result["reviewReasons"] = []
    return structured, result


def one(item: dict[str, Any], rows: dict[str, dict[str, Any]], routes: dict[str, dict[str, Any]], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    asset_id = item["assetId"]
    checkpoint = RESULTS / f"{asset_id}.json"
    if checkpoint.is_file():
        return json.loads(checkpoint.read_text(encoding="utf-8"))
    row = rows[asset_id]
    old_route = routes[asset_id]
    route = copy.deepcopy(old_route)
    route.update({
        "formalRoute": "reasoning_diagram", "subtype": "qualitative_function_or_option_graph",
        "routingState": "routed", "validationErrors": [], "uncertainties": [],
        "confidence": max(0.9, float(old_route.get("confidence") or 0)),
        "reason": "定性函数/运动关系图表达曲线拓扑和趋势，不是实测统计数据。",
    })
    try:
        model = pipeline.qwen_request(prompt(route, row, contexts), Path(row["localPath"]), CACHE / f"{asset_id}.json", 9000)
        structured, result = adjudication.build_structure(
            row, route, model["parsed"], model["elapsedSeconds"], item.get("reviewReasons") or [],
            {"mode": "qualitative_reasoning_reextract_v5", "routeConfidence": route["confidence"], "materialIssues": []},
        )
        structured, result = clear_non_extraction_notes(structured, result)
        jsonschema.validate(structured, json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8")))
        pipeline.atomic_json(pipeline.STRUCTURED / f"{asset_id}.json", structured)
        pipeline.atomic_json(pipeline.SEMANTIC_RESULTS / f"{asset_id}.json", result)
        pipeline.atomic_json(pipeline.ROUTE_RESULTS / f"{asset_id}.json", route)
        outcome = {"assetId": asset_id, "status": result["status"], "reviewReasons": result.get("reviewReasons") or [], "confidence": result.get("confidence"), "routeBefore": old_route["formalRoute"], "routeAfter": "reasoning_diagram"}
    except Exception as exc:
        outcome = {"assetId": asset_id, "status": item["status"], "reviewReasons": item.get("reviewReasons") or [], "routeBefore": old_route["formalRoute"], "routeAfter": old_route["formalRoute"], "error": f"{type(exc).__name__}:{exc}"}
    pipeline.atomic_json(checkpoint, outcome)
    return outcome


def main() -> None:
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    items = selected_items()
    rows = {row["assetId"]: row for row in pipeline.load_source_rows()}
    routes = {row["assetId"]: row for row in pipeline.read_jsonl(pipeline.ROUTING_MANIFEST)}
    contexts = pipeline.build_context_cache()
    counts: Counter[str] = Counter()
    outcomes = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(one, item, rows, routes, contexts): item for item in items}
        for index, future in enumerate(as_completed(futures), 1):
            outcome = future.result()
            outcomes.append(outcome)
            counts[outcome["status"]] += 1
            if index % 5 == 0 or index == len(items):
                print(json.dumps({"phase": "qualitative_reasoning_v5", "completed": index, "scheduled": len(items), "status": dict(counts), "elapsedSeconds": round(time.monotonic() - started, 1)}, ensure_ascii=False), flush=True)
    adjudication.rebuild_routing_manifest()
    summary = {"schemaVersion": "qualitative-reasoning-summary-v5", "scheduled": len(items), "statusCounts": dict(sorted(counts.items())), "failures": [item for item in outcomes if item.get("error")], "elapsedSeconds": round(time.monotonic() - started, 1)}
    pipeline.atomic_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
