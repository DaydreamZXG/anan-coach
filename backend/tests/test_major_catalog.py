"""Regression tests for migration 20260731_89 (major catalog adoption).

GET /api/v2/positions/majors previously failed with UndefinedTable on every
database built from the migration chain because the major catalog tables only
existed via a manually applied script in production.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.main import create_app

MAJORS_TOKEN = "major-catalog-route-token"


def test_migrated_database_contains_seeded_major_catalog(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as session:
        counts = session.execute(
            text(
                """
                select
                  (select count(*) from product.major_categories),
                  (select count(*) from product.major_sub_categories),
                  (select count(*) from product.major_catalog)
                """
            )
        ).one()
        orphan_majors = session.execute(
            text(
                """
                select count(*)
                from product.major_catalog m
                left join product.major_sub_categories s
                  on s.code = m.sub_category_code
                where s.code is null
                """
            )
        ).scalar_one()

    categories, sub_categories, majors = counts
    assert categories > 0, "major_categories must be seeded by 20260731_89"
    assert sub_categories > 0, "major_sub_categories must be seeded by 20260731_89"
    assert majors > 0, "major_catalog must be seeded by 20260731_89"
    assert orphan_majors == 0, "every major must resolve to a sub category"


def test_majors_route_returns_full_tree_for_authenticated_user(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
            {"id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  :session_id, :user_id, :token_hash, 'major-catalog-test', :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(MAJORS_TOKEN),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )

    response = _client(db_session_factory).get(
        "/api/v2/positions/majors",
        headers={"Authorization": f"Bearer {MAJORS_TOKEN}"},
    )

    assert response.status_code == 200
    categories = response.json()["data"]["categories"]
    assert categories, "majors tree must not be empty"
    first = categories[0]
    assert first["code"] and first["name"]
    assert first["subCategories"], "category must expose sub categories"
    assert first["subCategories"][0]["majors"], "sub category must expose majors"


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    return TestClient(app)
