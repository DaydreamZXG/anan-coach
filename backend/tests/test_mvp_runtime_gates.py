from __future__ import annotations

from app.core.config import Settings
from app.db import session as database_session


def test_regular_postgres_engine_does_not_install_enterprise_publication_gate(
    monkeypatch,
) -> None:
    settings = Settings(
        environment="local",
        database_url="postgresql+psycopg://user:password@127.0.0.1:5432/gongkao_test",
    )
    monkeypatch.setattr(database_session, "get_settings", lambda: settings)
    monkeypatch.setattr(database_session, "_engine", None)

    engine = database_session.get_engine()
    try:
        assert database_session.commercial_data_publication_guard_installed(engine) is False
    finally:
        engine.dispose()
        monkeypatch.setattr(database_session, "_engine", None)
