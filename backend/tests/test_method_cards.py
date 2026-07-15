from __future__ import annotations

from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.deps import get_auth_repository
from app.domains.auth.repository import AuthenticatedUser
from app.domains.method_cards.importer import (
    MethodCardImportError,
    import_method_cards,
)
from app.domains.method_cards.repository import MethodCardRepository
from app.domains.method_cards.service import MethodCardNotFoundError, MethodCardService
from app.main import create_app


FIXTURE_DIR = Path(__file__).with_name("fixtures").joinpath("method_cards")
DATA_METHOD_CARDS_DIR = Path(__file__).resolve().parents[2].joinpath(
    "data",
    "method_cards",
)


class FakeAuthRepository:
    def get_user_by_access_token(self, access_token: str) -> AuthenticatedUser | None:
        if access_token != "valid-session":
            return None
        return AuthenticatedUser(
            user_id="00000000-0000-0000-0000-000000000001",
            learner_key="00000000-0000-0000-0000-000000000001",
            display_name=None,
            email=None,
        )


def make_client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    return TestClient(app)


def test_import_method_cards_is_idempotent_and_updates_existing_row(
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = FIXTURE_DIR / "mc_ziliao_jiqiliang_published.yaml"
    yaml_path = tmp_path / source.name
    yaml_text = source.read_text(encoding="utf-8")
    yaml_path.write_text(yaml_text, encoding="utf-8")

    first = import_method_cards(session=db_session, paths=[yaml_path])
    updated_text = yaml_text.replace(
        "title: 基期量计算：化除为乘",
        "title: 基期量计算：化除为乘更新版",
    )
    yaml_path.write_text(updated_text, encoding="utf-8")
    second = import_method_cards(session=db_session, paths=[yaml_path])

    row = db_session.execute(
        text(
            """
            select count(*) as row_count, max(title) as title
            from product.method_cards
            where id = 'mc_ziliao_jiqiliang_01'
            """
        )
    ).mappings().one()

    assert first.card_count == 1
    assert second.card_count == 1
    assert int(row["row_count"]) == 1
    assert row["title"] == "基期量计算：化除为乘更新版"


def test_data_method_card_files_are_importable_drafts(db_session: Session) -> None:
    result = import_method_cards(session=db_session, paths=[DATA_METHOD_CARDS_DIR])

    row = db_session.execute(
        text(
            """
            select count(*) as card_count,
                   count(*) filter (where status = 'draft') as draft_count,
                   count(*) filter (where validation ->> 'reviewer' is null) as pending_review
            from product.method_cards
            where id = any(:card_ids)
            """
        ),
        {"card_ids": result.card_ids},
    ).mappings().one()

    assert result.card_count >= 13
    assert int(row["card_count"]) == result.card_count
    assert int(row["draft_count"]) == result.card_count
    assert int(row["pending_review"]) == result.card_count


def test_import_rejects_published_card_without_validation_threshold(
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = FIXTURE_DIR / "mc_ziliao_jiqiliang_published.yaml"
    yaml_path = tmp_path / source.name
    yaml_path.write_text(
        source.read_text(encoding="utf-8").replace("passed_count: 8", "passed_count: 7"),
        encoding="utf-8",
    )

    try:
        import_method_cards(session=db_session, paths=[yaml_path])
    except MethodCardImportError as exc:
        assert "published cards require tested_count >= 10 and passed_count >= 8" in str(exc)
    else:
        raise AssertionError("Expected MethodCardImportError")


def test_import_rejects_duplicate_ids_in_same_batch(
    db_session: Session,
    tmp_path: Path,
) -> None:
    source_text = (FIXTURE_DIR / "mc_ziliao_jiqiliang_published.yaml").read_text(
        encoding="utf-8"
    )
    (tmp_path / "a.yaml").write_text(source_text, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(source_text, encoding="utf-8")

    try:
        import_method_cards(session=db_session, paths=[tmp_path])
    except MethodCardImportError as exc:
        assert "Duplicate method-card id(s): mc_ziliao_jiqiliang_01" in str(exc)
    else:
        raise AssertionError("Expected MethodCardImportError")


def test_method_card_service_returns_only_published_cards(db_session: Session) -> None:
    import_method_cards(session=db_session, paths=[FIXTURE_DIR])
    service = MethodCardService(MethodCardRepository(db_session))

    card = service.get_card("mc_ziliao_jiqiliang_01")

    assert card.status == "published"
    assert card.title == "基期量计算：化除为乘"
    assert card.method_steps[0].startswith("基期")
    try:
        service.get_card("mc_ziliao_jiqiliang_draft_01")
    except MethodCardNotFoundError:
        pass
    else:
        raise AssertionError("Draft card must not be returned by service")


def test_method_card_api_requires_bearer_and_hides_internal_fields(
    db_session: Session,
    db_session_factory: sessionmaker[Session],
) -> None:
    import_method_cards(session=db_session, paths=[FIXTURE_DIR])
    client = make_client(db_session_factory)

    missing_auth = client.get("/api/v1/method-cards/mc_ziliao_jiqiliang_01")
    response = client.get(
        "/api/v1/method-cards/mc_ziliao_jiqiliang_01",
        headers={"Authorization": "Bearer valid-session"},
    )

    assert missing_auth.status_code == 401
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "mc_ziliao_jiqiliang_01"
    assert body["examScope"] == "guokao"
    assert body["track"] == "xingce"
    assert body["methodSteps"][0].startswith("基期")
    assert body["errorSignatures"][0]["code"] == "base_period_formula_flip"
    assert "authorNote" not in body
    assert "validation" not in body


def test_method_card_api_returns_404_for_draft_or_missing_cards(
    db_session: Session,
    db_session_factory: sessionmaker[Session],
) -> None:
    import_method_cards(session=db_session, paths=[FIXTURE_DIR])
    client = make_client(db_session_factory)

    draft = client.get(
        "/api/v1/method-cards/mc_ziliao_jiqiliang_draft_01",
        headers={"Authorization": "Bearer valid-session"},
    )
    missing = client.get(
        "/api/v1/method-cards/mc_missing",
        headers={"Authorization": "Bearer valid-session"},
    )

    assert draft.status_code == 404
    assert missing.status_code == 404
