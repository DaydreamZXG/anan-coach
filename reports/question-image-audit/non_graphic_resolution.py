#!/usr/bin/env python3
"""Targeted third-pass resolution for residual non-graphic review items."""

from __future__ import annotations

import argparse
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
MATERIAL_CACHE = ROOT / "cache/qwen-materiality-resolution-v3"
GLYPH_CACHE = ROOT / "cache/qwen-glyph-consensus-v3"
ROUTE_CACHE = ROOT / "cache/qwen-route-repair-v3"
EXTRACT_CACHE = ROOT / "cache/qwen-extraction-repair-v3"
RESULTS = ROOT / "non-graphic-work/resolution-results-v3"
SUMMARY = ROOT / "non-graphic-work/resolution-summary-v3.json"

MATERIAL_REASONS = {
    "semantic_uncertainties_present", "topology_conflicts_present",
    "answer_sensitive_below_0.90", "semantic_confidence_below_0.78",
}
CHART_ALIASES = {
    "bar": "grouped_bar", "bar_chart": "grouped_bar", "column": "grouped_bar",
    "column_chart": "grouped_bar", "grouped_bar_chart": "grouped_bar",
    "stacked_bar_chart": "stacked_bar", "line_chart": "line", "line_graph": "line",
    "pie": "pie_donut", "pie_chart": "pie_donut", "donut": "pie_donut",
    "donut_chart": "pie_donut", "scatter_plot": "scatter",
    "statistical_table": "table", "data_table": "table",
    "table_and_pie": "combo", "table_and_chart": "combo",
}


def compact(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit] + "…"


def probability(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def strings(value: Any) -> list[str]:
    return [pipeline.compact_text(item, 500) for item in value] if isinstance(value, list) else []


def material_prompt(row: dict[str, Any], route: dict[str, Any], layer: dict[str, Any], reasons: list[str], contexts: dict[str, dict[str, Any]]) -> str:
    return f"""你是关键图片结构的终审员。只输出完整 JSON，不要 Markdown，不要坐标。必须重新查看图片。
判断当前结构中的剩余说明是否真的会妨碍 AI 理解题目。图片本来没有标题、比例尺、完整刻度、专名或不可见细节，不算缺陷；选项之间的差异或错误选项违反规则，不算抽取拓扑冲突。
只有可见文字、数值、实体、连接、方向、拓扑或题目所需关系仍可能被误读，才是 material issue。
返回：{{"decision":"accept或review","confidence":0到1,"route_correct":true或false,
"material_issues":["具体问题"],"non_material_notes":["说明"],"verified_evidence":["至少两条图像核对事实"],"reason":"理由"}}
资产：{row['assetId']}；通道：{route['formalRoute']}；剩余原因：{json.dumps(reasons, ensure_ascii=False)}
当前结构：{compact(layer, 14000)}
题目上下文：{compact(pipeline.contexts_for(row, contexts), 5000)}
"""


def glyph_prompt(row: dict[str, Any], current: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> str:
    return f"""你是知觉速度题图片的逐 token 终审转录员。只输出完整 JSON，不要 Markdown，不要坐标。
重新逐像素查看原图。视觉上并列的一整串字符若是一个比较项，就把整串作为一个 token；只有明确逐格分开的单元才拆字。
必须保留原 Unicode、大小写和标点，不归一化 0/O、1/l/I、5/S、2/Z、6/G、8/B 或形近汉字。
不要因为字体理论上可能形近就判歧义；只有像素确实不足以区分时才列 ambiguous_cells。
返回：{{"reading_order":"row_major或column_major","rows":[["逐格原 token"]],
"ambiguous_cells":[{{"row":0,"column":0,"candidates":["候选"],"reason":"像素证据"}}],
"confidence":0到1,"verified_evidence":["转录核对证据"],"reason":"分组依据"}}
资产：{row['assetId']}；当前转录仅供独立对照：{compact({'rows': current.get('rows'), 'ambiguous_cells': current.get('ambiguous_cells')}, 7000)}
题目上下文：{compact(pipeline.contexts_for(row, contexts), 3500)}
"""


def original_layer(asset_id: str) -> dict[str, Any] | None:
    path = adjudication.BACKUP / asset_id / "structured.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("reasoningLayer")


def normalize_data(layer: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    chart_type = str(layer.get("chart_type") or "")
    replacement = CHART_ALIASES.get(chart_type)
    if replacement:
        layer["chart_type"] = replacement
        changes.append(f"chart_type:{chart_type}->{replacement}")
    elif chart_type not in {"grouped_bar", "stacked_bar", "line", "combo", "pie_donut", "scatter", "table"}:
        table = layer.get("table")
        series = layer.get("series") or []
        if isinstance(table, dict) and table.get("rows") and series:
            layer["chart_type"] = "combo"
            changes.append(f"chart_type:{chart_type}->combo_by_content")
        elif isinstance(table, dict) and table.get("rows"):
            layer["chart_type"] = "table"
            changes.append(f"chart_type:{chart_type}->table_by_content")
        elif series and all(str(item.get("render_as")) == "bar" for item in series if isinstance(item, dict)):
            layer["chart_type"] = "grouped_bar"
            changes.append(f"chart_type:{chart_type}->grouped_bar_by_content")
        elif series and all(str(item.get("render_as")) == "line" for item in series if isinstance(item, dict)):
            layer["chart_type"] = "line"
            changes.append(f"chart_type:{chart_type}->line_by_content")

    table = layer.get("table")
    if isinstance(table, dict):
        columns = table.get("columns") or []
        rows = table.get("rows") or []
        row_header = str(table.get("row_header_label") or "").strip()
        valid_rows = [item for item in rows if isinstance(item, dict)]
        lengths = {len(item.get("values") or []) for item in valid_rows}
        if (
            row_header and columns and len(lengths) == 1 and next(iter(lengths), -1) == len(columns) - 1
            and str(columns[0]).strip() == row_header
        ):
            table["columns"] = columns[1:]
            changes.append("removed_duplicated_row_header_column")
    return changes


def validation_errors(route: dict[str, Any], layer: dict[str, Any]) -> list[str]:
    if route["formalRoute"] == "data_visual":
        return pipeline.data_validation(layer)
    return []


def current_reasons(route: dict[str, Any], structured: dict[str, Any], layer: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors = validation_errors(route, layer)
    _status, reasons = pipeline.semantic_status(route, layer, errors, structured["verification"]["cvChecks"])
    return errors, reasons


def save(asset_id: str, structured: dict[str, Any], result: dict[str, Any], evidence: dict[str, Any]) -> None:
    structured["verification"]["cvChecks"]["resolutionV3"] = evidence
    jsonschema.validate(structured, json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8")))
    pipeline.atomic_json(pipeline.STRUCTURED / f"{asset_id}.json", structured)
    pipeline.atomic_json(pipeline.SEMANTIC_RESULTS / f"{asset_id}.json", result)


def result_from(structured: dict[str, Any], old_result: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    result = copy.deepcopy(old_result)
    result.update({
        "formalRoute": structured["route"], "subtype": structured["subtype"],
        "status": "needs_review" if reasons else "success",
        "confidence": structured["reasoningLayer"].get("confidence"),
        "reviewReasons": reasons,
        "structuredPath": str((pipeline.STRUCTURED / f"{structured['assetId']}.json").relative_to(ROOT)),
    })
    return result


def resolve_glyph(row: dict[str, Any], route: dict[str, Any], structured: dict[str, Any], result: dict[str, Any], reasons_before: list[str], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    layer = copy.deepcopy(structured["reasoningLayer"])
    model = pipeline.qwen_request(glyph_prompt(row, layer, contexts), Path(row["localPath"]), GLYPH_CACHE / f"{row['assetId']}.json", 5000)
    raw = model["parsed"]
    rows = raw.get("rows")
    confidence = probability(raw.get("confidence"))
    ambiguous = raw.get("ambiguous_cells") if isinstance(raw.get("ambiguous_cells"), list) else []
    rectangular = isinstance(rows, list) and bool(rows) and all(isinstance(item, list) and item for item in rows) and len({len(item) for item in rows}) == 1
    candidates = [("current", layer.get("rows")), ("first_pass", (original_layer(row["assetId"]) or {}).get("rows"))]
    agreement = next((name for name, value in candidates if value == rows), None)
    accepted = rectangular and not ambiguous and confidence >= (0.97 if route.get("answerSensitive") else 0.95) and agreement is not None
    evidence = {
        "mode": "glyph_consensus", "reasonsBefore": reasons_before, "modelElapsedSeconds": model["elapsedSeconds"],
        "confidence": confidence, "agreementWith": agreement, "ambiguousCells": ambiguous,
        "verifiedEvidence": strings(raw.get("verified_evidence")), "accepted": False,
    }
    if accepted:
        layer["rows"] = rows
        layer["ambiguous_cells"] = []
        layer["uncertainties"] = []
        layer["confidence"] = confidence
        _rgb, gray, _metrics = pipeline.image_cv(Path(row["localPath"]))
        cv = pipeline.grid_cv(gray, len(rows), len(rows[0]))
        occupancy = adjudication.occupancy_check(gray, cv.get("cells") or [])
        evidence["occupancyValidation"] = occupancy
        if occupancy["coverage"] >= 0.9:
            structured["reasoningLayer"] = layer
            structured["renderLayer"]["cells"] = cv["cells"]
            structured["verification"]["cvChecks"] = cv
            structured["verification"]["cvChecks"]["occupancyValidation"] = occupancy
            errors, reasons = current_reasons(route, structured, layer)
            reasons = [item for item in reasons if item not in {"cv_row_cluster_conflict", "cv_column_cluster_conflict"}]
            accepted = not reasons
        else:
            reasons = reasons_before
            accepted = False
    else:
        reasons = reasons_before
    evidence["accepted"] = accepted
    if accepted:
        structured["status"] = "success"
        structured["verification"]["validationErrors"] = []
        structured["verification"]["reviewReasons"] = []
        revised = result_from(structured, result, [])
    else:
        revised = result
    save(row["assetId"], structured, revised, evidence)
    return {"assetId": row["assetId"], "mode": "glyph_consensus", "statusAfter": revised["status"], "reasonsAfter": revised.get("reviewReasons") or [], "accepted": accepted}


def resolve_material(row: dict[str, Any], route: dict[str, Any], structured: dict[str, Any], result: dict[str, Any], reasons_before: list[str], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    layer = copy.deepcopy(structured["reasoningLayer"])
    deterministic_changes = normalize_data(layer) if route["formalRoute"] == "data_visual" else []
    errors, reasons = current_reasons(route, structured, layer)
    if not reasons:
        accepted = True
        evidence = {"mode": "deterministic_normalization", "changes": deterministic_changes, "reasonsBefore": reasons_before, "accepted": True}
    else:
        model = pipeline.qwen_request(material_prompt(row, route, layer, reasons, contexts), Path(row["localPath"]), MATERIAL_CACHE / f"{row['assetId']}.json", 2600)
        raw = model["parsed"]
        confidence = probability(raw.get("confidence"))
        material = strings(raw.get("material_issues"))
        verified = strings(raw.get("verified_evidence"))
        route_correct = raw.get("route_correct") is True
        decision = str(raw.get("decision") or "review")
        eligible = set(reasons).issubset(MATERIAL_REASONS)
        accepted = decision == "accept" and confidence >= 0.95 and route_correct and not material and len(verified) >= 2 and eligible
        evidence = {
            "mode": "materiality_resolution", "changes": deterministic_changes, "reasonsBefore": reasons_before,
            "reasonsAfterNormalization": reasons, "decision": decision, "confidence": confidence,
            "routeCorrect": route_correct, "materialIssues": material,
            "nonMaterialNotes": strings(raw.get("non_material_notes")),
            "verifiedEvidence": verified, "modelElapsedSeconds": model["elapsedSeconds"], "accepted": accepted,
        }
        if accepted:
            old_uncertainties = list(layer.get("uncertainties") or [])
            old_conflicts = list(layer.get("topology_conflicts") or [])
            if old_uncertainties:
                layer["uncertainties"] = []
                layer["non_material_notes"] = list(dict.fromkeys((layer.get("non_material_notes") or []) + old_uncertainties + evidence["nonMaterialNotes"]))
            if old_conflicts:
                layer["topology_conflicts"] = []
                topology = layer.setdefault("topology", {})
                topology["observed_option_constraints"] = list(dict.fromkeys((topology.get("observed_option_constraints") or []) + old_conflicts))
            layer["confidence"] = confidence
            errors, reasons = current_reasons(route, structured, layer)
            accepted = not reasons
            evidence["accepted"] = accepted

    if accepted:
        structured["reasoningLayer"] = layer
        structured["status"] = "success"
        structured["verification"]["validationErrors"] = []
        structured["verification"]["reviewReasons"] = []
        revised = result_from(structured, result, [])
    else:
        if deterministic_changes:
            structured["reasoningLayer"] = layer
            structured["verification"]["validationErrors"] = errors
            structured["verification"]["reviewReasons"] = reasons
            structured["status"] = "needs_review"
            revised = result_from(structured, result, reasons)
        else:
            revised = result
    save(row["assetId"], structured, revised, evidence)
    return {"assetId": row["assetId"], "mode": evidence["mode"], "statusAfter": revised["status"], "reasonsAfter": revised.get("reviewReasons") or [], "accepted": accepted}


def repair_missing(row: dict[str, Any], route: dict[str, Any], result: dict[str, Any], reasons: list[str], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current_route = route
    if route.get("formalRoute") == "needs_review" or route.get("routingState") != "routed":
        model = pipeline.qwen_request(pipeline.routing_prompt(row, pipeline.contexts_for(row, contexts)), Path(row["localPath"]), ROUTE_CACHE / f"{row['assetId']}.json", 3000)
        current_route = pipeline.normalize_route(row, model["parsed"], model["elapsedSeconds"])
        if current_route.get("routingState") != "routed":
            raise RuntimeError("route_repair_did_not_converge")
        pipeline.atomic_json(pipeline.ROUTE_RESULTS / f"{row['assetId']}.json", current_route)
    verdict = {"materialIssues": reasons, "subtype": current_route.get("subtype"), "confidence": 0, "routeConfidence": current_route.get("confidence")}
    model = pipeline.qwen_request(pipeline.semantic_prompt(current_route, pipeline.contexts_for(row, contexts)), Path(row["localPath"]), EXTRACT_CACHE / f"{row['assetId']}.json", 12000)
    structured, revised = adjudication.build_structure(row, current_route, model["parsed"], model["elapsedSeconds"], reasons, verdict)
    save(row["assetId"], structured, revised, {"mode": "missing_structure_repair", "reasonsBefore": reasons, "accepted": revised["status"] == "success"})
    return {"assetId": row["assetId"], "mode": "missing_structure_repair", "statusAfter": revised["status"], "reasonsAfter": revised.get("reviewReasons") or [], "accepted": revised["status"] == "success"}


def resolve_one(error: dict[str, Any], rows: dict[str, dict[str, Any]], routes: dict[str, dict[str, Any]], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    asset_id = error["assetId"]
    checkpoint = RESULTS / f"{asset_id}.json"
    if checkpoint.is_file():
        return json.loads(checkpoint.read_text(encoding="utf-8"))
    row, route = rows[asset_id], routes[asset_id]
    result_path = pipeline.SEMANTIC_RESULTS / f"{asset_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    structured = adjudication.load_structured(result)
    try:
        if structured is None:
            outcome = repair_missing(row, route, result, error.get("reasons") or [], contexts)
        elif route["formalRoute"] == "perceptual_grid":
            outcome = resolve_glyph(row, route, structured, result, error.get("reasons") or [], contexts)
        else:
            outcome = resolve_material(row, route, structured, result, error.get("reasons") or [], contexts)
    except Exception as exc:
        outcome = {"assetId": asset_id, "mode": "resolution_failed", "statusAfter": error["status"], "reasonsAfter": error.get("reasons") or [], "accepted": False, "error": f"{type(exc).__name__}:{exc}"}
    pipeline.atomic_json(checkpoint, outcome)
    return outcome


def run(workers: int) -> dict[str, Any]:
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    errors = list(pipeline.read_jsonl(pipeline.ERRORS))
    rows = {row["assetId"]: row for row in pipeline.load_source_rows()}
    routes = {row["assetId"]: row for row in pipeline.read_jsonl(pipeline.ROUTING_MANIFEST)}
    contexts = pipeline.build_context_cache()
    counts: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    outcomes: list[dict[str, Any]] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(resolve_one, error, rows, routes, contexts): error for error in errors}
        for index, future in enumerate(as_completed(futures), 1):
            outcome = future.result()
            outcomes.append(outcome)
            counts[outcome["statusAfter"]] += 1
            modes[outcome["mode"]] += 1
            if index % 10 == 0 or index == len(errors):
                print(json.dumps({"phase": "resolution_v3", "completed": index, "scheduled": len(errors), "statusAfterThisRun": dict(counts), "elapsedSeconds": round(time.monotonic() - started, 1)}, ensure_ascii=False), flush=True)
    adjudication.rebuild_routing_manifest()
    summary = {
        "schemaVersion": "non-graphic-resolution-summary-v3", "scheduled": len(errors),
        "statusAfterThisRun": dict(sorted(counts.items())), "modeCounts": dict(sorted(modes.items())),
        "acceptedCount": sum(item.get("accepted") is True for item in outcomes),
        "resolutionFailures": [item for item in outcomes if item["mode"] == "resolution_failed"],
        "elapsedSeconds": round(time.monotonic() - started, 1),
    }
    pipeline.atomic_json(SUMMARY, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(run(args.workers), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
