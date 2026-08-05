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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from tests.conftest import _split_postgres_statements

REPO_ROOT = Path(__file__).resolve().parents[2]
VOCAB_DIR = REPO_ROOT / "scripts" / "sql" / "vocabulary"

COLLAPSE_THRESHOLD_DEFAULT = 30
DEFAULT_RELEASE_VERSION = "knowledge-map@test-v1"
DEFAULT_SOURCE_DATA_VERSION = "question-bank@test-v1"


def seed_vocabulary_tables(session: Session) -> None:
    """从治理 CSV 装载词表（与 load_vocabulary_seed.sql 的 \\copy 装载等价）。"""
    module_rows: list[dict[str, Any]] = []
    with (VOCAB_DIR / "module_alias_map.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            module_rows.append(
                {
                    "version": row["version"],
                    "raw_module_name": row["raw_module_name"],
                    "action": row["action"],
                    # CSV 中 quarantine 行的 canonical_code 为空串，装载为 NULL
                    "canonical_code": row["canonical_code"] or None,
                    "local_question_count": int(row["local_question_count"]),
                    "basis": row["basis"],
                }
            )

    facet_rows: list[dict[str, Any]] = []
    with (VOCAB_DIR / "facet_rules.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            facet_rows.append(
                {
                    "version": row["version"],
                    "term": row["term"],
                    "facet": row["facet"],
                    "tag_levels_observed": row["tag_levels_observed"],
                    "local_occurrences": int(row["local_occurrences"]),
                    "needs_review": row["needs_review"].strip().lower() == "true",
                    "basis": row["basis"],
                }
            )

    existing_module_count = int(
        session.execute(
            text("select count(*) from product.vocab_module_alias_map where version = 'vocab@v1'")
        ).scalar_one()
    )
    existing_facet_count = int(
        session.execute(
            text("select count(*) from product.vocab_facet_rules where version = 'vocab@v1'")
        ).scalar_one()
    )
    if existing_module_count == 0 and existing_facet_count == 0:
        for row in module_rows:
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
                row,
            )
        for row in facet_rows:
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
                row,
            )
    else:
        stored_modules = [
            dict(row)
            for row in session.execute(
                text(
                    """
                    select version, raw_module_name, action, canonical_code,
                           local_question_count, basis
                    from product.vocab_module_alias_map
                    where version = 'vocab@v1'
                    order by raw_module_name
                    """
                )
            ).mappings()
        ]
        stored_facets = [
            dict(row)
            for row in session.execute(
                text(
                    """
                    select version, term, facet, tag_levels_observed,
                           local_occurrences, needs_review, basis
                    from product.vocab_facet_rules
                    where version = 'vocab@v1'
                    order by term
                    """
                )
            ).mappings()
        ]
        if stored_modules != sorted(module_rows, key=lambda item: item["raw_module_name"]):
            raise AssertionError("module vocabulary fixture replay drifted")
        if stored_facets != sorted(facet_rows, key=lambda item: item["term"]):
            raise AssertionError("facet vocabulary fixture replay drifted")
    session.commit()


def run_knowledge_map_refresh(
    session: Session,
    *,
    release_version: str = DEFAULT_RELEASE_VERSION,
    source_data_version: str = DEFAULT_SOURCE_DATA_VERSION,
    fresh_until: datetime | None = None,
) -> None:
    effective_fresh_until = fresh_until or datetime.now(UTC) + timedelta(days=30)
    content_catalog = SQLQuestionContentCatalog(session)
    for question_id in session.execute(
        text(
            """
            select question_id
            from product.questions
            where domain = 'objective'
              and status = 'active'
            order by question_id
            """
        )
    ).scalars():
        content_catalog.publish_current(question_id=str(question_id))
    session.execute(
        text(
            """
            select set_config(
              'app.knowledge_map_release_version',
              :release_version,
              true
            )
            """
        ),
        {"release_version": release_version},
    )
    session.execute(
        text(
            """
            select set_config(
              'app.knowledge_map_source_data_version',
              :source_data_version,
              true
            )
            """
        ),
        {"source_data_version": source_data_version},
    )
    session.execute(
        text(
            """
            select set_config(
              'app.knowledge_map_fresh_until',
              :fresh_until,
              true
            )
            """
        ),
        {"fresh_until": effective_fresh_until.isoformat()},
    )
    _execute_knowledge_map_refresh(session)
    session.commit()


def _execute_knowledge_map_refresh(session: Session) -> None:
    sql_path = REPO_ROOT / "scripts" / "sql" / "refresh_knowledge_map_nodes.sql"
    script = "\n".join(
        line
        for line in sql_path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("\\") and line.strip().lower() not in {"begin;", "commit;"}
    )
    for statement in _split_postgres_statements(script):
        session.execute(text(statement))


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
READINESS_SUPPORT_QUESTIONS = (
    ("q_ready_political", "政治理论", "政治理论测试"),
    ("q_ready_general", "常识判断", "常识判断测试"),
    ("q_ready_verbal", "言语理解与表达", "言语理解测试"),
    ("q_ready_quantity", "数量关系", "数量关系测试"),
    ("q_ready_judgment", "判断推理", "逻辑判断测试"),
)


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

    # Group H：发布 readiness 要求六个 canonical 模块各有可操作考点。
    # 资料分析已由主干覆盖；其余模块各放一条不参与折叠断言的直挂考点。
    for question_id, module_name, tag_value in READINESS_SUPPORT_QUESTIONS:
        add(
            question_id,
            module_name,
            [("knowledge_point", tag_value)],
        )

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
    for index, (question_id, module_name, _tag_value) in enumerate(
        READINESS_SUPPORT_QUESTIONS,
        start=101,
    ):
        session.execute(
            text(
                """
                insert into product.question_occurrences (
                  occurrence_id, question_id, province, year, number,
                  section, paper_title, canonical_occurrence_rank
                ) values (
                  :occurrence_id, :question_id, '国考', 2025, :number,
                  :section, '2026年国家公务员录用考试《行测》题（测试）', 1
                )
                """
            ),
            {
                "occurrence_id": f"occ_{question_id}",
                "question_id": question_id,
                "number": index,
                "section": module_name,
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
    session.execute(
        text(
            """
            insert into product.question_options (
              question_id, option_index, label, option_text
            ) values (
              :question_id, :option_index, :label, :option_text
            )
            """
        ),
        [
            {
                "question_id": question_id,
                "option_index": option_index,
                "label": label,
                "option_text": f"选项 {label}",
            }
            for option_index, label in enumerate(("A", "B", "C", "D"))
        ],
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
              node.release_id, release.release_version,
              node_id, node.exam_scope, node.track, module_name, node_type, tag_type,
              tag_value, display_path, parent_node_id, aliases, support_status,
              near5_window_start_year, near5_window_end_year, near5_weight,
              near5_occurrence_count, stable_for_old_questions, method_card_ids, evidence,
              node.status
            from product.knowledge_map_nodes node
            join product.knowledge_map_releases release
              on release.release_id = node.release_id
             and release.exam_scope = node.exam_scope
             and release.track = node.track
            where release.status = 'active'
              and release.fresh_until > now()
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _node_index(nodes: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {(n["module_name"], n["node_type"], n["tag_value"]): n for n in nodes}


def _build_tree(session: Session) -> dict[tuple[str, str, str], dict[str, Any]]:
    seed_vocabulary_tables(session)
    insert_tree_fixture(session)
    insert_guokao_occurrences_for_near5(session)
    run_exam_weight_refresh(session)
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
    insert_guokao_occurrences_for_near5(db_session)
    run_exam_weight_refresh(db_session)
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


def test_v2_ai_knowledge_nodes_attach_cards_when_tag_path_does_not_match(
    db_session: Session,
) -> None:
    """AI knowledge_nodes mapping attaches method cards to exam-point nodes.

    A method card whose tag_path describes a teaching approach
    (``判断推理/逻辑判断/因果推理``) never matches the tree tag_value for an
    exam point (``对称性``/``一般增长率``). The card's AI-mapped
    ``knowledge_nodes`` display_path list is the fallback: exact display_path
    match OR last-segment == node tag_value must attach the card.
    """
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)
    insert_guokao_occurrences_for_near5(db_session)
    run_exam_weight_refresh(db_session)

    db_session.execute(
        text(
            """
            insert into product.method_cards (
              id, exam_scope, track, module_name, tag_path, title,
              recognition, applicability, method_steps, error_signatures,
              fix_action, time_budget_sec, accuracy_rule, pitfall,
              author_note, validation, priority, status, knowledge_nodes
            ) values (
              'mc_ai_mapped', 'guokao', 'xingce', '判断推理',
              '判断推理/逻辑判断/因果推理', 'AI 映射方法卡', '识别因果推理',
              '{}'::jsonb, '["步骤"]'::jsonb, '[]'::jsonb,
              '执行步骤', 60, '答案正确', null, 'test',
              '{}'::jsonb, 4, 'published',
              '["行测/判断推理/图形推理/对称性"]'::jsonb
            )
            """
        )
    )
    db_session.execute(
        text(
            """
            insert into product.method_cards (
              id, exam_scope, track, module_name, tag_path, title,
              recognition, applicability, method_steps, error_signatures,
              fix_action, time_budget_sec, accuracy_rule, pitfall,
              author_note, validation, priority, status, knowledge_nodes
            ) values (
              'mc_ai_last_segment', 'guokao', 'xingce', '资料分析',
              '资料分析/速算/估算技巧', '末段匹配方法卡', '识别估算',
              '{}'::jsonb, '["步骤"]'::jsonb, '[]'::jsonb,
              '执行步骤', 60, '答案正确', null, 'test',
              '{}'::jsonb, 3, 'published',
              '["行测/资料分析/增长率"]'::jsonb
            )
            """
        )
    )
    db_session.commit()

    run_knowledge_map_refresh(db_session)
    index = _node_index(_fetch_nodes(db_session))

    point = index[("判断推理", "knowledge_point", "对称性")]
    family = index[("资料分析", "knowledge_family", "增长率")]

    # 精确 display_path 命中：knowledge_nodes 里是 行测/判断推理/图形推理/对称性
    assert point["method_card_ids"] == ["mc_ai_mapped"]
    # 末段 == tag_value 命中：knowledge_nodes 末段「增长率」== 节点 tag_value
    assert "mc_ai_last_segment" in family["method_card_ids"]


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


def test_release_publication_keeps_two_versions_and_only_newest_active(
    db_session: Session,
) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)
    insert_guokao_occurrences_for_near5(db_session)
    run_exam_weight_refresh(db_session)

    run_knowledge_map_refresh(
        db_session,
        release_version="knowledge-map@test-release-1",
        source_data_version="question-bank@test-release-1",
    )
    first_release = dict(
        db_session.execute(
            text(
                """
                select *
                from product.knowledge_map_releases
                where release_version = 'knowledge-map@test-release-1'
                """
            )
        )
        .mappings()
        .one()
    )
    first_node_count = db_session.execute(
        text(
            """
            select count(*)
            from product.knowledge_map_nodes
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": str(first_release["release_id"])},
    ).scalar_one()

    run_knowledge_map_refresh(
        db_session,
        release_version="knowledge-map@test-release-2",
        source_data_version="question-bank@test-release-2",
    )
    releases = {
        row["release_version"]: dict(row)
        for row in db_session.execute(
            text(
                """
                select *
                from product.knowledge_map_releases
                where generator_version = 'refresh_knowledge_map_nodes_v4'
                order by release_version
                """
            )
        ).mappings()
    }

    assert set(releases) == {
        "knowledge-map@test-release-1",
        "knowledge-map@test-release-2",
    }
    assert releases["knowledge-map@test-release-1"]["status"] == "retired"
    assert releases["knowledge-map@test-release-1"]["retired_at"] is not None
    assert releases["knowledge-map@test-release-2"]["status"] == "active"
    assert releases["knowledge-map@test-release-2"]["activated_at"] is not None
    assert (
        releases["knowledge-map@test-release-2"]["supersedes_release_id"]
        == releases["knowledge-map@test-release-1"]["release_id"]
    )
    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where exam_scope = 'guokao'
                  and track = 'xingce'
                  and status = 'active'
                """
            )
        ).scalar_one()
        == 1
    )

    # node_id 跨 release 可重复，但每个版本的节点事实完整、旧版仍可追溯。
    second_release = releases["knowledge-map@test-release-2"]
    second_node_count = db_session.execute(
        text(
            """
            select count(*)
            from product.knowledge_map_nodes
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": str(second_release["release_id"])},
    ).scalar_one()
    assert first_node_count > 0
    assert second_node_count == first_node_count
    assert first_node_count == first_release["node_count"]
    assert second_node_count == second_release["node_count"]
    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from (
                  select node_id
                  from product.knowledge_map_nodes
                  where release_id in (
                    cast(:first_release_id as uuid),
                    cast(:second_release_id as uuid)
                  )
                  group by node_id
                  having count(*) = 2
                ) shared_nodes
                """
            ),
            {
                "first_release_id": str(first_release["release_id"]),
                "second_release_id": str(second_release["release_id"]),
            },
        ).scalar_one()
        == first_node_count
    )
    assert len(str(first_release["source_fingerprint"])) == 64
    assert len(str(second_release["source_fingerprint"])) == 64
    assert first_release["source_fingerprint"] != second_release["source_fingerprint"]


def test_release_publication_rejects_missing_module_vocabulary(
    db_session: Session,
) -> None:
    insert_tree_fixture(db_session)

    with pytest.raises(DBAPIError):
        run_knowledge_map_refresh(
            db_session,
            release_version="knowledge-map@missing-vocab",
            source_data_version="question-bank@missing-vocab",
        )
    db_session.rollback()

    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where release_version = 'knowledge-map@missing-vocab'
                """
            )
        ).scalar_one()
        == 0
    )


def test_release_publication_rejects_duplicate_canonical_module_code(
    db_session: Session,
) -> None:
    seed_vocabulary_tables(db_session)
    db_session.execute(
        text(
            """
            update product.vocab_module_alias_map
            set canonical_code = 'judgment_reasoning'
            where version = 'vocab@v1'
              and raw_module_name = '资料分析'
              and action = 'canonical'
            """
        )
    )
    db_session.commit()

    with pytest.raises(DBAPIError):
        run_knowledge_map_refresh(
            db_session,
            release_version="knowledge-map@duplicate-canonical-code",
            source_data_version="question-bank@duplicate-canonical-code",
        )
    db_session.rollback()

    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where release_version =
                      'knowledge-map@duplicate-canonical-code'
                """
            )
        ).scalar_one()
        == 0
    )


def test_release_publication_rejects_rotated_canonical_module_code_name_pairs(
    db_session: Session,
) -> None:
    seed_vocabulary_tables(db_session)
    db_session.execute(
        text(
            """
            update product.vocab_module_alias_map
            set canonical_code = case canonical_code
              when 'political_theory' then 'general_knowledge'
              when 'general_knowledge' then 'verbal_comprehension'
              when 'verbal_comprehension' then 'quantitative_relations'
              when 'quantitative_relations' then 'judgment_reasoning'
              when 'judgment_reasoning' then 'data_analysis'
              when 'data_analysis' then 'political_theory'
            end
            where version = 'vocab@v1'
              and action = 'canonical'
            """
        )
    )
    db_session.commit()

    with pytest.raises(DBAPIError):
        run_knowledge_map_refresh(
            db_session,
            release_version="knowledge-map@rotated-canonical-pairs",
            source_data_version="question-bank@rotated-canonical-pairs",
        )
    db_session.rollback()

    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where release_version =
                      'knowledge-map@rotated-canonical-pairs'
                """
            )
        ).scalar_one()
        == 0
    )


def test_release_publication_rejects_missing_actionable_module(
    db_session: Session,
) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)
    insert_guokao_occurrences_for_near5(db_session)
    run_exam_weight_refresh(db_session)
    db_session.execute(
        text(
            """
            delete from product.question_tags
            where question_id = 'q_ready_political'
            """
        )
    )
    db_session.commit()

    with pytest.raises(DBAPIError):
        run_knowledge_map_refresh(
            db_session,
            release_version="knowledge-map@missing-actionable-module",
            source_data_version="question-bank@missing-actionable-module",
        )
    db_session.rollback()

    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where release_version =
                      'knowledge-map@missing-actionable-module'
                """
            )
        ).scalar_one()
        == 0
    )


def test_release_publication_exact_replay_is_idempotent(
    db_session: Session,
) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)
    insert_guokao_occurrences_for_near5(db_session)
    run_exam_weight_refresh(db_session)
    fresh_until = datetime.now(UTC) + timedelta(days=30)

    run_knowledge_map_refresh(
        db_session,
        release_version="knowledge-map@replay-v1",
        source_data_version="question-bank@replay-v1",
        fresh_until=fresh_until,
    )
    original = dict(
        db_session.execute(
            text(
                """
                select
                  release_id,
                  source_fingerprint,
                  node_count,
                  question_binding_count,
                  question_binding_fingerprint,
                  activated_at
                from product.knowledge_map_releases
                where release_version = 'knowledge-map@replay-v1'
                """
            )
        )
        .mappings()
        .one()
    )
    original_nodes = db_session.execute(
        text(
            """
            select jsonb_agg(to_jsonb(node) order by node.node_id)
            from product.knowledge_map_nodes node
            where node.release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": str(original["release_id"])},
    ).scalar_one()
    original_bindings = db_session.execute(
        text(
            """
            select jsonb_agg(to_jsonb(binding) order by node_id, question_id)
            from product.knowledge_map_question_bindings binding
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": str(original["release_id"])},
    ).scalar_one()

    run_knowledge_map_refresh(
        db_session,
        release_version="knowledge-map@replay-v1",
        source_data_version="question-bank@replay-v1",
        fresh_until=fresh_until,
    )
    replayed = dict(
        db_session.execute(
            text(
                """
                select
                  release_id,
                  source_fingerprint,
                  node_count,
                  question_binding_count,
                  question_binding_fingerprint,
                  activated_at
                from product.knowledge_map_releases
                where release_version = 'knowledge-map@replay-v1'
                """
            )
        )
        .mappings()
        .one()
    )
    replayed_nodes = db_session.execute(
        text(
            """
            select jsonb_agg(to_jsonb(node) order by node.node_id)
            from product.knowledge_map_nodes node
            where node.release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": str(replayed["release_id"])},
    ).scalar_one()
    replayed_bindings = db_session.execute(
        text(
            """
            select jsonb_agg(to_jsonb(binding) order by node_id, question_id)
            from product.knowledge_map_question_bindings binding
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": str(replayed["release_id"])},
    ).scalar_one()

    assert replayed == original
    assert replayed_nodes == original_nodes
    assert replayed_bindings == original_bindings
    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where release_version = 'knowledge-map@replay-v1'
                """
            )
        ).scalar_one()
        == 1
    )


def test_release_publication_replay_rejects_changed_source_content(
    db_session: Session,
) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)
    insert_guokao_occurrences_for_near5(db_session)
    run_exam_weight_refresh(db_session)
    fresh_until = datetime.now(UTC) + timedelta(days=30)
    run_knowledge_map_refresh(
        db_session,
        release_version="knowledge-map@content-guard-v1",
        source_data_version="question-bank@content-guard-v1",
        fresh_until=fresh_until,
    )
    original_release_id = db_session.execute(
        text(
            """
            select release_id
            from product.knowledge_map_releases
            where release_version = 'knowledge-map@content-guard-v1'
            """
        )
    ).scalar_one()

    db_session.execute(
        text(
            """
            update product.question_tags
            set tag_value = '基期计算-内容已变化'
            where question_id = 'q_da_main_01'
              and tag_type = 'micro_skill'
            """
        )
    )
    with pytest.raises(DBAPIError):
        run_knowledge_map_refresh(
            db_session,
            release_version="knowledge-map@content-guard-v1",
            source_data_version="question-bank@content-guard-v1",
            fresh_until=fresh_until,
        )
    db_session.rollback()

    preserved = db_session.execute(
        text(
            """
            select release_id, status
            from product.knowledge_map_releases
            where release_version = 'knowledge-map@content-guard-v1'
            """
        )
    ).one()
    assert preserved.release_id == original_release_id
    assert preserved.status == "active"


def test_release_publication_rejects_legacy_release_metadata(
    db_session: Session,
) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)

    with pytest.raises(DBAPIError):
        run_knowledge_map_refresh(
            db_session,
            release_version="knowledge-map@legacy-pre-test",
            source_data_version="question-bank@legacy-name-test",
        )
    db_session.rollback()

    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where release_version = 'knowledge-map@legacy-pre-test'
                """
            )
        ).scalar_one()
        == 0
    )


def test_release_publication_rejects_expired_freshness(
    db_session: Session,
) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)

    with pytest.raises(DBAPIError):
        run_knowledge_map_refresh(
            db_session,
            release_version="knowledge-map@expired",
            source_data_version="question-bank@expired",
            fresh_until=datetime.now(UTC) - timedelta(seconds=1),
        )
    db_session.rollback()

    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where release_version = 'knowledge-map@expired'
                """
            )
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where status = 'active'
                """
            )
        ).scalar_one()
        == 0
    )


@pytest.mark.parametrize(
    "missing_setting",
    [
        "app.knowledge_map_release_version",
        "app.knowledge_map_source_data_version",
        "app.knowledge_map_fresh_until",
    ],
)
def test_release_publication_without_required_parameter_fails_closed(
    db_session: Session,
    missing_setting: str,
) -> None:
    seed_vocabulary_tables(db_session)
    insert_tree_fixture(db_session)
    settings = {
        "app.knowledge_map_release_version": "knowledge-map@required-guc-test",
        "app.knowledge_map_source_data_version": "question-bank@required-guc-test",
        "app.knowledge_map_fresh_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
    }
    for setting_name, setting_value in settings.items():
        if setting_name != missing_setting:
            db_session.execute(
                text("select set_config(:setting_name, :setting_value, true)"),
                {
                    "setting_name": setting_name,
                    "setting_value": setting_value,
                },
            )

    with pytest.raises(DBAPIError):
        _execute_knowledge_map_refresh(db_session)
    db_session.rollback()

    assert (
        db_session.execute(
            text(
                """
                select count(*)
                from product.knowledge_map_releases
                where generator_version = 'refresh_knowledge_map_nodes_v4'
                """
            )
        ).scalar_one()
        == 0
    )
