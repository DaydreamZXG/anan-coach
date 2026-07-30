from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

from alembic import command as alembic_command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.auth.repository import token_hash_for_tests
from app.domains.data_rights.repository import (
    CONFIRMATION_PHRASE,
    DataRightsRepository,
)
from app.domains.data_rights.schemas import AccountDeletionCreateRequest
from app.domains.data_rights.service import DataRightsService
from app.domains.data_rights.worker import DataRightsWorker
from app.domains.xingce_learning.favorites.repository import (
    QuestionFavoriteRepository,
)
from app.domains.xingce_learning.favorites.schemas import (
    FAVORITE_PREVIEW_MODULE_MAX_LENGTH,
    FAVORITE_PREVIEW_STEM_MAX_LENGTH,
    QuestionFavoriteReceipt,
    SetQuestionFavoriteCommand,
)
from app.domains.xingce_learning.favorites.service import (
    QuestionFavoriteService,
)
from app.main import create_app
from tests.factories.frozen_release import seed_route_frozen_release
from tests.test_planning_routes import (
    _client as planning_client,
    _seed_learner_and_published_essay_content,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_question_favorite_routes_require_authentication() -> None:
    client = TestClient(create_app())
    question_id = "auth-required-question"
    source_session_id = str(uuid4())
    requests = (
        client.get("/api/v2/xingce/question-favorites"),
        client.get(f"/api/v2/xingce/question-favorites/{question_id}"),
        client.put(
            f"/api/v2/xingce/question-favorites/{question_id}",
            headers={"Idempotency-Key": "favorite-auth-required"},
            json={
                "desiredState": "favorited",
                "contentVersionId": "a" * 64,
                "sourceSessionId": source_session_id,
            },
        ),
    )

    assert [response.status_code for response in requests] == [401, 401, 401]
    assert {response.json()["code"] for response in requests} == {"authentication_required"}


def test_set_state_exact_replay_drift_and_mark_independence(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, client, auth, started = _prepare_source(
        db_session_factory,
        token="favorite-contract-token",
    )
    question = started["bundle"]["questions"][0]
    question_id = question["questionId"]
    body = _favorite_body(question=question, session_id=started["sessionId"])
    path = f"/api/v2/xingce/question-favorites/{question_id}"

    before_checkpoint = _checkpoint(
        db_session_factory,
        user_id=user_id,
        session_id=started["sessionId"],
    )
    first = client.put(
        path,
        headers={**auth, "Idempotency-Key": "favorite-set-command-0001"},
        json=body,
    )
    replay = client.put(
        path,
        headers={**auth, "Idempotency-Key": "favorite-set-command-0001"},
        json=body,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert first.json()["data"] == {
        "contractVersion": "xingce-question-favorite@v1",
        "questionId": question_id,
        "favoriteState": "favorited",
        "revision": 1,
        "savedContentVersionId": question["contentVersionId"],
        "favoritedAt": first.json()["data"]["favoritedAt"],
        "updatedAt": first.json()["data"]["updatedAt"],
    }
    assert "preview" not in first.json()["data"]
    assert "availability" not in first.json()["data"]

    drift = client.put(
        path,
        headers={**auth, "Idempotency-Key": "favorite-set-command-0001"},
        json={"desiredState": "not_favorited"},
    )
    assert drift.status_code == 409
    assert drift.json()["code"] == "xingce_favorite_idempotency_key_reused"

    repeated_set = client.put(
        path,
        headers={**auth, "Idempotency-Key": "favorite-set-command-0002"},
        json=body,
    )
    assert repeated_set.status_code == 200
    assert repeated_set.json()["data"]["revision"] == 1

    state = client.get(path, headers=auth)
    assert state.status_code == 200
    assert state.json()["data"]["favoriteState"] == "favorited"
    assert state.json()["data"]["availability"] == "available"
    assert state.json()["data"]["contentChanged"] is False

    page = client.get("/api/v2/xingce/question-favorites", headers=auth)
    assert page.status_code == 200
    item = page.json()["data"]["items"][0]
    assert item["questionId"] == question_id
    assert item["preview"]["stem"] == question["stem"]
    serialized = json.dumps(item, ensure_ascii=False)
    assert "options" not in serialized
    assert "answer" not in serialized
    assert "startUrl" not in serialized

    removed = client.put(
        path,
        headers={**auth, "Idempotency-Key": "favorite-unset-command-0001"},
        json={"desiredState": "not_favorited"},
    )
    repeated_remove = client.put(
        path,
        headers={**auth, "Idempotency-Key": "favorite-unset-command-0002"},
        json={"desiredState": "not_favorited"},
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["revision"] == 2
    assert removed.json()["data"]["favoriteState"] == "not_favorited"
    assert repeated_remove.status_code == 200
    assert repeated_remove.json()["data"]["revision"] == 2
    assert (
        client.get(
            "/api/v2/xingce/question-favorites",
            headers=auth,
        ).json()["data"]["items"]
        == []
    )

    assert (
        _checkpoint(
            db_session_factory,
            user_id=user_id,
            session_id=started["sessionId"],
        )
        == before_checkpoint
    )
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    """
                select count(*)
                from learning.xingce_cause_candidate_versions
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )


def test_source_ownership_unknown_question_and_account_isolation(
    db_session_factory: sessionmaker[Session],
) -> None:
    first_user_id, client, first_auth, started = _prepare_source(
        db_session_factory,
        token="favorite-owner-token",
    )
    second_user_id = _seed_account(
        db_session_factory,
        token="favorite-other-token",
    )
    second_auth = {"Authorization": "Bearer favorite-other-token"}
    question = started["bundle"]["questions"][0]
    question_id = question["questionId"]
    path = f"/api/v2/xingce/question-favorites/{question_id}"

    wrong_source = client.put(
        path,
        headers={**first_auth, "Idempotency-Key": "favorite-wrong-source-0001"},
        json={
            **_favorite_body(
                question=question,
                session_id=str(uuid4()),
            ),
        },
    )
    assert wrong_source.status_code == 404
    assert wrong_source.json()["code"] == "xingce_favorite_source_not_found"

    unknown = client.get(
        "/api/v2/xingce/question-favorites/not-a-question",
        headers=first_auth,
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "xingce_favorite_question_not_found"

    created = client.put(
        path,
        headers={**first_auth, "Idempotency-Key": "favorite-owner-set-0001"},
        json=_favorite_body(
            question=question,
            session_id=started["sessionId"],
        ),
    )
    assert created.status_code == 200

    other_state = client.get(path, headers=second_auth)
    other_page = client.get(
        "/api/v2/xingce/question-favorites",
        headers=second_auth,
    )
    assert other_state.status_code == 200
    assert other_state.json()["data"]["favoriteState"] == "not_favorited"
    assert other_state.json()["data"]["revision"] == 0
    other_serialized = json.dumps(other_state.json()["data"], ensure_ascii=False)
    assert "stem" not in other_serialized
    assert "options" not in other_serialized
    assert "answer" not in other_serialized
    assert "preview" not in other_serialized
    assert other_page.status_code == 200
    assert other_page.json()["data"]["items"] == []

    with db_session_factory() as session:
        owners = set(
            session.execute(
                text(
                    """
                    select user_id::text
                    from learning.xingce_question_favorite_states
                    where question_id = :question_id
                    """
                ),
                {"question_id": question_id},
            ).scalars()
        )
    assert owners == {first_user_id}
    assert second_user_id not in owners


def test_content_update_and_withdrawal_keep_safe_tombstone(
    db_session_factory: sessionmaker[Session],
) -> None:
    _user_id, client, auth, started = _prepare_source(
        db_session_factory,
        token="favorite-content-token",
    )
    question = started["bundle"]["questions"][0]
    question_id = question["questionId"]
    path = f"/api/v2/xingce/question-favorites/{question_id}"
    command_key = "favorite-content-set-0001"
    body = _favorite_body(
        question=question,
        session_id=started["sessionId"],
    )
    created = client.put(
        path,
        headers={**auth, "Idempotency-Key": command_key},
        json=body,
    )
    assert created.status_code == 200

    updated_version = "d" * 64
    updated_stem = "题" * (FAVORITE_PREVIEW_STEM_MAX_LENGTH + 40)
    updated_module = "模块" * FAVORITE_PREVIEW_MODULE_MAX_LENGTH
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.question_content_versions (
                  content_version_id, question_id, track, snapshot_payload,
                  normalized_search_text, created_at
                ) values (
                  :content_version_id, :question_id, 'xingce',
                  cast(:snapshot as jsonb), :search_text, :now
                )
                """
            ),
            {
                "content_version_id": updated_version,
                "question_id": question_id,
                "snapshot": json.dumps(
                    {
                        "question_id": question_id,
                        "module_name": updated_module,
                        "stem": updated_stem,
                    },
                    ensure_ascii=False,
                ),
                "search_text": updated_stem,
                "now": datetime.now(UTC),
            },
        )
        session.execute(
            text(
                """
                update product.question_content_heads
                set content_version_id = :content_version_id,
                    updated_at = :now
                where question_id = :question_id
                """
            ),
            {
                "content_version_id": updated_version,
                "question_id": question_id,
                "now": datetime.now(UTC),
            },
        )

    updated_state = client.get(path, headers=auth).json()["data"]
    updated_item = client.get(
        "/api/v2/xingce/question-favorites",
        headers=auth,
    ).json()["data"]["items"][0]
    assert updated_state["savedContentVersionId"] == question["contentVersionId"]
    assert updated_state["currentContentVersionId"] == updated_version
    assert updated_state["contentChanged"] is True
    assert updated_item["preview"]["stem"] == (
        updated_stem[: FAVORITE_PREVIEW_STEM_MAX_LENGTH - 1] + "…"
    )
    assert len(updated_item["preview"]["stem"]) == FAVORITE_PREVIEW_STEM_MAX_LENGTH
    assert updated_item["preview"]["moduleName"] == (
        updated_module[: FAVORITE_PREVIEW_MODULE_MAX_LENGTH - 1] + "…"
    )
    assert len(updated_item["preview"]["moduleName"]) == FAVORITE_PREVIEW_MODULE_MAX_LENGTH

    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.questions set status = 'retired' where question_id = :question_id"
            ),
            {"question_id": question_id},
        )

    tombstone_state = client.get(path, headers=auth)
    tombstone_page = client.get(
        "/api/v2/xingce/question-favorites",
        headers=auth,
    )
    assert tombstone_state.status_code == 200
    assert tombstone_state.json()["data"]["favoriteState"] == "favorited"
    assert tombstone_state.json()["data"]["availability"] == "unavailable"
    tombstone_item = tombstone_page.json()["data"]["items"][0]
    assert tombstone_item["preview"] is None
    assert "stem" not in json.dumps(tombstone_item, ensure_ascii=False)
    assert "options" not in json.dumps(tombstone_item, ensure_ascii=False)

    exact_replay = client.put(
        path,
        headers={**auth, "Idempotency-Key": command_key},
        json=body,
    )
    assert exact_replay.status_code == 200
    assert exact_replay.json()["data"] == created.json()["data"]
    assert "preview" not in exact_replay.json()["data"]

    new_set = client.put(
        path,
        headers={**auth, "Idempotency-Key": "favorite-content-set-0002"},
        json=body,
    )
    assert new_set.status_code == 409
    assert new_set.json()["code"] == "xingce_favorite_question_unavailable"

    removed = client.put(
        path,
        headers={**auth, "Idempotency-Key": "favorite-content-unset-0001"},
        json={"desiredState": "not_favorited"},
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["favoriteState"] == "not_favorited"


def test_favorite_list_live_keyset_is_scoped_and_refreshes_concurrent_moves(
    db_session_factory: sessionmaker[Session],
) -> None:
    _user_id, client, auth, started = _prepare_source(
        db_session_factory,
        token="favorite-page-token",
    )
    empty = client.get("/api/v2/xingce/question-favorites", headers=auth)
    assert empty.status_code == 200
    assert empty.json()["data"]["items"] == []
    assert empty.json()["data"]["nextCursor"] is None

    questions = started["bundle"]["questions"]
    for index, question in enumerate(questions):
        response = client.put(
            f"/api/v2/xingce/question-favorites/{question['questionId']}",
            headers={
                **auth,
                "Idempotency-Key": f"favorite-page-set-{index:04d}",
            },
            json=_favorite_body(
                question=question,
                session_id=started["sessionId"],
            ),
        )
        assert response.status_code == 200, response.text

    first = client.get(
        "/api/v2/xingce/question-favorites",
        headers=auth,
        params={"pageSize": 2},
    )
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert len(first_data["items"]) == 2
    assert first_data["nextCursor"]

    second = client.get(
        "/api/v2/xingce/question-favorites",
        headers=auth,
        params={"pageSize": 2, "cursor": first_data["nextCursor"]},
    )
    assert second.status_code == 200
    second_data = second.json()["data"]
    assert len(second_data["items"]) == 1
    assert second_data["nextCursor"] is None
    ids = [item["questionId"] for item in [*first_data["items"], *second_data["items"]]]
    assert len(ids) == len(set(ids)) == len(questions)

    invalid = client.get(
        "/api/v2/xingce/question-favorites",
        headers=auth,
        params={"cursor": "not-a-valid-cursor"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "xingce_favorite_cursor_invalid"

    tampered = client.get(
        "/api/v2/xingce/question-favorites",
        headers=auth,
        params={"cursor": f"{first_data['nextCursor']}$"},
    )
    assert tampered.status_code == 422
    assert tampered.json()["code"] == "xingce_favorite_cursor_invalid"

    live_first = client.get(
        "/api/v2/xingce/question-favorites",
        headers=auth,
        params={"pageSize": 1},
    ).json()["data"]
    assert live_first["nextCursor"]
    first_live_id = live_first["items"][0]["questionId"]
    moved_question = next(
        question for question in questions if question["questionId"] != first_live_id
    )
    moved_id = moved_question["questionId"]
    moved_path = f"/api/v2/xingce/question-favorites/{moved_id}"
    assert (
        client.put(
            moved_path,
            headers={
                **auth,
                "Idempotency-Key": "favorite-page-live-unset-0001",
            },
            json={"desiredState": "not_favorited"},
        ).status_code
        == 200
    )
    assert (
        client.put(
            moved_path,
            headers={
                **auth,
                "Idempotency-Key": "favorite-page-live-reset-0001",
            },
            json=_favorite_body(
                question=moved_question,
                session_id=started["sessionId"],
            ),
        ).status_code
        == 200
    )

    continued_ids: list[str] = []
    cursor = live_first["nextCursor"]
    while cursor is not None:
        continued = client.get(
            "/api/v2/xingce/question-favorites",
            headers=auth,
            params={"pageSize": 1, "cursor": cursor},
        )
        assert continued.status_code == 200
        continued_data = continued.json()["data"]
        continued_ids.extend(item["questionId"] for item in continued_data["items"])
        cursor = continued_data["nextCursor"]
    assert len(continued_ids) == len(set(continued_ids))
    assert first_live_id not in continued_ids
    assert moved_id not in continued_ids

    other_auth = {"Authorization": "Bearer favorite-page-other-token"}
    _seed_account(
        db_session_factory,
        token="favorite-page-other-token",
    )
    other_account_page = client.get(
        "/api/v2/xingce/question-favorites",
        headers=other_auth,
        params={"cursor": live_first["nextCursor"]},
    )
    assert other_account_page.status_code == 200
    assert other_account_page.json()["data"]["items"] == []

    refreshed = client.get(
        "/api/v2/xingce/question-favorites",
        headers=auth,
        params={"pageSize": 100},
    )
    assert refreshed.status_code == 200
    refreshed_ids = [item["questionId"] for item in refreshed.json()["data"]["items"]]
    assert len(refreshed_ids) == len(set(refreshed_ids)) == len(questions)
    assert moved_id in refreshed_ids


def test_question_lock_serializes_distinct_commands_and_revision(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, _client, _auth, started = _prepare_source(
        db_session_factory,
        token="favorite-race-token",
    )
    question = started["bundle"]["questions"][0]
    question_id = question["questionId"]
    body = SetQuestionFavoriteCommand.model_validate(
        _favorite_body(
            question=question,
            session_id=started["sessionId"],
        )
    )
    reached_question_lock = Event()

    class SignalingRepository(QuestionFavoriteRepository):
        def lock_question(
            self,
            session: Session,
            *,
            user_id: str,
            question_id: str,
        ) -> None:
            reached_question_lock.set()
            super().lock_question(
                session,
                user_id=user_id,
                question_id=question_id,
            )

    def remove_in_second_transaction() -> int:
        with db_session_factory() as session:
            result = QuestionFavoriteService(repository=SignalingRepository()).set_state(
                session,
                user_id=user_id,
                question_id=question_id,
                command_id="favorite-race-unset-0001",
                command=SetQuestionFavoriteCommand(desiredState="not_favorited"),
            )
            session.commit()
            return result.revision

    first_session = db_session_factory()
    try:
        repository = QuestionFavoriteRepository()
        repository.lock_question(
            first_session,
            user_id=user_id,
            question_id=question_id,
        )
        first = QuestionFavoriteService(repository=repository).set_state(
            first_session,
            user_id=user_id,
            question_id=question_id,
            command_id="favorite-race-set-0001",
            command=body,
        )
        assert first.revision == 1
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(remove_in_second_transaction)
            assert reached_question_lock.wait(timeout=5)
            first_session.commit()
            assert future.result(timeout=10) == 2
    finally:
        first_session.close()

    with db_session_factory() as session:
        stored = QuestionFavoriteRepository().state(
            session,
            user_id=user_id,
            question_id=question_id,
        )
        assert stored is not None
        assert stored["favorited"] is False
        assert stored["revision"] == 2


def test_question_lock_orders_timestamp_by_commit_not_request_start(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, _client, _auth, started = _prepare_source(
        db_session_factory,
        token="favorite-lock-order-token",
    )
    question = started["bundle"]["questions"][0]
    question_id = question["questionId"]
    waiting_before_question_lock = Event()
    allow_question_lock = Event()

    class DelayedQuestionLockRepository(QuestionFavoriteRepository):
        def lock_question(
            self,
            session: Session,
            *,
            user_id: str,
            question_id: str,
        ) -> None:
            waiting_before_question_lock.set()
            if not allow_question_lock.wait(timeout=10):
                raise TimeoutError("question-lock test was not released")
            super().lock_question(
                session,
                user_id=user_id,
                question_id=question_id,
            )

    def earlier_request_committing_last() -> QuestionFavoriteReceipt:
        with db_session_factory() as session:
            receipt = QuestionFavoriteService(repository=DelayedQuestionLockRepository()).set_state(
                session,
                user_id=user_id,
                question_id=question_id,
                command_id="favorite-lock-order-unset-0001",
                command=SetQuestionFavoriteCommand(desiredState="not_favorited"),
            )
            session.commit()
            return receipt

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(earlier_request_committing_last)
        assert waiting_before_question_lock.wait(timeout=5)
        try:
            with db_session_factory() as session:
                first = QuestionFavoriteService().set_state(
                    session,
                    user_id=user_id,
                    question_id=question_id,
                    command_id="favorite-lock-order-set-0001",
                    command=SetQuestionFavoriteCommand.model_validate(
                        _favorite_body(
                            question=question,
                            session_id=started["sessionId"],
                        )
                    ),
                )
                session.commit()
        finally:
            allow_question_lock.set()
        last = future.result(timeout=10)

    assert first.revision == 1
    assert last.revision == 2
    assert last.favorite_state == "not_favorited"
    assert first.updated_at is not None
    assert last.updated_at is not None
    assert last.updated_at >= first.updated_at


def test_state_and_receipt_roll_back_together_on_receipt_failure(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, _client, _auth, started = _prepare_source(
        db_session_factory,
        token="favorite-rollback-token",
    )
    question = started["bundle"]["questions"][0]

    class FailingReceiptRepository(QuestionFavoriteRepository):
        def insert_command(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("injected receipt failure")

    with db_session_factory() as session:
        with pytest.raises(RuntimeError, match="injected receipt failure"):
            QuestionFavoriteService(repository=FailingReceiptRepository()).set_state(
                session,
                user_id=user_id,
                question_id=question["questionId"],
                command_id="favorite-rollback-set-0001",
                command=SetQuestionFavoriteCommand.model_validate(
                    _favorite_body(
                        question=question,
                        session_id=started["sessionId"],
                    )
                ),
            )
        session.rollback()

    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    """
                select count(*)
                from learning.xingce_question_favorite_states
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    """
                select count(*)
                from learning.xingce_question_favorite_commands
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )


def test_database_rejects_revision_and_receipt_drift(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, client, auth, started = _prepare_source(
        db_session_factory,
        token="favorite-database-guard-token",
    )
    question = started["bundle"]["questions"][0]
    question_id = question["questionId"]
    created = client.put(
        f"/api/v2/xingce/question-favorites/{question_id}",
        headers={**auth, "Idempotency-Key": "favorite-database-guard-0001"},
        json=_favorite_body(
            question=question,
            session_id=started["sessionId"],
        ),
    )
    assert created.status_code == 200

    with pytest.raises(DBAPIError, match="invalid Xingce favorite state transition"):
        with db_session_factory.begin() as session:
            session.execute(
                text(
                    """
                    update learning.xingce_question_favorite_states
                    set revision = revision + 2
                    where user_id = cast(:user_id as uuid)
                      and question_id = :question_id
                    """
                ),
                {"user_id": user_id, "question_id": question_id},
            )

    for statement in (
        """
        update learning.xingce_question_favorite_commands
        set response_payload = '{}'::jsonb
        where user_id = cast(:user_id as uuid)
          and command_id = 'favorite-database-guard-0001'
        """,
        """
        delete from learning.xingce_question_favorite_commands
        where user_id = cast(:user_id as uuid)
          and command_id = 'favorite-database-guard-0001'
        """,
        """
        delete from learning.xingce_question_favorite_states
        where user_id = cast(:user_id as uuid)
          and question_id = :question_id
        """,
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            with db_session_factory.begin() as session:
                session.execute(
                    text(statement),
                    {"user_id": user_id, "question_id": question_id},
                )

    with db_session_factory() as session:
        stored = QuestionFavoriteRepository().state(
            session,
            user_id=user_id,
            question_id=question_id,
        )
        assert stored is not None
        assert stored["revision"] == 1
        assert (
            session.execute(
                text(
                    """
                select count(*)
                from learning.xingce_question_favorite_commands
                where user_id = cast(:user_id as uuid)
                  and command_id = 'favorite-database-guard-0001'
                """
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )


def test_favorites_are_exported_and_deleted_with_the_account(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, client, auth, started = _prepare_source(
        db_session_factory,
        token="favorite-privacy-token",
    )
    question = started["bundle"]["questions"][0]
    created = client.put(
        f"/api/v2/xingce/question-favorites/{question['questionId']}",
        headers={**auth, "Idempotency-Key": "favorite-privacy-set-0001"},
        json=_favorite_body(
            question=question,
            session_id=started["sessionId"],
        ),
    )
    assert created.status_code == 200

    now = datetime.now(UTC)
    with db_session_factory() as session:
        rights = DataRightsRepository(session)
        overview = rights.overview(user_id=user_id, now=now)
        learning_evidence = next(
            item for item in overview.categories if item.category == "learning_evidence"
        )
        assert learning_evidence.item_count >= 1
        exported = rights.build_export_payload(user_id=user_id, now=now)
        assert exported["questionFavorites"][0]["question_id"] == question["questionId"]
        assert (
            exported["questionFavorites"][0]["saved_content_version_id"]
            == question["contentVersionId"]
        )
        assert exported["questionFavoriteCommands"][0]["command_id"] == (
            "favorite-privacy-set-0001"
        )
        assert "stem" not in json.dumps(
            exported["questionFavoriteCommands"],
            ensure_ascii=False,
        )

        deletion = DataRightsService(
            rights,
            now=lambda: now,
        ).request_deletion(
            user_id=user_id,
            request=AccountDeletionCreateRequest(
                commandKey="favorite-account-delete-0001",
                confirmationPhrase=CONFIRMATION_PHRASE,
            ),
        )
        assert deletion.state == "queued"

        completed = DataRightsWorker(
            storage=_EmptyPrivateStorage(),
            settings=Settings(privacy_export_expiry_seconds=86_400),
            now=lambda: now + timedelta(seconds=1),
        ).process_next(session)
        assert completed is not None
        assert completed.status == "succeeded"
        manifest = dict(completed.result or {})["manifest"]
        erased = manifest["personalContentErased"]
        assert erased["xingce_question_favorite_states"] == 1
        assert erased["xingce_question_favorite_commands"] == 1

        assert (
            session.execute(
                text(
                    "select count(*) "
                    "from learning.xingce_question_favorite_states "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "select count(*) "
                    "from learning.xingce_question_favorite_commands "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )


def test_revision_74_schema_and_73_to_74_round_trip(
    db_engine_at_revision_76: Engine,
) -> None:
    inspector = inspect(db_engine_at_revision_76)
    assert {
        "xingce_question_favorite_states",
        "xingce_question_favorite_commands",
    }.issubset(inspector.get_table_names(schema="learning"))
    foreign_keys = {
        item["name"]
        for item in inspector.get_foreign_keys(
            "xingce_question_favorite_states",
            schema="learning",
        )
    }
    assert {
        "xingce_question_favorite_saved_content_fk",
        "xingce_question_favorite_source_session_fk",
    }.issubset(foreign_keys)

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        db_engine_at_revision_76.url.render_as_string(hide_password=False),
    )
    alembic_command.downgrade(config, "20260728_73")
    assert "xingce_question_favorite_states" not in inspect(
        db_engine_at_revision_76
    ).get_table_names(schema="learning")
    alembic_command.upgrade(config, "20260728_74")
    assert {
        "xingce_question_favorite_states",
        "xingce_question_favorite_commands",
    }.issubset(inspect(db_engine_at_revision_76).get_table_names(schema="learning"))


def _prepare_source(
    factory: sessionmaker[Session],
    *,
    token: str,
) -> tuple[str, TestClient, dict[str, str], dict[str, Any]]:
    user_id = _seed_learner_and_published_essay_content(
        factory,
        access_token=token,
        include_objective_questions=False,
    )
    seed_route_frozen_release(factory, count=3)
    client = planning_client(factory)
    auth = {"Authorization": f"Bearer {token}"}
    dashboard = client.get("/api/v2/plans/today", headers=auth)
    assert dashboard.status_code == 200, dashboard.text
    task = next(item for item in dashboard.json()["data"]["tasks"] if item["track"] == "xingce")
    started = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": f"favorite-source-{token}"},
        json={"planTaskId": task["id"]},
    )
    assert started.status_code == 200, started.text
    return user_id, client, auth, started.json()["data"]


def _seed_account(
    factory: sessionmaker[Session],
    *,
    token: str,
) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '收藏隔离账户')"
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid),
                  :token_hash, 'favorite-isolation-device', :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
    return user_id


def _favorite_body(
    *,
    question: dict[str, Any],
    session_id: str,
) -> dict[str, str]:
    return {
        "desiredState": "favorited",
        "contentVersionId": str(question["contentVersionId"]),
        "sourceSessionId": session_id,
    }


def _checkpoint(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    with factory() as session:
        value = session.execute(
            text(
                """
                select checkpoint
                from learning.xingce_task_sessions
                where user_id = cast(:user_id as uuid)
                  and session_id = cast(:session_id as uuid)
                """
            ),
            {"user_id": user_id, "session_id": session_id},
        ).scalar_one()
    return dict(value)


class _EmptyPrivateStorage:
    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        raise AssertionError(
            f"favorite privacy fixture unexpectedly owned {object_ref} for {operation_key}"
        )
