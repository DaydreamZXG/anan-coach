from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from app.domains.xingce_learning.real_attempt.schemas import XingceResultItem
from app.domains.xingce_learning.real_attempt.service import (
    _group_summary,
    _verification_status,
)
from tests.factories import seed_route_frozen_release
from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)
from tests.test_xingce_learning_routes import _xingce_task
from tests.task_editing_helpers import claim_task_edit_headers


def test_result_projection_separates_verdict_disposition_confidence_and_timing(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, task, started = _started_session(
        db_session_factory,
        token="xingce-result-facts-token",
    )
    questions = started["bundle"]["questions"]
    advisory = task["estimatedMinutes"] * 20
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "result-facts-checkpoint"},
        json={
            "expectedRevision": started["revision"],
            "currentQuestionId": questions[-1]["questionId"],
            "answers": [
                _answer(questions[0]["questionId"], "A", "high", 30),
                _answer(
                    questions[1]["questionId"],
                    "B",
                    "low",
                    advisory + 1,
                ),
                _answer(
                    questions[2]["questionId"],
                    None,
                    None,
                    10,
                    skipped=True,
                ),
            ],
            "totalElapsedSeconds": advisory + 50,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    payload = {
        "expectedRevision": checkpoint.json()["data"]["revision"],
        "totalElapsedSeconds": advisory + 55,
    }
    headers = {**auth, "Idempotency-Key": "result-facts-submit"}
    response = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers=headers,
        json=payload,
    )
    replay = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert replay.status_code == 200, replay.text
    result = response.json()["data"]["result"]
    assert replay.json()["data"]["result"] == result
    assert result["contractVersion"] == "xingce-attempt-result@v2"
    assert [item["objectiveVerdict"] for item in result["items"]] == [
        "correct",
        "correct",
        "unanswered",
    ]
    assert [item["disposition"] for item in result["items"]] == [
        "answered",
        "answered",
        "active_abandon",
    ]
    assert [item["confidenceEvidence"] for item in result["items"]] == [
        "high",
        "low",
        "unreported",
    ]
    assert [item["resultCode"] for item in result["items"]] == [
        "correct",
        "guessed_correct",
        "skipped",
    ]
    assert result["items"][1]["timingEvidence"] == {
        "status": "advisory_exceeded",
        "elapsedSeconds": advisory + 1,
        "advisorySeconds": advisory,
    }
    assert result["items"][2]["resultCode"] != "incorrect"
    assert result["groupSummary"] == {
        "outcome": "not_attained",
        "genuineCorrect": 1,
        "guessedCorrect": 1,
        "methodCorrectCalculationError": 0,
        "advisoryExceeded": 1,
        "activeAbandon": 1,
        "pendingReflection": True,
        "pendingCause": False,
        "immediateVerification": False,
        "scheduled": False,
    }

    with db_session_factory() as session:
        scoring = session.execute(
            text(
                "select scoring_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
        assert scoring["timingPolicy"]["perQuestionAdvisorySeconds"] == advisory
        assert {item["advisorySeconds"] for item in scoring["questions"]} == {advisory}
        rows = (
            session.execute(
                text(
                    "select result_code, correct, result_snapshot "
                    "from learning.xingce_assessments order by created_at, question_id"
                )
            )
            .mappings()
            .all()
        )
        assert [row["result_code"] for row in rows] == [
            "correct",
            "guessed_correct",
            "unanswered",
        ]
        assert rows[2]["correct"] is False
        assert rows[2]["result_snapshot"]["disposition"] == "active_abandon"
        assert rows[2]["result_snapshot"]["objectiveVerdict"] == "unanswered"
        timing = session.execute(
            text(
                "select timing_evidence from learning.xingce_attempt_records "
                "where question_id = :question_id"
            ),
            {"question_id": questions[1]["questionId"]},
        ).scalar_one()
        assert timing["timingStatus"] == "advisory_exceeded"
        assert timing["advisorySeconds"] == advisory


def test_existing_session_replay_does_not_backfill_timing_policy(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, task, started = _started_session(
        db_session_factory,
        token="xingce-legacy-replay-token",
    )
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learning.xingce_task_sessions
                   set scoring_bundle = scoring_bundle
                     - 'timingPolicy' - 'scoringBundleVersion'
                 where session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": started["sessionId"]},
        )
    replay = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": "legacy-session-replay"},
        json={"planTaskId": task["id"]},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["sessionId"] == started["sessionId"]
    with db_session_factory() as session:
        scoring = session.execute(
            text(
                "select scoring_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
        assert "timingPolicy" not in scoring
        assert "scoringBundleVersion" not in scoring


def test_result_session_is_account_isolated(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, _auth, _task, started = _started_session(
        db_session_factory,
        token="xingce-result-owner-token",
    )
    other = "xingce-result-other-token"
    _seed_other_auth(db_session_factory, other)
    response = client.get(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}",
        headers={"Authorization": f"Bearer {other}"},
    )
    assert response.status_code == 404


def test_group_interruption_and_failed_verification_are_deterministic() -> None:
    abandoned = [
        _fact_item(index, verdict="unanswered", abandoned=True) for index in range(3)
    ]
    summary = _group_summary(
        abandoned,
        review_state="reflection_due",
        reflection_payload=None,
    )
    assert summary is not None
    assert summary.outcome == "interrupted"
    assert summary.active_abandon == 3

    answered_with_error = [
        _fact_item(0, verdict="incorrect", abandoned=False),
        _fact_item(1, verdict="correct", abandoned=False),
        _fact_item(2, verdict="correct", abandoned=False),
    ]
    assert _verification_status(
        answered_with_error,
        has_pending_dispute=False,
    ) == ("not_passed", "valid")
    assert _verification_status(
        answered_with_error,
        has_pending_dispute=True,
    ) == ("disputed_or_invalidated", "pending_dispute")
    assert _verification_status(
        answered_with_error,
        has_pending_dispute=False,
        confirmed_invalidated=True,
    ) == ("disputed_or_invalidated", "confirmed_invalidated")

    unreported_correct = [
        _fact_item(
            index,
            verdict="correct",
            abandoned=False,
            confidence="unreported",
        )
        for index in range(3)
    ]
    unreported_summary = _group_summary(
        unreported_correct,
        review_state="reflection_due",
        reflection_payload=None,
    )
    assert unreported_summary is not None
    assert unreported_summary.genuine_correct == 0
    assert unreported_summary.guessed_correct == 0
    assert unreported_summary.outcome == "not_attained"

    genuine_correct = [
        _fact_item(index, verdict="correct", abandoned=False) for index in range(3)
    ]
    immediate = _group_summary(
        genuine_correct,
        review_state="immediate_verification_due",
        reflection_payload=None,
    )
    scheduled = _group_summary(
        genuine_correct,
        review_state="mastery_recorded",
        reflection_payload=None,
    )
    assert immediate is not None and immediate.outcome == "attained"
    assert immediate.immediate_verification is True
    assert immediate.scheduled is False
    assert scheduled is not None and scheduled.scheduled is True


def _started_session(
    factory: sessionmaker[Session],
    *,
    token: str,
) -> tuple[TestClient, dict[str, str], dict, dict]:
    _seed_learner_and_published_essay_content(
        factory,
        access_token=token,
        include_objective_questions=False,
    )
    seed_route_frozen_release(factory, count=6)
    client = _client(factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)
    response = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": f"start-{token}"},
        json={"planTaskId": task["id"]},
    )
    assert response.status_code == 200, response.text
    started = response.json()["data"]
    auth = {
        **auth,
        **claim_task_edit_headers(
            client,
            auth,
            resource_type="xingce_task_session",
            resource_id=started["sessionId"],
            revision=started["revision"],
        ),
    }
    return client, auth, task, started


def _answer(
    question_id: str,
    choice: str | None,
    confidence: str | None,
    elapsed_seconds: int,
    *,
    skipped: bool = False,
) -> dict:
    return {
        "questionId": question_id,
        "selectedOptionIds": [choice] if choice is not None else [],
        "eliminatedOptionIds": [],
        "confidence": confidence,
        "marked": False,
        "skipped": skipped,
        "elapsedSeconds": elapsed_seconds,
        "draftText": "",
        "materialPosition": None,
    }


def _fact_item(
    index: int,
    *,
    verdict: str,
    abandoned: bool,
    confidence: str | None = None,
) -> XingceResultItem:
    return XingceResultItem(
        questionId=f"question-{index}",
        resultCode=(
            "skipped"
            if abandoned
            else ("correct" if verdict == "correct" else "incorrect")
        ),
        selectedOptionIds=[] if abandoned else ["A"],
        correctOptionIds=["A"],
        factContractVersion="xingce-result-facts@v1",
        objectiveVerdict=verdict,
        disposition="active_abandon" if abandoned else "answered",
        confidenceEvidence=(confidence or ("unreported" if abandoned else "high")),
        timingEvidence={
            "status": "within_advisory",
            "elapsedSeconds": 20,
            "advisorySeconds": 60,
        },
    )


def _seed_other_auth(factory: sessionmaker[Session], token: str) -> None:
    with factory.begin() as session:
        user_id = str(uuid4())
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '其他学习者')"
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
                  :token_hash, 'result-other-device', :expires_at
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
