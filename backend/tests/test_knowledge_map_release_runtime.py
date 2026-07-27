from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.knowledge.repository import KnowledgeRepository
from app.domains.knowledge.service import (
    KnowledgeMapUnavailableError,
    KnowledgeService,
)
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    bind_question_to_release_node,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)
from tests.test_exam_tag_weights import _insert_weight_fixture


NOW = datetime(2026, 7, 27, 8, tzinfo=UTC)


def test_weighted_selection_uses_fresh_active_release_and_exposes_lineage(
    db_session: Session,
) -> None:
    _insert_weight_fixture(db_session)
    fresh_until = datetime.now(UTC) + timedelta(days=7)
    release_id = _insert_release(
        db_session,
        release_version="knowledge-map@fresh-active",
        source_data_version="questions@2026-07-27",
        status="active",
        fresh_until=fresh_until,
    )
    _insert_node(
        db_session,
        release_id=release_id,
        node_id="km_growth",
        module_name="资料分析",
        tag_value="增长率",
        weight=0.85,
        occurrence_count=4,
    )
    _transition_release(db_session, release_id=release_id, status="active")

    service = KnowledgeService(KnowledgeRepository(db_session))
    selection = service.select_weighted_xingce_practice(
        exam_scope="guokao",
        module_name="资料分析",
        limit=3,
    )

    assert selection.release_id == release_id
    assert selection.release_version == "knowledge-map@fresh-active"
    assert selection.source_data_version == "questions@2026-07-27"
    assert selection.fresh_until == fresh_until
    assert selection.node_id == "km_growth"
    assert [item.question_id for item in selection.items] == [
        "q_growth_2026",
        "q_growth_2025",
        "q_growth_2024",
    ]


@pytest.mark.parametrize(
    ("status", "fresh_until"),
    (
        ("draft", NOW + timedelta(days=7)),
        ("retired", NOW + timedelta(days=7)),
        ("active", NOW - timedelta(hours=1)),
    ),
)
def test_non_current_release_statuses_are_not_selectable(
    db_session: Session,
    status: str,
    fresh_until: datetime,
) -> None:
    release_id = _insert_release(
        db_session,
        release_version=f"knowledge-map@{status}-not-current",
        source_data_version=f"questions@{status}",
        status=status,
        fresh_until=fresh_until,
    )
    _insert_node(
        db_session,
        release_id=release_id,
        node_id=f"km_{status}",
        module_name="资料分析",
        tag_value="增长率",
        weight=1.0,
        occurrence_count=100,
    )
    if status != "draft":
        _transition_release(db_session, release_id=release_id, status=status)

    repository = KnowledgeRepository(db_session)

    assert (
        repository.get_top_knowledge_map_node(
            exam_scope="guokao",
            track="xingce",
            node_type="knowledge_point",
            now=NOW,
        )
        is None
    )
    assert (
        repository.get_knowledge_map_node_by_tag(
            exam_scope="guokao",
            track="xingce",
            module_name="资料分析",
            tag_type="knowledge_point",
            tag_value="增长率",
            now=NOW,
        )
        is None
    )


def test_requested_module_does_not_fallback_to_another_module(
    db_session: Session,
) -> None:
    _insert_weight_fixture(db_session)
    release_id = _insert_release(
        db_session,
        release_version="knowledge-map@module-boundary",
        source_data_version="questions@module-boundary",
        status="active",
        fresh_until=datetime.now(UTC) + timedelta(days=7),
    )
    _insert_node(
        db_session,
        release_id=release_id,
        node_id="km_data_analysis_only",
        module_name="资料分析",
        tag_value="增长率",
        weight=1.0,
        occurrence_count=4,
    )
    _transition_release(db_session, release_id=release_id, status="active")

    service = KnowledgeService(KnowledgeRepository(db_session))

    with pytest.raises(KnowledgeMapUnavailableError):
        service.select_weighted_xingce_practice(
            exam_scope="guokao",
            module_name="判断推理",
            limit=3,
        )


def test_candidates_read_only_the_active_release_and_keep_type_priority(
    db_session: Session,
) -> None:
    active_release_id = _insert_release(
        db_session,
        release_version="knowledge-map@current",
        source_data_version="questions@current",
        status="active",
        fresh_until=NOW + timedelta(days=7),
        node_count=2,
    )
    draft_release_id = _insert_release(
        db_session,
        release_version="knowledge-map@next-draft",
        source_data_version="questions@next",
        status="draft",
        fresh_until=NOW + timedelta(days=14),
    )
    _insert_node(
        db_session,
        release_id=active_release_id,
        node_id="km_shared",
        module_name="资料分析",
        node_type="knowledge_point",
        tag_type="knowledge_point",
        tag_value="增长率",
        weight=0.99,
        occurrence_count=99,
    )
    _insert_node(
        db_session,
        release_id=active_release_id,
        node_id="km_micro_skill",
        module_name="资料分析",
        node_type="micro_skill",
        tag_type="micro_skill",
        tag_value="两期比重差心算",
        weight=0.1,
        occurrence_count=5,
    )
    _transition_release(db_session, release_id=active_release_id, status="active")
    _insert_node(
        db_session,
        release_id=draft_release_id,
        node_id="km_shared",
        module_name="判断推理",
        node_type="knowledge_point",
        tag_type="knowledge_point",
        tag_value="图形推理",
        weight=1.0,
        occurrence_count=1000,
    )

    nodes = KnowledgeRepository(db_session).list_current_knowledge_map_nodes(
        exam_scope="guokao",
        track="xingce",
        module_name=None,
        node_types=("micro_skill", "knowledge_point"),
        now=NOW,
    )

    assert [node.node_id for node in nodes] == ["km_micro_skill", "km_shared"]
    assert {node.release_id for node in nodes} == {active_release_id}
    assert all(node.release_version == "knowledge-map@current" for node in nodes)
    assert all(node.source_data_version == "questions@current" for node in nodes)


def _insert_release(
    session: Session,
    *,
    release_version: str,
    source_data_version: str,
    status: str,
    fresh_until: datetime,
    node_count: int = 1,
) -> str:
    seed_released_module_vocabulary(
        session,
        version="vocab@runtime-test",
        aliases={"资科分析": "data_analysis"},
    )
    release_id = str(uuid4())
    generated_at = min(NOW - timedelta(days=2), fresh_until - timedelta(days=1))
    session.execute(
        text(
            """
            insert into product.knowledge_map_releases (
              release_id,
              release_version,
              exam_scope,
              track,
              source_data_version,
              vocab_version,
              generator_version,
              source_fingerprint,
              generated_at,
              fresh_until,
              status,
              node_count,
              activated_at,
              retired_at
            ) values (
              cast(:release_id as uuid),
              :release_version,
              'guokao',
              'xingce',
              :source_data_version,
              'vocab@runtime-test',
              'knowledge-map-generator@runtime-test',
              :source_fingerprint,
              :generated_at,
              :fresh_until,
              'draft',
              0,
              null,
              null
            )
            """
        ),
        {
            "release_id": release_id,
            "release_version": release_version,
            "source_data_version": source_data_version,
            "source_fingerprint": release_id.replace("-", "") * 2,
            "generated_at": generated_at,
            "fresh_until": fresh_until,
        },
    )
    return release_id


def _insert_node(
    session: Session,
    *,
    release_id: str,
    node_id: str,
    module_name: str,
    tag_value: str,
    weight: float,
    occurrence_count: int,
    node_type: str = "knowledge_point",
    tag_type: str = "knowledge_point",
) -> None:
    session.execute(
        text(
            """
            insert into product.knowledge_map_nodes (
              release_id,
              node_id,
              exam_scope,
              track,
              module_name,
              node_type,
              tag_type,
              tag_value,
              display_path,
              aliases,
              support_status,
              near5_window_start_year,
              near5_window_end_year,
              near5_weight,
              near5_occurrence_count,
              stable_for_old_questions,
              method_card_ids,
              evidence,
              status
            ) values (
              cast(:release_id as uuid),
              :node_id,
              'guokao',
              'xingce',
              :module_name,
              :node_type,
              :tag_type,
              :tag_value,
              :display_path,
              '[]'::jsonb,
              'text_supported',
              2022,
              2026,
              :weight,
              :occurrence_count,
              true,
              '[]'::jsonb,
              '{}'::jsonb,
              'published'
            )
            """
        ),
        {
            "release_id": release_id,
            "node_id": node_id,
            "module_name": module_name,
            "node_type": node_type,
            "tag_type": tag_type,
            "tag_value": tag_value,
            "display_path": f"行测/{module_name}/{tag_value}",
            "weight": weight,
            "occurrence_count": occurrence_count,
        },
    )


def _transition_release(
    session: Session,
    *,
    release_id: str,
    status: str,
) -> None:
    candidates = session.execute(
        text(
            """
            select
              node.node_id,
              node.near5_window_start_year,
              node.near5_window_end_year,
              question.question_id,
              occurrence.latest_recent_exam_year,
              occurrence.recent_occurrence_count,
              occurrence.latest_old_exam_year,
              occurrence.old_occurrence_count
            from product.knowledge_map_nodes node
            left join product.question_tags tag
              on tag.tag_type = node.tag_type
             and tag.tag_value = node.tag_value
            left join product.questions question
              on question.question_id = tag.question_id
             and question.status in ('active', 'canonical')
             and question.module_name = node.module_name
            left join lateral (
              select
                max(source.exam_year) filter (
                  where source.exam_year between
                    node.near5_window_start_year and node.near5_window_end_year
                )::integer as latest_recent_exam_year,
                count(distinct source.occurrence_id) filter (
                  where source.exam_year between
                    node.near5_window_start_year and node.near5_window_end_year
                )::integer as recent_occurrence_count,
                max(source.exam_year) filter (
                  where source.exam_year < node.near5_window_start_year
                )::integer as latest_old_exam_year,
                count(distinct source.occurrence_id) filter (
                  where source.exam_year < node.near5_window_start_year
                )::integer as old_occurrence_count
              from (
                select
                  occurrence.occurrence_id,
                  coalesce(
                    nullif(
                      substring(
                        coalesce(
                          occurrence.paper_title,
                          occurrence.occurrence_paper_title,
                          ''
                        )
                        from '([12][0-9]{3})年'
                      ),
                      ''
                    )::integer,
                    occurrence.year
                  ) as exam_year
                from product.question_occurrences occurrence
                where occurrence.question_id = question.question_id
                  and (
                    coalesce(occurrence.province, '') = '国考'
                    or coalesce(
                      occurrence.paper_title,
                      occurrence.occurrence_paper_title,
                      ''
                    ) ~ '(国考|国家公务员|中央机关|中央、国家机关|中央国家机关)'
                    or coalesce(occurrence.source_path, '') ~
                       '(国考|国家公务员|中央机关|中央、国家机关|中央国家机关)'
                  )
              ) source
            ) occurrence on true
            where node.release_id = cast(:release_id as uuid)
            order by node.node_id, question.question_id
            """
        ),
        {"release_id": release_id},
    ).mappings().all()
    bound = 0
    for candidate in candidates:
        if candidate["question_id"] is None:
            continue
        bind_question_to_release_node(
            session,
            release_id=release_id,
            node_id=str(candidate["node_id"]),
            question_id=str(candidate["question_id"]),
            latest_recent_exam_year=candidate["latest_recent_exam_year"],
            recent_occurrence_count=int(candidate["recent_occurrence_count"]),
            latest_old_exam_year=candidate["latest_old_exam_year"],
            old_occurrence_count=int(candidate["old_occurrence_count"]),
        )
        bound += 1
    if bound == 0:
        first_node_id = next(
            str(candidate["node_id"]) for candidate in candidates
        )
        seed_minimal_release_binding(
            session,
            release_id=release_id,
            node_id=first_node_id,
            question_id=f"q_runtime_binding_{release_id}",
        )
    generated_at = session.execute(
        text(
            """
            select generated_at
            from product.knowledge_map_releases
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": release_id},
    ).scalar_one()
    activate_knowledge_release(
        session,
        release_id=release_id,
        activated_at=generated_at,
    )
    if status == "retired":
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'retired', retired_at = :retired_at
                where release_id = cast(:release_id as uuid)
                  and status = 'active'
                """
            ),
            {"release_id": release_id, "retired_at": NOW},
        )
    session.commit()
