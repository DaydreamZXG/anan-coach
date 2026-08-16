from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.xingce_learning.real_attempt.service import (
    _can_auto_settle_objective_verification,
)

from tests.test_xingce_cause_agent_workflow import (
    SequenceProvider,
    _reflect,
    _run_one,
)
from tests.test_xingce_planning_closure import _submitted_attempt
from tests.task_editing_helpers import claim_task_edit_headers


@pytest.mark.parametrize(
    ("mode", "result_codes", "expected"),
    [
        ("verification", ["correct", "correct"], True),
        ("practice", ["correct", "correct"], False),
        ("verification", [], False),
        ("verification", ["guessed_correct"], False),
        ("verification", ["incorrect"], False),
        ("verification", ["timeout"], False),
        ("verification", ["skipped"], False),
        ("verification", ["correct", "unanswered"], False),
    ],
)
def test_objective_verification_auto_settlement_boundary(
    mode: str,
    result_codes: list[str],
    expected: bool,
) -> None:
    assert (
        _can_auto_settle_objective_verification(
            mode=mode,
            result_items=[{"resultCode": code} for code in result_codes],
        )
        is expected
    )


def test_verification_outcome_binds_source_and_treats_dispute_as_pending(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-verification-outcome-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="verification-source-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    ready = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate_id = ready["review"]["aiCandidate"]["candidateVersionId"]
    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "verification-source-decision"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "confirm",
        },
    )
    assert decided.status_code == 200, decided.text
    verification_task = next(
        task
        for task in decided.json()["data"]["completion"]["plan"]["tasks"]
        if task["stage"] == "objective_verification"
    )
    started = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": "verification-outcome-start"},
        json={"planTaskId": verification_task["id"]},
    )
    assert started.status_code == 200, started.text
    verification = started.json()["data"]
    auth = {
        **auth,
        **claim_task_edit_headers(
            client,
            auth,
            resource_type="xingce_task_session",
            resource_id=verification["sessionId"],
            revision=verification["revision"],
        ),
    }
    questions = verification["bundle"]["questions"]
    verification_question_ids = [item["questionId"] for item in questions]
    with db_session_factory() as session:
        source_scoring_bundle = session.execute(
            text(
                "select scoring_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": submitted["sessionId"]},
        ).scalar_one()
        verification_scoring_bundle = session.execute(
            text(
                "select scoring_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": verification["sessionId"]},
        ).scalar_one()
    source_question_ids = [str(item["questionId"]) for item in source_scoring_bundle["questions"]]
    correct_options_by_question = {
        str(item["questionId"]): [str(option) for option in item["correctOptionIds"]]
        for item in verification_scoring_bundle["questions"]
    }
    assert len(verification_question_ids) == 3
    assert len(set(verification_question_ids)) == 3
    assert set(verification_question_ids).isdisjoint(source_question_ids)
    assert set(verification_question_ids) | set(source_question_ids) <= {
        f"xingce-route-question-{index}"
        for index in range(1, 61)
    }
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{verification['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "verification-outcome-checkpoint"},
        json={
            "expectedRevision": verification["revision"],
            "currentQuestionId": questions[-1]["questionId"],
            "answers": [
                _answer(
                    question["questionId"],
                    correct_options_by_question[question["questionId"]][0],
                    35 + index,
                )
                for index, question in enumerate(questions)
            ],
            "totalElapsedSeconds": 120,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    submitted_verification = client.post(
        f"/api/v2/xingce/task-sessions/{verification['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": "verification-outcome-submit"},
        json={
            "expectedRevision": checkpoint.json()["data"]["revision"],
            "totalElapsedSeconds": 125,
        },
    )
    assert submitted_verification.status_code == 200, submitted_verification.text
    result = submitted_verification.json()["data"]
    outcome = result["verificationOutcome"]
    assert outcome["outcome"] == "passed"
    assert outcome["evidenceState"] == "valid"
    assert outcome["verificationStage"] == "immediate"
    assert outcome["sourceMasteryVersionId"]
    assert result["result"]["groupSummary"]["outcome"] == "attained"

    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.questions set status = 'withdrawn' where question_id = :question_id"
            ),
            {"question_id": questions[0]["questionId"]},
        )
    invalidated = client.get(
        f"/api/v2/xingce/task-sessions/{verification['sessionId']}",
        headers=auth,
    )
    assert invalidated.status_code == 200, invalidated.text
    assert invalidated.json()["data"]["verificationOutcome"] == {
        "outcome": "disputed_or_invalidated",
        "evidenceState": "confirmed_invalidated",
        "sourceMasteryVersionId": outcome["sourceMasteryVersionId"],
        "verificationStage": "immediate",
    }

    dispute_payload = {
        "questionId": questions[0]["questionId"],
        "contentVersionId": questions[0]["contentVersionId"],
        "category": "answer_error",
        "description": "题目答案可能存在争议，请人工核验。",
    }
    dispute_headers = {**auth, "Idempotency-Key": "verification-answer-dispute"}
    dispute = client.post(
        f"/api/v2/xingce/task-sessions/{verification['sessionId']}/disputes",
        headers=dispute_headers,
        json=dispute_payload,
    )
    replay = client.post(
        f"/api/v2/xingce/task-sessions/{verification['sessionId']}/disputes",
        headers=dispute_headers,
        json=dispute_payload,
    )
    assert dispute.status_code == 200, dispute.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == dispute.json()["data"]
    refreshed = client.get(
        f"/api/v2/xingce/task-sessions/{verification['sessionId']}",
        headers=auth,
    )
    assert refreshed.status_code == 200, refreshed.text
    pending = refreshed.json()["data"]["verificationOutcome"]
    assert pending == {
        "outcome": "disputed_or_invalidated",
        "evidenceState": "pending_dispute",
        "sourceMasteryVersionId": outcome["sourceMasteryVersionId"],
        "verificationStage": "immediate",
    }
    assert pending["evidenceState"] != "confirmed_invalidated"

    with db_session_factory() as session:
        stored = (
            session.execute(
                text(
                    "select source_mastery_version_id::text, scoring_bundle "
                    "from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": verification["sessionId"]},
            )
            .mappings()
            .one()
        )
        assert stored["source_mastery_version_id"] == outcome["sourceMasteryVersionId"]
        assert stored["scoring_bundle"]["verificationContext"] == {
            "sourceMasteryVersionId": outcome["sourceMasteryVersionId"],
            "verificationStage": "immediate",
        }
        assert (
            session.execute(
                text("select count(*) from learning.xingce_question_disputes")
            ).scalar_one()
            == 1
        )


def test_mixed_module_baseline_binds_mastery_to_the_failed_question_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-focused-mastery-module-token",
        choices=["D", "B", "C"],
    )
    failed_question_id = submitted["bundle"]["questions"][0]["questionId"]
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.questions set module_name = '资料分析' "
                "where question_id = :question_id"
            ),
            {"question_id": failed_question_id},
        )

    review = submitted["review"]
    _reflect(client, auth, review, key="focused-mastery-module-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    ready = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate_id = ready["review"]["aiCandidate"]["candidateVersionId"]
    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "focused-mastery-module-decision"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "confirm",
        },
    )
    assert decided.status_code == 200, decided.text
    with db_session_factory() as session:
        mastery = session.execute(
            text(
                "select module_name from learning.xingce_mastery_state_versions "
                "where source_review_id = cast(:review_id as uuid)"
            ),
            {"review_id": review["reviewId"]},
        ).scalar_one()
    assert mastery == "资料分析"


def _answer(question_id: str, choice: str, elapsed_seconds: int) -> dict:
    return {
        "questionId": question_id,
        "selectedOptionIds": [choice],
        "eliminatedOptionIds": [],
        "confidence": "high",
        "marked": False,
        "skipped": False,
        "elapsedSeconds": elapsed_seconds,
        "draftText": "",
        "materialPosition": None,
    }
