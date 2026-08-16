from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


PIPELINE_PATH = Path(__file__).with_name("pipeline.py")
SPEC = importlib.util.spec_from_file_location("data_analysis_batch_pipeline", PIPELINE_PATH)
assert SPEC and SPEC.loader
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)
sys.path.insert(0, str(PIPELINE_PATH.parent))
import repair_table_semantics as REPAIR
import finalize as FINALIZE


class CompactVerificationTest(unittest.TestCase):
    def test_process_asset_exposes_explicit_retry_without_changing_default(self) -> None:
        self.assertFalse(
            PIPELINE.process_asset.__kwdefaults__["force_retry"]
        )

    def test_chart_delta_changes_only_allowlisted_point(self) -> None:
        initial = {
            "chart_type": "grouped_bar",
            "categories": ["一季度", "二季度"],
            "series": [{"label": "2025年", "values": [10, 20]}],
        }
        actual = PIPELINE.apply_verification_delta(
            initial,
            {
                "value_corrections": [
                    {"category": "二季度", "series": "2025年", "value": 21},
                    {"category": "三季度", "series": "2025年", "value": 999},
                ],
                "notes": ["二季度标签为21"],
            },
        )
        self.assertEqual(actual["series"][0]["values"], [10, 21])
        self.assertEqual(initial["series"][0]["values"], [10, 20])
        self.assertIn("ignored_unknown_chart_point:三季度:2025年", actual["verification_notes"])

    def test_table_delta_uses_candidate_row_and_column(self) -> None:
        initial = {
            "chart_type": "table",
            "table": {
                "columns": ["2024", "2025"],
                "rows": [{"label": "收入", "values": [12, 15]}],
            },
        }
        actual = PIPELINE.apply_verification_delta(
            initial,
            {
                "cell_corrections": [
                    {"row": "收入", "column": "2025", "value": 16},
                    {"row": "支出", "column": "2025", "value": 9},
                ],
                "notes": [],
            },
        )
        self.assertEqual(actual["table"]["rows"][0]["values"], [12, 16])
        self.assertIn("ignored_unknown_table_cell:支出:2025", actual["verification_notes"])

    def test_pie_delta_cannot_change_geometry(self) -> None:
        initial = {
            "chart_type": "pie_donut",
            "labels_clockwise": ["农业", "工业"],
            "boundaries": [0, 120, 360],
        }
        actual = PIPELINE.apply_verification_delta(
            initial,
            {
                "label_corrections": [{"index": 1, "value": "服务业"}],
                "boundaries": [0, 180, 360],
            },
        )
        self.assertEqual(actual["labels_clockwise"], ["农业", "服务业"])
        self.assertEqual(actual["boundaries"], [0, 120, 360])

    def test_empty_pie_profile_does_not_call_min_on_empty_sequence(self) -> None:
        original = PIPELINE.radial_profile_boundaries
        original_hough = PIPELINE.cv2.HoughCircles
        original_lines = PIPELINE.cv2.HoughLinesP
        original_read = PIPELINE.cv2.imread
        try:
            PIPELINE.radial_profile_boundaries = lambda *_args, **_kwargs: []
            PIPELINE.cv2.imread = lambda *_args, **_kwargs: np.full((120, 120, 3), 255, dtype=np.uint8)
            PIPELINE.cv2.HoughCircles = lambda *_args, **_kwargs: np.asarray([[[60, 60, 40]]], dtype=np.float32)
            PIPELINE.cv2.HoughLinesP = lambda *_args, **_kwargs: None
            geometry, notes = PIPELINE.pie_geometry(Path("unused.png"), ["甲"])
            self.assertIsNone(geometry)
            self.assertIn("radial_profile_boundaries_empty", notes)
        finally:
            PIPELINE.radial_profile_boundaries = original
            PIPELINE.cv2.HoughCircles = original_hough
            PIPELINE.cv2.HoughLinesP = original_lines
            PIPELINE.cv2.imread = original_read

    def test_explicit_table_missing_value_wins_over_adjacent_number(self) -> None:
        table = {
            "columns": ["2018", "2019"],
            "rows": [{"label": "基金收入", "values": [6971, 8191]}],
            "missing_cells": [],
        }
        notes: list[str] = []
        PIPELINE.apply_explicit_table_missing_values(
            table,
            [{"row": "基金收入", "col": "2019", "value": None, "display": "（？）"}],
            notes,
        )
        self.assertEqual(table["rows"][0]["values"], [6971, None])
        self.assertEqual(
            table["missing_cells"],
            [{"row": "基金收入", "column": "2019", "display": "（？）"}],
        )
        self.assertIn("explicit_missing_value_applied:基金收入:2019", notes)

    def test_repeated_table_row_labels_are_not_auto_success(self) -> None:
        candidate = {
            "chart_type": "table",
            "categories": ["地区生产总值", "固定资产投资"],
            "table": {
                "row_header_label": None,
                "corner_header": None,
                "columns": ["长三角", "全国"],
                "column_groups": [],
                "rows": [
                    {"label": "占全国比重", "values": [21.4, 10.3]},
                    {"label": "增长速度", "values": [11.8, 10.3]},
                    {"label": "占全国比重", "values": [14.7, 23.8]},
                    {"label": "增长速度", "values": [17.7, 23.8]},
                ],
                "missing_cells": [],
            },
            "series": [],
            "pie_geometry": None,
            "axes": {"x": {"min": None, "max": None}, "y": []},
        }
        errors = PIPELINE.deterministic_errors(candidate, {})
        self.assertIn("table_row_labels_not_unique", errors)
        self.assertIn("table_categories_not_represented_in_topology", errors)

    def test_grouped_columns_receive_unique_parent_qualified_keys(self) -> None:
        table = PIPELINE.canonicalize_grouped_table_columns({
            "columns": ["出口额", "比上年增长", "进口额", "比上年增长"],
            "column_groups": [
                {"label": "出口", "columns": ["出口额", "比上年增长"]},
                {"label": "进口", "columns": ["进口额", "比上年增长"]},
            ],
            "rows": [],
        })
        self.assertEqual(table["columns"], [
            "出口｜出口额", "出口｜比上年增长", "进口｜进口额", "进口｜比上年增长"
        ])
        self.assertEqual(table["column_groups"][1]["columns"], [
            "进口｜进口额", "进口｜比上年增长"
        ])

    def test_grouped_columns_preserve_deeper_leaf_hierarchy(self) -> None:
        table = PIPELINE.canonicalize_grouped_table_columns({
            "columns": ["单位GDP能耗｜指标", "单位GDP电耗｜指标"],
            "column_groups": [{
                "label": "2007年",
                "columns": ["单位GDP能耗｜指标", "单位GDP电耗｜指标"],
            }],
            "rows": [],
        })
        self.assertEqual(table["columns"], [
            "2007年｜单位GDP能耗｜指标", "2007年｜单位GDP电耗｜指标"
        ])

    def test_repeated_derived_columns_are_qualified_by_base_metric(self) -> None:
        table = PIPELINE.qualify_repeated_context_columns({
            "columns": ["企业数", "同比增速", "投资金额", "同比增速"],
            "column_groups": [],
            "rows": [{"label": "甲", "values": [10, 2.1, 20, 3.2]}],
            "missing_cells": [],
        })
        self.assertEqual(table["columns"], [
            "企业数", "企业数｜同比增速", "投资金额", "投资金额｜同比增速"
        ])

    def test_repeated_missing_columns_follow_null_occurrence(self) -> None:
        table = PIPELINE.qualify_repeated_context_columns({
            "columns": ["出口额", "同比增减", "进口额", "同比增减"],
            "column_groups": [],
            "rows": [{"label": "棉花", "values": [None, None, 20.6, -41]}],
            "missing_cells": [
                {"row": "棉花", "column": "出口额", "display": "—"},
                {"row": "棉花", "column": "同比增减", "display": "—"},
            ],
        })
        self.assertEqual(table["columns"], [
            "出口额", "出口额｜同比增减", "进口额", "进口额｜同比增减"
        ])
        self.assertEqual(table["missing_cells"][1]["column"], "出口额｜同比增减")

    def test_alternating_row_subtypes_are_not_duplicate_rows(self) -> None:
        table = PIPELINE.canonicalize_repeated_column_groups_as_rows({
            "columns": ["甲区", "乙区"],
            "column_groups": [
                {"label": "拨款", "columns": ["甲区", "乙区"]},
                {"label": "支出", "columns": ["甲区", "乙区"]},
            ],
            "rows": [
                {"label": "图书馆", "values": [10, 20]},
                {"label": "图书馆", "values": [8, 18]},
            ],
            "missing_cells": [],
        })
        self.assertEqual([row["label"] for row in table["rows"]], ["图书馆｜拨款", "图书馆｜支出"])
        self.assertEqual(table["column_groups"], [])

    def test_repeated_metric_blocks_become_row_groups(self) -> None:
        table = PIPELINE.canonicalize_repeated_column_groups_as_rows({
            "columns": ["计算单位", "实绩", "增长"],
            "column_groups": [
                {"label": "内河", "columns": ["计算单位", "实绩", "增长"]},
                {"label": "沿海", "columns": ["计算单位", "实绩", "增长"]},
            ],
            "rows": [
                {"label": "数量", "unit": "艘", "values": [None, 10, 1]},
                {"label": "重量", "unit": "吨", "values": [None, 20, 2]},
                {"label": "数量", "unit": "艘", "values": [None, 30, 3]},
                {"label": "重量", "unit": "吨", "values": [None, 40, 4]},
            ],
            "missing_cells": [],
        })
        self.assertEqual(table["columns"], ["实绩", "增长"])
        self.assertEqual(table["rows"][2]["label"], "沿海｜数量")
        self.assertEqual(table["rows"][2]["values"], [30, 3])

    def test_parallel_rankings_restore_three_columns_per_group(self) -> None:
        table = PIPELINE.canonicalize_repeated_column_groups_as_rows({
            "columns": ["排名｜城市", "排名｜业务量", "排名｜城市", "排名｜收入"],
            "column_groups": [
                {"label": "业务量", "columns": ["排名｜城市", "排名｜业务量"]},
                {"label": "收入", "columns": ["排名｜城市", "排名｜收入"]},
            ],
            "rows": [{"label": "1", "values": [1, "上海", 10, 1, "北京", 20]}],
            "missing_cells": [],
        })
        self.assertEqual(len(table["columns"]), 6)
        self.assertEqual(table["columns"][1], "业务量｜城市")
        self.assertEqual(table["columns"][5], "收入｜收入")

    def test_tsv_duplicate_child_rows_receive_parent_qualified_keys(self) -> None:
        table = {
            "columns": ["2022", "2023"],
            "column_groups": [],
            "rows": [],
        }
        actual = REPAIR.parse_tsv_table(
            "\n".join([
                "H\t2022\t2023",
                "D\t旅客运输量\t10\t11",
                "D\t国内航线\t8\t9",
                "D\t货邮运输量\t5\t6",
                "D\t国内航线\t3\t4",
            ]),
            table,
        )
        self.assertIsNotNone(actual)
        self.assertEqual([row["label"] for row in actual["rows"]], [
            "旅客运输量", "旅客运输量｜国内航线", "货邮运输量", "货邮运输量｜国内航线"
        ])

    def test_tsv_duplicate_merged_labels_use_visible_text_identity(self) -> None:
        actual = REPAIR.parse_tsv_table(
            "\n".join([
                "H\t发生时间\t受灾地区\t损失",
                "D\t烟花台风\t7月22日\t江苏\t10",
                "D\t烟花台风\t7月22日\t浙江\t20",
            ]),
            {"columns": ["发生时间", "受灾地区", "损失"], "column_groups": [], "rows": []},
        )
        self.assertIsNotNone(actual)
        self.assertEqual([row["label"] for row in actual["rows"]], [
            "烟花台风｜7月22日｜江苏", "烟花台风｜7月22日｜浙江"
        ])
        self.assertEqual(actual["rows"][0]["values"], ["7月22日", "江苏", 10])

    def test_tsv_block_headers_become_grouped_row_keys(self) -> None:
        table = {
            "columns": ["一季度", "二季度"],
            "column_groups": [{"label": "错误列组", "columns": ["一季度", "二季度"]}],
            "rows": [],
        }
        actual = REPAIR.parse_tsv_table(
            "\n".join([
                "H\t货运量（单位：亿吨）\t一季度\t二季度",
                "H\t客运量（单位：万人）\t一季度\t二季度",
                "D\t2013年\t1.4\t1.5",
                "D\t2014年\t1.6\t1.7",
                "D\t2013年\t342\t370",
                "D\t2014年\t335\t387",
            ]),
            table,
        )
        self.assertIsNotNone(actual)
        self.assertEqual(actual["column_groups"], [])
        self.assertEqual(actual["row_header_label"], "指标｜分类")
        self.assertEqual(actual["rows"][2]["label"], "客运量（单位：万人）｜2013年")
        self.assertEqual(actual["rows"][2]["group"], "客运量（单位：万人）")
        self.assertEqual(actual["rows"][2]["unit"], "万人")

    def test_tsv_multi_table_image_preserves_all_sections(self) -> None:
        actual = REPAIR.parse_tsv_table(
            "\n".join([
                "T\t经济指标",
                "H\t指标\t东部\t西部",
                "D\t生产总值\t10\t8",
                "T\t历年总值",
                "H\t区域\t2023年\t2024年",
                "D\t东部\t9\t10",
                "D\t西部\t7\t8",
            ]),
            {"columns": [], "column_groups": [], "rows": []},
        )
        self.assertIsNotNone(actual)
        self.assertEqual(len(actual["sections"]), 2)
        self.assertEqual(actual["sections"][0]["title"], "经济指标")
        self.assertEqual(actual["sections"][1]["columns"], ["2023年", "2024年"])
        self.assertEqual(actual["sections"][1]["rows"][1]["values"], [7, 8])

    def test_tsv_preserves_real_diagonal_corner_header(self) -> None:
        actual = REPAIR.parse_tsv_table(
            "\n".join([
                "T\t居民医保基金收支情况",
                "C\t年份\t项目",
                "H\t2018\t2019\t2020",
                "D\t基金收入\t6971\t（？）\t9115",
            ]),
            {
                "row_header_label": None,
                "corner_header": None,
                "columns": [],
                "column_groups": [],
                "rows": [],
                "missing_cells": [],
            },
        )
        self.assertIsNotNone(actual)
        self.assertEqual(actual["corner_header"], {
            "style": "diagonal",
            "upper_label": "年份",
            "lower_label": "项目",
        })
        self.assertEqual(actual["row_header_label"], "项目")
        self.assertEqual(actual["rows"][0]["values"], [6971, None, 9115])
        self.assertEqual(actual["missing_cells"], [
            {"row": "基金收入", "column": "2019", "display": "（？）"}
        ])

    def test_axis_titles_do_not_invent_diagonal_corner_header(self) -> None:
        parsed = {
            "chart_type": "table",
            "title": "普通表格",
            "unit": None,
            "categories": [],
            "series": [],
            "table": {
                "row_header_label": "项目",
                "corner_header": None,
                "columns": ["2023", "2024"],
                "column_groups": [],
                "rows": [{"label": "收入", "unit": None, "values": [10, 11]}],
                "missing_cells": [],
            },
            "axes": {
                "x": {"title": "年份"},
                "y": [{"title": "项目"}],
            },
            "confidence": 0.9,
        }
        candidate = PIPELINE.normalize_candidate(
            {
                "asset_id": "plain-table",
                "kind": "image",
                "local_path": "unused.png",
                "sha256": "0" * 64,
                "occurrences": [],
                "width": 800,
                "height": 400,
            },
            parsed,
            {},
            None,
            [],
            {},
        )
        self.assertIsNone(candidate["table"]["corner_header"])

    def test_table_svg_renders_every_section_in_multi_table_image(self) -> None:
        table = {
            "columns": ["东部", "西部"],
            "column_groups": [],
            "row_header_label": "项目",
            "corner_header": None,
            "rows": [{"label": "生产总值", "unit": None, "values": [10, 8]}],
            "missing_cells": [],
        }
        candidate = {
            "asset_id": "multi-table-test",
            "chart_type": "table",
            "title": "两张表",
            "table": {
                **table,
                "sections": [
                    {**table, "title": "经济指标", "sections": []},
                    {**table, "title": "历年总值", "sections": []},
                ],
            },
        }
        svg = FINALIZE.chart_svg(candidate)
        self.assertIn("经济指标", svg)
        self.assertIn("历年总值", svg)
        self.assertIn('viewBox="0 0 900 720"', svg)

    def test_tsv_variable_row_headers_use_trailing_numeric_cells(self) -> None:
        actual = REPAIR.parse_tsv_table(
            "\n".join([
                "T\t市场行情",
                "H\t品类\t品牌型号\t原价\t现价\t涨跌",
                "D\t彩电\t长虹\tPT4209\t16990\t10990\t6000",
                "D\t小家电\t格兰仕\t微波炉\tG8023ETL-X8\t1198\t958\t240",
            ]),
            {"columns": ["原价", "现价", "涨跌"], "column_groups": [], "rows": []},
        )
        self.assertIsNotNone(actual)
        self.assertEqual(actual["columns"], ["原价", "现价", "涨跌"])
        self.assertEqual(actual["rows"][0]["label"], "彩电｜长虹｜PT4209")
        self.assertEqual(actual["rows"][1]["values"], [1198, 958, 240])

    def test_tsv_text_identifier_column_becomes_part_of_row_key(self) -> None:
        actual = REPAIR.parse_tsv_table(
            "\n".join([
                "H\t品牌型号\t原价\t现价",
                "D\t彩电\t长虹 PT4209\t16990\t10990",
                "D\t彩电\t长虹 LT4018\t19990\t12990",
            ]),
            {"columns": [], "column_groups": [], "rows": []},
        )
        self.assertIsNotNone(actual)
        self.assertEqual(actual["columns"], ["原价", "现价"])
        self.assertEqual([row["label"] for row in actual["rows"]], [
            "彩电｜长虹 PT4209", "彩电｜长虹 LT4018"
        ])
        self.assertEqual(actual["rows"][0]["values"], [16990, 10990])


if __name__ == "__main__":
    unittest.main()
