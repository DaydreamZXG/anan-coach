from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from tests.factories import seed_route_frozen_release
from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)
from tests.test_xingce_learning_routes import _xingce_task


def test_c01_briefing_is_owned_versioned_and_does_not_start_session(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "xingce-stage3-briefing-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
        include_objective_questions=False,
    )
    seed_route_frozen_release(db_session_factory)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)

    response = client.get(
        f"/api/v2/xingce/plan-tasks/{task['id']}/briefing",
        headers=auth,
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["contractVersion"] == "xingce-task-briefing@v1"
    assert body["planTaskId"] == task["id"]
    assert str(UUID(body["planVersionId"])) == body["planVersionId"]
    assert len(body["taskDefinitionVersion"]) == 64
    assert body["questionCount"] == 3
    assert body["timing"] == {
        "behavior": "advisory",
        "targetSeconds": task["estimatedMinutes"] * 60,
        "perQuestionTargetSeconds": task["estimatedMinutes"] * 20,
        "overtimeIsRecorded": True,
    }
    assert body["abandonment"] == {
        "allowed": True,
        "semantics": "active_abandon_is_not_automatic_error",
        "requiresExplicitAction": True,
    }
    assert body["completion"]["stableMasteryRequiresDelayedVerification"] is True

    with db_session_factory() as session:
        count = session.execute(
            text("select count(*) from learning.xingce_task_sessions")
        ).scalar_one()
    assert count == 0


def test_c01_briefing_rejects_another_account_task(
    db_session_factory: sessionmaker[Session],
) -> None:
    owner_token = "xingce-briefing-owner-token"
    other_token = "xingce-briefing-other-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=owner_token,
        include_objective_questions=False,
    )
    with db_session_factory.begin() as session:
        other_user_id = str(uuid4())
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (:user_id, '其他学习者')"
            ),
            {"user_id": other_user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  :session_id, :user_id, :token_hash, 'briefing-other-device',
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": other_user_id,
                "token_hash": token_hash_for_tests(other_token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
    seed_route_frozen_release(db_session_factory)
    client = _client(db_session_factory)
    task = _xingce_task(client, {"Authorization": f"Bearer {owner_token}"})

    response = client.get(
        f"/api/v2/xingce/plan-tasks/{task['id']}/briefing",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "xingce_briefing_unavailable"
