from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


def test_binding_manifest_fingerprint_and_published_rows_are_immutable(
    db_session: Session,
) -> None:
    release_id = _seed_draft_release_with_binding(db_session)

    with pytest.raises(
        IntegrityError,
        match="knowledge-map question binding fingerprint is invalid",
    ):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    update product.knowledge_map_question_bindings
                    set delivery_eligible = false
                    where release_id = cast(:release_id as uuid)
                    """
                ),
                {"release_id": release_id},
            )

    db_session.execute(
        text(
            """
            update product.knowledge_map_releases
            set
              question_binding_count = 1,
              question_binding_fingerprint = repeat('f', 64)
            where release_id = cast(:release_id as uuid)
              and status = 'draft'
            """
        ),
        {"release_id": release_id},
    )
    with pytest.raises(
        IntegrityError,
        match="knowledge-map release question binding manifest is incomplete",
    ):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    update product.knowledge_map_releases
                    set status = 'active', node_count = 1,
                        activated_at = statement_timestamp()
                    where release_id = cast(:release_id as uuid)
                      and status = 'draft'
                    """
                ),
                {"release_id": release_id},
            )

    activate_knowledge_release(
        db_session,
        release_id=release_id,
        activated_at=datetime.now(UTC),
    )

    for mutation in (
        """
        update product.knowledge_map_question_bindings
        set delivery_eligible = false
        where release_id = cast(:release_id as uuid)
        """,
        """
        delete from product.knowledge_map_question_bindings
        where release_id = cast(:release_id as uuid)
        """,
    ):
        with pytest.raises(
            IntegrityError,
            match="active or retired knowledge-map question bindings are immutable",
        ):
            with db_session.begin_nested():
                db_session.execute(text(mutation), {"release_id": release_id})


def test_binding_rejects_self_hashed_semantic_authority_mismatch(
    db_session: Session,
) -> None:
    release_id = _seed_draft_release_with_binding(db_session)

    with pytest.raises(
        IntegrityError,
        match="knowledge-map question binding authority is invalid",
    ):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    with candidate as (
                      select
                        binding.*,
                        'political_theory'::text as invalid_module_code,
                        '政治理论'::text as invalid_canonical_module,
                        'micro_skill'::text as invalid_source_tag_type,
                        '完全不存在的标签'::text as invalid_source_tag_value
                      from product.knowledge_map_question_bindings binding
                      where binding.release_id = cast(:release_id as uuid)
                    )
                    update product.knowledge_map_question_bindings binding
                    set
                      module_code = candidate.invalid_module_code,
                      canonical_module = candidate.invalid_canonical_module,
                      source_tag_type = candidate.invalid_source_tag_type,
                      source_tag_value = candidate.invalid_source_tag_value,
                      binding_fingerprint =
                        product.knowledge_map_question_binding_row_fingerprint(
                          row(
                            candidate.release_id,
                            candidate.node_id,
                            candidate.question_id,
                            candidate.content_version_id,
                            candidate.invalid_module_code,
                            candidate.invalid_canonical_module,
                            candidate.binding_kind,
                            candidate.invalid_source_tag_type,
                            candidate.invalid_source_tag_value,
                            candidate.delivery_eligible,
                            candidate.visual_required,
                            candidate.shared_context_required,
                            candidate.latest_year,
                            candidate.occurrence_count,
                            candidate.latest_recent_exam_year,
                            candidate.recent_occurrence_count,
                            candidate.latest_old_exam_year,
                            candidate.old_occurrence_count,
                            repeat('0', 64),
                            candidate.created_at
                          )::product.knowledge_map_question_bindings
                        )
                    from candidate
                    where binding.release_id = candidate.release_id
                      and binding.node_id = candidate.node_id
                      and binding.question_id = candidate.question_id
                    """
                ),
                {"release_id": release_id},
            )


def test_v68_downgrade_restores_the_exact_v66_lifecycle_guard() -> None:
    v66 = import_module("migrations.versions.20260727_66_knowledge_release_immutability")
    v68 = import_module("migrations.versions.20260727_68_knowledge_map_question_bindings")
    v66_function = next(
        statement
        for statement in v66._STATEMENTS
        if "create function product.guard_knowledge_map_release_lifecycle()" in statement
    )
    restored_function = v68._RESTORE_V66_LIFECYCLE.replace(
        "create or replace function",
        "create function",
        1,
    )
    v66_trigger = next(
        statement
        for statement in v66._STATEMENTS
        if "create trigger knowledge_map_releases_lifecycle_guard" in statement
    )

    assert _normalized_sql(restored_function) == _normalized_sql(v66_function)
    assert _normalized_sql(v68._DOWN_STATEMENTS[2]) == _normalized_sql(v66_trigger)


def _seed_draft_release_with_binding(session: Session) -> str:
    now = datetime.now(UTC)
    release_id = str(uuid4())
    vocab_version = f"vocab@binding-test-{release_id}"
    seed_released_module_vocabulary(session, version=vocab_version)
    session.execute(
        text(
            """
            insert into product.knowledge_map_releases (
              release_id, release_version, exam_scope, track,
              source_data_version, vocab_version, generator_version,
              source_fingerprint, generated_at, fresh_until, status,
              node_count
            ) values (
              cast(:release_id as uuid), :release_version, 'guokao', 'xingce',
              'question-bank@binding-test', :vocab_version,
              'generator@binding-test', :source_fingerprint,
              :generated_at, :fresh_until, 'draft', 0
            )
            """
        ),
        {
            "release_id": release_id,
            "release_version": f"knowledge-map@binding-test-{release_id}",
            "vocab_version": vocab_version,
            "source_fingerprint": release_id.replace("-", "") * 2,
            "generated_at": now - timedelta(minutes=5),
            "fresh_until": now + timedelta(days=7),
        },
    )
    session.execute(
        text(
            """
            insert into product.knowledge_map_nodes (
              release_id, node_id, exam_scope, track, module_name,
              node_type, tag_type, tag_value, display_path,
              support_status, near5_occurrence_count, method_card_ids,
              status
            ) values (
              cast(:release_id as uuid), 'km_binding_test',
              'guokao', 'xingce', '资料分析', 'knowledge_point',
              'knowledge_point', '增长率', '行测/资料分析/增长率',
              'text_supported', 1, '[]'::jsonb, 'published'
            )
            """
        ),
        {"release_id": release_id},
    )
    seed_minimal_release_binding(
        session,
        release_id=release_id,
        node_id="km_binding_test",
        question_id=f"q_binding_test_{release_id}",
    )
    return release_id


def _normalized_sql(statement: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in statement.strip().splitlines() if line.strip())
