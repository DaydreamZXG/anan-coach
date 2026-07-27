from __future__ import annotations

from importlib import import_module
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_v67_statements_replay_after_bootstrap_contract(
    db_engine: Engine,
) -> None:
    migration = import_module(
        "migrations.versions.20260727_67_external_question_knowledge_lineage"
    )
    with db_engine.begin() as connection:
        for statement in migration._STATEMENTS:
            connection.exec_driver_sql(statement)
        constraints = set(
            connection.execute(
                text(
                    """
                    select conname
                    from pg_constraint
                    where conrelid in (
                      'learner.external_question_intakes'::regclass,
                      'learner.external_wrong_questions'::regclass
                    )
                    """
                )
            ).scalars()
        )
    assert {
        "learner_external_intake_release_status_check",
        "learner_external_intake_release_node_fkey",
        "learner_external_intake_binding_key",
        "learner_external_wrong_intake_binding_fkey",
        "learner_external_wrong_release_node_fkey",
    } <= constraints


def test_v67_binds_only_rows_before_the_fixed_legacy_release_cutoff(
    db_engine: Engine,
) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        db_engine.url.render_as_string(hide_password=False),
    )
    with db_engine.connect() as connection:
        has_v68 = bool(
            connection.execute(
                text(
                    "select to_regclass("
                    "'product.knowledge_map_question_bindings'"
                    ") is not null"
                )
            ).scalar_one()
        )
    command.stamp(config, "20260727_68" if has_v68 else "20260727_67")
    command.downgrade(config, "20260727_64")

    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (
                  '00000000-0000-0000-0000-000000000761',
                  'external-lineage-migration-user'
                );

                insert into product.knowledge_map_nodes (
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  support_status,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status
                ) values (
                  'legacy-external-node',
                  'guokao',
                  'xingce',
                  '资料分析',
                  'knowledge_point',
                  'knowledge_point',
                  '增长率',
                  '行测/资料分析/增长率',
                  'text_supported',
                  true,
                  '[]'::jsonb,
                  '{"test":"v67-legacy-cutoff"}'::jsonb,
                  'published'
                );

                insert into learner.external_question_intakes (
                  intake_id,
                  user_id,
                  source,
                  stem,
                  selected_node_id,
                  status,
                  created_at,
                  confirmed_at
                ) values
                (
                  '00000000-0000-0000-0000-000000000762',
                  '00000000-0000-0000-0000-000000000761',
                  'external',
                  '迁移前已经确认且应绑定固定历史发布的题目。',
                  'legacy-external-node',
                  'confirmed',
                  statement_timestamp() - interval '1 day',
                  statement_timestamp() - interval '1 day'
                ),
                (
                  '00000000-0000-0000-0000-000000000763',
                  '00000000-0000-0000-0000-000000000761',
                  'external',
                  '晚于历史发布边界且没有精确发布血缘的题目。',
                  'legacy-external-node',
                  'pending_confirmation',
                  statement_timestamp() + interval '1 day',
                  null
                );

                insert into learner.external_wrong_questions (
                  external_question_id,
                  intake_id,
                  user_id,
                  node_id,
                  source,
                  stem,
                  error_summary
                ) values (
                  '00000000-0000-0000-0000-000000000764',
                  '00000000-0000-0000-0000-000000000762',
                  '00000000-0000-0000-0000-000000000761',
                  'legacy-external-node',
                  'external',
                  '迁移前已经确认且应绑定固定历史发布的题目。',
                  '把增长率和增长量混淆'
                );
                """
            )
        )

    command.upgrade(config, "20260727_66")
    with pytest.raises(
        DBAPIError,
        match="legacy knowledge binding is missing or ambiguous",
    ):
        command.upgrade(config, "20260727_67")

    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                delete from learner.external_question_intakes
                where intake_id =
                      '00000000-0000-0000-0000-000000000763'::uuid
                """
            )
        )
    command.upgrade(config, "20260727_67")

    with db_engine.connect() as connection:
        intake = connection.execute(
            text(
                """
                select
                  intake.knowledge_release_id,
                  release.release_version
                from learner.external_question_intakes intake
                join product.knowledge_map_releases release
                  on release.release_id = intake.knowledge_release_id
                where intake.intake_id =
                      '00000000-0000-0000-0000-000000000762'::uuid
                """
            )
        ).mappings().one()
        assert intake["release_version"] == (
            "knowledge-map@legacy-pre-20260727-65:guokao:xingce"
        )
        wrong_release_id = connection.execute(
            text(
                """
                select knowledge_release_id
                from learner.external_wrong_questions
                where external_question_id =
                      '00000000-0000-0000-0000-000000000764'::uuid
                """
            )
        ).scalar_one()
        assert wrong_release_id == intake["knowledge_release_id"]


def test_external_question_binding_constraints_reject_reinterpretation(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (
                  '00000000-0000-0000-0000-000000000771',
                  'external-lineage-constraint-user'
                );

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
                  '00000000-0000-0000-0000-000000000772',
                  'knowledge-map@external-lineage-constraint',
                  'guokao',
                  'xingce',
                  'questions@external-lineage-constraint',
                  'vocab@external-lineage-constraint',
                  'generator@external-lineage-constraint',
                  repeat('7', 64),
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
                  support_status,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status
                ) values (
                  '00000000-0000-0000-0000-000000000772',
                  'external-lineage-node',
                  'guokao',
                  'xingce',
                  '资料分析',
                  'knowledge_point',
                  'knowledge_point',
                  '增长率',
                  '行测/资料分析/增长率',
                  'text_supported',
                  true,
                  '[]'::jsonb,
                  '{}'::jsonb,
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
                  '00000000-0000-0000-0000-000000000773',
                  '00000000-0000-0000-0000-000000000771',
                  'external',
                  '约束测试必须保留最初解析时的发布血缘。',
                  '00000000-0000-0000-0000-000000000772',
                  'external-lineage-node',
                  'pending_confirmation'
                );
                """
            )
        )

    with pytest.raises(
        IntegrityError,
        match="resolved knowledge binding is immutable",
    ):
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    update learner.external_question_intakes
                    set selected_node_id = 'another-node'
                    where intake_id =
                          '00000000-0000-0000-0000-000000000773'::uuid
                    """
                )
            )
