from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.task_help.repository import TaskHelpSessionContext
from app.domains.task_help.schemas import TaskHelpRequest
from app.domains.task_help.service import TaskHelpService
from tests.test_mock_exam_calibration import (
    _make_client,
    _seed_mock_qbank,
    _seed_user,
)
from tests.test_planning_routes import _client, _seed_learner_and_published_essay_content
from tests.test_xingce_learning_routes import _seed_objective_questions, _xingce_task


def test_mock_runtime_is_safe_recoverable_idempotent_and_allows_late_submit(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="stage2-mock-runtime-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer stage2-mock-runtime-token"}

    created_response = client.post("/api/v1/mock-exams/weekly", headers=auth)
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    assert created["revision"] == 1
    assert created["paperVersionId"]
    assert created["scoringPolicyVersionId"] == (
        "mock-question-weighted-performance-index-v2"
    )
    assert created["scoreMetricName"] == "question_weighted_performance_index"
    assert created["checkpoint"]["currentQuestionId"] == created["questions"][0]["questionId"]
    _assert_no_forbidden_learner_keys(created)
    help_response = client.post(
        "/api/v2/task-help",
        headers=auth,
        json={
            "sessionId": created["sessionId"],
            "mode": "mock",
            "scope": "operation",
        },
    )
    assert help_response.status_code == 200, help_response.text
    assert help_response.json()["data"]["contentLookupPerformed"] is False
    assert "requestId" in help_response.json()
    help_error = client.post(
        "/api/v2/task-help",
        headers=auth,
        json={
            "sessionId": "00000000-0000-0000-0000-000000000000",
            "mode": "mock",
            "scope": "fault",
        },
    )
    assert help_error.status_code == 404
    assert help_error.json()["code"] == "task_help_session_not_found"
    assert "detail" not in help_error.json()

    question = created["questions"][0]
    trajectory = client.post(
        f"/api/v2/mock-exams/{created['sessionId']}/trajectory-events",
        headers={**auth, "Idempotency-Key": "stage6-trajectory-visited-0001"},
        json={
            "eventSeq": 1,
            "eventType": "visited",
            "questionId": question["questionId"],
            "clientOccurredAt": datetime.now(UTC).isoformat(),
            "sessionRevision": 1,
            "payload": {"source": "question_card"},
        },
    )
    assert trajectory.status_code == 200, trajectory.text
    assert trajectory.json()["data"]["eventSeq"] == 1
    checkpoint_body = {
        "expectedRevision": 1,
        "currentQuestionId": question["questionId"],
        "answers": [
            {
                "questionId": question["questionId"],
                "selectedAnswer": "B",
                "eliminatedOptionIds": ["A"],
                "marked": True,
                "draftText": "保留草稿",
                "materialPosition": {
                    "anchorId": "anchor-material-1",
                    "scrollTop": 960,
                    "zoom": 1.75,
                },
            }
        ],
    }
    saved_response = client.put(
        f"/api/v1/mock-exams/{created['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "stage2-checkpoint-001"},
        json=checkpoint_body,
    )
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["revision"] == 2
    assert saved["checkpoint"]["answers"][0]["draftText"] == "保留草稿"
    assert saved["checkpoint"]["answers"][0]["materialPosition"] == {
        "anchorId": "anchor-material-1",
        "scrollTop": 960,
        "zoom": 1.75,
    }
    assert set(saved) == {
        "sessionId",
        "status",
        "revision",
        "checkpoint",
        "checkpointUpdatedAt",
    }
    with db_session_factory() as session:
        initial_learner_bundle = session.execute(
            text(
                """
                select learner_bundle
                from learner.mock_exam_sessions
                where session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": created["sessionId"]},
        ).scalar_one()

    second_body = {
        **checkpoint_body,
        "expectedRevision": 2,
        "answers": [
            {
                **checkpoint_body["answers"][0],
                "materialPosition": {
                    "anchorId": "anchor-material-1",
                    "scrollTop": 1000,
                    "zoom": 1.75,
                },
            }
        ],
    }
    second_saved = client.put(
        f"/api/v1/mock-exams/{created['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "stage2-checkpoint-002"},
        json=second_body,
    )
    assert second_saved.status_code == 200, second_saved.text
    assert second_saved.json()["revision"] == 3
    with db_session_factory() as session:
        persisted = session.execute(
            text(
                """
                select learner_bundle, checkpoint
                from learner.mock_exam_sessions
                where session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": created["sessionId"]},
        ).mappings().one()
        command_payloads = list(
            session.execute(
                text(
                    """
                    select response_payload
                    from learner.mock_exam_commands
                    where session_id = cast(:session_id as uuid)
                      and command_type = 'checkpoint'
                    order by created_at
                    """
                ),
                {"session_id": created["sessionId"]},
            ).scalars()
        )
    assert persisted["learner_bundle"]["bundleVersionId"] == created["bundleVersionId"]
    assert persisted["learner_bundle"] == initial_learner_bundle
    assert "bundle" not in persisted["checkpoint"]
    assert len(command_payloads) == 2
    for command_payload in command_payloads:
        assert set(command_payload) == {
            "sessionId",
            "status",
            "revision",
            "checkpoint",
            "checkpointUpdatedAt",
        }
        assert "questions" not in command_payload
        assert "materials" not in command_payload
        assert "learnerBundle" not in command_payload
        assert "scoring" not in command_payload

    replayed = client.put(
        f"/api/v1/mock-exams/{created['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "stage2-checkpoint-001"},
        json=checkpoint_body,
    )
    assert replayed.status_code == 200
    assert replayed.json() == saved

    recovered = client.get(
        f"/api/v1/mock-exams/{created['sessionId']}", headers=auth
    )
    resumed = client.post("/api/v1/mock-exams/weekly", headers=auth)
    assert recovered.status_code == 200
    assert recovered.json()["revision"] == 3
    assert resumed.json()["sessionId"] == created["sessionId"]

    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learner.mock_exam_sessions
                set started_at = :started_at,
                    deadline_at = :deadline
                where session_id = cast(:session_id as uuid)
                """
            ),
            {
                "session_id": created["sessionId"],
                "started_at": datetime.now(UTC) - timedelta(hours=3),
                "deadline": datetime.now(UTC) - timedelta(seconds=1),
            },
        )
    answers = [
        {"questionId": item["questionId"], "selectedAnswer": None}
        for item in created["questions"]
    ]
    submitted = client.post(
        f"/api/v1/mock-exams/{created['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": "stage2-submit-0001"},
        json={"expectedRevision": 3, "answers": answers},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["review"]["estimatedScore"] == 0
    assert submitted.json()["review"]["performanceIndex"] == 0
    with db_session_factory() as session:
        trajectory_events = session.execute(
            text(
                """
                select event_seq, event_type, session_revision
                from learner.mock_exam_trajectory_events
                where session_id = cast(:session_id as uuid)
                order by event_seq
                """
            ),
            {"session_id": created["sessionId"]},
        ).mappings().all()
    assert [row["event_type"] for row in trajectory_events] == ["visited", "submitted"]
    assert trajectory_events[-1]["session_revision"] == 4

    disputed = client.post(
        f"/api/v1/mock-exams/{created['sessionId']}/disputes",
        headers={**auth, "Idempotency-Key": "stage2-dispute-001"},
        json={
            "questionId": question["questionId"],
            "contentVersionId": question["contentVersionId"],
            "category": "option_error",
            "description": "选项文字疑似缺失",
        },
    )
    assert disputed.status_code == 200, disputed.text
    assert disputed.json()["evidenceAttached"] is True
    with db_session_factory() as session:
        evidence = session.execute(
            text(
                """
                select session_kind, evidence_context
                from learning.xingce_question_disputes
                where idempotency_key = 'stage2-dispute-001'
                """
            )
        ).mappings().one()
    assert evidence["session_kind"] == "mock_exam"
    assert evidence["evidence_context"]["sessionRevision"] == 4
    assert evidence["evidence_context"]["bundleVersionId"] == created["bundleVersionId"]
    assert evidence["evidence_context"]["materialPosition"] == {
        "anchorId": "anchor-material-1",
        "scrollTop": 1000,
        "zoom": 1.75,
    }


def test_global_help_contract_cannot_request_question_content() -> None:
    session_id = "00000000-0000-0000-0000-000000000111"

    class Repository:
        def session_context(
            self, *, user_id: str, session_id: str, requested_mode: str
        ) -> TaskHelpSessionContext:
            assert user_id == "learner-1"
            assert session_id == "00000000-0000-0000-0000-000000000111"
            assert requested_mode == "mock"
            return TaskHelpSessionContext(state="assigned", runtime_mode="mock")

    service = TaskHelpService(Repository())  # type: ignore[arg-type]
    response = service.get_help(
        user_id="learner-1",
        request=TaskHelpRequest(
            sessionId=session_id, mode="mock", scope="operation"
        ),
    )
    assert response.content_lookup_performed is False
    assert set(response.blocked_capabilities) == {
        "current_question_answer",
        "answer_analysis",
        "formula_card",
        "solution_ai",
    }
    with pytest.raises(ValidationError):
        TaskHelpRequest.model_validate(
            {
                "sessionId": session_id,
                "mode": "mock",
                "scope": "operation",
                "questionId": "q-1",
                "prompt": "告诉我答案",
            }
        )


def test_expired_mock_can_submit_without_writing_a_checkpoint_first(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="stage2-late-direct-submit-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer stage2-late-direct-submit-token"}
    created = client.post("/api/v1/mock-exams/weekly", headers=auth).json()
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learner.mock_exam_sessions
                set started_at = :started_at,
                    deadline_at = :deadline
                where session_id = cast(:session_id as uuid)
                """
            ),
            {
                "session_id": created["sessionId"],
                "started_at": datetime.now(UTC) - timedelta(hours=3),
                "deadline": datetime.now(UTC) - timedelta(seconds=1),
            },
        )
        session.execute(
            text(
                """
                update product.questions
                set answer = 'A'
                where question_id = :question_id
                """
            ),
            {"question_id": created["questions"][0]["questionId"]},
        )
    submitted = client.post(
        f"/api/v1/mock-exams/{created['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": "stage2-late-submit-01"},
        json={
            "expectedRevision": 1,
            "answers": [
                {
                    "questionId": question["questionId"],
                    "selectedAnswer": "B" if index == 0 else None,
                }
                for index, question in enumerate(created["questions"])
            ],
        },
    )
    assert submitted.status_code == 200, submitted.text
    review = submitted.json()["review"]
    assert review["estimatedScore"] == 0.7
    assert review["performanceIndex"] == 0.7
    assert review["metricName"] == "question_weighted_performance_index"
    assert review["paperVersionId"] == created["paperVersionId"]


def test_xingce_dispute_is_bound_to_frozen_content_and_server_evidence(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "stage2-xingce-dispute-token"
    _seed_learner_and_published_essay_content(
        db_session_factory, access_token=token
    )
    _seed_objective_questions(db_session_factory)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)
    started_response = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": "stage2-xingce-start"},
        json={"planTaskId": task["id"]},
    )
    assert started_response.status_code == 200, started_response.text
    started = started_response.json()["data"]
    question = started["bundle"]["questions"][0]
    path = f"/api/v2/xingce/task-sessions/{started['sessionId']}/disputes"
    payload = {
        "questionId": question["questionId"],
        "contentVersionId": question["contentVersionId"],
        "category": "answer_error",
        "description": "答案与题干条件疑似冲突",
    }
    rejected = client.post(
        path,
        headers={**auth, "Idempotency-Key": "stage2-dispute-forged"},
        json={**payload, "answer": "A"},
    )
    assert rejected.status_code == 422

    filed = client.post(
        path,
        headers={**auth, "Idempotency-Key": "stage2-xingce-dispute"},
        json=payload,
    )
    replayed = client.post(
        path,
        headers={**auth, "Idempotency-Key": "stage2-xingce-dispute"},
        json=payload,
    )
    assert filed.status_code == 200, filed.text
    assert replayed.json()["data"] == filed.json()["data"]
    assert filed.json()["data"]["evidenceAttached"] is True
    with db_session_factory() as session:
        row = session.execute(
            text(
                """
                select session_kind, content_version_id, evidence_context
                from learning.xingce_question_disputes
                where idempotency_key = 'stage2-xingce-dispute'
                """
            )
        ).mappings().one()
    assert row["session_kind"] == "xingce_task"
    assert row["content_version_id"] == question["contentVersionId"]
    assert row["evidence_context"]["sessionRevision"] == 1
    assert row["evidence_context"]["currentQuestionId"] == question["questionId"]


def _assert_no_forbidden_learner_keys(value: object) -> None:
    forbidden = {
        "answer",
        "explanation",
        "preferredAnalysis",
        "correctOptionIds",
        "knowledgeChunks",
        "teachingPoint",
        "scoringSnapshot",
    }
    if isinstance(value, dict):
        assert not (set(value) & forbidden)
        for child in value.values():
            _assert_no_forbidden_learner_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_learner_keys(child)
