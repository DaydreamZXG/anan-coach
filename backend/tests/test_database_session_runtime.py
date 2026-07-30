from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest

from app.core.config import Settings, get_settings
from app.db import session as session_module
from app.db.session import DatabaseConfigurationError, _postgres_engine_options
from app.platform.job_consumer_principals import (
    JOB_CONSUMER_PRINCIPALS,
    JobConsumerPrincipal,
)


CAUSE_AGENT_IDENTITY = {
    "consumer_unit": "gongkao-dazi-cause-agent-worker",
    "consumer_entrypoint": "app.workers.xingce_cause_agent",
}


def test_postgres_runtime_bounds_pool_and_database_waits() -> None:
    options = _postgres_engine_options(Settings())

    assert options["pool_size"] == 5
    assert options["max_overflow"] == 5
    assert options["pool_timeout"] == 12.0
    assert options["pool_recycle"] == 1_800
    connect_args = options["connect_args"]
    assert isinstance(connect_args, dict)
    assert connect_args["options"] == (
        "-c jit=off "
        "-c statement_timeout=15000 "
        "-c lock_timeout=3000 "
        "-c idle_in_transaction_session_timeout=120000"
    )


def test_nonlocal_runtime_requires_an_immutable_application_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "preproduction")
    monkeypatch.delenv("APP_GIT_COMMIT", raising=False)
    monkeypatch.delenv("APP_DATA_RECEIPT_SHA256", raising=False)
    with pytest.raises(ValueError, match="APP_GIT_COMMIT"):
        get_settings()

    monkeypatch.setenv("APP_GIT_COMMIT", "4" * 40)
    with pytest.raises(ValueError, match="APP_DATA_RECEIPT_SHA256"):
        get_settings()

    monkeypatch.setenv("APP_DATA_RECEIPT_SHA256", "5" * 64)
    settings = get_settings()
    assert settings.environment == "preproduction"
    assert settings.application_git_commit == "4" * 40
    assert settings.application_data_receipt_sha256 == "5" * 64


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("database_pool_size", 0),
        ("database_max_overflow", -1),
        ("database_pool_timeout_seconds", 0),
        ("database_statement_timeout_ms", 0),
        ("database_lock_timeout_ms", 0),
        ("database_idle_transaction_timeout_ms", 0),
    ),
)
def test_postgres_runtime_rejects_unbounded_or_invalid_values(
    field: str,
    value: int,
) -> None:
    settings = Settings(**{field: value})

    with pytest.raises(DatabaseConfigurationError):
        _postgres_engine_options(settings)


def test_engine_is_constructed_once_under_concurrent_cold_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callers = 40
    barrier = Barrier(callers)
    construction_lock = Lock()
    constructions = 0
    fake_engine = object()

    def fake_create_engine(*_args: object, **_kwargs: object) -> object:
        nonlocal constructions
        with construction_lock:
            constructions += 1
        return fake_engine

    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(database_url="sqlite+pysqlite:///:memory:"),
    )
    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)

    def cold_start() -> object:
        barrier.wait()
        return session_module.get_engine()

    with ThreadPoolExecutor(max_workers=callers) as executor:
        engines = tuple(executor.map(lambda _index: cold_start(), range(callers)))

    assert constructions == 1
    assert all(engine is fake_engine for engine in engines)


def test_local_worker_heartbeat_also_requires_its_dedicated_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(environment="local"),
    )

    with pytest.raises(
        DatabaseConfigurationError,
        match="required for every worker",
    ):
        session_module.get_worker_heartbeat_session_factory(
            **CAUSE_AGENT_IDENTITY,
        )


def test_nonlocal_worker_heartbeat_requires_a_distinct_dedicated_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_url = "postgresql+psycopg://gongkao_api:api-secret@db.example/gongkao_dazi"
    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(
            environment="production",
            database_url=runtime_url,
        ),
    )
    with pytest.raises(
        DatabaseConfigurationError,
        match="WORKER_HEARTBEAT_DATABASE_URL is required",
    ):
        session_module.get_worker_heartbeat_session_factory(**CAUSE_AGENT_IDENTITY)

    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(
            environment="production",
            database_url=runtime_url,
            worker_heartbeat_database_url=runtime_url,
        ),
    )
    with pytest.raises(
        DatabaseConfigurationError,
        match="this consumer's dedicated",
    ):
        session_module.get_worker_heartbeat_session_factory(**CAUSE_AGENT_IDENTITY)


def test_worker_heartbeat_uses_a_bounded_independent_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat_url = (
        "postgresql+psycopg://gongkao_worker_heartbeat_cause_agent_login:"
        "heartbeat-secret@db.example/gongkao_dazi"
    )
    captured: dict[str, object] = {}
    fake_engine = object()

    def fake_create_engine(
        url: str,
        **options: object,
    ) -> object:
        captured["url"] = url
        captured["options"] = options
        return fake_engine

    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(
            environment="production",
            database_url=("postgresql+psycopg://gongkao_api:api-secret@db.example/gongkao_dazi"),
            worker_heartbeat_database_url=heartbeat_url,
        ),
    )
    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(session_module, "_worker_heartbeat_engine", None)
    monkeypatch.setattr(
        session_module,
        "_worker_heartbeat_session_factory",
        None,
    )
    monkeypatch.setattr(
        session_module,
        "_worker_heartbeat_runtime_identity",
        None,
    )

    factory = session_module.get_worker_heartbeat_session_factory(**CAUSE_AGENT_IDENTITY)

    assert factory.kw["bind"] is fake_engine
    assert captured["url"] == heartbeat_url
    options = captured["options"]
    assert isinstance(options, dict)
    assert options["pool_size"] == 1
    assert options["max_overflow"] == 0


@pytest.mark.parametrize("binding", JOB_CONSUMER_PRINCIPALS)
def test_each_worker_requires_its_own_heartbeat_login(
    binding: JobConsumerPrincipal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sibling = next(
        candidate
        for candidate in JOB_CONSUMER_PRINCIPALS
        if candidate.database_principal != binding.database_principal
    )
    heartbeat_url = (
        f"postgresql+psycopg://{sibling.database_principal}:"
        "heartbeat-secret@db.example/gongkao_dazi"
    )
    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(
            environment="production",
            database_url=("postgresql+psycopg://gongkao_api:api-secret@db.example/gongkao_dazi"),
            worker_heartbeat_database_url=heartbeat_url,
        ),
    )

    with pytest.raises(
        DatabaseConfigurationError,
        match=binding.database_principal,
    ):
        session_module.get_worker_heartbeat_session_factory(
            consumer_unit=binding.consumer_unit,
            consumer_entrypoint=binding.consumer_entrypoint,
        )
