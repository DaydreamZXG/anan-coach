from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.session import get_db_session
from app.main import create_app


def test_ready_rejects_missing_position_rule_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "position rule release is not ready"


def test_ready_rejects_expired_position_rule_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(
        db_session_factory,
        activated_at=datetime.now(UTC) - timedelta(days=366),
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "position rule release is not ready"


def test_ready_reports_active_position_rule_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert datetime.fromisoformat(payload["time"]).tzinfo is not None
    assert payload == {
        "status": "ready",
        "migration": get_settings().required_database_revision,
        "positionRuleRelease": "guokao-2026-rules-v1",
        "positionDataVersion": "guokao-2026-source-sha256-test",
        "time": payload["time"],
    }


def _client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def _seed_alembic_revision(db_session_factory: sessionmaker[Session]) -> None:
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "create table if not exists alembic_version "
                "(version_num varchar(32) not null)"
            )
        )
        session.execute(text("delete from alembic_version"))
        session.execute(
            text("insert into alembic_version (version_num) values (:revision)"),
            {"revision": get_settings().required_database_revision},
        )


def _seed_release(
    db_session_factory: sessionmaker[Session],
    *,
    activated_at: datetime,
) -> None:
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  cast(:release_id as uuid), 'guokao', 2026,
                  'guokao-2026-rules-v1', 'guokao-2026-source-sha256-test',
                  'active', :activated_at
                )
                """
            ),
            {
                "release_id": str(uuid4()),
                "activated_at": activated_at,
            },
        )
