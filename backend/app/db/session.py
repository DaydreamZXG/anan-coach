from __future__ import annotations

from collections.abc import Generator
from hashlib import sha256
import json
import re
from threading import Lock
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.platform.job_consumer_principals import (
    JobConsumerPrincipalContractError,
    heartbeat_database_principal,
)


class DatabaseConfigurationError(RuntimeError):
    """Raised when a database-backed route is used without database config."""


class DatabasePublicationUnavailable(RuntimeError):
    """Commercial data is not backed by one active signed publication."""


# "GKDATA P1" encoded as one fixed positive int8. The publisher helper mirrors
# this protocol constant. It must never become an environment-specific value.
COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY = 0x474B444154415001
_PUBLICATION_GUARD_ATTRIBUTE = "_gongkao_commercial_publication_guard_v1"
_PUBLICATION_RUNTIME_IDENTITY_ATTRIBUTE = "_gongkao_commercial_publication_runtime_identity_v1"
_PUBLICATION_LOCK_RECORD_KEY = "gongkao_commercial_publication_shared_lock_v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


_engine: Engine | None = None
_engine_lock = Lock()
_session_factory: sessionmaker[Session] | None = None
_session_factory_lock = Lock()
_worker_heartbeat_engine: Engine | None = None
_worker_heartbeat_engine_lock = Lock()
_worker_heartbeat_session_factory: sessionmaker[Session] | None = None
_worker_heartbeat_session_factory_lock = Lock()
_worker_heartbeat_runtime_identity: tuple[str, str, str] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            settings = get_settings()
            if not settings.database_url:
                raise DatabaseConfigurationError("DATABASE_URL is required for database routes")
            options: dict[str, object] = {
                "pool_pre_ping": True,
                "future": True,
            }
            if make_url(settings.database_url).get_backend_name() == "postgresql":
                options.update(_postgres_engine_options(settings))
            _engine = create_engine(settings.database_url, **options)
            if make_url(settings.database_url).get_backend_name() == "postgresql":
                install_commercial_data_publication_guard(
                    _engine,
                    runtime_environment=settings.environment,
                    application_git_commit=settings.application_git_commit,
                    data_receipt_sha256=(settings.application_data_receipt_sha256),
                )
        return _engine


def _postgres_engine_options(settings: Settings) -> dict[str, object]:
    if settings.database_pool_size <= 0:
        raise DatabaseConfigurationError("DATABASE_POOL_SIZE must be positive")
    if settings.database_max_overflow < 0:
        raise DatabaseConfigurationError("DATABASE_MAX_OVERFLOW cannot be negative")
    if settings.database_pool_timeout_seconds <= 0:
        raise DatabaseConfigurationError("DATABASE_POOL_TIMEOUT_SECONDS must be positive")
    timeout_values = (
        settings.database_statement_timeout_ms,
        settings.database_lock_timeout_ms,
        settings.database_idle_transaction_timeout_ms,
    )
    if any(value <= 0 for value in timeout_values):
        raise DatabaseConfigurationError("Database timeout values must be positive")
    postgres_options = " ".join(
        (
            "-c jit=off",
            f"-c statement_timeout={settings.database_statement_timeout_ms}",
            f"-c lock_timeout={settings.database_lock_timeout_ms}",
            "-c idle_in_transaction_session_timeout="
            f"{settings.database_idle_transaction_timeout_ms}",
        )
    )
    return {
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "pool_recycle": 1_800,
        "connect_args": {"options": postgres_options},
    }


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    with _session_factory_lock:
        if _session_factory is None:
            _session_factory = sessionmaker(
                bind=get_engine(),
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
                future=True,
            )
        return _session_factory


def get_db_session() -> Generator[Session, None, None]:
    session_factory = get_session_factory()
    with session_factory() as session:
        yield session


def get_worker_heartbeat_session_factory(
    *,
    consumer_unit: str,
    consumer_entrypoint: str,
) -> sessionmaker[Session]:
    """Return the function-only heartbeat authority used by worker processes.

    Every worker, including local runs, must use its own login whose only
    inherited database privilege is executing the heartbeat pulse function.
    """

    global _worker_heartbeat_engine
    global _worker_heartbeat_runtime_identity
    global _worker_heartbeat_session_factory
    try:
        required_login = heartbeat_database_principal(
            consumer_unit=consumer_unit,
            consumer_entrypoint=consumer_entrypoint,
        )
    except JobConsumerPrincipalContractError as exc:
        raise DatabaseConfigurationError("worker heartbeat identity is not registered") from exc
    requested_identity = (
        required_login,
        consumer_unit,
        consumer_entrypoint,
    )
    settings = get_settings()
    heartbeat_database_url = settings.worker_heartbeat_database_url.strip()
    if not heartbeat_database_url:
        raise DatabaseConfigurationError(
            "WORKER_HEARTBEAT_DATABASE_URL is required for every worker"
        )
    heartbeat_url = make_url(heartbeat_database_url)
    if heartbeat_url.get_backend_name() != "postgresql":
        raise DatabaseConfigurationError("WORKER_HEARTBEAT_DATABASE_URL must use PostgreSQL")
    if heartbeat_url.username != required_login:
        raise DatabaseConfigurationError(
            "WORKER_HEARTBEAT_DATABASE_URL must use this consumer's dedicated "
            f"{required_login} role"
        )
    if settings.database_url:
        runtime_url = make_url(settings.database_url)
        if heartbeat_url == runtime_url or (
            heartbeat_url.username is not None and heartbeat_url.username == runtime_url.username
        ):
            raise DatabaseConfigurationError(
                "worker heartbeat and API database identities must be distinct"
            )
    if _worker_heartbeat_session_factory is not None:
        if _worker_heartbeat_runtime_identity != requested_identity:
            raise DatabaseConfigurationError(
                "worker heartbeat pool is already bound to another consumer"
            )
        return _worker_heartbeat_session_factory
    with _worker_heartbeat_session_factory_lock:
        if _worker_heartbeat_session_factory is not None:
            if _worker_heartbeat_runtime_identity != requested_identity:
                raise DatabaseConfigurationError(
                    "worker heartbeat pool is already bound to another consumer"
                )
            return _worker_heartbeat_session_factory
        with _worker_heartbeat_engine_lock:
            if _worker_heartbeat_engine is None:
                options = _postgres_engine_options(settings)
                options["pool_size"] = 1
                options["max_overflow"] = 0
                _worker_heartbeat_engine = create_engine(
                    heartbeat_database_url,
                    **options,
                )
        _worker_heartbeat_session_factory = sessionmaker(
            bind=_worker_heartbeat_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
        _worker_heartbeat_runtime_identity = requested_identity
        return _worker_heartbeat_session_factory


def install_commercial_data_publication_guard(
    engine: Engine,
    *,
    runtime_environment: str | None = None,
    application_git_commit: str | None = None,
    data_receipt_sha256: str | None = None,
) -> Engine:
    """Install the session-level shared publication lock on a PostgreSQL engine.

    The shared advisory lock is acquired for the complete SQLAlchemy checkout,
    then released on checkin. A publisher first persists ``publishing`` and then
    waits for the corresponding exclusive lock, which drains already checked
    out work and prevents new work from entering until publication finishes.
    """

    if engine.dialect.name != "postgresql":
        return engine
    if getattr(engine, _PUBLICATION_GUARD_ATTRIBUTE, False):
        return engine
    if runtime_environment is None or application_git_commit is None or data_receipt_sha256 is None:
        settings = get_settings()
        runtime_environment = (
            settings.environment if runtime_environment is None else runtime_environment
        )
        application_git_commit = (
            settings.application_git_commit
            if application_git_commit is None
            else application_git_commit
        )
        data_receipt_sha256 = (
            settings.application_data_receipt_sha256
            if data_receipt_sha256 is None
            else data_receipt_sha256
        )

    def checkout_with_runtime_identity(
        dbapi_connection: Any,
        connection_record: Any,
        connection_proxy: Any,
    ) -> None:
        _publication_guard_checkout(
            dbapi_connection,
            connection_record,
            connection_proxy,
            runtime_environment=runtime_environment,
            application_git_commit=application_git_commit,
            data_receipt_sha256=data_receipt_sha256,
        )

    event.listen(engine, "checkout", checkout_with_runtime_identity)
    event.listen(engine, "checkin", _publication_guard_checkin)
    setattr(engine, _PUBLICATION_GUARD_ATTRIBUTE, True)
    setattr(
        engine,
        _PUBLICATION_RUNTIME_IDENTITY_ATTRIBUTE,
        (
            runtime_environment,
            application_git_commit,
            data_receipt_sha256,
        ),
    )
    return engine


def commercial_data_publication_guard_installed(engine: Engine) -> bool:
    return bool(getattr(engine, _PUBLICATION_GUARD_ATTRIBUTE, False))


def commercial_data_publication_runtime_identity(
    engine: Engine,
) -> tuple[str, str, str] | None:
    identity = getattr(
        engine,
        _PUBLICATION_RUNTIME_IDENTITY_ATTRIBUTE,
        None,
    )
    if (
        not isinstance(identity, tuple)
        or len(identity) != 3
        or not all(isinstance(value, str) for value in identity)
    ):
        return None
    return identity


def _publication_guard_checkout(
    dbapi_connection: Any,
    connection_record: Any,
    _connection_proxy: Any,
    *,
    runtime_environment: str = "local",
    application_git_commit: str = "",
    data_receipt_sha256: str = "",
) -> None:
    cursor = dbapi_connection.cursor()
    locked = False
    try:
        cursor.execute(
            "select pg_advisory_lock_shared(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        locked = True
        cursor.execute(
            """
            select
              singleton_key,
              state,
              request_digest,
              request_payload::text,
              publication_digest,
              previous_receipt_sha256,
              active_receipt_sha256,
              expected_database_revision,
              compatible_application_commits::text,
              activated_at
            from platform.commercial_data_publication_state
            order by singleton_key
            """
        )
        rows = cursor.fetchall()
        cursor.execute("select version_num from public.alembic_version order by version_num")
        revisions = [str(row[0]) for row in cursor.fetchall()]
        _validate_runtime_publication(
            rows=rows,
            revisions=revisions,
            runtime_environment=runtime_environment,
            application_git_commit=application_git_commit,
            data_receipt_sha256=data_receipt_sha256,
        )
        # The lock is session-scoped, so end the validation transaction before
        # the connection is handed to application code.
        dbapi_connection.rollback()
        connection_record.info[_PUBLICATION_LOCK_RECORD_KEY] = True
    except Exception as exc:
        if locked:
            try:
                cursor.execute(
                    "select pg_advisory_unlock_shared(%s)",
                    (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
                )
                dbapi_connection.rollback()
            except Exception as unlock_exc:
                raise DatabasePublicationUnavailable(
                    "commercial data publication validation and lock release failed"
                ) from unlock_exc
        connection_record.info.pop(_PUBLICATION_LOCK_RECORD_KEY, None)
        if isinstance(exc, DatabasePublicationUnavailable):
            raise
        raise DatabasePublicationUnavailable(
            "commercial data publication authority is unavailable"
        ) from exc
    finally:
        cursor.close()


def _publication_guard_checkin(
    dbapi_connection: Any | None,
    connection_record: Any,
) -> None:
    locked = bool(connection_record.info.pop(_PUBLICATION_LOCK_RECORD_KEY, False))
    if not locked or dbapi_connection is None:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(
            "select pg_advisory_unlock_shared(%s)",
            (COMMERCIAL_DATA_PUBLICATION_ADVISORY_LOCK_KEY,),
        )
        result = cursor.fetchone()
        if result is None or result[0] is not True:
            raise DatabasePublicationUnavailable("commercial data publication shared lock was lost")
    finally:
        cursor.close()
        dbapi_connection.rollback()


def _validate_runtime_publication(
    *,
    rows: list[tuple[Any, ...]],
    revisions: list[str],
    runtime_environment: str = "local",
    application_git_commit: str = "",
    data_receipt_sha256: str = "",
) -> None:
    if len(rows) != 1:
        raise DatabasePublicationUnavailable(
            "commercial data publication authority must contain one row"
        )
    (
        singleton_key,
        state,
        stored_request_digest,
        encoded_request,
        publication_digest,
        previous_receipt,
        active_receipt,
        expected_revision,
        encoded_commits,
        activated_at,
    ) = rows[0]
    try:
        request = json.loads(encoded_request)
        commits = json.loads(encoded_commits)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DatabasePublicationUnavailable(
            "commercial data publication authority JSON is invalid"
        ) from exc
    actual_request_digest = sha256(
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    expected_commits = [
        request.get("candidateApplicationCommit"),
        request.get("rollbackApplicationCommit"),
    ]
    expected_publication_digest = sha256(
        json.dumps(
            {
                "schemaVersion": "commercial-data-publication@v1",
                "requestDigest": stored_request_digest,
                "activeReceiptSha256": active_receipt,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        singleton_key is not True
        or state != "active"
        or not isinstance(stored_request_digest, str)
        or stored_request_digest != actual_request_digest
        or not isinstance(publication_digest, str)
        or not _SHA256.fullmatch(publication_digest)
        or not isinstance(previous_receipt, str)
        or not _SHA256.fullmatch(previous_receipt)
        or not isinstance(active_receipt, str)
        or not _SHA256.fullmatch(active_receipt)
        or publication_digest != expected_publication_digest
        or activated_at is None
        or not isinstance(expected_revision, str)
        or request.get("schemaVersion") != "commercial-data-publication-request@v1"
        or request.get("expectedDatabaseRevision") != expected_revision
        or commits != expected_commits
        or revisions != [expected_revision]
    ):
        raise DatabasePublicationUnavailable(
            "commercial data publication authority is not active and exact"
        )
    if runtime_environment != "local" and (
        re.fullmatch(r"[A-Za-z0-9._-]+", runtime_environment) is None
        or re.fullmatch(
            r"[0-9a-f]{40,64}",
            application_git_commit,
        )
        is None
        or request.get("deploymentEnvironment") != runtime_environment
        or application_git_commit not in commits
        or not _SHA256.fullmatch(data_receipt_sha256)
        or active_receipt != data_receipt_sha256
    ):
        raise DatabasePublicationUnavailable(
            "runtime application identity does not match the data publication"
        )
