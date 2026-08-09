from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
from collections.abc import Iterator
from importlib import import_module

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def postgres_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    configured_url = os.getenv("TEST_DATABASE_URL")
    if configured_url:
        _provision_test_heartbeat_roles(configured_url)
        yield configured_url
        return

    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.fail("PostgreSQL initdb/pg_ctl is required for database contract tests")

    data_dir = tmp_path_factory.mktemp("postgres-data")
    log_file = tmp_path_factory.mktemp("postgres-log") / "postgres.log"
    port = _free_port()

    subprocess.run(
        [initdb, "-D", str(data_dir), "--auth=trust", "--username=postgres"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data_dir),
            "-l",
            str(log_file),
            "-o",
            (f"-p {port} -h 127.0.0.1 -c unix_socket_directories=''"),
            "start",
            "-w",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    database_url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
    _provision_test_heartbeat_roles(database_url)
    try:
        yield database_url
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data_dir), "stop", "-m", "fast", "-w"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )


def _provision_test_heartbeat_roles(database_url: str) -> None:
    """Provision the migration-84 role prerequisite inside the isolated test PG."""

    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                do $$
                begin
                  if not exists (
                    select 1 from pg_roles
                    where rolname = 'gongkao_worker_heartbeat'
                  ) then
                    create role gongkao_worker_heartbeat
                      nologin inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  if not exists (
                    select 1 from pg_roles
                    where rolname = 'gongkao_worker_heartbeat_login'
                  ) then
                    create role gongkao_worker_heartbeat_login
                      login inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  grant gongkao_worker_heartbeat
                    to gongkao_worker_heartbeat_login;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_data_rights'
                  ) then
                    create role gongkao_worker_heartbeat_data_rights
                      nologin inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_data_rights_login'
                  ) then
                    create role
                      gongkao_worker_heartbeat_data_rights_login
                      login inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  grant gongkao_worker_heartbeat_data_rights
                    to gongkao_worker_heartbeat_data_rights_login;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_cause_agent'
                  ) then
                    create role gongkao_worker_heartbeat_cause_agent
                      nologin inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_cause_agent_login'
                  ) then
                    create role
                      gongkao_worker_heartbeat_cause_agent_login
                      login inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  grant gongkao_worker_heartbeat_cause_agent
                    to gongkao_worker_heartbeat_cause_agent_login;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_external_learning'
                  ) then
                    create role
                      gongkao_worker_heartbeat_external_learning
                      nologin inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_external_learning_login'
                  ) then
                    create role
                      gongkao_worker_heartbeat_external_learning_login
                      login inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  grant gongkao_worker_heartbeat_external_learning
                    to gongkao_worker_heartbeat_external_learning_login;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_essay_review'
                  ) then
                    create role gongkao_worker_heartbeat_essay_review
                      nologin inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_essay_review_login'
                  ) then
                    create role
                      gongkao_worker_heartbeat_essay_review_login
                      login inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  grant gongkao_worker_heartbeat_essay_review
                    to gongkao_worker_heartbeat_essay_review_login;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_coach_notification'
                  ) then
                    create role
                      gongkao_worker_heartbeat_coach_notification
                      nologin inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_coach_notification_login'
                  ) then
                    create role
                      gongkao_worker_heartbeat_coach_notification_login
                      login inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  grant gongkao_worker_heartbeat_coach_notification
                    to
                      gongkao_worker_heartbeat_coach_notification_login;
                  if not exists (
                    select 1 from pg_roles
                    where rolname = 'gongkao_worker_heartbeat_ilink'
                  ) then
                    create role gongkao_worker_heartbeat_ilink
                      nologin inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  if not exists (
                    select 1 from pg_roles
                    where rolname =
                      'gongkao_worker_heartbeat_ilink_login'
                  ) then
                    create role gongkao_worker_heartbeat_ilink_login
                      login inherit nosuperuser nocreatedb nocreaterole
                      noreplication nobypassrls;
                  end if;
                  grant gongkao_worker_heartbeat_ilink
                    to gongkao_worker_heartbeat_ilink_login;
                end;
                $$
                """
            )
    finally:
        engine.dispose()


@pytest.fixture()
def db_engine(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    _reset_test_schema(engine)
    try:
        yield engine
    finally:
        _drop_test_schema(engine)
        engine.dispose()


@pytest.fixture()
def db_engine_at_revision_64(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    _reset_test_schema(engine, target_revision="20260727_64")
    try:
        yield engine
    finally:
        _drop_test_schema(engine)
        engine.dispose()


@pytest.fixture()
def db_engine_at_revision_76(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    _reset_test_schema(engine, target_revision="20260729_76")
    try:
        yield engine
    finally:
        _drop_test_schema(engine)
        engine.dispose()


@pytest.fixture()
def db_engine_at_revision_77(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    _reset_test_schema(engine, target_revision="20260730_77")
    try:
        yield engine
    finally:
        _drop_test_schema(engine)
        engine.dispose()


@pytest.fixture()
def db_engine_at_revision_83(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    _reset_test_schema(engine, target_revision="20260730_83")
    try:
        yield engine
    finally:
        _drop_test_schema(engine)
        engine.dispose()


@pytest.fixture()
def db_engine_at_revision_85(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    _reset_test_schema(engine, target_revision="20260730_85")
    try:
        yield engine
    finally:
        _drop_test_schema(engine)
        engine.dispose()


@pytest.fixture()
def db_engine_at_revision_103(postgres_url: str) -> Iterator[Engine]:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    _reset_test_schema(engine, target_revision="20260809_103")
    try:
        yield engine
    finally:
        _drop_test_schema(engine)
        engine.dispose()


@pytest.fixture()
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


@pytest.fixture()
def db_session(db_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with db_session_factory() as session:
        yield session


def _reset_test_schema(
    engine: Engine,
    *,
    target_revision: str = "head",
) -> None:
    _drop_test_schema(engine)
    schema_sql = Path(__file__).with_name("sql").joinpath("minimal_schema.sql").read_text()
    with engine.begin() as conn:
        for statement in _split_postgres_statements(schema_sql):
            conn.execute(text(statement))
        stage15 = import_module(
            "migrations.versions.20260715_20_stage15_external_learning_foundation"
        )
        for statement in stage15._STATEMENTS:
            conn.execute(text(statement))
        stage15_runtime = import_module(
            "migrations.versions.20260715_21_stage15_external_learning_runtime"
        )
        for statement in stage15_runtime._STATEMENTS:
            conn.execute(text(statement))
        conn.execute(
            text(
                "alter table learning.essay_answer_versions "
                "add constraint essay_answer_recognition_evidence_fk "
                "foreign key (recognition_revision_id, user_id, source_artifact_version_id) "
                "references learning.recognition_revisions("
                "recognition_revision_id, owner_user_id, artifact_version_id)"
            )
        )
        stage2 = import_module("migrations.versions.20260715_22_stage2_task_content_runtime")
        for statement in stage2._STATEMENTS:
            conn.execute(text(statement))
        stage3_mastery = import_module("migrations.versions.20260716_23_xingce_mastery_scheduler")
        for statement in stage3_mastery._STATEMENTS:
            conn.execute(text(statement))
        stage3_cause_agent = import_module("migrations.versions.20260716_24_xingce_cause_agent")
        for statement in stage3_cause_agent._STATEMENTS:
            conn.execute(text(statement))
        essay_closure = import_module("migrations.versions.20260716_25_essay_closure_mastery")
        for statement in essay_closure._STATEMENTS:
            conn.execute(text(statement))
        learning_media = import_module("migrations.versions.20260716_26_learning_media")
        for statement in learning_media._STATEMENTS:
            conn.execute(text(statement))
        question_quality = import_module(
            "migrations.versions.20260716_27_question_quality_decisions"
        )
        for statement in question_quality._STATEMENTS:
            conn.execute(text(statement))
        community_cards = import_module("migrations.versions.20260716_28_community_method_cards")
        for statement in community_cards._STATEMENTS:
            conn.execute(text(statement))
        community_delayed = import_module(
            "migrations.versions.20260716_29_community_delayed_verifications"
        )
        community_delayed._assert_no_mutable_outcomes(conn)
        for statement in community_delayed._STATEMENTS:
            conn.execute(text(statement))
        community_production = import_module(
            "migrations.versions.20260716_30_community_production_closure"
        )
        for statement in community_production._STATEMENTS:
            conn.execute(text(statement))
        learning_brain = import_module("migrations.versions.20260716_31_learning_brain_planning")
        for statement in learning_brain._STATEMENTS:
            conn.execute(text(statement))
        stage6_reporting = import_module("migrations.versions.20260716_33_stage6_mock_reporting")
        for statement in stage6_reporting._STATEMENTS:
            conn.execute(text(statement))
        ai_governance = import_module("migrations.versions.20260716_34_ai_governance")
        for statement in ai_governance._STATEMENTS:
            conn.execute(text(statement))
        stage8 = import_module("migrations.versions.20260717_35_commerce_privacy_operations")
        for statement in stage8._STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        virtual_payment = import_module("migrations.versions.20260717_36_wechat_virtual_payment")
        for statement in virtual_payment._STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        stage15_completion = import_module("migrations.versions.20260718_37_stage15_completion")
        for statement in stage15_completion._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        free_cold_start = import_module("migrations.versions.20260718_38_free_cold_start_access")
        for statement in free_cold_start._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        six_modules = import_module("migrations.versions.20260718_39_guokao_xingce_six_modules")
        for statement in six_modules._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        preparation_experience = import_module(
            "migrations.versions.20260718_40_preparation_experience_retake_owner"
        )
        for statement in preparation_experience._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        external_ai_boundary = import_module(
            "migrations.versions.20260719_41_external_ai_data_boundary"
        )
        for statement in external_ai_boundary._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        essay_multi_model = import_module(
            "migrations.versions.20260719_42_essay_multi_model_grading"
        )
        for statement in essay_multi_model._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        essay_multi_model_runtime = import_module(
            "migrations.versions.20260719_43_complete_essay_multi_model_runtime"
        )
        for statement in essay_multi_model_runtime._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        step6 = import_module("migrations.versions.20260719_44_private_knowledge_math_ink")
        for statement in step6._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        step7 = import_module("migrations.versions.20260719_45_coach_notifications")
        for statement in step7._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        step8 = import_module("migrations.versions.20260719_46_task_editing_recovery")
        for statement in step8._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        step10 = import_module("migrations.versions.20260719_47_daily_opening")
        for statement in step10._STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        ilink_photo_channel = import_module("migrations.versions.20260721_48_ilink_photo_channel")
        for statement in ilink_photo_channel._STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        mock_plan_closure = import_module("migrations.versions.20260723_55_mock_plan_closure")
        for statement in mock_plan_closure._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        fuzzy_weather_location = import_module(
            "migrations.versions.20260723_56_fuzzy_weather_location"
        )
        for statement in fuzzy_weather_location._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        preparation_current_status = import_module(
            "migrations.versions.20260724_57_preparation_current_status"
        )
        for statement in preparation_current_status._TEST_STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        confirmed_cause_replacement_code = import_module(
            "migrations.versions.20260727_62_confirmed_cause_replacement_code"
        )
        for statement in confirmed_cause_replacement_code._STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        ability_v2_shadow = import_module(
            "migrations.versions.20260727_63_ability_estimate_v2_shadow"
        )
        for statement in ability_v2_shadow._STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        mock_trajectory_conflicts = import_module(
            "migrations.versions.20260727_64_mock_trajectory_conflict_contract"
        )
        for statement in mock_trajectory_conflicts._STATEMENTS:
            conn.exec_driver_sql(statement.replace("%", "%%"))
        if target_revision != "20260727_64":
            knowledge_map_releases = import_module(
                "migrations.versions.20260727_65_knowledge_map_releases"
            )
            for statement in knowledge_map_releases._STATEMENTS:
                conn.exec_driver_sql(statement.replace("%", "%%"))
            knowledge_release_immutability = import_module(
                "migrations.versions.20260727_66_knowledge_release_immutability"
            )
            for statement in knowledge_release_immutability._STATEMENTS:
                conn.exec_driver_sql(statement.replace("%", "%%"))
            external_question_knowledge_lineage = import_module(
                "migrations.versions.20260727_67_external_question_knowledge_lineage"
            )
            for statement in external_question_knowledge_lineage._STATEMENTS:
                conn.exec_driver_sql(statement.replace("%", "%%"))
            knowledge_map_question_bindings = import_module(
                "migrations.versions.20260727_68_knowledge_map_question_bindings"
            )
            for statement in knowledge_map_question_bindings._STATEMENTS:
                conn.exec_driver_sql(statement.replace("%", "%%"))
            openilink_hub_bot_links = import_module(
                "migrations.versions.20260727_69_openilink_hub_bot_links"
            )
            for statement in openilink_hub_bot_links._STATEMENTS:
                conn.exec_driver_sql(statement.replace("%", "%%"))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False),
    )
    fixture_revision = "20260727_64" if target_revision == "20260727_64" else "20260727_69"
    command.stamp(config, fixture_revision)
    command.upgrade(config, target_revision)


def _split_postgres_statements(script: str) -> list[str]:
    """Split fixture DDL without breaking quoted PL/pgSQL function bodies."""

    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_tag: str | None = None
    line_comment = False
    block_comment_depth = 0

    while index < len(script):
        if dollar_tag is not None:
            if script.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
            else:
                index += 1
            continue
        if line_comment:
            if script[index] == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment_depth:
            if script.startswith("/*", index):
                block_comment_depth += 1
                index += 2
            elif script.startswith("*/", index):
                block_comment_depth -= 1
                index += 2
            else:
                index += 1
            continue
        if quote is not None:
            if script[index] == quote:
                if index + 1 < len(script) and script[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if script.startswith("--", index):
            line_comment = True
            index += 2
            continue
        if script.startswith("/*", index):
            block_comment_depth = 1
            index += 2
            continue
        if script[index] in {"'", '"'}:
            quote = script[index]
            index += 1
            continue
        if script[index] == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", script[index:])
            if match is not None:
                dollar_tag = match.group(0)
                index += len(dollar_tag)
                continue
        if script[index] == ";":
            statement = script[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1

    trailing = script[start:].strip()
    if trailing:
        statements.append(trailing)
    if quote is not None or dollar_tag is not None or block_comment_depth:
        raise ValueError("minimal_schema.sql contains an unterminated SQL construct")
    return statements


def _drop_test_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("drop table if exists public.alembic_version"))
        conn.execute(text("drop schema if exists messaging cascade"))
        conn.execute(text("drop schema if exists product cascade"))
        conn.execute(text("drop schema if exists learner cascade"))
        conn.execute(text("drop schema if exists ai_audit cascade"))
        conn.execute(text("drop schema if exists commerce cascade"))
        conn.execute(text("drop schema if exists platform cascade"))
        conn.execute(text("drop schema if exists learning cascade"))
        conn.execute(text("drop schema if exists privacy cascade"))
