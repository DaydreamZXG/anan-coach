from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.memory.repository import LearnerMemoryRepository
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


def test_external_wrong_workbench_keeps_its_frozen_release_after_expiry(
    db_session: Session,
) -> None:
    seed_released_module_vocabulary(
        db_session,
        version="exam-tag-vocab@memory-expiry-test",
    )
    db_session.execute(
        text(
            """
            insert into learner.users (user_id, display_name)
            values ('00000000-0000-0000-0000-000000000711', 'memory-release-user');

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
              node_count
            ) values (
              '00000000-0000-0000-0000-000000000712',
              'knowledge-map@memory-expiry-test',
              'guokao',
              'xingce',
              'question-catalog@memory-expiry-test',
              'exam-tag-vocab@memory-expiry-test',
              'refresh_knowledge_map_nodes_v4',
              repeat('7', 64),
              transaction_timestamp() - interval '1 day',
              transaction_timestamp() + interval '1 second',
              'draft',
              0
            );

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
              stable_for_old_questions,
              method_card_ids,
              evidence,
              status
            ) values (
              '00000000-0000-0000-0000-000000000712',
              'memory-expiry-node',
              'guokao',
              'xingce',
              '资料分析',
              'knowledge_point',
              'knowledge_point',
              '增长率',
              '行测/资料分析/增长率',
              '[]'::jsonb,
              'text_supported',
              true,
              '[]'::jsonb,
              '{"test":"memory-release-expiry"}'::jsonb,
              'published'
            );

            insert into learner.external_question_intakes (
              intake_id,
              user_id,
              source,
              stem,
              knowledge_release_id,
              selected_node_id,
              status
            ) values (
              '00000000-0000-0000-0000-000000000713',
              '00000000-0000-0000-0000-000000000711',
              'external',
              '增长率测试题',
              '00000000-0000-0000-0000-000000000712',
              'memory-expiry-node',
              'confirmed'
            );

            insert into learner.external_wrong_questions (
              external_question_id,
              intake_id,
              user_id,
              knowledge_release_id,
              node_id,
              source,
              stem,
              error_summary
            ) values (
              '00000000-0000-0000-0000-000000000714',
              '00000000-0000-0000-0000-000000000713',
              '00000000-0000-0000-0000-000000000711',
              '00000000-0000-0000-0000-000000000712',
              'memory-expiry-node',
              'external',
              '增长率测试题',
              '混淆了现期量与基期量'
            );
            """
        )
    )
    seed_minimal_release_binding(
        db_session,
        release_id="00000000-0000-0000-0000-000000000712",
        node_id="memory-expiry-node",
        question_id="q-memory-expiry-node",
    )
    activate_knowledge_release(
        db_session,
        release_id="00000000-0000-0000-0000-000000000712",
    )
    repository = LearnerMemoryRepository(db_session)

    assert len(
        repository._list_external_wrong_workbench_objects(
            user_id="00000000-0000-0000-0000-000000000711",
            limit=12,
        )
    ) == 1

    db_session.execute(text("select pg_sleep(1.1)"))

    after_expiry = repository._list_external_wrong_workbench_objects(
        user_id="00000000-0000-0000-0000-000000000711",
        limit=12,
    )
    assert len(after_expiry) == 1
    assert (
        after_expiry[0].payload["knowledgeReleaseId"]
        == "00000000-0000-0000-0000-000000000712"
    )

    seed_released_module_vocabulary(
        db_session,
        version="exam-tag-vocab@memory-next-test",
    )
    db_session.execute(
        text(
            """
            update product.knowledge_map_releases
            set
              status = 'retired',
              retired_at = statement_timestamp()
            where release_id = '00000000-0000-0000-0000-000000000712'
              and status = 'active';

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
              node_count
            ) values (
              '00000000-0000-0000-0000-000000000715',
              'knowledge-map@memory-next-test',
              'guokao',
              'xingce',
              'question-catalog@memory-next-test',
              'exam-tag-vocab@memory-next-test',
              'refresh_knowledge_map_nodes_v4',
              repeat('8', 64),
              statement_timestamp() - interval '1 hour',
              statement_timestamp() + interval '7 days',
              'draft',
              0
            );

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
              stable_for_old_questions,
              method_card_ids,
              evidence,
              status
            ) values (
              '00000000-0000-0000-0000-000000000715',
              'memory-expiry-node',
              'guokao',
              'xingce',
              '资料分析',
              'knowledge_point',
              'knowledge_point',
              '增长率',
              '新版本/资料分析/增长率（不得覆盖历史）',
              '[]'::jsonb,
              'text_supported',
              true,
              '[]'::jsonb,
              '{"test":"memory-next-release"}'::jsonb,
              'published'
            );

            """
        )
    )
    seed_minimal_release_binding(
        db_session,
        release_id="00000000-0000-0000-0000-000000000715",
        node_id="memory-expiry-node",
        question_id="q-memory-next-node",
    )
    activate_knowledge_release(
        db_session,
        release_id="00000000-0000-0000-0000-000000000715",
    )

    after_switch = repository._list_external_wrong_workbench_objects(
        user_id="00000000-0000-0000-0000-000000000711",
        limit=12,
    )
    assert len(after_switch) == 1
    assert after_switch[0].payload["knowledgeReleaseId"] == (
        "00000000-0000-0000-0000-000000000712"
    )
    assert after_switch[0].payload["knowledgeReleaseVersion"] == (
        "knowledge-map@memory-expiry-test"
    )
    assert after_switch[0].payload["displayPath"] == (
        "行测/资料分析/增长率"
    )
