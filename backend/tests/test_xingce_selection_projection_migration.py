from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.test_knowledge_point_ability import _seed_xingce_assessments
from tests.test_xingce_targeted_knowledge_selection import (
    _node,
    _seed_targeted_learner,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class _OptionalMappingResult:
    def __init__(self, value: dict[str, bool] | None) -> None:
        self._value = value

    def mappings(self) -> _OptionalMappingResult:
        return self

    def one_or_none(self) -> dict[str, bool] | None:
        return self._value


def test_projection_indexes_are_built_without_table_write_locks() -> None:
    migration = import_module("migrations.versions.20260730_77_xingce_selection_projection")

    assert len(migration._CONCURRENT_INDEX_STATEMENTS) == 5
    assert all(
        "create index concurrently" in " ".join(statement.split()).lower()
        for statement in migration._CONCURRENT_INDEX_STATEMENTS
    )
    assert all(
        "if not exists" not in " ".join(statement.split()).lower()
        for statement in migration._CONCURRENT_INDEX_STATEMENTS
    )
    assert len(migration._CONCURRENT_INDEX_NAMES) == 5
    assert migration._BACKFILL_BATCH_SIZE == 500


def test_selection_projection_migration_is_forward_only() -> None:
    migration = import_module("migrations.versions.20260730_77_xingce_selection_projection")

    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


@pytest.mark.parametrize(
    ("index_state", "expected_statements"),
    [
        (None, ("create index concurrently test_idx on learning.test_table (id)",)),
        ({"indisvalid": True, "indisready": True}, ()),
        (
            {"indisvalid": False, "indisready": True},
            (
                'drop index concurrently if exists "learning"."test_idx"',
                "create index concurrently test_idx on learning.test_table (id)",
            ),
        ),
    ],
)
def test_concurrent_index_retry_rebuilds_invalid_indexes(
    monkeypatch: pytest.MonkeyPatch,
    index_state: dict[str, bool] | None,
    expected_statements: tuple[str, ...],
) -> None:
    migration = import_module("migrations.versions.20260730_77_xingce_selection_projection")

    class _IndexStateBind:
        def execute(
            self,
            _statement: object,
            _parameters: dict[str, str],
        ) -> _OptionalMappingResult:
            return _OptionalMappingResult(index_state)

    executed: list[str] = []
    monkeypatch.setattr(
        migration,
        "_CONCURRENT_INDEX_NAMES",
        (("learning", "test_idx"),),
    )
    monkeypatch.setattr(
        migration,
        "_CONCURRENT_INDEX_STATEMENTS",
        ("create index concurrently test_idx on learning.test_table (id)",),
    )
    monkeypatch.setattr(migration.op, "get_bind", _IndexStateBind)
    monkeypatch.setattr(migration.op, "execute", executed.append)

    migration._ensure_concurrent_indexes()

    assert tuple(executed) == expected_statements


def test_xingce_selection_projection_schema_uses_compatible_function_indexes(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory.begin() as session:
        selection_key_column_exists = bool(
            session.execute(
                text(
                    """
                    select exists (
                      select 1
                      from information_schema.columns
                      where table_schema = 'product'
                        and table_name = 'knowledge_map_question_bindings'
                        and column_name = 'selection_key'
                    )
                    """
                )
            ).scalar_one()
        )
        indexes = {
            str(row["indexname"]): str(row["indexdef"])
            for row in session.execute(
                text(
                    """
                    select indexname, indexdef
                    from pg_indexes
                    where schemaname in ('product', 'learning')
                      and indexname in (
                        'product_knowledge_map_binding_release_selection_idx',
                        'product_knowledge_map_binding_node_selection_idx',
                        'product_knowledge_map_binding_module_selection_idx_v2',
                        'learning_xingce_assessments_user_question_idx',
                        'learning_xingce_exposures_user_question_idx'
                      )
                    """
                )
            ).mappings()
        }
        index_states = {
            str(row["indexname"]): (
                bool(row["indisvalid"]),
                bool(row["indisready"]),
            )
            for row in session.execute(
                text(
                    """
                    select
                      relation.relname as indexname,
                      metadata.indisvalid,
                      metadata.indisready
                    from pg_catalog.pg_index metadata
                    join pg_catalog.pg_class relation
                      on relation.oid = metadata.indexrelid
                    join pg_catalog.pg_namespace namespace
                      on namespace.oid = relation.relnamespace
                    where namespace.nspname in ('product', 'learning')
                      and relation.relname in (
                        'product_knowledge_map_binding_release_selection_idx',
                        'product_knowledge_map_binding_node_selection_idx',
                        'product_knowledge_map_binding_module_selection_idx_v2',
                        'learning_xingce_assessments_user_question_idx',
                        'learning_xingce_exposures_user_question_idx'
                      )
                    """
                )
            ).mappings()
        }
        exposure_columns = {
            str(column_name)
            for column_name in session.execute(
                text(
                    """
                    select column_name
                    from information_schema.columns
                    where table_schema = 'learning'
                      and table_name = 'xingce_question_exposures'
                    """
                )
            ).scalars()
        }
        trigger_exists = bool(
            session.execute(
                text(
                    """
                    select exists (
                      select 1
                      from pg_trigger trigger
                      join pg_class relation
                        on relation.oid = trigger.tgrelid
                      join pg_namespace namespace
                        on namespace.oid = relation.relnamespace
                      where namespace.nspname = 'learning'
                        and relation.relname = 'xingce_task_sessions'
                        and trigger.tgname =
                          'xingce_task_session_capture_question_exposures'
                        and not trigger.tgisinternal
                    )
                    """
                )
            ).scalar_one()
        )

    assert not selection_key_column_exists
    assert set(indexes) == {
        "product_knowledge_map_binding_release_selection_idx",
        "product_knowledge_map_binding_node_selection_idx",
        "product_knowledge_map_binding_module_selection_idx_v2",
        "learning_xingce_assessments_user_question_idx",
        "learning_xingce_exposures_user_question_idx",
    }
    assert set(index_states) == set(indexes)
    assert all(state == (True, True) for state in index_states.values())
    assert all(
        "digest(question_id, 'sha256'::text)" in definition
        for name, definition in indexes.items()
        if name.startswith("product_")
    )
    assert exposure_columns == {
        "user_id",
        "session_id",
        "question_id",
        "exposed_at",
        "created_at",
    }
    assert trigger_exists


def test_projection_runtime_role_has_read_only_table_access(
    db_engine_at_revision_76: Engine,
) -> None:
    db_engine = db_engine_at_revision_76
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        db_engine.url.render_as_string(hide_password=False),
    )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                do $$
                begin
                  if not exists (
                    select 1 from pg_roles where rolname = 'gongkao_api'
                  ) then
                    create role gongkao_api nologin;
                  end if;
                end
                $$
                """
            )
        )
        connection.execute(
            text(
                """
                alter default privileges in schema learning
                  grant select, insert, update, delete on tables to gongkao_api
                """
            )
        )

    try:
        command.upgrade(config, "head")
        with db_engine.begin() as connection:
            privileges = {
                privilege
                for privilege in connection.execute(
                    text(
                        """
                        select privilege_type
                        from information_schema.role_table_grants
                        where grantee = 'gongkao_api'
                          and table_schema = 'learning'
                          and table_name = 'xingce_question_exposures'
                        """
                    )
                ).scalars()
            }
        assert privileges == {"SELECT"}
    finally:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    alter default privileges in schema learning
                      revoke select, insert, update, delete
                      on tables from gongkao_api
                    """
                )
            )
            connection.execute(
                text(
                    """
                    revoke all privileges
                    on learning.xingce_question_exposures
                    from gongkao_api
                    """
                )
            )
            connection.execute(text("drop owned by gongkao_api"))
            connection.execute(text("drop role if exists gongkao_api"))


def test_task_session_projection_upgrades_from_revision_76_and_remains_replaceable(
    db_engine_at_revision_76: Engine,
) -> None:
    db_engine = db_engine_at_revision_76
    db_session_factory = sessionmaker(
        bind=db_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    now = datetime.now(UTC)
    token = "selection-projection-migration-token"
    user_id = _seed_targeted_learner(
        db_session_factory,
        token=token,
        now=now,
        nodes=(
            _node(
                "selection-projection-node",
                "micro_skill",
                "micro_skill",
                "投影迁移",
                0.90,
                3,
            ),
        ),
        tags={"投影迁移": (1, 2, 3)},
        question_ids_by_index={
            1: "projection-question-1",
            2: "projection-question-2",
            3: "projection-question-3",
        },
        legacy_visual_schema=True,
    )
    _seed_xingce_assessments(
        db_session_factory,
        user_id=user_id,
        id_prefix="selection-projection-migration",
        rows=[
            ("projection-question-1", True),
            ("projection-question-2", False),
        ],
        legacy_capacity_schema=True,
    )
    safe_bundle = {
        "questions": [
            {"questionId": "projection-question-1"},
            {"questionId": "projection-question-2"},
            {"questionId": "projection-question-2"},
            {"questionId": "   "},
            {"other": "ignored"},
        ]
    }

    with db_session_factory.begin() as session:
        session_id = str(
            session.execute(
                text(
                    """
                    select session_id
                    from learning.xingce_task_sessions
                    where user_id = cast(:user_id as uuid)
                    order by created_at desc, session_id desc
                    limit 1
                    """
                ),
                {"user_id": user_id},
            )
            .scalars()
            .one()
        )
        session.execute(
            text(
                """
                update learning.xingce_task_sessions
                set safe_bundle = cast(:safe_bundle as jsonb)
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "safe_bundle": json.dumps(safe_bundle),
            },
        )
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        db_engine.url.render_as_string(hide_password=False),
    )
    with db_engine.begin() as connection:
        assert (
            connection.execute(
                text("select to_regclass('learning.xingce_question_exposures')")
            ).scalar_one()
            is None
        )
        persisted_bundle = connection.execute(
            text(
                """
                select safe_bundle
                from learning.xingce_task_sessions
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {"session_id": session_id, "user_id": user_id},
        ).scalar_one()
    assert persisted_bundle == safe_bundle

    command.upgrade(config, "head")
    with db_engine.begin() as connection:
        backfilled = tuple(
            connection.execute(
                text(
                    """
                    select question_id
                    from learning.xingce_question_exposures
                    where user_id = cast(:user_id as uuid)
                      and session_id = cast(:session_id as uuid)
                    order by question_id
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).scalars()
        )
        replacement_bundle = {
            "questions": [
                {"questionId": "projection-question-2"},
                {"questionId": "projection-question-3"},
            ]
        }
        connection.execute(
            text(
                """
                update learning.xingce_task_sessions
                set safe_bundle = cast(:safe_bundle as jsonb)
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {
                "safe_bundle": json.dumps(replacement_bundle),
                "session_id": session_id,
                "user_id": user_id,
            },
        )
        replaced = tuple(
            connection.execute(
                text(
                    """
                    select question_id
                    from learning.xingce_question_exposures
                    where user_id = cast(:user_id as uuid)
                      and session_id = cast(:session_id as uuid)
                    order by question_id
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).scalars()
        )
        connection.execute(
            text(
                """
                update learning.xingce_task_sessions
                set safe_bundle = '{}'::jsonb
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {"session_id": session_id, "user_id": user_id},
        )
        cleared_count = int(
            connection.execute(
                text(
                    """
                    select count(*)
                    from learning.xingce_question_exposures
                    where user_id = cast(:user_id as uuid)
                      and session_id = cast(:session_id as uuid)
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).scalar_one()
        )

    assert backfilled == (
        "projection-question-1",
        "projection-question-2",
    )
    assert replaced == (
        "projection-question-2",
        "projection-question-3",
    )
    assert cleared_count == 0
