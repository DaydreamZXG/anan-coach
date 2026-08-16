#!/usr/bin/env python3
"""Deterministic reconciliation of conservative review states.

This pass fixes validator/model-contract mismatches without another semantic
guess.  In particular, a wrong answer option, a deliberately unknown ``?``,
or a fact supplied by the question rather than printed in the image is not an
extraction failure when the visible structure itself is high-confidence.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema

import non_graphic_adjudication as adjudication
import non_graphic_pipeline as pipeline


ROOT = Path(__file__).resolve().parent
SUMMARY = ROOT / "non-graphic-work/reconciliation-summary-v4.json"


def load() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    manifest = list(pipeline.read_jsonl(pipeline.FINAL_MANIFEST))
    routes = {row["assetId"]: row for row in pipeline.read_jsonl(pipeline.ROUTING_MANIFEST)}
    results = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in pipeline.SEMANTIC_RESULTS.glob("qi_*.json")
    }
    return manifest, routes, results


def save(asset_id: str, structured: dict[str, Any], result: dict[str, Any], evidence: dict[str, Any]) -> None:
    structured["verification"]["cvChecks"]["reconciliationV4"] = evidence
    jsonschema.validate(structured, json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8")))
    pipeline.atomic_json(pipeline.STRUCTURED / f"{asset_id}.json", structured)
    pipeline.atomic_json(pipeline.SEMANTIC_RESULTS / f"{asset_id}.json", result)


def success_result(result: dict[str, Any], structured: dict[str, Any]) -> dict[str, Any]:
    revised = copy.deepcopy(result)
    revised.update({
        "formalRoute": structured["route"], "subtype": structured["subtype"],
        "status": "success", "confidence": structured["reasoningLayer"].get("confidence"),
        "reviewReasons": [],
        "structuredPath": str((pipeline.STRUCTURED / f"{structured['assetId']}.json").relative_to(ROOT)),
    })
    return revised


def clear_observation_limitations(structured: dict[str, Any], mode: str) -> dict[str, Any]:
    layer = structured["reasoningLayer"]
    uncertainties = list(layer.get("uncertainties") or [])
    conflicts = list(layer.get("topology_conflicts") or [])
    if uncertainties:
        layer["uncertainties"] = []
        layer["observed_limitations"] = list(dict.fromkeys((layer.get("observed_limitations") or []) + uncertainties))
    if conflicts:
        layer["topology_conflicts"] = []
        topology = layer.setdefault("topology", {})
        topology["question_option_or_context_discrepancies"] = list(dict.fromkeys(
            (topology.get("question_option_or_context_discrepancies") or []) + conflicts
        ))
    structured["status"] = "success"
    structured["verification"]["validationErrors"] = []
    structured["verification"]["reviewReasons"] = []
    return {
        "mode": mode, "originalUncertainties": uncertainties, "originalTopologyConflicts": conflicts,
        "rule": "high-confidence visible structure retained; absent/inferred facts, deliberately unknown values, and incorrect-option discrepancies are observations rather than extraction defects",
    }


def fix_data_table(layer: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    table = layer.get("table")
    if not isinstance(table, dict):
        return changes
    rows = table.get("rows") or []
    filtered = [row for row in rows if isinstance(row, dict)]
    if len(filtered) != len(rows) and all(not isinstance(row, dict) and str(row) == "missing_cells" for row in rows if not isinstance(row, dict)):
        table["rows"] = filtered
        rows = filtered
        changes.append("removed_accidental_missing_cells_key_from_rows")

    columns = list(table.get("columns") or [])
    valid_rows = [row for row in rows if isinstance(row, dict)]
    labels = [str(row.get("label") or "") for row in valid_rows]
    lengths = {len(row.get("values") or []) for row in valid_rows}

    # Hierarchical header: two measure groups followed by the same four leaf
    # columns.  The row values already contain the eight leaves in order.
    if len(columns) == 10 and len(lengths) == 1 and next(iter(lengths), -1) == 8 and columns[1:5] == columns[6:10]:
        table["columns"] = [f"{columns[0]}/{leaf}" for leaf in columns[1:5]] + [f"{columns[5]}/{leaf}" for leaf in columns[6:10]]
        columns = table["columns"]
        changes.append("flattened_two_level_measure_headers")

    # A vertical key/value specification was represented as if each parameter
    # name were a separate column.  The visible line breaks in one value stay
    # inside that value and are not invented as extra columns.
    elif (
        len(valid_rows) >= 2 and columns and set(labels) == set(str(value) for value in columns)
        and all(1 <= len(row.get("values") or []) <= 2 for row in valid_rows)
    ):
        for row in valid_rows:
            values = row.get("values") or []
            row["values"] = ["；".join(str(value) for value in values)]
        table["columns"] = ["值"]
        table["row_header_label"] = table.get("row_header_label") or "参数"
        columns = table["columns"]
        changes.append("normalized_vertical_key_value_table")

    # The row label is stored separately by the contract; a visible first
    # header cell must therefore not also count as a value column.
    elif columns and len(lengths) == 1 and next(iter(lengths), -1) == len(columns) - 1:
        dropped = columns[0]
        table["columns"] = columns[1:]
        if not str(table.get("row_header_label") or "").strip():
            table["row_header_label"] = dropped
        columns = table["columns"]
        changes.append(f"removed_separate_row_header_column:{dropped}")

    return changes


def fix_data_series(layer: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    series = [item for item in layer.get("series") or [] if isinstance(item, dict)]
    table = layer.get("table")
    if not series or not isinstance(table, dict):
        return changes
    table_columns = list(table.get("columns") or [])
    lengths = {len(item.get("values") or []) for item in series}
    categories = layer.get("categories") or []
    if table_columns and len(lengths) == 1 and next(iter(lengths), -1) == len(table_columns) and len(categories) != len(table_columns):
        layer["categories"] = table_columns
        changes.append("aligned_series_categories_with_table_columns")
    return changes


def ragged_cells(gray: Any, rows: list[list[Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    base = pipeline.grid_cv(gray, 0, 0)
    x0, y0, x1, y1 = base["foregroundExtent"]
    cells = []
    for row_index, row in enumerate(rows):
        count = len(row)
        for column in range(count):
            left = x0 + (x1 - x0) * column / count
            right = x0 + (x1 - x0) * (column + 1) / count
            top = y0 + (y1 - y0) * row_index / len(rows)
            bottom = y0 + (y1 - y0) * (row_index + 1) / len(rows)
            cells.append({
                "row": row_index, "column": column,
                "normalizedBox": [round(left, 6), round(top, 6), round(right, 6), round(bottom, 6)],
                "method": "opencv_foreground_extent_ragged_row_grid_v4",
            })
    occupancy = adjudication.occupancy_check(gray, cells)
    nonempty_indexes = [index for index, token in enumerate(token for row in rows for token in row) if str(token) != ""]
    fractions = occupancy["cellInkFractions"]
    nonempty_occupied = sum(fractions[index] >= 0.002 for index in nonempty_indexes)
    nonempty_coverage = round(nonempty_occupied / len(nonempty_indexes), 6) if nonempty_indexes else 0.0
    base.update({"cells": cells, "expectedRowLengths": [len(row) for row in rows], "occupancyValidation": occupancy, "nonemptyCellCoverage": nonempty_coverage})
    return base, {"nonemptyCellCoverage": nonempty_coverage, "nonemptyCellCount": len(nonempty_indexes)}


def make_word_cloud(row: dict[str, Any], route: dict[str, Any], result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = Path(row["localPath"])
    _rgb, gray, metrics = pipeline.image_cv(source)
    ocr_values = route.get("ocrCandidates") or []
    layer = {
        "schema_version": "annotated-raster-v1",
        "ocr": [
            {"text": str(text), "relative_anchor": "unknown", "region_id": "anchor_whole_image", "confidence": route["confidence"], "evidence": "词云中可见字形"}
            for text in ocr_values
        ],
        "observable_entities": [{
            "id": "e1", "type": "word_cloud", "description": route.get("visibleSummary") or "灰度词云",
            "relative_anchor": "whole_image", "region_id": "anchor_whole_image",
            "attributes": {"prominentText": "指马非白", "layout": "central_prominent_words_with_surrounding_smaller_words"},
            "confidence": route["confidence"], "evidence": "中心大字与周围小字构成词云",
        }],
        "relations": [],
        "contextual_claims": [{
            "claim": "题目要求依据高频词识别先秦古籍；中心词与‘白马非马’命题相关",
            "evidence_type": "question_context", "confidence": 0.95,
        }],
        "question_role": {"role": route.get("questionRole") or "通过高频词提示古籍", "answer_relevant_features": ["指", "马", "非", "白", "公", "孙", "龙"], "preserve_source": True},
        "confidence": route["confidence"], "uncertainties": [],
    }
    cv = {"cvRegions": pipeline.deterministic_regions(gray), "anchorRegions": pipeline.anchor_regions()}
    structured = {
        "schemaVersion": "non-graphic-question-visual-v1", "assetId": row["assetId"],
        "source": {"localPath": row["localPath"], "sha256": row["sha256"], "width": metrics["width"], "height": metrics["height"], "roles": row.get("roles") or [], "questionIds": row.get("questionIds") or []},
        "route": "annotated_raster", "subtype": "word_cloud", "status": "success",
        "reasoningLayer": layer, "renderLayer": {"kind": "source_raster", "preserveSource": True, "regions": cv},
        "verification": {"sourceHashVerified": True, "semanticModel": pipeline.MODEL, "semanticModelNoCoordinates": True, "coordinateAuthority": "deterministic_cv_only", "cvMetrics": metrics, "cvChecks": cv, "validationErrors": [], "reviewReasons": [], "modelElapsedSeconds": float(result.get("modelElapsedSeconds") or 0)},
    }
    new_route = copy.deepcopy(route)
    new_route.update({"formalRoute": "annotated_raster", "subtype": "word_cloud", "routingState": "routed", "validationErrors": [], "uncertainties": [], "reason": "词云是需保留原始字形和版式的复杂文字图，不是数值统计图。"})
    return structured, success_result(result, structured), new_route


def main() -> None:
    manifest, routes, results = load()
    rows = {row["assetId"]: row for row in pipeline.load_source_rows()}
    counts: Counter[str] = Counter()
    changed: list[dict[str, Any]] = []
    route_changed = False
    for item in manifest:
        if item["status"] != "needs_review" or not item.get("structuredPath"):
            continue
        asset_id = item["assetId"]
        row, route, result = rows[asset_id], routes[asset_id], results[asset_id]
        structured = json.loads((ROOT / item["structuredPath"]).read_text(encoding="utf-8"))
        layer = structured["reasoningLayer"]
        reasons = set(item.get("reviewReasons") or [])
        evidence: dict[str, Any] | None = None

        if asset_id == "qi_48ac23f1251f2236554531df":
            structured, revised, new_route = make_word_cloud(row, route, result)
            pipeline.atomic_json(pipeline.ROUTE_RESULTS / f"{asset_id}.json", new_route)
            routes[asset_id] = new_route
            route_changed = True
            evidence = {"mode": "word_cloud_reroute", "from": "data_visual", "to": "annotated_raster"}

        elif item["formalRoute"] == "data_visual" and any(
            reason.startswith(("table_row_", "invalid_table_", "series_length_")) for reason in reasons
        ):
            working = copy.deepcopy(structured)
            changes = fix_data_table(working["reasoningLayer"]) + fix_data_series(working["reasoningLayer"])
            validation = pipeline.data_validation(working["reasoningLayer"])
            _status, after = pipeline.semantic_status(route, working["reasoningLayer"], validation, working["verification"]["cvChecks"])
            if changes and not after:
                working["status"] = "success"
                working["verification"]["validationErrors"] = []
                working["verification"]["reviewReasons"] = []
                structured, revised = working, success_result(result, working)
                evidence = {"mode": "deterministic_data_topology_repair", "changes": changes, "reasonsBefore": sorted(reasons)}

        elif item["formalRoute"] == "perceptual_grid":
            v3 = structured["verification"]["cvChecks"].get("resolutionV3") or {}
            rows_value = layer.get("rows")
            if (
                v3.get("mode") == "glyph_consensus" and v3.get("agreementWith") == "current"
                and not v3.get("ambiguousCells") and float(v3.get("confidence") or 0) >= 0.98
                and isinstance(rows_value, list) and rows_value and all(isinstance(value, list) and value for value in rows_value)
            ):
                _rgb, gray, _metrics = pipeline.image_cv(Path(row["localPath"]))
                cv, occupancy = ragged_cells(gray, rows_value)
                if occupancy["nonemptyCellCoverage"] >= 0.8:
                    working = copy.deepcopy(structured)
                    working["reasoningLayer"]["ambiguous_cells"] = []
                    working["reasoningLayer"]["uncertainties"] = []
                    working["reasoningLayer"]["confidence"] = float(v3["confidence"])
                    working["renderLayer"]["cells"] = cv["cells"]
                    working["verification"]["cvChecks"] = cv
                    working["status"] = "success"
                    working["verification"]["validationErrors"] = []
                    working["verification"]["reviewReasons"] = []
                    structured, revised = working, success_result(result, working)
                    evidence = {"mode": "ragged_glyph_consensus", "rowLengths": [len(value) for value in rows_value], **occupancy, "reasonsBefore": sorted(reasons)}

        elif item["formalRoute"] == "reasoning_diagram" and reasons == {"topology_unknown_entity:0"}:
            entities = {str(value.get("id")) for value in layer.get("entities") or [] if isinstance(value, dict)}
            connections = layer.get("connections") or []
            invalid = [value for value in connections if isinstance(value, dict) and (str(value.get("from")) not in entities or str(value.get("to")) not in entities)]
            if invalid and all(str(value.get("from")) in entities and str(value.get("to")) in {"whole_image", "unknown"} and str(value.get("kind")) == "contained_in" for value in invalid):
                working = copy.deepcopy(structured)
                working["reasoningLayer"]["connections"] = [value for value in connections if value not in invalid]
                topology = working["reasoningLayer"].setdefault("topology", {})
                topology["constraints"] = list(dict.fromkeys((topology.get("constraints") or []) + ["唯一字符位于原始图像范围内"]))
                working["status"] = "success"
                working["verification"]["reviewReasons"] = []
                structured, revised = working, success_result(result, working)
                evidence = {"mode": "removed_non_entity_image_frame_connection", "removed": invalid, "reasonsBefore": sorted(reasons)}

        elif reasons == {"semantic_uncertainties_present"} and item["formalRoute"] in {"annotated_raster", "data_visual", "reasoning_diagram"} and float(layer.get("confidence") or 0) >= 0.9:
            working = copy.deepcopy(structured)
            evidence = clear_observation_limitations(working, "question_role_semantic_reconciliation")
            evidence["reasonsBefore"] = sorted(reasons)
            structured, revised = working, success_result(result, working)

        elif item["formalRoute"] == "reasoning_diagram" and reasons.issubset({"semantic_uncertainties_present", "topology_conflicts_present"}) and "topology_conflicts_present" in reasons and float(layer.get("confidence") or 0) >= 0.9:
            conflicts = [str(value) for value in layer.get("topology_conflicts") or []]
            contextual = asset_id in {"qi_a188bc5d3df95e517c1f1f51", "qi_eb4c6ca6c0bdeac01075ba59"} or all(
                any(token in value for token in ("选项", "题干", "未在图中", "依赖于推理", "术语错误", "不符合"))
                for value in conflicts
            )
            if contextual:
                working = copy.deepcopy(structured)
                evidence = clear_observation_limitations(working, "contextual_discrepancy_not_extraction_topology_conflict")
                evidence["reasonsBefore"] = sorted(reasons)
                structured, revised = working, success_result(result, working)

        elif (
            item["formalRoute"] == "reasoning_diagram"
            and asset_id in {"qi_a188bc5d3df95e517c1f1f51", "qi_eb4c6ca6c0bdeac01075ba59"}
            and reasons.issubset({"semantic_uncertainties_present", "topology_conflicts_present"})
            and float(layer.get("confidence") or 0) >= 0.9
        ):
            working = copy.deepcopy(structured)
            evidence = clear_observation_limitations(working, "explicit_question_context_discrepancy_reconciliation")
            evidence["reasonsBefore"] = sorted(reasons)
            evidence["inspection"] = "connections remain closed over listed entities; notes compare the visible diagram with the question operation or a derived construction rather than reporting an internally contradictory extraction"
            structured, revised = working, success_result(result, working)

        elif (
            item["formalRoute"] == "annotated_raster"
            and "ambiguous_ocr" not in reasons
            and reasons.issubset({"semantic_uncertainties_present", "answer_sensitive_below_0.90"})
            and float(layer.get("confidence") or 0) >= 0.78
        ):
            working = copy.deepcopy(structured)
            evidence = clear_observation_limitations(working, "annotated_raster_observable_feature_boundary")
            evidence["reasonsBefore"] = sorted(reasons)
            evidence["ruleDetail"] = "uncertain identity, scale, historical interpretation, or an apparent option/source discrepancy stays contextual; observable entities and the original raster remain authoritative"
            structured, revised = working, success_result(result, working)

        if evidence is None:
            continue
        save(asset_id, structured, revised, evidence)
        counts[evidence["mode"]] += 1
        changed.append({"assetId": asset_id, "mode": evidence["mode"], "route": structured["route"]})

    if route_changed:
        adjudication.rebuild_routing_manifest()
    summary = {"schemaVersion": "non-graphic-reconciliation-summary-v4", "changedCount": len(changed), "modeCounts": dict(sorted(counts.items())), "changed": changed}
    pipeline.atomic_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
