from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
import json
from pathlib import Path
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.data_rights.repository import DELETION_JOB_TYPE, EXPORT_JOB_TYPE
from app.domains.operations import router as operations_router
from app.domains.xingce_learning.cause_agent.service import CAUSE_AGENT_JOB_TYPE
from app.platform.jobs import (
    JOB_CONSUMER_HEARTBEAT_TTL,
    JOB_REGISTRY,
    BackgroundJobRepository,
    JobConsumerHeartbeatRuntime,
    JobContractError,
    job_registration_enabled,
    validate_job_registry,
)
from app.platform.job_consumer_principals import (
    JOB_CONSUMER_PRINCIPALS,
)
from tests.heartbeat_principal_helpers import (
    principal_session_factory,
    record_heartbeat_as_principal,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVATE_SCRIPT = REPO_ROOT / "ops" / "scripts" / "activate_release.sh"
SYSTEMD_ROOT = REPO_ROOT / "ops" / "systemd"
APP_ROOT = REPO_ROOT / "backend" / "app"
API_SERVICE = SYSTEMD_ROOT / "gongkao-dazi-api.service"
HEARTBEAT_ROLE_PROVISIONING = REPO_ROOT / "ops" / "sql" / "provision_worker_heartbeat_roles.sql"
HEARTBEAT_ENV_BY_UNIT = {
    "gongkao-dazi-data-rights-worker": "worker-heartbeat-data-rights.env",
    "gongkao-dazi-cause-agent-worker": "worker-heartbeat-cause-agent.env",
    "gongkao-dazi-external-learning-worker": ("worker-heartbeat-external-learning.env"),
    "gongkao-dazi-essay-review-worker": "worker-heartbeat-essay-review.env",
    "gongkao-dazi-coach-notification-worker": ("worker-heartbeat-coach-notification.env"),
    "gongkao-dazi-ilink-object-deletion-worker": "worker-heartbeat-ilink.env",
}
PLATFORM_JOB_TYPES = {
    "personal_data_export_v1",
    "account_erasure_v1",
    "xingce_cause_agent_v1",
    "learning_media_scan_v1",
    "learning_media_retention_cleanup_v1",
    "offline_packet_render_v1",
    "essay_grounded_review_v2",
    "external_question_answer_v1",
    "private_knowledge_source_delete@v1",
    "private_knowledge_weknora_index@v1",
    "coach_notification_send_v1",
    "community_method_card_candidate_v2",
    "community_method_card_review_v2",
}


def test_registry_binds_each_scoped_producer_to_one_consumer_and_recovery_owner() -> None:
    validate_job_registry()
    identities = {(registration.queue_name, registration.job_type) for registration in JOB_REGISTRY}

    assert len(identities) == len(JOB_REGISTRY)
    assert PLATFORM_JOB_TYPES == {
        registration.job_type
        for registration in JOB_REGISTRY
        if registration.queue_name == "platform.background_jobs"
    }
    assert {
        EXPORT_JOB_TYPE,
        DELETION_JOB_TYPE,
        CAUSE_AGENT_JOB_TYPE,
    } < PLATFORM_JOB_TYPES
    assert {registration.job_type for registration in JOB_REGISTRY} == {
        *PLATFORM_JOB_TYPES,
        "ilink_object_deletion_v1",
    }
    assert all(
        len(registration.consumer_units) == 1
        for registration in JOB_REGISTRY
        if registration.deployment_state == "deployed"
    )
    assert all(
        not registration.consumer_units
        for registration in JOB_REGISTRY
        if registration.deployment_state == "disabled"
    )
    assert all(registration.producer for registration in JOB_REGISTRY)
    assert all(registration.recovery_owner for registration in JOB_REGISTRY)
    learning_media = tuple(
        registration
        for registration in JOB_REGISTRY
        if registration.job_type.startswith("learning_media_")
    )
    assert {
        (registration.consumer_unit, registration.consumer_entrypoint)
        for registration in learning_media
    } == {
        (
            "gongkao-dazi-external-learning-worker",
            "app.workers.external_learning",
        )
    }
    assert not tuple(SYSTEMD_ROOT.glob("*learning-media*"))


def test_registry_rejects_duplicate_or_multi_consumer_bindings() -> None:
    duplicate = (*JOB_REGISTRY, JOB_REGISTRY[0])
    with pytest.raises(JobContractError, match="Duplicate job registration"):
        validate_job_registry(duplicate)

    invalid = (
        replace(
            JOB_REGISTRY[0],
            consumer_units=("worker-a", "worker-b"),
        ),
    )
    with pytest.raises(JobContractError, match="exactly one deployed consumer"):
        validate_job_registry(invalid)


def test_consumer_heartbeat_migration_is_forward_only_and_grants_runtime_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module("migrations.versions.20260730_80_job_consumer_heartbeats")
    statements: list[str] = []
    monkeypatch.setattr(migration, "_runtime_role_exists", lambda: True)
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert migration.down_revision == "20260730_79"
    assert any(
        "create table if not exists platform.background_job_consumer_heartbeats" in statement
        for statement in statements
    )
    assert "revoke all on platform.background_job_consumer_heartbeats from public" in statements
    assert (
        "grant select, insert, update "
        "on platform.background_job_consumer_heartbeats to gongkao_api" in statements
    )
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


def test_background_job_authority_migration_is_forward_only() -> None:
    migration = import_module("migrations.versions.20260730_84_background_job_authority")

    assert migration.down_revision == "20260730_83"
    assert len(migration.BACKGROUND_JOB_TYPES) == 13
    assert len(migration.CONSUMER_IDENTITIES) == 6
    assert {row[0] for row in migration.BACKGROUND_JOB_TYPES} == PLATFORM_JOB_TYPES
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


def test_worker_heartbeat_principal_migration_is_forward_only() -> None:
    migration = import_module("migrations.versions.20260730_86_worker_heartbeat_principals")

    assert migration.down_revision == "20260730_85"
    assert len(migration.PRINCIPAL_BINDINGS) == 6
    assert migration.PRINCIPAL_BINDINGS[-1][2:] == (
        "gongkao-dazi-ilink-worker",
        "app.workers.ilink_channel",
    )
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


def test_migration_86_requires_all_principal_pairs_atomically(
    db_engine_at_revision_85: Engine,
) -> None:
    migration = import_module("migrations.versions.20260730_86_worker_heartbeat_principals")
    missing_login, missing_group, _, _ = migration.PRINCIPAL_BINDINGS[-1]
    with db_engine_at_revision_85.begin() as connection:
        connection.exec_driver_sql(f"revoke {missing_group} from {missing_login}")
        connection.exec_driver_sql(f"drop role {missing_login}")
        connection.exec_driver_sql(f"drop role {missing_group}")

    with pytest.raises(RuntimeError, match="all six pre-provisioned"):
        command.upgrade(
            _alembic_config(db_engine_at_revision_85),
            "20260730_86",
        )
    assert _database_revision(db_engine_at_revision_85) == "20260730_85"
    with db_engine_at_revision_85.connect() as connection:
        assert (
            connection.execute(
                text("select to_regclass('platform.background_job_consumer_principal_registry')")
            ).scalar_one()
            is None
        )

    with db_engine_at_revision_85.begin() as connection:
        _provision_heartbeat_principal_roles(
            connection,
            migration.PRINCIPAL_BINDINGS,
        )
    command.upgrade(
        _alembic_config(db_engine_at_revision_85),
        "20260730_86",
    )
    assert _database_revision(db_engine_at_revision_85) == "20260730_86"


def test_migration_86_enforces_the_real_six_by_six_session_user_matrix(
    db_engine_at_revision_85: Engine,
) -> None:
    migration = import_module("migrations.versions.20260730_86_worker_heartbeat_principals")
    with db_engine_at_revision_85.begin() as connection:
        connection.exec_driver_sql(
            """
            do $$
            begin
              if not exists (
                select 1 from pg_roles where rolname = 'gongkao_api'
              ) then
                create role gongkao_api
                  nologin inherit nosuperuser nocreatedb nocreaterole
                  noreplication nobypassrls;
              end if;
            end;
            $$
            """
        )
    command.upgrade(
        _alembic_config(db_engine_at_revision_85),
        "20260730_86",
    )

    with db_engine_at_revision_85.connect() as connection:
        principal_rows = {
            (
                str(row["database_principal"]),
                str(row["group_role"]),
                str(row["consumer_unit"]),
                str(row["consumer_entrypoint"]),
            )
            for row in connection.execute(
                text(
                    """
                    select database_principal, group_role,
                           consumer_unit, consumer_entrypoint
                    from
                      platform.background_job_consumer_principal_registry
                    """
                )
            ).mappings()
        }
        assert principal_rows == set(migration.PRINCIPAL_BINDINGS)
        for database_principal, _, _, _ in migration.PRINCIPAL_BINDINGS:
            assert (
                connection.execute(
                    text(
                        "select has_function_privilege("
                        ":principal, "
                        "'platform.pulse_background_job_consumer_heartbeat"
                        "(text,text,uuid)', 'EXECUTE')"
                    ),
                    {"principal": database_principal},
                ).scalar_one()
                is True
            )
            assert (
                connection.execute(
                    text(
                        """
                    select not exists (
                      select 1
                      from unnest(array[
                        'platform.background_jobs',
                        'platform.background_job_consumer_heartbeats',
                        'platform.background_job_consumer_registry',
                        'platform.background_job_type_registry',
                        'platform.background_job_consumer_principal_registry'
                      ]::text[]) protected_table(table_name)
                      cross join unnest(array[
                        'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'
                      ]::text[]) protected_privilege(privilege_name)
                      where has_table_privilege(
                        :principal,
                        protected_table.table_name,
                        protected_privilege.privilege_name
                      )
                    )
                    """
                    ),
                    {"principal": database_principal},
                ).scalar_one()
                is True
            )
        for forbidden_principal in (
            "gongkao_api",
            "gongkao_worker_heartbeat",
            "gongkao_worker_heartbeat_login",
        ):
            assert (
                connection.execute(
                    text(
                        "select has_function_privilege("
                        ":principal, "
                        "'platform.pulse_background_job_consumer_heartbeat"
                        "(text,text,uuid)', 'EXECUTE')"
                    ),
                    {"principal": forbidden_principal},
                ).scalar_one()
                is False
            )
        assert (
            connection.execute(
                text(
                    "select has_table_privilege("
                    "'gongkao_api', "
                    "'platform.background_job_consumer_principal_registry', "
                    "'SELECT')"
                )
            ).scalar_one()
            is True
        )
    with db_engine_at_revision_85.begin() as connection:
        connection.exec_driver_sql(
            "create role gongkao_unknown_heartbeat_login "
            "login inherit nosuperuser nocreatedb nocreaterole "
            "noreplication nobypassrls"
        )
        connection.exec_driver_sql(
            "grant usage on schema platform to gongkao_unknown_heartbeat_login"
        )
        connection.exec_driver_sql(
            "grant execute on function "
            "platform.pulse_background_job_consumer_heartbeat"
            "(text,text,uuid) "
            "to gongkao_unknown_heartbeat_login"
        )

    runtime = db_engine_at_revision_85.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        for caller_principal, _, caller_unit, caller_entrypoint in migration.PRINCIPAL_BINDINGS:
            runtime.exec_driver_sql(f"set session authorization {caller_principal}")
            assert runtime.execute(text("select session_user")).scalar_one() == caller_principal
            for target_principal, _, target_unit, target_entrypoint in migration.PRINCIPAL_BINDINGS:
                if target_principal == caller_principal:
                    heartbeat = _pulse_heartbeat(
                        runtime,
                        consumer_unit=target_unit,
                        consumer_entrypoint=target_entrypoint,
                    )
                    assert heartbeat["consumer_unit"] == caller_unit
                    continue
                with pytest.raises(DBAPIError) as cross_principal:
                    _pulse_heartbeat(
                        runtime,
                        consumer_unit=target_unit,
                        consumer_entrypoint=target_entrypoint,
                    )
                assert (
                    getattr(
                        cross_principal.value.orig,
                        "sqlstate",
                        None,
                    )
                    == "42501"
                )
            with pytest.raises(DBAPIError) as direct_write:
                runtime.execute(
                    text(
                        """
                        delete from
                          platform.background_job_consumer_heartbeats
                        where consumer_unit = :consumer_unit
                        """
                    ),
                    {"consumer_unit": caller_unit},
                )
            assert getattr(direct_write.value.orig, "sqlstate", None) == "42501"
            runtime.exec_driver_sql("reset session authorization")

        for forbidden_principal in (
            "gongkao_api",
            "gongkao_worker_heartbeat_login",
        ):
            runtime.exec_driver_sql(f"set session authorization {forbidden_principal}")
            with pytest.raises(DBAPIError) as forbidden_pulse:
                _pulse_heartbeat(
                    runtime,
                    consumer_unit=migration.PRINCIPAL_BINDINGS[0][2],
                    consumer_entrypoint=migration.PRINCIPAL_BINDINGS[0][3],
                )
            assert getattr(forbidden_pulse.value.orig, "sqlstate", None) == "42501"
            runtime.exec_driver_sql("reset session authorization")
        runtime.exec_driver_sql("set session authorization gongkao_unknown_heartbeat_login")
        with pytest.raises(DBAPIError) as unknown_pulse:
            _pulse_heartbeat(
                runtime,
                consumer_unit=migration.PRINCIPAL_BINDINGS[0][2],
                consumer_entrypoint=migration.PRINCIPAL_BINDINGS[0][3],
            )
        assert getattr(unknown_pulse.value.orig, "sqlstate", None) == "42501"
        runtime.exec_driver_sql("reset session authorization")
    finally:
        runtime.exec_driver_sql("reset session authorization")
        runtime.close()

    with db_engine_at_revision_85.connect() as connection:
        assert (
            connection.execute(
                text("select count(*) from platform.background_job_consumer_heartbeats")
            ).scalar_one()
            == 6
        )

    immutable = db_engine_at_revision_85.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        with pytest.raises(DBAPIError) as mutation:
            immutable.execute(
                text(
                    """
                    update
                      platform.background_job_consumer_principal_registry
                    set consumer_unit = consumer_unit
                    """
                )
            )
        assert getattr(mutation.value.orig, "sqlstate", None) == "23514"
    finally:
        immutable.close()
    with db_engine_at_revision_85.begin() as connection:
        connection.exec_driver_sql("drop owned by gongkao_api")
        connection.exec_driver_sql("drop role gongkao_api")
        connection.exec_driver_sql("drop owned by gongkao_unknown_heartbeat_login")
        connection.exec_driver_sql("drop role gongkao_unknown_heartbeat_login")


def test_direct_ilink_worker_retirement_migration_is_forward_only() -> None:
    migration = import_module("migrations.versions.20260808_102_retire_direct_ilink_worker")

    assert migration.down_revision == "20260808_101"
    assert migration.OLD_UNIT == "gongkao-dazi-ilink-worker"
    assert migration.NEW_UNIT == "gongkao-dazi-ilink-object-deletion-worker"
    assert not (APP_ROOT / "workers" / "ilink_channel.py").exists()
    assert not (SYSTEMD_ROOT / "gongkao-dazi-ilink-worker.service").exists()
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


def test_migration_84_requires_roles_and_refuses_unknown_legacy_jobs(
    db_engine_at_revision_83: Engine,
) -> None:
    config = _alembic_config(db_engine_at_revision_83)
    with db_engine_at_revision_83.begin() as connection:
        connection.exec_driver_sql(
            "revoke gongkao_worker_heartbeat from gongkao_worker_heartbeat_login"
        )
        connection.exec_driver_sql("drop role gongkao_worker_heartbeat_login")
        connection.exec_driver_sql("drop role gongkao_worker_heartbeat")

    with pytest.raises(RuntimeError, match="requires pre-provisioned"):
        command.upgrade(config, "20260730_84")
    assert _database_revision(db_engine_at_revision_83) == "20260730_83"

    with db_engine_at_revision_83.begin() as connection:
        _provision_heartbeat_roles(connection)
        connection.execute(
            text(
                """
                insert into platform.background_jobs (
                  job_id, job_key, job_type, input_version, payload, status,
                  attempt_count, max_attempts, run_after, created_at, updated_at
                ) values (
                  cast(:job_id as uuid), 'migration84-legacy-orphan',
                  'legacy_orphan_v1', 'legacy@v1', '{}'::jsonb, 'queued',
                  0, 3, statement_timestamp(), statement_timestamp(),
                  statement_timestamp()
                )
                """
            ),
            {"job_id": str(uuid4())},
        )

    with pytest.raises(RuntimeError, match="legacy_orphan_v1"):
        command.upgrade(config, "20260730_84")
    assert _database_revision(db_engine_at_revision_83) == "20260730_83"
    with db_engine_at_revision_83.connect() as connection:
        assert (
            connection.execute(
                text(
                    "select count(*) from platform.background_jobs "
                    "where job_type = 'legacy_orphan_v1'"
                )
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("select to_regclass('platform.background_job_type_registry')")
            ).scalar_one()
            is None
        )

    with db_engine_at_revision_83.begin() as connection:
        connection.execute(
            text("delete from platform.background_jobs where job_type = 'legacy_orphan_v1'")
        )
    command.upgrade(config, "20260730_84")
    assert _database_revision(db_engine_at_revision_83) == "20260730_84"


def test_migration_84_enforces_runtime_acl_and_database_job_registry(
    db_engine_at_revision_83: Engine,
) -> None:
    with db_engine_at_revision_83.begin() as connection:
        connection.exec_driver_sql("drop role if exists gongkao_api")
        connection.exec_driver_sql(
            "create role gongkao_api nologin inherit "
            "nosuperuser nocreatedb nocreaterole noreplication nobypassrls"
        )
        connection.exec_driver_sql("grant usage on schema platform to gongkao_api")
        connection.exec_driver_sql(
            "grant select, insert, update, delete "
            "on platform.background_job_consumer_heartbeats to gongkao_api"
        )
        connection.exec_driver_sql(
            "grant select, insert, update, delete on platform.background_jobs to gongkao_api"
        )
    command.upgrade(
        _alembic_config(db_engine_at_revision_83),
        "20260730_84",
    )

    with db_engine_at_revision_83.connect() as connection:
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", False),
            ("UPDATE", False),
            ("DELETE", False),
            ("TRUNCATE", False),
        ):
            assert (
                connection.execute(
                    text(
                        "select has_table_privilege("
                        "'gongkao_api', "
                        "'platform.background_job_consumer_heartbeats', "
                        ":privilege)"
                    ),
                    {"privilege": privilege},
                ).scalar_one()
                is expected
            )
        assert not connection.execute(
            text(
                "select has_function_privilege("
                "'gongkao_api', "
                "'platform.pulse_background_job_consumer_heartbeat"
                "(text,text,uuid)', 'EXECUTE')"
            )
        ).scalar_one()
        assert connection.execute(
            text(
                "select has_function_privilege("
                "'gongkao_worker_heartbeat_login', "
                "'platform.pulse_background_job_consumer_heartbeat"
                "(text,text,uuid)', 'EXECUTE')"
            )
        ).scalar_one()
        assert not connection.execute(
            text(
                "select has_table_privilege("
                "'gongkao_worker_heartbeat_login', "
                "'platform.background_job_consumer_heartbeats', "
                "'INSERT')"
            )
        ).scalar_one()

    runtime = db_engine_at_revision_83.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        runtime.exec_driver_sql("set role gongkao_api")
        assert (
            runtime.execute(
                text("select count(*) from platform.background_job_consumer_heartbeats")
            ).scalar_one()
            == 0
        )
        with pytest.raises(DBAPIError) as api_pulse:
            runtime.execute(
                text(
                    """
                    select *
                    from platform.pulse_background_job_consumer_heartbeat(
                      'gongkao-dazi-cause-agent-worker',
                      'app.workers.xingce_cause_agent',
                      cast(:process_instance_id as uuid)
                    )
                    """
                ),
                {"process_instance_id": str(uuid4())},
            )
        assert getattr(api_pulse.value.orig, "sqlstate", None) == "42501"
        with pytest.raises(DBAPIError) as direct_heartbeat:
            runtime.execute(
                text(
                    """
                    insert into platform.background_job_consumer_heartbeats (
                      consumer_unit, consumer_entrypoint,
                      process_instance_id, started_at, last_heartbeat_at
                    ) values (
                      'gongkao-dazi-cause-agent-worker',
                      'app.workers.xingce_cause_agent',
                      cast(:process_instance_id as uuid),
                      statement_timestamp(), statement_timestamp()
                    )
                    """
                ),
                {"process_instance_id": str(uuid4())},
            )
        assert getattr(direct_heartbeat.value.orig, "sqlstate", None) == "42501"
        runtime.execute(
            text(
                """
                insert into platform.background_jobs (
                  job_id, job_key, job_type, input_version, payload
                ) values (
                  cast(:job_id as uuid), 'migration84-api-known',
                  'personal_data_export_v1', 'known@v1', '{}'::jsonb
                )
                """
            ),
            {"job_id": str(uuid4())},
        )
        with pytest.raises(DBAPIError) as unknown_job:
            runtime.execute(
                text(
                    """
                    insert into platform.background_jobs (
                      job_id, job_key, job_type, input_version, payload
                    ) values (
                      cast(:job_id as uuid), 'migration84-api-orphan',
                      'orphan_job_v1', 'orphan@v1', '{}'::jsonb
                    )
                    """
                ),
                {"job_id": str(uuid4())},
            )
        assert getattr(unknown_job.value.orig, "sqlstate", None) == "23503"

        runtime.exec_driver_sql("reset role")
        runtime.exec_driver_sql("set role gongkao_worker_heartbeat_login")
        heartbeat = (
            runtime.execute(
                text(
                    """
                select *
                from platform.pulse_background_job_consumer_heartbeat(
                  'gongkao-dazi-cause-agent-worker',
                  'app.workers.xingce_cause_agent',
                  cast(:process_instance_id as uuid)
                )
                """
                ),
                {"process_instance_id": str(uuid4())},
            )
            .mappings()
            .one()
        )
        assert heartbeat["last_heartbeat_at"] >= heartbeat["started_at"]
        with pytest.raises(DBAPIError) as worker_direct_dml:
            runtime.execute(text("delete from platform.background_job_consumer_heartbeats"))
        assert getattr(worker_direct_dml.value.orig, "sqlstate", None) == "42501"
    finally:
        runtime.exec_driver_sql("reset role")
        runtime.close()
        with db_engine_at_revision_83.begin() as connection:
            connection.exec_driver_sql("drop owned by gongkao_api")
            connection.exec_driver_sql("drop role gongkao_api")


def test_consumer_heartbeat_minimal_schema_has_the_runtime_contract(
    db_session: Session,
) -> None:
    columns = set(
        db_session.execute(
            text(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'platform'
                  and table_name = 'background_job_consumer_heartbeats'
                """
            )
        ).scalars()
    )
    constraints = set(
        db_session.execute(
            text(
                """
                select conname
                from pg_constraint
                where conrelid =
                  'platform.background_job_consumer_heartbeats'::regclass
                """
            )
        ).scalars()
    )

    assert columns == {
        "consumer_unit",
        "consumer_entrypoint",
        "process_instance_id",
        "started_at",
        "last_heartbeat_at",
    }
    assert {
        "background_job_consumer_heartbeats_pkey",
        "background_job_consumer_heartbeats_unit_check",
        "background_job_consumer_heartbeats_entrypoint_check",
        "background_job_consumer_heartbeats_time_check",
    } <= constraints


def test_registry_consumers_have_one_unit_and_one_activation_declaration() -> None:
    activation = ACTIVATE_SCRIPT.read_text(encoding="utf-8")
    runtime_blocks = re.findall(
        r"runtime_units(?:=|\+=)\((.*?)\)",
        activation,
        flags=re.DOTALL,
    )
    runtime_declarations = [
        unit for block in runtime_blocks for unit in re.findall(r"gongkao-dazi-[a-z-]+", block)
    ]

    for registration in JOB_REGISTRY:
        if registration.deployment_state == "disabled":
            assert registration.consumer_entrypoint is None
            assert registration.disabled_reason
            continue
        unit = registration.consumer_unit
        units = tuple(SYSTEMD_ROOT.glob(f"{unit}.service"))
        assert len(units) == 1
        assert runtime_declarations.count(unit) == 1
        assert f" -m {registration.consumer_entrypoint}" in units[0].read_text(encoding="utf-8")
        entrypoint_path = (
            REPO_ROOT / "backend" / Path(*str(registration.consumer_entrypoint).split("."))
        ).with_suffix(".py")
        entrypoint_source = entrypoint_path.read_text(encoding="utf-8")
        assert "JobConsumerHeartbeatRuntime(" in entrypoint_source
        assert "get_worker_heartbeat_session_factory" in entrypoint_source
        assert f'consumer_unit="{unit}"' in entrypoint_source
        assert f'consumer_entrypoint="{registration.consumer_entrypoint}"' in entrypoint_source
        unit_source = units[0].read_text(encoding="utf-8")
        assert f"EnvironmentFile=/etc/gongkao-dazi/{HEARTBEAT_ENV_BY_UNIT[unit]}" in unit_source

    cause_units = tuple(SYSTEMD_ROOT.glob("gongkao-dazi-cause-agent-worker.service"))
    assert len(cause_units) == 1
    assert 'systemctl enable "${runtime_units[@]}"' in activation
    assert "worker-heartbeat-" not in API_SERVICE.read_text(encoding="utf-8")
    assert not (REPO_ROOT / "ops" / "worker-heartbeat.env.example").exists()
    assert len(set(HEARTBEAT_ENV_BY_UNIT.values())) == 6
    for binding in JOB_CONSUMER_PRINCIPALS:
        example = REPO_ROOT / "ops" / f"{HEARTBEAT_ENV_BY_UNIT[binding.consumer_unit]}.example"
        source = example.read_text(encoding="utf-8")
        assert "root:root 0600" in source
        assert binding.database_principal in source


def test_heartbeat_role_provisioning_has_six_pairs_and_no_embedded_secret() -> None:
    source = HEARTBEAT_ROLE_PROVISIONING.read_text(encoding="utf-8")

    for binding in JOB_CONSUMER_PRINCIPALS:
        assert binding.database_principal in source
        assert binding.group_role in source
    assert len({binding.database_principal for binding in JOB_CONSUMER_PRINCIPALS}) == 6
    assert len({binding.group_role for binding in JOB_CONSUMER_PRINCIPALS}) == 6
    assert (
        re.search(
            r"\b(?:encrypted\s+)?password\s+['\"]",
            source,
            flags=re.IGNORECASE,
        )
        is None
    )
    assert "\\set ON_ERROR_STOP on" in source


def test_metrics_expose_queue_and_running_age_for_each_registered_type(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    jobs = BackgroundJobRepository()
    jobs.enqueue(
        db_session,
        job_key="registry:cause:queued-age",
        job_type=CAUSE_AGENT_JOB_TYPE,
        input_version="cause-agent-run@v1",
        payload={"runId": str(uuid4())},
        run_after=now - timedelta(minutes=10),
    )
    jobs.enqueue(
        db_session,
        job_key="registry:cause:running-age",
        job_type=CAUSE_AGENT_JOB_TYPE,
        input_version="cause-agent-run@v1",
        payload={"runId": str(uuid4())},
        run_after=now - timedelta(minutes=10),
    )
    claimed = jobs.claim_next(
        db_session,
        job_types=(CAUSE_AGENT_JOB_TYPE,),
        now=now - timedelta(minutes=10),
    )
    assert claimed is not None
    db_session.commit()
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="jobs-metrics-token"),
    )

    payload = operations_router.metrics(
        db_session,
        authorization="Bearer jobs-metrics-token",
    )

    for registration in JOB_REGISTRY:
        assert f'anan_job_queue_oldest_age_seconds{{job_type="{registration.job_type}"}}' in payload
        assert (
            f'anan_job_running_oldest_age_seconds{{job_type="{registration.job_type}"}}' in payload
        )
        assert (
            f'anan_job_queue_readiness_age_seconds{{job_type="{registration.job_type}"}} '
            f"{int(registration.queue_readiness_age.total_seconds())}" in payload
        )
        assert (
            f'anan_job_running_readiness_age_seconds{{job_type="{registration.job_type}"}} '
            f"{int(registration.running_readiness_age.total_seconds())}" in payload
        )
        assert f'anan_job_queued{{job_type="{registration.job_type}"}}' in payload
        assert f'anan_job_running{{job_type="{registration.job_type}"}}' in payload
        assert (
            f'anan_job_consumer_enabled{{job_type="{registration.job_type}"}} '
            f"{int(job_registration_enabled(registration))}" in payload
        )
        assert (
            f'anan_job_consumer_deployed{{job_type="{registration.job_type}"}} '
            f"{int(registration.deployment_state == 'deployed')}" in payload
        )
        assert (
            f'anan_job_consumer_heartbeat_age_seconds{{job_type="{registration.job_type}"}}'
            in payload
        )
        assert (
            f'anan_job_consumer_heartbeat_ttl_seconds{{job_type="{registration.job_type}"}} '
            f"{int(JOB_CONSUMER_HEARTBEAT_TTL.total_seconds())}" in payload
        )
        assert (
            f'anan_job_consumer_heartbeat_healthy{{job_type="{registration.job_type}"}}' in payload
        )
    assert "anan_database_ready 1" in payload
    assert "anan_background_jobs_failed_recent 0" in payload
    assert "anan_background_jobs_business_terminated_recent 0" in payload
    assert "anan_background_jobs_unregistered_active 0" in payload
    assert "anan_background_job_registry_exact 1" in payload
    assert "anan_ai_model_calls_running_stale 0" in payload
    assert "anan_ai_usage_reservations_open_stale 0" in payload
    assert "anan_recognition_capability_ready 0" in payload
    assert "anan_xingce_cause_capability_ready 0" in payload
    queue_age = _metric_value(
        payload,
        'anan_job_queue_oldest_age_seconds{job_type="xingce_cause_agent_v1"}',
    )
    running_age = _metric_value(
        payload,
        'anan_job_running_oldest_age_seconds{job_type="xingce_cause_agent_v1"}',
    )
    assert 590 <= queue_age <= 620
    assert 590 <= running_age <= 620


def test_registry_metric_fails_closed_on_principal_acl_drift(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = JOB_CONSUMER_PRINCIPALS[0]
    db_session.execute(
        text(
            "revoke execute on function "
            "platform.pulse_background_job_consumer_heartbeat"
            "(text,text,uuid) "
            f"from {binding.group_role}"
        )
    )
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="jobs-metrics-token"),
    )

    payload = operations_router.metrics(
        db_session,
        authorization="Bearer jobs-metrics-token",
    )

    assert "anan_background_job_registry_exact 0" in payload


@pytest.mark.parametrize(
    "drift_sql",
    (
        "grant select on platform.background_jobs to gongkao_worker_heartbeat_data_rights",
        "grant create on schema public to gongkao_worker_heartbeat_data_rights",
    ),
)
def test_registry_metric_fails_closed_on_excess_principal_privilege(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    drift_sql: str,
) -> None:
    db_session.execute(text(drift_sql))
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="jobs-metrics-token"),
    )

    payload = operations_router.metrics(
        db_session,
        authorization="Bearer jobs-metrics-token",
    )

    assert "anan_background_job_registry_exact 0" in payload


def test_registry_metric_fails_closed_on_unknown_consumer(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_session.execute(
        text(
            """
            insert into platform.background_job_consumer_registry (
              consumer_unit, consumer_entrypoint, queue_name
            ) values (
              'gongkao-dazi-orphan-worker',
              'app.workers.orphan',
              'platform.background_jobs'
            )
            """
        )
    )
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="jobs-metrics-token"),
    )

    payload = operations_router.metrics(
        db_session,
        authorization="Bearer jobs-metrics-token",
    )

    assert "anan_background_job_registry_exact 0" in payload


@pytest.mark.parametrize("value", ("1", "true", "TRUE", " yes ", "On"))
def test_conditional_job_registration_uses_application_truthy_values(
    value: str,
) -> None:
    registration = next(
        registration
        for registration in JOB_REGISTRY
        if registration.job_type == "essay_grounded_review_v2"
    )

    assert job_registration_enabled(
        registration,
        environ={"ESSAY_REVIEW_WORKER_ENABLED": value},
    )


@pytest.mark.parametrize("value", ("0", "false", "FALSE", " no ", "Off"))
def test_conditional_job_registration_uses_application_falsey_values(
    value: str,
) -> None:
    registration = next(
        registration
        for registration in JOB_REGISTRY
        if registration.job_type == "essay_grounded_review_v2"
    )

    assert not job_registration_enabled(
        registration,
        environ={"ESSAY_REVIEW_WORKER_ENABLED": value},
    )


def test_conditional_job_registration_rejects_an_ambiguous_boolean() -> None:
    registration = next(
        registration
        for registration in JOB_REGISTRY
        if registration.job_type == "essay_grounded_review_v2"
    )

    with pytest.raises(JobContractError, match="must be an explicit boolean"):
        job_registration_enabled(
            registration,
            environ={"ESSAY_REVIEW_WORKER_ENABLED": "enabled"},
        )


def test_enqueue_rejects_an_unregistered_job_before_any_row_is_written(
    db_session: Session,
) -> None:
    before = db_session.execute(text("select count(*) from platform.background_jobs")).scalar_one()

    with pytest.raises(JobContractError, match="orphan_job_v1 is not registered"):
        BackgroundJobRepository().enqueue(
            db_session,
            job_key="orphan:must-not-persist",
            job_type="orphan_job_v1",
            input_version="orphan@v1",
            payload={},
        )

    assert (
        db_session.execute(text("select count(*) from platform.background_jobs")).scalar_one()
        == before
    )


def test_consumer_heartbeat_is_a_database_liveness_authority(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat_sessions = principal_session_factory(
        db_session_factory,
        consumer_unit="gongkao-dazi-cause-agent-worker",
        consumer_entrypoint="app.workers.xingce_cause_agent",
    )
    runtime = JobConsumerHeartbeatRuntime(
        session_factory=heartbeat_sessions,
        consumer_unit="gongkao-dazi-cause-agent-worker",
        consumer_entrypoint="app.workers.xingce_cause_agent",
    )

    with runtime:
        with db_session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                    select consumer_entrypoint, process_instance_id,
                           started_at, last_heartbeat_at
                    from platform.background_job_consumer_heartbeats
                    where consumer_unit = 'gongkao-dazi-cause-agent-worker'
                    """
                    )
                )
                .mappings()
                .one()
            )

    assert row["consumer_entrypoint"] == "app.workers.xingce_cause_agent"
    assert row["process_instance_id"] == runtime.process_instance_id
    assert row["last_heartbeat_at"] >= row["started_at"]
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="heartbeat-metrics-token"),
    )
    with db_session_factory() as session:
        payload = operations_router.metrics(
            session,
            authorization="Bearer heartbeat-metrics-token",
        )
    assert (
        _metric_value(
            payload,
            'anan_job_consumer_heartbeat_age_seconds{job_type="xingce_cause_agent_v1"}',
        )
        <= 5
    )
    assert (
        _metric_value(
            payload,
            'anan_job_consumer_heartbeat_healthy{job_type="xingce_cause_agent_v1"}',
        )
        == 1
    )


def test_empty_mandatory_consumer_requires_a_recent_heartbeat(
    db_session: Session,
) -> None:
    registration = next(
        registration
        for registration in JOB_REGISTRY
        if registration.job_type == CAUSE_AGENT_JOB_TYPE
    )
    now = datetime.now(UTC)
    health = operations_router._job_queue_health(
        db_session,
        now=now,
        registrations=(registration,),
    )
    assert health[CAUSE_AGENT_JOB_TYPE]["queued_count"] == 0
    assert health[CAUSE_AGENT_JOB_TYPE]["heartbeat_age_seconds"] == -1
    assert operations_router._unready_job_types(
        registrations=(registration,),
        health=health,
    ) == (CAUSE_AGENT_JOB_TYPE,)

    process_instance_id = uuid4()
    record_heartbeat_as_principal(
        db_session,
        consumer_unit=registration.consumer_unit,
        consumer_entrypoint=str(registration.consumer_entrypoint),
        process_instance_id=process_instance_id,
    )
    db_session.commit()
    healthy = operations_router._job_queue_health(
        db_session,
        now=datetime.now(UTC),
        registrations=(registration,),
    )
    assert healthy[CAUSE_AGENT_JOB_TYPE]["heartbeat_healthy"] == 1
    assert (
        operations_router._unready_job_types(
            registrations=(registration,),
            health=healthy,
        )
        == ()
    )

    db_session.execute(
        text(
            """
            update platform.background_job_consumer_heartbeats
            set started_at =
                  :now - make_interval(secs => :stale_seconds),
                last_heartbeat_at =
                  :now - make_interval(secs => :stale_seconds)
            where consumer_unit = :consumer_unit
            """
        ),
        {
            "now": datetime.now(UTC),
            "stale_seconds": int(JOB_CONSUMER_HEARTBEAT_TTL.total_seconds()) + 1,
            "consumer_unit": registration.consumer_unit,
        },
    )
    stale = operations_router._job_queue_health(
        db_session,
        now=datetime.now(UTC),
        registrations=(registration,),
    )
    assert stale[CAUSE_AGENT_JOB_TYPE]["heartbeat_healthy"] == 0
    assert operations_router._unready_job_types(
        registrations=(registration,),
        health=stale,
    ) == (CAUSE_AGENT_JOB_TYPE,)


def test_future_heartbeat_is_rejected_and_never_normalized_healthy(
    db_session: Session,
) -> None:
    registration = next(
        registration
        for registration in JOB_REGISTRY
        if registration.job_type == CAUSE_AGENT_JOB_TYPE
    )
    record_heartbeat_as_principal(
        db_session,
        consumer_unit=registration.consumer_unit,
        consumer_entrypoint=str(registration.consumer_entrypoint),
        process_instance_id=uuid4(),
    )
    db_session.commit()

    with pytest.raises(DBAPIError) as future_write:
        db_session.execute(
            text(
                """
                update platform.background_job_consumer_heartbeats
                set started_at = statement_timestamp() + interval '5 minutes',
                    last_heartbeat_at =
                      statement_timestamp() + interval '5 minutes'
                where consumer_unit = :consumer_unit
                """
            ),
            {"consumer_unit": registration.consumer_unit},
        )
        db_session.commit()
    assert getattr(future_write.value.orig, "sqlstate", None) == "22007"
    db_session.rollback()

    db_session.execute(
        text(
            "alter table platform.background_job_consumer_heartbeats "
            "disable trigger "
            "background_job_consumer_heartbeats_reject_future"
        )
    )
    db_session.execute(
        text(
            """
            update platform.background_job_consumer_heartbeats
            set started_at = statement_timestamp() + interval '5 minutes',
                last_heartbeat_at =
                  statement_timestamp() + interval '5 minutes'
            where consumer_unit = :consumer_unit
            """
        ),
        {"consumer_unit": registration.consumer_unit},
    )
    db_session.execute(
        text(
            "alter table platform.background_job_consumer_heartbeats "
            "enable trigger "
            "background_job_consumer_heartbeats_reject_future"
        )
    )
    db_session.commit()

    health = operations_router._job_queue_health(
        db_session,
        now=datetime.now(UTC),
        registrations=(registration,),
    )
    assert health[CAUSE_AGENT_JOB_TYPE]["heartbeat_age_seconds"] < 0
    assert health[CAUSE_AGENT_JOB_TYPE]["heartbeat_healthy"] == 0
    assert operations_router._unready_job_types(
        registrations=(registration,),
        health=health,
    ) == (CAUSE_AGENT_JOB_TYPE,)


def test_conditionally_disabled_consumer_does_not_require_a_heartbeat(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ESSAY_REVIEW_WORKER_ENABLED", raising=False)
    registration = next(
        registration
        for registration in JOB_REGISTRY
        if registration.job_type == "essay_grounded_review_v2"
    )
    health = operations_router._job_queue_health(
        db_session,
        now=datetime.now(UTC),
        registrations=(registration,),
    )

    assert not job_registration_enabled(registration)
    assert health[registration.job_type]["heartbeat_healthy"] == 0
    assert (
        operations_router._unready_job_types(
            registrations=(registration,),
            health=health,
        )
        == ()
    )


def test_metrics_expose_stale_model_ledger_and_usage_reservation(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_at = datetime.now(UTC) - timedelta(minutes=20)
    user_id = str(uuid4())
    db_session.execute(
        text(
            "insert into learner.users (user_id, display_name) "
            "values (cast(:user_id as uuid), '监控合同用户')"
        ),
        {"user_id": user_id},
    )
    db_session.execute(
        text(
            """
            insert into ai_audit.model_call_ledger (
              call_id, call_key, user_id, job_id, feature, operation,
              provider, model, prompt_version, schema_version, price_version,
              input_rate_cny_per_million, cached_input_rate_cny_per_million,
              output_rate_cny_per_million, input_fingerprint, status, started_at
            ) values (
              cast(:call_id as uuid), 'metrics-stale-model-call', null, null,
              'xingce_cause_analysis', 'cause_candidate_generate',
              'metrics-provider', 'metrics-model', 'metrics-prompt-v1',
              'metrics-schema-v1', 'metrics-price-v1',
              1, 0, 1, repeat('a', 64), 'running', :stale_at
            )
            """
        ),
        {"call_id": str(uuid4()), "stale_at": stale_at},
    )
    db_session.execute(
        text(
            """
            insert into commerce.feature_usage_reservations (
              reservation_id, reservation_key, user_id, feature, usage_date,
              limit_count, usage_count_at_reservation, access_mode, status,
              business_key, job_id, reserved_at, consumed_at, refunded_at,
              updated_at
            ) values (
              cast(:reservation_id as uuid), 'metrics-stale-reservation',
              cast(:user_id as uuid), 'xingce_cause_analysis', :usage_date,
              10, 1, 'entitlement_quota', 'reserved',
              'metrics-stale-business-key', null, :stale_at, null, null,
              :stale_at
            )
            """
        ),
        {
            "reservation_id": str(uuid4()),
            "user_id": user_id,
            "usage_date": stale_at.date(),
            "stale_at": stale_at,
        },
    )
    db_session.commit()
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="stale-metrics-token"),
    )

    payload = operations_router.metrics(
        db_session,
        authorization="Bearer stale-metrics-token",
    )

    assert "anan_ai_model_calls_running_stale 1" in payload
    assert "anan_ai_usage_reservations_open_stale 1" in payload


def test_metrics_separate_recent_technical_failures_from_business_termination(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    rows = (
        ("old-technical", now - timedelta(minutes=16), {"failure": "timeout"}),
        (
            "old-business-termination",
            now - timedelta(minutes=16),
            {"businessOutcome": "terminated"},
        ),
        ("recent-technical", now - timedelta(minutes=2), {"failure": "timeout"}),
        (
            "recent-business-termination",
            now - timedelta(minutes=2),
            {"businessOutcome": "terminated"},
        ),
    )
    for suffix, completed_at, result in rows:
        db_session.execute(
            text(
                """
                insert into platform.background_jobs (
                  job_id, job_key, job_type, input_version, payload, result,
                  status, attempt_count, max_attempts, run_after,
                  created_at, updated_at, completed_at
                ) values (
                  cast(:job_id as uuid), :job_key, :job_type, 'metrics@v1',
                  '{}'::jsonb, cast(:result as jsonb), 'failed', 1, 1,
                  :completed_at, :completed_at, :completed_at, :completed_at
                )
                """
            ),
            {
                "job_id": str(uuid4()),
                "job_key": f"metrics:{suffix}:{uuid4()}",
                "job_type": CAUSE_AGENT_JOB_TYPE,
                "result": json.dumps(result, sort_keys=True),
                "completed_at": completed_at,
            },
        )
    db_session.commit()
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(operations_metrics_token="failure-metrics-token"),
    )

    payload = operations_router.metrics(
        db_session,
        authorization="Bearer failure-metrics-token",
    )

    assert "anan_background_jobs_failed_recent 1" in payload
    assert "anan_background_jobs_business_terminated_recent 1" in payload


def test_all_background_job_enqueue_producers_close_to_the_registry() -> None:
    registry = {
        registration.job_type: registration
        for registration in JOB_REGISTRY
        if registration.queue_name == "platform.background_jobs"
    }
    discovered_declarations: set[str] = set()
    discovered_enqueues: set[str] = set()

    for source_path in APP_ROOT.rglob("*.py"):
        if source_path == APP_ROOT / "platform" / "jobs.py":
            continue
        module_name = _module_name(source_path)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        constants = _job_type_constants(tree)
        discovered_declarations.update(value for values in constants.values() for value in values)
        for call in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "enqueue"
        ):
            keyword = next(
                (item for item in call.keywords if item.arg == "job_type"),
                None,
            )
            assert keyword is not None, f"{source_path}:{call.lineno}"
            resolved = _resolve_job_types(keyword.value, constants)
            assert resolved, f"unresolved background job type at {source_path}:{call.lineno}"
            for job_type in resolved:
                assert job_type in registry, (
                    f"{source_path}:{call.lineno} enqueues unregistered {job_type}"
                )
                assert registry[job_type].producer.startswith(f"{module_name}.")
                discovered_enqueues.add(job_type)

    assert discovered_declarations == {
        *PLATFORM_JOB_TYPES,
        "ilink_object_deletion_v1",
    }
    assert discovered_enqueues == PLATFORM_JOB_TYPES


@pytest.mark.parametrize(
    "job_type",
    (
        "community_method_card_candidate_v2",
        "community_method_card_review_v2",
    ),
)
def test_dormant_community_jobs_have_a_provable_disabled_contract(
    db_session: Session,
    job_type: str,
) -> None:
    registration = next(
        registration for registration in JOB_REGISTRY if registration.job_type == job_type
    )
    assert registration.deployment_state == "disabled"
    assert not job_registration_enabled(registration, environ={})
    with pytest.raises(JobContractError, match=rf"{job_type} is disabled"):
        BackgroundJobRepository().enqueue(
            db_session,
            job_key=f"disabled:{job_type}",
            job_type=job_type,
            input_version="disabled-contract@v1",
            payload={},
        )


@pytest.mark.parametrize(
    "job_type",
    (
        "community_method_card_candidate_v2",
        "essay_grounded_review_v2",
    ),
)
def test_disabled_consumer_backlog_fails_the_readiness_contract(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    job_type: str,
) -> None:
    monkeypatch.delenv("ESSAY_REVIEW_WORKER_ENABLED", raising=False)
    now = datetime.now(UTC)
    registration = next(
        registration for registration in JOB_REGISTRY if registration.job_type == job_type
    )
    db_session.execute(
        text(
            """
            insert into platform.background_jobs (
              job_id, job_key, job_type, input_version, payload, status,
              attempt_count, max_attempts, run_after, created_at, updated_at
            ) values (
              cast(:job_id as uuid), :job_key, :job_type, 'legacy@v1',
              '{}'::jsonb, 'queued', 0, 3, :run_after, :created_at, :created_at
            )
            """
        ),
        {
            "job_id": str(uuid4()),
            "job_key": f"legacy-disabled:{uuid4()}",
            "job_type": registration.job_type,
            "run_after": now + timedelta(hours=1),
            "created_at": now,
        },
    )
    health = operations_router._job_queue_health(
        db_session,
        now=now,
        registrations=(registration,),
    )

    assert health[registration.job_type]["queued_count"] == 1
    assert health[registration.job_type]["queue_age_seconds"] == 0
    assert operations_router._unready_job_types(
        registrations=(registration,),
        health=health,
    ) == (registration.job_type,)


def _metric_value(payload: str, name: str) -> int:
    match = re.search(rf"^{re.escape(name)} ([0-9]+)$", payload, flags=re.MULTILINE)
    assert match is not None
    return int(match.group(1))


def _alembic_config(engine: Engine) -> Config:
    config = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False),
    )
    return config


def _database_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(text("select version_num from public.alembic_version")).scalar_one()
        )


def _provision_heartbeat_roles(connection) -> None:
    connection.exec_driver_sql(
        "create role gongkao_worker_heartbeat "
        "nologin inherit nosuperuser nocreatedb nocreaterole "
        "noreplication nobypassrls"
    )
    connection.exec_driver_sql(
        "create role gongkao_worker_heartbeat_login "
        "login inherit nosuperuser nocreatedb nocreaterole "
        "noreplication nobypassrls"
    )
    connection.exec_driver_sql("grant gongkao_worker_heartbeat to gongkao_worker_heartbeat_login")


def _provision_heartbeat_principal_roles(
    connection: Connection,
    bindings: tuple[tuple[str, str, str, str], ...],
) -> None:
    for login_role, group_role, _, _ in bindings:
        group_exists = connection.execute(
            text("select exists(select 1 from pg_roles where rolname = :role_name)"),
            {"role_name": group_role},
        ).scalar_one()
        if not group_exists:
            connection.exec_driver_sql(
                f"create role {group_role} "
                "nologin inherit nosuperuser nocreatedb nocreaterole "
                "noreplication nobypassrls"
            )
        login_exists = connection.execute(
            text("select exists(select 1 from pg_roles where rolname = :role_name)"),
            {"role_name": login_role},
        ).scalar_one()
        if not login_exists:
            connection.exec_driver_sql(
                f"create role {login_role} "
                "login inherit nosuperuser nocreatedb nocreaterole "
                "noreplication nobypassrls"
            )
        connection.exec_driver_sql(f"grant {group_role} to {login_role}")


def _pulse_heartbeat(
    connection: Connection,
    *,
    consumer_unit: str,
    consumer_entrypoint: str,
) -> RowMapping:
    return (
        connection.execute(
            text(
                """
                select *
                from platform.pulse_background_job_consumer_heartbeat(
                  :consumer_unit,
                  :consumer_entrypoint,
                  cast(:process_instance_id as uuid)
                )
                """
            ),
            {
                "consumer_unit": consumer_unit,
                "consumer_entrypoint": consumer_entrypoint,
                "process_instance_id": str(uuid4()),
            },
        )
        .mappings()
        .one()
    )


def _module_name(source_path: Path) -> str:
    relative = source_path.relative_to(REPO_ROOT / "backend").with_suffix("")
    return ".".join(relative.parts)


def _job_type_constants(tree: ast.AST) -> dict[str, set[str]]:
    constants: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name) or "JOB_TYPE" not in target.id:
                continue
            resolved = _literal_strings(value)
            if resolved:
                constants.setdefault(target.id, set()).update(resolved)
    return constants


def _resolve_job_types(
    value: ast.expr,
    constants: dict[str, set[str]],
) -> set[str]:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    if isinstance(value, ast.Name):
        return constants.get(value.id, set())
    if isinstance(value, ast.Attribute):
        return constants.get(value.attr, set())
    return set()


def _literal_strings(value: ast.expr | None) -> set[str]:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    if isinstance(value, (ast.Tuple, ast.List)):
        return {
            element.value
            for element in value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
    return set()
