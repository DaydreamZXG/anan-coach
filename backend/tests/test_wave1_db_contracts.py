from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domains.method_cards.repository import MethodCardRecord, MethodCardRepository
from tests.knowledge_map_vocabulary_helpers import (
    activate_knowledge_release,
    seed_minimal_release_binding,
    seed_released_module_vocabulary,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WAVE1_TABLES = (
    "learning.xingce_commands",
    "messaging.ilink_object_deletion_queue",
    "platform.background_jobs",
    "product.method_cards",
)


def test_wave1_revisions_are_linear_and_single_head() -> None:
    revision71 = import_module("migrations.versions.20260728_71_review_verdict_command_contract")
    revision72 = import_module("migrations.versions.20260728_72_job_lease_contract")
    revision73 = import_module("migrations.versions.20260728_73_method_card_exact_replay")
    revision74 = import_module("migrations.versions.20260728_74_xingce_question_favorites")
    revision75 = import_module("migrations.versions.20260729_75_xingce_delivery_selection_index")
    revision76 = import_module("migrations.versions.20260729_76_optional_reflection_text")
    revision77 = import_module("migrations.versions.20260730_77_xingce_selection_projection")
    script = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))

    assert revision71.down_revision == "20260728_70"
    assert revision72.down_revision == "20260728_71"
    assert revision73.down_revision == "20260728_72"
    assert revision74.down_revision == "20260728_73"
    assert revision75.down_revision == "20260728_74"
    assert revision76.down_revision == "20260729_75"
    assert revision77.down_revision == "20260729_76"
    assert script.get_heads() == ["20260730_77"]


def test_xingce_delivery_selection_has_release_question_covering_index(
    db_engine: Engine,
) -> None:
    indexes = {
        item["name"]: item
        for item in inspect(db_engine).get_indexes(
            "knowledge_map_question_bindings",
            schema="product",
        )
    }
    index = indexes["product_knowledge_map_binding_baseline_selection_idx"]
    assert index["column_names"] == ["release_id", "question_id"]
    assert "delivery_eligible" in str(index["dialect_options"]["postgresql_where"])


def test_review_verdict_command_extends_receipts_without_weakening_append_only(
    db_engine: Engine,
) -> None:
    user_id = str(uuid4())
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), 'wave1 command owner')"
            ),
            {"user_id": user_id},
        )
        for command_id, command_type in (
            ("wave1-review-verdict-command", "review_verdict"),
            ("wave1-session-start-command", "session_start"),
            ("wave1-checkpoint-save-command", "checkpoint_save"),
            ("wave1-attempt-submit-command", "attempt_submit"),
            ("wave1-reflection-submit-command", "reflection_submit"),
            ("wave1-cause-followup-command", "cause_followup"),
            ("wave1-cause-decision-command", "cause_decision"),
        ):
            connection.execute(
                text(
                    """
                    insert into learning.xingce_commands (
                      user_id, command_id, command_type, target_id,
                      request_fingerprint, response_payload, created_at
                    ) values (
                      cast(:user_id as uuid), :command_id, :command_type, null,
                      repeat('a', 64), jsonb_build_object('accepted', true), now()
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "command_id": command_id,
                    "command_type": command_type,
                },
            )

    command_columns = {
        column["name"]
        for column in inspect(db_engine).get_columns(
            "xingce_commands",
            schema="learning",
        )
    }
    assert command_columns == {
        "user_id",
        "command_id",
        "command_type",
        "target_id",
        "request_fingerprint",
        "response_payload",
        "created_at",
    }
    assert inspect(db_engine).get_pk_constraint(
        "xingce_commands",
        schema="learning",
    )["constrained_columns"] == ["user_id", "command_id"]

    with db_engine.begin() as connection:
        with pytest.raises(IntegrityError) as unknown:
            connection.execute(
                text(
                    """
                    insert into learning.xingce_commands (
                      user_id, command_id, command_type, target_id,
                      request_fingerprint, response_payload, created_at
                    ) values (
                      cast(:user_id as uuid), 'wave1-unknown-command',
                      'unknown_command', null, repeat('b', 64), '{}'::jsonb, now()
                    )
                    """
                ),
                {"user_id": user_id},
            )
    assert _sqlstate(unknown.value) == "23514"

    for statement in (
        """
        update learning.xingce_commands
        set response_payload = jsonb_build_object('accepted', false)
        where user_id = cast(:user_id as uuid)
          and command_id = 'wave1-review-verdict-command'
        """,
        """
        delete from learning.xingce_commands
        where user_id = cast(:user_id as uuid)
          and command_id = 'wave1-review-verdict-command'
        """,
    ):
        with db_engine.begin() as connection:
            with pytest.raises(IntegrityError) as append_only:
                connection.execute(text(statement), {"user_id": user_id})
        assert _sqlstate(append_only.value) == "23514"

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        db_engine.url.render_as_string(hide_password=False),
    )
    with pytest.raises(RuntimeError, match="review-verdict receipts exist"):
        command.downgrade(config, "20260728_70")
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                    select count(*)
                    from learning.xingce_commands
                    where user_id = cast(:user_id as uuid)
                    """
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 7
        )


def test_job_lease_schema_preserves_legacy_nulls_and_fences_new_leases(
    db_engine: Engine,
) -> None:
    legacy_job_id = str(uuid4())
    valid_job_id = str(uuid4())
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into platform.background_jobs (
                  job_id, job_key, job_type, input_version, payload, status,
                  attempt_count, max_attempts, run_after, claimed_at,
                  claim_token, lease_expires_at
                ) values
                (
                  cast(:legacy_job_id as uuid), 'wave1-legacy-running',
                  'data_rights_export', 'v1', '{}'::jsonb, 'running',
                  1, 3, now(), null, null, null
                ),
                (
                  cast(:valid_job_id as uuid), 'wave1-fenced-running',
                  'data_rights_export', 'v1', '{}'::jsonb, 'running',
                  1, 3, now(), now(), cast(:claim_token as uuid),
                  now() + interval '5 minutes'
                )
                """
            ),
            {
                "legacy_job_id": legacy_job_id,
                "valid_job_id": valid_job_id,
                "claim_token": str(uuid4()),
            },
        )
        connection.execute(
            text(
                """
                insert into messaging.ilink_object_deletion_queue (
                  object_ref, state, attempt_count, failure_code,
                  claim_token, claimed_at, lease_expires_at,
                  created_at, updated_at
                ) values
                  ('wave1-legacy-deleting', 'deleting', 1, null,
                    null, null, null, now(), now()),
                  ('wave1-old-pending', 'pending', 0, null,
                    null, null, null, now(), now()),
                  ('wave1-old-failed', 'failed', 1, 'transient',
                    null, null, null, now(), now())
                """
            )
        )
        connection.execute(
            text(
                """
                update messaging.ilink_object_deletion_queue
                set
                  state = 'deleting',
                  claim_token = cast(:claim_token as uuid),
                  claimed_at = now(),
                  lease_expires_at = now() + interval '5 minutes',
                  updated_at = now()
                where object_ref in ('wave1-old-pending', 'wave1-old-failed')
                """
            ),
            {"claim_token": str(uuid4())},
        )

    with db_engine.connect() as connection:
        legacy_background = connection.execute(
            text(
                """
                select claim_token, claimed_at, lease_expires_at
                from platform.background_jobs
                where job_id = cast(:job_id as uuid)
                """
            ),
            {"job_id": legacy_job_id},
        ).one()
        legacy_ilink = connection.execute(
            text(
                """
                select claim_token, claimed_at, lease_expires_at
                from messaging.ilink_object_deletion_queue
                where object_ref = 'wave1-legacy-deleting'
                """
            )
        ).one()
        indexes = {
            row[0]
            for row in connection.execute(
                text(
                    """
                    select schemaname || '.' || indexname
                    from pg_indexes
                    where indexname in (
                      'platform_background_jobs_lease_recovery_idx',
                      'ilink_object_deletion_lease_recovery_idx'
                    )
                    """
                )
            )
        }
    assert tuple(legacy_background) == (None, None, None)
    assert tuple(legacy_ilink) == (None, None, None)
    assert indexes == {
        "messaging.ilink_object_deletion_lease_recovery_idx",
        "platform.platform_background_jobs_lease_recovery_idx",
    }

    invalid_writes = (
        """
        update platform.background_jobs
        set lease_expires_at = now() + interval '5 minutes'
        where job_id = cast(:row_id as uuid)
        """,
        """
        update messaging.ilink_object_deletion_queue
        set lease_expires_at = now() + interval '5 minutes'
        where object_ref = :row_id
        """,
    )
    for statement, row_id in zip(
        invalid_writes,
        (legacy_job_id, "wave1-legacy-deleting"),
        strict=True,
    ):
        with db_engine.begin() as connection:
            with pytest.raises(IntegrityError) as unfenced:
                connection.execute(text(statement), {"row_id": row_id})
        assert _sqlstate(unfenced.value) == "23514"

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        db_engine.url.render_as_string(hide_password=False),
    )
    with pytest.raises(RuntimeError, match="claim or lease facts exist"):
        command.downgrade(config, "20260728_71")
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                select count(*)
                from messaging.ilink_object_deletion_queue
                where object_ref like 'wave1-%'
                """
                )
            ).scalar_one()
            == 3
        )
        assert (
            connection.execute(
                text(
                    """
                select count(*)
                from platform.background_jobs
                where job_key like 'wave1-%'
                """
                )
            ).scalar_one()
            == 2
        )


def test_shadow_schema_matches_real_wave1_migration_round_trip(
    db_engine: Engine,
) -> None:
    expected = _wave1_schema_fingerprint(db_engine)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        db_engine.url.render_as_string(hide_password=False),
    )

    command.downgrade(config, "20260728_70")
    command.upgrade(config, "head")

    assert _wave1_schema_fingerprint(db_engine) == expected


@pytest.mark.parametrize("release_status", ("active", "retired"))
def test_active_and_retired_method_cards_allow_exact_replay_without_timestamp_change(
    db_session: Session,
    release_status: str,
) -> None:
    card = _method_card(f"wave1-replay-{release_status}")
    release_id = _seed_referenced_method_card(
        db_session,
        card=card,
        release_status=release_status,
    )
    repository = MethodCardRepository(db_session)
    original_updated_at = db_session.execute(
        text("select updated_at from product.method_cards where id = :card_id"),
        {"card_id": card.id},
    ).scalar_one()

    assert repository.upsert_cards([card]) == 1

    replayed_updated_at = db_session.execute(
        text("select updated_at from product.method_cards where id = :card_id"),
        {"card_id": card.id},
    ).scalar_one()
    assert replayed_updated_at == original_updated_at
    assert (
        db_session.execute(
            text(
                "select status from product.knowledge_map_releases "
                "where release_id = cast(:release_id as uuid)"
            ),
            {"release_id": release_id},
        ).scalar_one()
        == release_status
    )


def test_referenced_method_card_drift_update_and_delete_remain_fail_closed(
    db_session: Session,
) -> None:
    card = _method_card("wave1-replay-immutable")
    _seed_referenced_method_card(db_session, card=card, release_status="active")
    repository = MethodCardRepository(db_session)

    with pytest.raises(IntegrityError) as drift:
        repository.upsert_cards([replace(card, fix_action="drift must fail")])
    assert _sqlstate(drift.value) == "23514"
    db_session.rollback()

    for statement in (
        "update product.method_cards set priority = 4 where id = :card_id",
        "delete from product.method_cards where id = :card_id",
    ):
        with pytest.raises(IntegrityError) as immutable:
            db_session.execute(text(statement), {"card_id": card.id})
            db_session.commit()
        assert _sqlstate(immutable.value) == "23514"
        db_session.rollback()


def test_unreferenced_draft_method_card_remains_mutable(db_session: Session) -> None:
    card = replace(
        _method_card("wave1-unreferenced-draft"),
        status="draft",
    )
    repository = MethodCardRepository(db_session)

    assert repository.upsert_cards([card]) == 1
    assert repository.upsert_cards([replace(card, priority=4)]) == 1

    stored_priority = db_session.execute(
        text("select priority from product.method_cards where id = :card_id"),
        {"card_id": card.id},
    ).scalar_one()
    assert stored_priority == 4


def test_concurrent_exact_method_card_replay_converges(db_engine: Engine) -> None:
    factory = sessionmaker(bind=db_engine, expire_on_commit=False, future=True)
    card = _method_card("wave1-concurrent-replay")
    with factory() as session:
        _seed_referenced_method_card(
            session,
            card=card,
            release_status="active",
        )
        original_updated_at = session.execute(
            text("select updated_at from product.method_cards where id = :card_id"),
            {"card_id": card.id},
        ).scalar_one()

    def replay() -> int:
        with factory() as session:
            return MethodCardRepository(session).upsert_cards([card])

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: replay(), range(8)))

    with factory() as session:
        rows = session.execute(
            text(
                """
                select count(*), min(updated_at), max(updated_at)
                from product.method_cards
                where id = :card_id
                """
            ),
            {"card_id": card.id},
        ).one()
    assert results == [1] * 8
    assert rows == (1, original_updated_at, original_updated_at)


def _seed_referenced_method_card(
    session: Session,
    *,
    card: MethodCardRecord,
    release_status: str,
) -> str:
    MethodCardRepository(session).upsert_cards([card])
    now = datetime.now(UTC)
    release_id = str(uuid4())
    node_id = f"node-{card.id}"
    vocab_version = f"vocab@{card.id}"
    seed_released_module_vocabulary(session, version=vocab_version)
    session.execute(
        text(
            """
            insert into product.knowledge_map_releases (
              release_id, release_version, exam_scope, track,
              source_data_version, vocab_version, generator_version,
              source_fingerprint, generated_at, fresh_until, status, node_count
            ) values (
              cast(:release_id as uuid), :release_version, 'guokao', 'xingce',
              :source_version, :vocab_version, 'wave1-db-contracts@v1',
              :fingerprint, :generated_at, :fresh_until, 'draft', 0
            )
            """
        ),
        {
            "release_id": release_id,
            "release_version": f"knowledge-map@{card.id}",
            "source_version": f"question-bank@{card.id}",
            "vocab_version": vocab_version,
            "fingerprint": release_id.replace("-", "") * 2,
            "generated_at": now - timedelta(hours=1),
            "fresh_until": now + timedelta(days=30),
        },
    )
    session.execute(
        text(
            """
            insert into product.knowledge_map_nodes (
              release_id, node_id, exam_scope, track, module_name,
              node_type, tag_type, tag_value, display_path, support_status,
              method_card_ids, status
            ) values (
              cast(:release_id as uuid), :node_id, 'guokao', 'xingce',
              '资料分析', 'knowledge_point', 'knowledge_point', '增长率',
              '行测/资料分析/增长率', 'text_supported',
              jsonb_build_array(cast(:card_id as text)), 'published'
            )
            """
        ),
        {
            "release_id": release_id,
            "node_id": node_id,
            "card_id": card.id,
        },
    )
    seed_minimal_release_binding(
        session,
        release_id=release_id,
        node_id=node_id,
        question_id=f"question-{card.id}",
    )
    activate_knowledge_release(
        session,
        release_id=release_id,
        activated_at=now,
    )
    if release_status == "retired":
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'retired', retired_at = :retired_at
                where release_id = cast(:release_id as uuid)
                """
            ),
            {
                "release_id": release_id,
                "retired_at": now + timedelta(seconds=1),
            },
        )
    session.commit()
    return release_id


def _method_card(card_id: str) -> MethodCardRecord:
    return MethodCardRecord(
        id=card_id,
        exam_scope="guokao",
        track="xingce",
        module_name="资料分析",
        tag_path="增长率",
        title="增长率识别",
        recognition="看到增长与基期关系时使用。",
        applicability={"modules": ["资料分析"]},
        method_steps=["识别口径", "代入计算"],
        error_signatures=[{"code": "base_period", "description": "基期混淆"}],
        fix_action="先标出现期与基期，再代入公式。",
        time_budget_sec=90,
        accuracy_rule="单位和时期均一致。",
        pitfall="不要混用同比与环比。",
        author_note="Wave 1 数据库合同测试",
        validation={"status": "verified"},
        priority=3,
        status="published",
    )


def _wave1_schema_fingerprint(engine: Engine) -> tuple[tuple[object, ...], ...]:
    with engine.connect() as connection:
        return tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    with target_tables as (
                      select
                        namespace.oid as namespace_oid,
                        relation.oid as relation_oid,
                        namespace.nspname as schema_name,
                        relation.relname as table_name
                      from pg_class relation
                      join pg_namespace namespace
                        on namespace.oid = relation.relnamespace
                      where namespace.nspname || '.' || relation.relname =
                        any(:table_names)
                    ), objects as (
                      select
                        'column' as object_kind,
                        target.schema_name,
                        target.table_name,
                        attribute.attname as object_name,
                        concat_ws(
                          '|',
                          format_type(attribute.atttypid, attribute.atttypmod),
                          attribute.attnotnull::text,
                          coalesce(
                            pg_get_expr(default_value.adbin, default_value.adrelid),
                            ''
                          )
                        ) as definition
                      from target_tables target
                      join pg_attribute attribute
                        on attribute.attrelid = target.relation_oid
                       and attribute.attnum > 0
                       and not attribute.attisdropped
                      left join pg_attrdef default_value
                        on default_value.adrelid = attribute.attrelid
                       and default_value.adnum = attribute.attnum
                      union all
                      select
                        'constraint',
                        target.schema_name,
                        target.table_name,
                        constraint_def.conname,
                        pg_get_constraintdef(constraint_def.oid, true)
                      from target_tables target
                      join pg_constraint constraint_def
                        on constraint_def.conrelid = target.relation_oid
                      union all
                      select
                        'index',
                        target.schema_name,
                        target.table_name,
                        index_relation.relname,
                        pg_get_indexdef(index_def.indexrelid)
                      from target_tables target
                      join pg_index index_def
                        on index_def.indrelid = target.relation_oid
                      join pg_class index_relation
                        on index_relation.oid = index_def.indexrelid
                    )
                    select
                      object_kind, schema_name, table_name, object_name, definition
                    from objects
                    union all
                    select
                      'function', 'product',
                      'guard_released_method_card_mutation',
                      'guard_released_method_card_mutation',
                      pg_get_functiondef(
                        'product.guard_released_method_card_mutation()'::regprocedure
                      )
                    order by 1, 2, 3, 4, 5
                    """
                ),
                {"table_names": list(WAVE1_TABLES)},
            )
        )


def _sqlstate(error: IntegrityError) -> str | None:
    return getattr(error.orig, "sqlstate", None)
