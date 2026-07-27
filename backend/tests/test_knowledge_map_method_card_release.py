from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.community_method_cards.composition import (
    SqlValidationBindingAuthority,
    _node_version,
)
from app.domains.community_method_cards.schemas import Track
from app.domains.community_method_cards.service import CommunityMethodCardError
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    bind_question_to_release_node,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


def test_published_release_rejects_late_method_card_insert(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    release_id = _insert_release(
        db_session,
        generated_at=now - timedelta(hours=2),
        fresh_until=now + timedelta(days=30),
    )
    _insert_node(
        db_session,
        release_id=release_id,
        updated_at=now - timedelta(hours=1),
    )
    db_session.execute(
        text(
            """
            update product.knowledge_map_nodes
            set method_card_ids = '["mc_late_insert"]'::jsonb
            where release_id = cast(:release_id as uuid)
              and node_id = 'km_method_card_growth'
            """
        ),
        {"release_id": release_id},
    )
    _activate_release(
        db_session,
        release_id=release_id,
        activated_at=now - timedelta(minutes=30),
    )

    with pytest.raises(
        IntegrityError,
        match="method cards referenced by published knowledge releases are immutable",
    ):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    insert into product.method_cards (
                      id, exam_scope, track, module_name, tag_path, title,
                      recognition, applicability, method_steps, error_signatures,
                      fix_action, time_budget_sec, accuracy_rule, pitfall,
                      author_note, validation, priority, status
                    ) values (
                      'mc_late_insert', 'guokao', 'xingce', '资料分析',
                      '增长率', '迟到方法卡', '识别增长率题',
                      '{}'::jsonb, '["步骤"]'::jsonb, '[]'::jsonb,
                      '执行步骤', 60, '答案正确', null, 'test',
                      '{}'::jsonb, 3, 'published'
                    )
                    """
                )
            )


def test_method_card_binding_reads_only_the_fresh_active_release(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    retired_release_id = _insert_release(
        db_session,
        generated_at=now - timedelta(days=10),
        fresh_until=now - timedelta(days=2),
    )
    retired_updated_at = now - timedelta(days=8)
    active_updated_at = now - timedelta(hours=20)
    _insert_node(
        db_session,
        release_id=retired_release_id,
        updated_at=retired_updated_at,
    )
    _activate_release(
        db_session,
        release_id=retired_release_id,
        activated_at=now - timedelta(days=9),
        retired_at=now - timedelta(days=3),
    )
    active_release_id = _insert_release(
        db_session,
        generated_at=now - timedelta(days=1),
        fresh_until=now + timedelta(days=30),
    )
    _insert_node(
        db_session,
        release_id=active_release_id,
        updated_at=active_updated_at,
    )
    _activate_release(
        db_session,
        release_id=active_release_id,
        activated_at=now - timedelta(hours=23),
    )

    authority = SqlValidationBindingAuthority(db_session)

    direct = authority._published_node(
        track="xingce",
        module_name="资科分析",
        tag_value="增长率",
    )
    applicability = authority._unique_applicability_node(
        track=Track.XINGCE,
        values=["增长率"],
    )

    assert direct["updated_at"] == active_updated_at
    assert applicability["updated_at"] == active_updated_at
    assert str(direct["release_id"]) == active_release_id
    assert str(applicability["release_id"]) == active_release_id
    assert _node_version(direct).startswith(
        f"knowledge-map-release:knowledge-map@method-card-{active_release_id}/"
    )


def test_method_card_binding_does_not_fallback_to_a_stale_or_retired_release(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    retired_release_id = _insert_release(
        db_session,
        generated_at=now - timedelta(days=10),
        fresh_until=now - timedelta(days=2),
    )
    _insert_node(
        db_session,
        release_id=retired_release_id,
        updated_at=now - timedelta(days=8),
    )
    _activate_release(
        db_session,
        release_id=retired_release_id,
        activated_at=now - timedelta(days=9),
        retired_at=now - timedelta(days=3),
    )
    active_release_id = _insert_release(
        db_session,
        generated_at=now - timedelta(days=2),
        fresh_until=now - timedelta(hours=1),
    )
    _insert_node(
        db_session,
        release_id=active_release_id,
        updated_at=now - timedelta(days=1),
    )
    _activate_release(
        db_session,
        release_id=active_release_id,
        activated_at=now - timedelta(days=1),
    )

    authority = SqlValidationBindingAuthority(db_session)

    with pytest.raises(CommunityMethodCardError) as captured:
        authority._unique_applicability_node(
            track=Track.XINGCE,
            values=["增长率"],
        )

    assert captured.value.code == "knowledge_binding_ambiguous"


def test_method_card_binding_fails_closed_for_multiple_active_releases(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.execute(
        text("drop index product.product_knowledge_map_one_active_release_idx")
    )
    first_release_id = _insert_release(
        db_session,
        generated_at=now - timedelta(days=2),
        fresh_until=now + timedelta(days=10),
    )
    _insert_node(
        db_session,
        release_id=first_release_id,
        updated_at=now - timedelta(days=2),
    )
    _activate_release(
        db_session,
        release_id=first_release_id,
        activated_at=now - timedelta(days=2),
    )
    second_release_id = _insert_release(
        db_session,
        generated_at=now - timedelta(days=1),
        fresh_until=now + timedelta(days=20),
    )
    _insert_node(
        db_session,
        release_id=second_release_id,
        updated_at=now - timedelta(days=1),
    )
    _activate_release(
        db_session,
        release_id=second_release_id,
        activated_at=now - timedelta(days=1),
    )
    authority = SqlValidationBindingAuthority(db_session)

    with pytest.raises(CommunityMethodCardError) as direct:
        authority._published_node(
            track="xingce",
            module_name="资料分析",
            tag_value="增长率",
        )
    with pytest.raises(CommunityMethodCardError) as applicability:
        authority._unique_applicability_node(
            track=Track.XINGCE,
            values=["增长率"],
        )

    assert direct.value.code == "knowledge_binding_ambiguous"
    assert applicability.value.code == "knowledge_binding_ambiguous"


def test_canonical_question_binding_uses_frozen_release_content_not_live_tags(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    release_id = _insert_release(
        db_session,
        generated_at=now - timedelta(hours=2),
        fresh_until=now + timedelta(days=30),
    )
    _insert_node(
        db_session,
        release_id=release_id,
        updated_at=now - timedelta(hours=1),
    )
    db_session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type,
              stem, answer
            ) values (
              'q_method_card_v3_tag', 'objective', 'active', '资科分析',
              'question_type_is_not_a_v3_node', '测试题干', 'A'
            );
            insert into product.question_options (
              question_id, option_index, label, option_text
            ) values
              ('q_method_card_v3_tag', 1, 'A', '正确'),
              ('q_method_card_v3_tag', 2, 'B', '错误');
            insert into product.question_tags (
              question_tag_id, question_id, tag_type, tag_value, sort_order
            ) values (
              'qt_method_card_v3_tag', 'q_method_card_v3_tag',
              'knowledge_point', '增长率', 1
            )
            """
        )
    )
    content_version_id = bind_question_to_release_node(
        db_session,
        release_id=release_id,
        node_id="km_method_card_growth",
        question_id="q_method_card_v3_tag",
    )
    _activate_release(
        db_session,
        release_id=release_id,
        activated_at=now - timedelta(minutes=30),
    )
    db_session.execute(
        text(
            """
            delete from product.question_tags
            where question_id = 'q_method_card_v3_tag';
            update product.questions
            set module_name = '判断推理',
                question_type = 'live_taxonomy_drift',
                stem = '已漂移的题面'
            where question_id = 'q_method_card_v3_tag'
            """
        )
    )

    node = SqlValidationBindingAuthority(db_session)._published_question_node(
        question_id="q_method_card_v3_tag",
        content_version_id=content_version_id,
    )

    assert node["node_id"] == "km_method_card_growth"
    assert node["node_type"] == "knowledge_point"
    assert str(node["release_id"]) == release_id


def _insert_release(
    session: Session,
    *,
    generated_at: datetime,
    fresh_until: datetime,
) -> str:
    seed_released_module_vocabulary(
        session,
        version="vocab@method-card-test-v1",
        aliases={
            "言语理解": "verbal_comprehension",
            "逻辑判断": "judgment_reasoning",
            "资科分析": "data_analysis",
        },
    )
    release_id = str(uuid4())
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
              'vocab@method-card-test-v1',
              'method-card-test-generator@v1',
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
            "release_version": f"knowledge-map@method-card-{release_id}",
            "source_data_version": f"question-bank@{release_id}",
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
    updated_at: datetime,
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
              support_status,
              near5_occurrence_count,
              status,
              updated_at
            ) values (
              cast(:release_id as uuid),
              'km_method_card_growth',
              'guokao',
              'xingce',
              '资料分析',
              'knowledge_point',
              'knowledge_point',
              '增长率',
              '行测/资料分析/增长率',
              'text_supported',
              5,
              'published',
              :updated_at
            )
            """
        ),
        {
            "release_id": release_id,
            "updated_at": updated_at,
        },
    )


def _activate_release(
    session: Session,
    *,
    release_id: str,
    activated_at: datetime,
    retired_at: datetime | None = None,
) -> None:
    binding_count = session.execute(
        text(
            """
            select count(*)::integer
            from product.knowledge_map_question_bindings
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": release_id},
    ).scalar_one()
    if binding_count == 0:
        node_id = session.execute(
            text(
                """
                select node_id
                from product.knowledge_map_nodes
                where release_id = cast(:release_id as uuid)
                order by node_id
                limit 1
                """
            ),
            {"release_id": release_id},
        ).scalar_one()
        seed_minimal_release_binding(
            session,
            release_id=release_id,
            node_id=str(node_id),
            question_id=f"q_method_card_binding_{release_id}",
        )
    activate_knowledge_release(
        session,
        release_id=release_id,
        activated_at=activated_at,
    )
    if retired_at is not None:
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'retired', retired_at = :retired_at
                where release_id = cast(:release_id as uuid)
                  and status = 'active'
                """
            ),
            {"release_id": release_id, "retired_at": retired_at},
        )
