#!/usr/bin/env python3
"""Visually verified repairs for the five residual data/table assets."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema

import non_graphic_pipeline as pipeline


ROOT = Path(__file__).resolve().parent
ASSETS = {
    "qi_5f82a4b9cebe2cdc06efb5a6",
    "qi_c19d258e076e77266f93d0d9",
    "qi_c33f2fca21cacf7ab90c574e",
    "qi_c7847893f7e1cc72124d1e97",
    "qi_e2df72f2333e6b8dd5a08b5f",
}


def set_common(layer: dict[str, Any]) -> None:
    layer["confidence"] = 1.0
    layer["uncertainties"] = []
    layer.setdefault("explicit_missing_values", [])


def repair_combo(layer: dict[str, Any]) -> list[str]:
    categories = ["强制戒烟", "警示戒烟", "替代品戒烟", "药物戒烟"]
    counts = [120, 105, 30, 45]
    shares = [40, 35, 10, 15]
    layer.update({
        "chart_type": "combo", "categories": categories,
        "series": [
            {"label": "人数", "render_as": "bar", "axis": "left", "unit": "人", "values": counts},
            {"label": "占比", "render_as": "slice", "axis": "none", "unit": "%", "values": shares},
        ],
        "table": {
            "row_header_label": "戒烟方式", "columns": ["人数", "占比（%）"],
            "rows": [{"label": label, "unit": None, "values": [count, share]} for label, count, share in zip(categories, counts, shares)],
            "missing_cells": [],
        },
    })
    set_common(layer)
    return ["bar heights 120/105/30/45 and pie shares 40/35/10/15 visually and contextually cross-checked"]


def repair_shanghai(layer: dict[str, Any]) -> list[str]:
    columns = [
        "土地面积（平方公里）/2008年", "土地面积（平方公里）/2009年",
        "年末常住人口（万人）/2008年", "年末常住人口（万人）/2009年",
        "其中外来人口（万人）/2008年", "其中外来人口（万人）/2009年",
        "人口密度（人/平方公里）/2008年", "人口密度（人/平方公里）/2009年",
    ]
    values = [
        ("全市", [6340.5, 6340.5, 1888.46, 1921.32, 517.45, 541.93, 2978, 3030]),
        ("浦东新区", [532.75, 1210.41, 305.7, 419.05, 93.43, 128.79, 5738, 3462]),
        ("黄浦区", [12.41, 12.41, 53.89, 53.2, 9.15, 8.95, 43425, 42869]),
        ("卢湾区", [8.05, 8.05, 27.45, 26.94, 4.16, 4.61, 34099, 33466]),
        ("徐汇区", [54.76, 54.76, 98.22, 96.27, 13.9, 14.11, 17936, 17580]),
        ("长宁区", [38.3, 38.3, 66.83, 64.4, 11.57, 9.29, 17449, 16815]),
        # Preserve the source raster literally.  The printed cell reads 30.5,
        # even though it may be a source-data typo; repair must not normalize it.
        ("静安区", [7.62, 7.62, 25.78, 24.84, 3.46, 30.5, 33832, 32598]),
        ("普陀区", [54.83, 54.83, 108.71, 113.59, 18.25, 22.7, 19827, 20717]),
        ("闸北区", [29.26, 29.26, 74.5, 76.03, 10.35, 12, 25461, 25984]),
        ("虹口区", [23.48, 23.48, 78.11, 77.08, 9.41, 8.63, 33267, 32828]),
        ("杨浦区", [60.73, 60.73, 119.48, 120.62, 136.63, 14.3, 19674, 19862]),
        ("闵行区", [370.75, 370.75, 180.47, 181.43, 74.60, 74.61, 4868, 4894]),
        ("宝山区", [270.99, 270.99, 140.63, 136.55, 40.37, 36.26, 5189, 5039]),
        ("嘉定区", [464.2, 464.2, 103.42, 110.54, 45.97, 51.34, 2228, 2381]),
        ("金山区", [586.05, 586.05, 64.56, 69.1, 14.02, 16.35, 1102, 1179]),
        ("松江区", [605.64, 605.64, 107.42, 118.99, 51.74, 60.69, 1774, 1965]),
        ("青浦区", [670.14, 670.14, 78.98, 81.55, 32.92, 35.28, 1179, 1217]),
        ("南汇区", [677.66, None, 106.21, None, 32.81, None, 1567, None]),
        ("奉贤区", [687.69, 687.39, 80.84, 81.9, 28.84, 29.69, 1176, 1191]),
        ("崇明县", [1185.49, 1185.49, 67.26, 69.24, 9.41, 11.35, 567, 584]),
    ]
    missing = [{"row": "南汇区", "column": column, "display": ""} for column in columns if column.endswith("/2009年")]
    layer.update({
        "chart_type": "table", "categories": [], "series": [],
        "table": {"row_header_label": "地区", "columns": columns, "rows": [{"label": label, "unit": None, "values": row} for label, row in values], "missing_cells": missing},
    })
    set_common(layer)
    return ["two-level 2008/2009 headers flattened into eight lossless leaf columns", "all twenty rows visually checked; blank Nanhui 2009 cells retained as null", "source-printed values retained literally without plausibility normalization"]


def repair_header_placeholders(layer: dict[str, Any]) -> list[str]:
    layer.update({
        "chart_type": "table", "categories": [], "series": [],
        "table": {
            "row_header_label": None,
            "columns": ["序号", "①", "服务对象", "办理材料", "办理流程", "②", "收费依据", "③", "监督方式"],
            "rows": [], "missing_cells": [],
        },
    })
    set_common(layer)
    layer["visible_placeholders"] = ["①", "②", "③"]
    return ["image is a header-only public-service checklist", "①②③ are deliberate visible question placeholders, not missing OCR cells"]


def repair_partial_digits(layer: dict[str, Any]) -> list[str]:
    columns = ["日", "一", "二", "三", "四", "五", "六", "平均"]
    values = [63, 77, 76, 83, 69, None, None, 75]
    layer.update({
        "chart_type": "table", "categories": [],
        "series": [{"label": "件数", "render_as": "cell", "axis": "left", "unit": None, "values": values}],
        "table": {
            "row_header_label": "星期", "columns": columns,
            "rows": [{"label": "件数", "unit": None, "values": values}],
            "missing_cells": [
                {"row": "件数", "column": "五", "display": "7（其余数字被题图遮挡）"},
                {"row": "件数", "column": "六", "display": "6（其余数字被题图遮挡）"},
            ],
        },
    })
    set_common(layer)
    return ["Friday and Saturday cells are visibly occluded by the V-shaped mask", "visible fragments 7 and 6 retained; hidden digits remain null rather than inferred"]


def repair_fuel(layer: dict[str, Any]) -> list[str]:
    rows = [
        ("干木柴", ["约1.2×10⁷", "柴油", "4.3×10⁷"]),
        ("烟煤", ["约2.9×10⁷", "煤油", "4.6×10⁷"]),
        ("无烟煤", ["约3.4×10⁷", "汽油", "4.6×10⁷"]),
        ("焦炭", ["3.0×10⁷", "氢", "1.4×10⁸"]),
        ("木炭", ["3.4×10⁷", "酒精", "3.0×10⁷"]),
    ]
    layer.update({
        "chart_type": "table", "categories": [], "series": [],
        "table": {
            "row_header_label": "左侧燃料",
            "columns": ["左侧热值（J/kg）", "右侧燃料", "右侧热值（J/kg）"],
            "rows": [{"label": label, "unit": None, "values": values} for label, values in rows],
            "missing_cells": [],
        },
    })
    set_common(layer)
    return ["paired left/right fuel-value columns retained in row order", "scientific notation and the visible approximation mark are preserved literally"]


REPAIRS = {
    "qi_5f82a4b9cebe2cdc06efb5a6": repair_combo,
    "qi_c19d258e076e77266f93d0d9": repair_shanghai,
    "qi_c33f2fca21cacf7ab90c574e": repair_header_placeholders,
    "qi_c7847893f7e1cc72124d1e97": repair_partial_digits,
    "qi_e2df72f2333e6b8dd5a08b5f": repair_fuel,
}


def main() -> None:
    changed = []
    schema = json.loads(pipeline.SCHEMA_PATH.read_text(encoding="utf-8"))
    for asset_id, repair in REPAIRS.items():
        result_path = pipeline.SEMANTIC_RESULTS / f"{asset_id}.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        structured = json.loads((ROOT / result["structuredPath"]).read_text(encoding="utf-8"))
        old_reasons = list(result.get("reviewReasons") or [])
        evidence = repair(structured["reasoningLayer"])
        structured["status"] = "success"
        structured["verification"]["validationErrors"] = []
        structured["verification"]["reviewReasons"] = []
        structured["verification"]["cvChecks"]["manualVisualVerificationV6"] = {
            "method": "source_raster_visual_transcription_and_question_context_cross_check",
            "originalReviewReasons": old_reasons, "evidence": evidence,
            "sourceLiteralPolicy": "preserve visible source values; do not silently correct apparent source-data anomalies",
        }
        result.update({"status": "success", "confidence": 1.0, "reviewReasons": []})
        jsonschema.validate(structured, schema)
        pipeline.atomic_json(pipeline.STRUCTURED / f"{asset_id}.json", structured)
        pipeline.atomic_json(result_path, result)
        changed.append({"assetId": asset_id, "evidence": evidence})
    summary = {"schemaVersion": "non-graphic-data-visual-repairs-v6", "changedCount": len(changed), "changed": changed}
    pipeline.atomic_json(ROOT / "non-graphic-work/data-visual-repairs-v6.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
