from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session
import pytest

from app.domains.knowledge.repository import KnowledgeRepository
from app.domains.knowledge.service import KnowledgeMapUnavailableError, KnowledgeService
from tests.test_knowledge_map_tree_v2 import seed_vocabulary_tables


def test_refresh_guokao_exam_tag_weights_uses_latest_five_exam_years(
    db_session: Session,
) -> None:
    _insert_weight_fixture(db_session)
    _run_exam_weight_refresh_sql(db_session)

    row = db_session.execute(
        text(
            """
            select
              module_name,
              tag_type,
              tag_value,
              window_start_year,
              window_end_year,
              question_count,
              occurrence_count
            from product.exam_tag_weights
            where exam_scope = 'guokao'
              and track = 'xingce'
              and tag_type = 'knowledge_point'
            order by weight desc, occurrence_count desc
            limit 1
            """
        )
    ).mappings().one()

    assert row["module_name"] == "资料分析"
    assert row["tag_value"] == "增长率"
    assert row["window_start_year"] == 2022
    assert row["window_end_year"] == 2026
    assert row["question_count"] == 4
    assert row["occurrence_count"] == 4


def test_weighted_practice_selection_uses_70_30_guokao_window(
    db_session: Session,
) -> None:
    _insert_weight_fixture(db_session)
    _run_exam_weight_refresh_sql(db_session)
    _run_knowledge_map_refresh_sql(db_session)

    service = KnowledgeService(KnowledgeRepository(db_session))
    selection = service.select_weighted_xingce_practice(
        exam_scope="guokao",
        limit=4,
    )

    assert selection.label == "资料分析/增长率"
    assert selection.node_id.startswith("km_")
    assert selection.display_path == "行测/资料分析/增长率"
    assert selection.window_start_year == 2022
    assert selection.window_end_year == 2026
    assert "近5年国考" in selection.basis_sentence
    assert [item.question_id for item in selection.items] == [
        "q_growth_2026",
        "q_growth_2025",
        "q_growth_2024",
        "q_growth_2020_old",
    ]
    assert all(item.visual_required is False for item in selection.items)


def test_weighted_practice_selection_does_not_fallback_without_weights(
    db_session: Session,
) -> None:
    service = KnowledgeService(KnowledgeRepository(db_session))

    with pytest.raises(KnowledgeMapUnavailableError):
        service.select_weighted_xingce_practice(exam_scope="guokao", limit=3)


def test_knowledge_map_refresh_publishes_nodes_and_rejects_free_tags(
    db_session: Session,
) -> None:
    _insert_weight_fixture(db_session)
    _run_exam_weight_refresh_sql(db_session)
    _run_knowledge_map_refresh_sql(db_session)

    service = KnowledgeService(KnowledgeRepository(db_session))
    node = service.resolve_knowledge_map_node(
        exam_scope="guokao",
        track="xingce",
        module_name="资料分析",
        tag_type="knowledge_point",
        tag_value="增长率",
    )
    free_tag = service.resolve_knowledge_map_node(
        exam_scope="guokao",
        track="xingce",
        module_name="资料分析",
        tag_type="knowledge_point",
        tag_value="AI自由发明tag",
    )

    assert node is not None
    assert node.display_path == "行测/资料分析/增长率"
    assert node.support_status == "text_supported"
    assert node.near5_occurrence_count == 4
    assert free_tag is None


def test_knowledge_map_marks_image_only_nodes_unsupported(
    db_session: Session,
) -> None:
    _insert_weight_fixture(db_session)
    _insert_image_only_fixture(db_session)
    _run_exam_weight_refresh_sql(db_session)
    _run_knowledge_map_refresh_sql(db_session)

    service = KnowledgeService(KnowledgeRepository(db_session))
    node = service.resolve_knowledge_map_node(
        exam_scope="guokao",
        track="xingce",
        module_name="判断推理",
        tag_type="knowledge_point",
        tag_value="图形推理",
    )

    assert node is not None
    assert node.support_status == "image_unsupported"


def test_knowledge_map_refresh_preserves_manually_governed_nodes(
    db_session: Session,
) -> None:
    _insert_weight_fixture(db_session)
    _run_exam_weight_refresh_sql(db_session)
    _run_knowledge_map_refresh_sql(db_session)

    node_id = db_session.execute(
        text(
            """
            select node_id
            from product.knowledge_map_nodes
            where exam_scope = 'guokao'
              and track = 'xingce'
              and module_name = '资料分析'
              and tag_type = 'knowledge_point'
              and tag_value = '增长率'
            """
        )
    ).scalar_one()

    db_session.execute(
        text(
            """
            update product.knowledge_map_nodes
            set
              aliases = '["现期增长"]'::jsonb,
              evidence = jsonb_build_object('manual_confirmed', true),
              support_status = 'draft',
              near5_occurrence_count = null,
              near5_weight = null
            where node_id = :node_id
            """
        ),
        {"node_id": node_id},
    )
    db_session.commit()

    _run_knowledge_map_refresh_sql(db_session)

    row = db_session.execute(
        text(
            """
            select aliases, evidence, support_status, near5_occurrence_count
            from product.knowledge_map_nodes
            where node_id = :node_id
            """
        ),
        {"node_id": node_id},
    ).mappings().one()

    assert row["aliases"] == ["现期增长"]
    assert row["evidence"]["manual_confirmed"] is True
    assert row["evidence"]["last_generated_refresh"]["generated_by"] == "refresh_knowledge_map_nodes_v2"
    assert row["support_status"] == "draft"
    assert row["near5_occurrence_count"] == 4


def _run_exam_weight_refresh_sql(session: Session) -> None:
    sql_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "sql"
        / "refresh_guokao_exam_tag_weights.sql"
    )
    for statement in sql_path.read_text().split(";"):
        if statement.strip():
            session.execute(text(statement))
    session.commit()


def _run_knowledge_map_refresh_sql(session: Session) -> None:
    # v2 生成器按 R0/R1/R2 消费治理词表（vocab@v1），执行前必须先装载，
    # 否则前置守卫会中止刷新。
    seed_vocabulary_tables(session)
    sql_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "sql"
        / "refresh_knowledge_map_nodes.sql"
    )
    for statement in sql_path.read_text().split(";"):
        if statement.strip():
            session.execute(text(statement))
    session.commit()


def _insert_weight_fixture(session: Session) -> None:
    questions = [
        ("q_growth_2026", "资料分析", "增长率", False, "2026年国家公务员录用考试《行测》题（地市级网友回忆版）"),
        ("q_growth_2025", "资料分析", "增长率", False, "2025年国家公务员录用考试《行测》题（地市级网友回忆版）"),
        ("q_growth_2024", "资料分析", "增长率", False, "2024年国家公务员录用考试《行测》题（地市级网友回忆版）"),
        ("q_growth_image", "资料分析", "增长率", True, "2026年国家公务员录用考试《行测》题（副省级网友回忆版）"),
        ("q_growth_2020_old", "资料分析", "增长率", False, "2020年国家公务员录用考试《行测》题（地市级网友回忆版）"),
        ("q_words_2026", "言语理解与表达", "成语填空", False, "2026年国家公务员录用考试《行测》题（地市级网友回忆版）"),
        ("q_words_2025", "言语理解与表达", "成语填空", False, "2025年国家公务员录用考试《行测》题（地市级网友回忆版）"),
        ("q_province_growth", "资料分析", "增长率", False, "2026年浙江省公务员录用考试《行测》题"),
    ]
    for index, (question_id, module_name, tag_value, visual_required, paper_title) in enumerate(
        questions,
        start=1,
    ):
        session.execute(
            text(
                """
                insert into product.questions (
                  question_id,
                  domain,
                  status,
                  module_name,
                  question_type,
                  stem,
                  answer,
                  shared_context_required,
                  visual_required,
                  needs_multimodal,
                  occurrence_count,
                  latest_year
                ) values (
                  :question_id,
                  'objective',
                  'active',
                  :module_name,
                  '资料分析',
                  :stem,
                  'B',
                  false,
                  :visual_required,
                  false,
                  1,
                  :latest_year
                )
                """
            ),
            {
                "question_id": question_id,
                "module_name": module_name,
                "stem": f"{tag_value} fixture {index}",
                "visual_required": visual_required,
                "latest_year": _title_year(paper_title),
            },
        )
        session.execute(
            text(
                """
                insert into product.question_occurrences (
                  occurrence_id,
                  question_id,
                  province,
                  year,
                  number,
                  section,
                  paper_title,
                  canonical_occurrence_rank
                ) values (
                  :occurrence_id,
                  :question_id,
                  :province,
                  :year,
                  :number,
                  '资料分析',
                  :paper_title,
                  1
                )
                """
            ),
            {
                "occurrence_id": f"occ_{question_id}",
                "question_id": question_id,
                "province": "国考" if "国家公务员" in paper_title else "浙江",
                "year": _title_year(paper_title) - 1,
                "number": index,
                "paper_title": paper_title,
            },
        )
        session.execute(
            text(
                """
                insert into product.question_tags (
                  question_tag_id,
                  question_id,
                  tag_type,
                  tag_value,
                  sort_order,
                  evidence_link_count
                ) values (
                  :question_tag_id,
                  :question_id,
                  'knowledge_point',
                  :tag_value,
                  1,
                  1
                )
                """
            ),
            {
                "question_tag_id": f"tag_{question_id}",
                "question_id": question_id,
                "tag_value": tag_value,
            },
        )


def _insert_image_only_fixture(session: Session) -> None:
    question_id = "q_image_graphic_2026"
    paper_title = "2026年国家公务员录用考试《行测》题（地市级网友回忆版）"
    session.execute(
        text(
            """
            insert into product.questions (
              question_id,
              domain,
              status,
              module_name,
              question_type,
              stem,
              answer,
              shared_context_required,
              visual_required,
              needs_multimodal,
              occurrence_count,
              latest_year
            ) values (
              :question_id,
              'objective',
              'active',
              '判断推理',
              '图形推理',
              '图形推理 fixture',
              'B',
              false,
              true,
              true,
              1,
              2026
            )
            """
        ),
        {"question_id": question_id},
    )
    session.execute(
        text(
            """
            insert into product.question_occurrences (
              occurrence_id,
              question_id,
              province,
              year,
              number,
              section,
              paper_title,
              canonical_occurrence_rank
            ) values (
              'occ_q_image_graphic_2026',
              :question_id,
              '国考',
              2025,
              88,
              '判断推理',
              :paper_title,
              1
            )
            """
        ),
        {"question_id": question_id, "paper_title": paper_title},
    )
    session.execute(
        text(
            """
            insert into product.question_tags (
              question_tag_id,
              question_id,
              tag_type,
              tag_value,
              sort_order,
              evidence_link_count
            ) values (
              'tag_q_image_graphic_2026',
              :question_id,
              'knowledge_point',
              '图形推理',
              1,
              1
            )
            """
        ),
        {"question_id": question_id},
    )


def _title_year(title: str) -> int:
    return int(title[:4])
