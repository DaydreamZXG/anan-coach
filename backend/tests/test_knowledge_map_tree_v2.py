"""refresh_knowledge_map_nodes.sql v2（四级知识树）契约测试。

对齐仓库现有 SQL 测试模式（见 test_exam_tag_weights.py）：在 db_session 提供的
本地 Postgres 上装载 minimal_schema，插入小 fixture，把仓库里的刷新 SQL 按分号
拆句执行，然后对产出断言。绝不连接生产库。

词表资产消费：product.vocab_module_alias_map / product.vocab_facet_rules 的数据
直接来自治理资产 scripts/sql/vocabulary/*.csv（vocab@v1，与
load_vocabulary_seed.sql 的 \\copy 等价），保证测试口径与生产装载口径一致。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
VOCAB_DIR = REPO_ROOT / "scripts" / "sql" / "vocabulary"

COLLAPSE_THRESHOLD_DEFAULT = 30


def seed_vocabulary_tables(session: Session) -> None:
    """从治理 CSV 装载词表（与 load_vocabulary_seed.sql 的 \\copy 装载等价）。"""
    session.execute(text("delete from product.vocab_module_alias_map"))
    session.execute(text("delete from product.vocab_facet_rules"))

    with (VOCAB_DIR / "module_alias_map.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            session.execute(
                text(
                    """
                    insert into product.vocab_module_alias_map (
                      version, raw_module_name, action, canonical_code,
                      local_question_count, basis
                    ) values (
                      :version, :raw_module_name, :action, :canonical_code,
                      :local_question_count, :basis
                    )
                    """
                ),
                {
                    "version": row["version"],
                    "raw_module_name": row["raw_module_name"],
                    "action": row["action"],
                    # CSV 中 quarantine 行的 canonical_code 为空串，装载为 NULL
                    "canonical_code": row["canonical_code"] or None,
                    "local_question_count": int(row["local_question_count"]),
                    "basis": row["basis"],
                },
            )

    with (VOCAB_DIR / "facet_rules.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            session.execute(
                text(
                    """
                    insert into product.vocab_facet_rules (
                      version, term, facet, tag_levels_observed,
                      local_occurrences, needs_review, basis
                    ) values (
                      :version, :term, :facet, :tag_levels_observed,
                      :local_occurrences, :needs_review, :basis
                    )
                    """
                ),
                {
                    "version": row["version"],
                    "term": row["term"],
                    "facet": row["facet"],
                    "tag_levels_observed": row["tag_levels_observed"],
                    "local_occurrences": int(row["local_occurrences"]),
                    "needs_review": row["needs_review"].strip().lower() == "true",
                    "basis": row["basis"],
                },
            )
    session.commit()


def run_knowledge_map_refresh(session: Session) -> None:
    sql_path = REPO_ROOT / "scripts" / "sql" / "refresh_knowledge_map_nodes.sql"
    for statement in sql_path.read_text().split(";"):
        if statement.strip():
            session.execute(text(statement))
    session.commit()


def run_exam_weight_refresh(session: Session) -> None:
    sql_path = REPO_ROOT / "scripts" / "sql" / "refresh_guokao_exam_tag_weights.sql"
    for statement in sql_path.read_text().split(";"):
        if statement.strip():
            session.execute(text(statement))
    session.commit()


# ---------------------------------------------------------------------------
# fixture：一棵小而全的树（题量对折叠阈值 30 精心取值）
#
# 资料分析（canonical 模块）
#   family 增长率 (47)
#     point 一般增长率 (42 = 40 本模块 + 2 来自别名模块「资科分析」)
#       micro 基期计算 (34 ≥ 30 存活；其中 10 题带材料形态 tag 统计表)
#       micro 速算技巧 (8 < 30 → 折叠进 一般增长率)
#     point 冷门比重 (5 < 30 → 折叠进 增长率)
#       micro 冷门技巧 (5，父点被折叠 → 折叠进 增长率)
#   （3 题额外带占位符 tag 待分类 → 不建节点）
# 判断推理
#   family 图形推理 (31，词面命中图形类 → image_unsupported)
#     point 对称性 (31 ≥ 30 存活，题目均非 visual_required → 只能靠父链继承不支持)
# 公共基础知识（quarantine） / 神秘模块（词表未命中，R0） /
# 常识应用能力（map_needs_review，R1 复核门）→ 均不产节点
# ---------------------------------------------------------------------------

DATA_MODULE_TOTAL = 47
FAMILY_TOTAL = 47
POINT_TOTAL = 42
SKILL_KEPT_TOTAL = 34
MATERIAL_TAGGED = 10


def insert_tree_fixture(session: Session) -> None:
    def add(
        question_id: str,
        module_name: str,
        tags: list[tuple[str, str]],
        *,
        visual_required: bool = False,
    ) -> None:
        _insert_question(
            session,
            question_id=question_id,
            module_name=module_name,
            visual_required=visual_required,
        )
        for index, (tag_type, tag_value) in enumerate(tags, start=1):
            _insert_tag(
                session,
                question_id=question_id,
                tag_type=tag_type,
                tag_value=tag_value,
                sort_order=index,
            )

    # Group A：一般增长率主干 40 题
    for i in range(1, 41):
        tags = [
            ("knowledge_family", "增长率"),
            ("knowledge_point", "一般增长率"),
        ]
        if i <= 32:
            tags.append(("micro_skill", "基期计算"))
        else:
            tags.append(("micro_skill", "速算技巧"))
        if i <= MATERIAL_TAGGED:
            tags.append(("micro_skill", "统计表"))  # material_form 面（R2）
        if i <= 3:
            tags.append(("micro_skill", "待分类"))  # exclude 面（R2）
        add(f"q_da_main_{i:02d}", "资料分析", tags)

    # Group B：冷门比重 5 题（点与其叶子都低于阈值）
    for i in range(1, 6):
        add(
            f"q_da_rare_{i:02d}",
            "资料分析",
            [
                ("knowledge_family", "增长率"),
                ("knowledge_point", "冷门比重"),
                ("micro_skill", "冷门技巧"),
            ],
        )

    # Group C：别名模块「资科分析」（OCR 笔误，词表 action=map → data_analysis）
    for i in range(1, 3):
        add(
            f"q_da_alias_{i:02d}",
            "资科分析",
            [
                ("knowledge_family", "增长率"),
                ("knowledge_point", "一般增长率"),
                ("micro_skill", "基期计算"),
            ],
        )

    # Group D：判断推理 图形推理/对称性 31 题（验证 image_unsupported 沿父链继承）
    for i in range(1, 32):
        add(
            f"q_jr_sym_{i:02d}",
            "判断推理",
            [
                ("knowledge_family", "图形推理"),
                ("knowledge_point", "对称性"),
            ],
        )

    # Group E：quarantine 模块（任务铁律：不产节点）
    for i in range(1, 4):
        add(f"q_quar_{i:02d}", "公共基础知识", [("knowledge_family", "法理学")])

    # Group F：词表未命中模块（R0 缺省隔离）
    for i in range(1, 3):
        add(f"q_unknown_{i:02d}", "神秘模块", [("knowledge_family", "神秘族")])

    # Group G：map_needs_review 模块（R1 复核门，等同隔离）
    for i in range(1, 3):
        add(f"q_review_{i:02d}", "常识应用能力", [("knowledge_family", "时政")])

    session.commit()


def insert_guokao_occurrences_for_near5(session: Session) -> None:
    """给 4 题主干题挂国考卷 occurrence（2023-2026），供 exam_tag_weights 产权重。"""
    for i, exam_year in enumerate((2026, 2025, 2024, 2023), start=1):
        question_id = f"q_da_main_{i:02d}"
        session.execute(
            text(
                """
                insert into product.question_occurrences (
                  occurrence_id, question_id, province, year, number,
                  section, paper_title, canonical_occurrence_rank
                ) values (
                  :occurrence_id, :question_id, '国考', :year, :number,
                  '资料分析', :paper_title, 1
                )
                """
            ),
            {
                "occurrence_id": f"occ_{question_id}",
                "question_id": question_id,
                "year": exam_year - 1,
                "number": i,
                "paper_title": f"{exam_year}年国家公务员录用考试《行测》题（地市级网友回忆版）",
            },
        )
    session.commit()


def _insert_question(
    session: Session,
    *,
    question_id: str,
    module_name: str,
    visual_required: bool,
) -> None:
    session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type,
              stem, answer, shared_context_required, visual_required,
              needs_multimodal, occurrence_count, latest_year
            ) values (
              :question_id, 'objective', 'active', :module_name, '单选',
              :stem, 'B', false, :visual_required,
              false, 1, 2026
            )
            """
        ),
        {
            "question_id": question_id,
            "module_name": module_name,
            "stem": f"tree fixture {question_id}",
            "visual_required": visual_required,
        },
    )


def _insert_tag(
    session: Session,
    *,
    question_id: str,
    tag_type: str,
    tag_value: str,
    sort_order: int,
) -> None:
    session.execute(
        text(
            """
            insert into product.question_tags (
              question_tag_id, question_id, tag_type, tag_value,
              sort_order, evidence_link_count
            ) values (
              :question_tag_id, :question_id, :tag_type, :tag_value,
              :sort_order, 1
            )
            """
        ),
        {
            "question_tag_id": f"tag_{question_id}_{sort_order}",
            "question_id": question_id,
            "tag_type": tag_type,
            "tag_value": tag_value,
            "sort_order": sort_order,
        },
    )


def _fetch_nodes(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            select
              node_id, exam_scope, track, module_name, node_type, tag_type,
              tag_value, display_path, parent_node_id, aliases, support_status,
              near5_window_start_year, near5_window_end_year, near5_weight,
              near5_occurrence_count, stable_for_old_questions, evidence, status
            from product.knowledge_map_nodes
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _node_index(nodes: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {(n["module_name"], n["node_type"], n["tag_value"]): n for n in nodes}


def _build_tree(session: Session) -> dict[tuple[str, str, str], dict[str, Any]]:
    seed_vocabulary_tables(session)
    insert_tree_fixture(session)
    run_knowledge_map_refresh(session)
    return _node_index(_fetch_nodes(session))


def test_v2_builds_four_level_parent_chain(db_session: Session) -> None:
    index = _build_tree(db_session)

    module = index[("资料分析", "module", "资料分析")]
    family = index[("资料分析", "knowledge_family", "增长率")]
    point = index[("资料分析", "knowledge_point", "一般增长率")]
    skill = index[("资料分析", "micro_skill", "基期计算")]

    # 四级父链闭合：micro_skill → knowledge_point → knowledge_family → module
    assert module["parent_node_id"] is None
    assert family["parent_node_id"] == module["node_id"]
    assert point["parent_node_id"] == family["node_id"]
    assert skill["parent_node_id"] == point["node_id"]

    # display_path 反映真实层级
    assert module["display_path"] == "行测/资料分析"
    assert family["display_path"] == "行测/资料分析/增长率"
    assert point["display_path"] == "行测/资料分析/增长率/一般增长率"
    assert skill["display_path"] == "行测/资料分析/增长率/一般增长率/基期计算"

    # v1 从不产出的 micro_skill node_type 现在真实产出，tag_type 与词表层对齐
    assert skill["node_type"] == "micro_skill"
    assert skill["tag_type"] == "micro_skill"
    assert skill["status"] == "published"


def test_v2_adaptive_collapse_folds_sparse_leaves_upward(db_session: Session) -> None:
    index = _build_tree(db_session)

    # 低于阈值的节点不再单独成节点
    assert ("资料分析", "micro_skill", "速算技巧") not in index
    assert ("资料分析", "knowledge_point", "冷门比重") not in index
    assert ("资料分析", "micro_skill", "冷门技巧") not in index

    point = index[("资料分析", "knowledge_point", "一般增长率")]
    family = index[("资料分析", "knowledge_family", "增长率")]

    # 叶子 < 30 → 上收进父 knowledge_point，记录 collapsed_from 与 aliases
    assert point["aliases"] == ["速算技巧"]
    assert point["evidence"]["collapsed_from"] == [
        {"node_type": "micro_skill", "tag_value": "速算技巧", "question_count": 8}
    ]

    # knowledge_point < 30 → 上收 family；其下叶子随之收进 family
    assert set(family["aliases"]) == {"冷门比重", "冷门技巧"}
    assert {
        (item["node_type"], item["tag_value"], item["question_count"])
        for item in family["evidence"]["collapsed_from"]
    } == {
        ("knowledge_point", "冷门比重", 5),
        ("micro_skill", "冷门技巧", 5),
    }

    # question_count 每层聚合（含别名模块并入的 2 题）
    module = index[("资料分析", "module", "资料分析")]
    skill = index[("资料分析", "micro_skill", "基期计算")]
    assert module["evidence"]["question_count"] == DATA_MODULE_TOTAL
    assert family["evidence"]["question_count"] == FAMILY_TOTAL
    assert point["evidence"]["question_count"] == POINT_TOTAL
    assert skill["evidence"]["question_count"] == SKILL_KEPT_TOTAL

    # 折叠阈值随 evidence 落盘，可审计
    assert module["evidence"]["collapse_threshold"] == COLLAPSE_THRESHOLD_DEFAULT


def test_v2_collapse_threshold_is_a_sql_variable(db_session: Session) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)
    # 折叠阈值做成 SQL 变量：会话级 GUC 覆盖默认值 30
    db_session.execute(text("set app.knowledge_map_collapse_threshold = 5"))
    run_knowledge_map_refresh(db_session)
    index = _node_index(_fetch_nodes(db_session))

    rare_point = index[("资料分析", "knowledge_point", "冷门比重")]
    rare_skill = index[("资料分析", "micro_skill", "冷门技巧")]
    family = index[("资料分析", "knowledge_family", "增长率")]

    assert rare_point["display_path"] == "行测/资料分析/增长率/冷门比重"
    assert rare_skill["parent_node_id"] == rare_point["node_id"]
    assert rare_skill["display_path"] == "行测/资料分析/增长率/冷门比重/冷门技巧"
    assert family["aliases"] == []
    assert rare_point["evidence"]["collapse_threshold"] == 5


def test_v2_quarantined_and_unmapped_modules_produce_no_nodes(
    db_session: Session,
) -> None:
    index = _build_tree(db_session)
    module_names = {key[0] for key in index}
    tag_values = {key[2] for key in index}

    # quarantine（公共基础知识）、词表未命中（R0）、map_needs_review（R1）均不产节点
    assert "公共基础知识" not in module_names
    assert "神秘模块" not in module_names
    assert "常识应用能力" not in module_names
    assert {"法理学", "神秘族", "时政"}.isdisjoint(tag_values)

    # 别名模块（资科分析 → 资料分析）不产生独立模块节点，题量并入 canonical 模块
    assert "资科分析" not in module_names
    module = index[("资料分析", "module", "资料分析")]
    assert module["evidence"]["question_count"] == DATA_MODULE_TOTAL


def test_v2_material_form_facet_is_attribute_not_node(db_session: Session) -> None:
    index = _build_tree(db_session)
    tag_values = {key[2] for key in index}

    # R2：材料形态词与占位符词不建任何节点
    assert "统计表" not in tag_values
    assert "待分类" not in tag_values

    # 材料形态降为节点属性：沿链每层都能看到形态分布
    module = index[("资料分析", "module", "资料分析")]
    family = index[("资料分析", "knowledge_family", "增长率")]
    skill = index[("资料分析", "micro_skill", "基期计算")]
    assert module["evidence"]["material_form_breakdown"] == {"统计表": MATERIAL_TAGGED}
    assert family["evidence"]["material_form_breakdown"] == {"统计表": MATERIAL_TAGGED}
    assert skill["evidence"]["material_form_breakdown"] == {"统计表": MATERIAL_TAGGED}

    # 未涉及材料形态的模块保持空对象
    other_module = index[("判断推理", "module", "判断推理")]
    assert other_module["evidence"]["material_form_breakdown"] == {}


def test_v2_image_unsupported_inherits_down_parent_chain(db_session: Session) -> None:
    index = _build_tree(db_session)

    module = index[("判断推理", "module", "判断推理")]
    family = index[("判断推理", "knowledge_family", "图形推理")]
    point = index[("判断推理", "knowledge_point", "对称性")]

    # family 因词面命中图形类不支持；point 自身既非图形词面也非全 visual，
    # 只能沿父链继承 image_unsupported
    assert module["support_status"] == "text_supported"
    assert family["support_status"] == "image_unsupported"
    assert point["support_status"] == "image_unsupported"
    assert point["parent_node_id"] == family["node_id"]


def test_v2_near5_weights_propagate_to_micro_skill_nodes(db_session: Session) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)
    insert_guokao_occurrences_for_near5(db_session)
    run_exam_weight_refresh(db_session)
    run_knowledge_map_refresh(db_session)
    index = _node_index(_fetch_nodes(db_session))

    point = index[("资料分析", "knowledge_point", "一般增长率")]
    skill = index[("资料分析", "micro_skill", "基期计算")]

    assert point["near5_occurrence_count"] == 4
    assert point["stable_for_old_questions"] is True

    # micro_skill 层的 near5 权重来自扩展后的 refresh_guokao_exam_tag_weights.sql
    assert skill["near5_occurrence_count"] == 4
    assert skill["near5_window_start_year"] == 2022
    assert skill["near5_window_end_year"] == 2026
    assert skill["near5_weight"] is not None
    assert skill["stable_for_old_questions"] is True
