from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


class DatabaseConfigurationError(RuntimeError):
    """Raised when a database-backed route is used without database config."""


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    if not settings.database_url:
        raise DatabaseConfigurationError("DATABASE_URL is required for database routes")
    options: dict[str, object] = {
        "pool_pre_ping": True,
        "future": True,
    }
    if make_url(settings.database_url).get_backend_name() == "postgresql":
        options.update(_postgres_engine_options(settings))
    return create_engine(settings.database_url, **options)


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


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def get_db_session() -> Generator[Session, None, None]:
    session_factory = get_session_factory()
    with session_factory() as session:
        yield session
