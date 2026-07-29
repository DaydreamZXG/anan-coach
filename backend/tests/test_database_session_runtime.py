from __future__ import annotations

import pytest

from app.core.config import Settings
from app.db.session import DatabaseConfigurationError, _postgres_engine_options


def test_postgres_runtime_bounds_pool_and_database_waits() -> None:
    options = _postgres_engine_options(Settings())

    assert options["pool_size"] == 5
    assert options["max_overflow"] == 5
    assert options["pool_timeout"] == 5.0
    assert options["pool_recycle"] == 1_800
    connect_args = options["connect_args"]
    assert isinstance(connect_args, dict)
    assert connect_args["options"] == (
        "-c statement_timeout=15000 "
        "-c lock_timeout=3000 "
        "-c idle_in_transaction_session_timeout=120000"
    )


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
