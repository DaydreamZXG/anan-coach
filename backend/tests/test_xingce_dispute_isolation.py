"""争议题隔离：错误模式聚合排除争议题，错因证据显式标注争议状态。

PRD §8:207 铁律：题目有争议时，该题暂停用于该用户的能力定级和错因归类；
争议被裁决为 confirmed_valid（撤销）后恢复。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.learning_brain.repository import LearningBrainRepository
from app.domains.task_content.quality.schemas import (
    RecordQuestionQualityDecisionCommand,
)
from app.domains.task_content.quality.service import QuestionQualityDecisionService
from app.domains.xingce_learning.cause_agent.repository import CauseAgentRepository
from app.domains.xingce_learning.cause_agent.tools import CauseAgentTools
from tests.test_xingce_cause_agent_workflow import SequenceProvider, _reflect, _run_one
from tests.test_xingce_planning_closure import _submitted_attempt


NOW = datetime(2026, 7, 20, 8, tzinfo=UTC)


def test_disputed_questions_leave_error_patterns_until_confirmed_valid(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="dispute-isolation-pattern-token",
        choices=["D", "B", "C"],
    )
    _confirm_cause(
        client,
        auth,
        db_session_factory,
        submitted=submitted,
        key="dispute-isolation-pattern",
    )
    user_id = _session_owner(db_session_factory, session_id=submitted["sessionId"])
    questions = submitted["bundle"]["questions"]

    baseline = _xingce_patterns(db_session_factory, user_id=user_id)
    assert len(baseline) == 1
    assert baseline[0]["module_key"] == "言语理解"
    assert baseline[0]["cause_code"] == "stem_misread"

    for index, question in enumerate(questions):
        _file_dispute(
            client,
            auth,
            session_id=submitted["sessionId"],
            question=question,
            key=f"dispute-isolation-pattern-{index}",
        )

    assert _xingce_patterns(db_session_factory, user_id=user_id) == []

    # needs_more_evidence 不等于撤销：争议仍然生效。
    _adjudicate(
        db_session_factory,
        question=questions[0],
        decision="needs_more_evidence",
        command_key="dispute-isolation-needs-more",
    )
    assert _xingce_patterns(db_session_factory, user_id=user_id) == []

    # confirmed_valid 裁决即撤销争议：错误模式恢复。
    for question in questions:
        _adjudicate(
            db_session_factory,
            question=question,
            decision="confirmed_valid",
            command_key="dispute-isolation-valid",
        )
    restored = _xingce_patterns(db_session_factory, user_id=user_id)
    assert len(restored) == 1
    assert restored[0]["module_key"] == "言语理解"
    assert restored[0]["cause_code"] == "stem_misread"
    assert restored[0]["occurrence_count"] == baseline[0]["occurrence_count"]


def test_attempt_evidence_marks_disputed_questions_without_hiding_them(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="dispute-isolation-evidence-token",
        choices=["D", "B", "C"],
    )
    user_id = _session_owner(db_session_factory, session_id=submitted["sessionId"])
    questions = submitted["bundle"]["questions"]
    disputed_question = questions[0]

    _file_dispute(
        client,
        auth,
        session_id=submitted["sessionId"],
        question=disputed_question,
        key="dispute-isolation-evidence",
    )

    payload = _load_attempt_evidence(
        db_session_factory,
        user_id=user_id,
        session_id=submitted["sessionId"],
    )
    items = {item["questionId"]: item for item in payload["items"]}
    # 争议题不被剔除，仍在证据里，但必须显式标注 disputed。
    assert len(items) == len(questions)
    assert items[disputed_question["questionId"]]["disputed"] is True
    for question in questions[1:]:
        assert items[question["questionId"]]["disputed"] is False
    dispute_ref = f"question-dispute:{disputed_question['questionId']}"
    assert dispute_ref in payload["evidenceRefs"]
    for item in payload["items"]:
        assert f"assessment:{item['assessmentId']}" in payload["evidenceRefs"]

    # confirmed_valid 裁决撤销争议后，证据不再标注 disputed。
    _adjudicate(
        db_session_factory,
        question=disputed_question,
        decision="confirmed_valid",
        command_key="dispute-isolation-evidence-valid",
    )
    restored = _load_attempt_evidence(
        db_session_factory,
        user_id=user_id,
        session_id=submitted["sessionId"],
    )
    assert all(item["disputed"] is False for item in restored["items"])
    assert dispute_ref not in restored["evidenceRefs"]


def _confirm_cause(
    client: Any,
    auth: dict[str, str],
    factory: sessionmaker[Session],
    *,
    submitted: dict[str, Any],
    key: str,
) -> None:
    review = submitted["review"]
    _reflect(client, auth, review, key=f"{key}-reflection")
    _run_one(factory, SequenceProvider(["candidate_ready"]))
    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    candidate_view = current.json()["data"]
    candidate = candidate_view["review"]["aiCandidate"]
    assert candidate["state"] == "candidate_ready"
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": f"{key}-decision"},
        json={
            "expectedRevision": candidate_view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text


def _file_dispute(
    client: Any,
    auth: dict[str, str],
    *,
    session_id: str,
    question: dict[str, Any],
    key: str,
) -> None:
    response = client.post(
        f"/api/v2/xingce/task-sessions/{session_id}/disputes",
        headers={**auth, "Idempotency-Key": key},
        json={
            "questionId": question["questionId"],
            "contentVersionId": question["contentVersionId"],
            "category": "answer_error",
            "description": "答案与题干条件疑似冲突",
        },
    )
    assert response.status_code == 200, response.text


def _adjudicate(
    factory: sessionmaker[Session],
    *,
    question: dict[str, Any],
    decision: str,
    command_key: str,
) -> None:
    service = QuestionQualityDecisionService()
    with factory() as session:
        dispute_ids = [
            str(item)
            for item in session.execute(
                text(
                    "select dispute_id from learning.xingce_question_disputes "
                    "where question_id = :question_id "
                    "order by created_at, dispute_id"
                ),
                {"question_id": question["questionId"]},
            ).scalars()
        ]
        assert dispute_ids, "adjudication requires an existing dispute"
        _register_published_content(
            session,
            question_id=question["questionId"],
            content_version_id=question["contentVersionId"],
        )
        service.record(
            session,
            command_id=f"{command_key}-{question['questionId']}",
            actor_ref="moderator:dispute-isolation",
            command=RecordQuestionQualityDecisionCommand(
                questionId=question["questionId"],
                contentVersionId=question["contentVersionId"],
                decision=decision,
                disputeIds=dispute_ids,
                reasonCode=(
                    "official_answer_verified"
                    if decision == "confirmed_valid"
                    else "needs_official_source"
                ),
                rationale=(
                    "复核官方来源后确认冻结内容正确，争议撤销。"
                    if decision == "confirmed_valid"
                    else "现有报告不足以定论，需要补充官方来源。"
                ),
                authorityVersion="content-moderation-policy@v1",
                evidenceRefs=[f"official-answer-review:{command_key}"],
            ),
            now=NOW,
        )
        session.commit()


def _register_published_content(
    session: Session,
    *,
    question_id: str,
    content_version_id: str,
) -> None:
    exists = session.execute(
        text(
            "select 1 from product.question_content_versions "
            "where question_id = :question_id "
            "and content_version_id = :content_version_id"
        ),
        {"question_id": question_id, "content_version_id": content_version_id},
    ).scalar_one_or_none()
    if exists is None:
        session.execute(
            text(
                """
                insert into product.question_content_versions (
                  content_version_id, question_id, track, snapshot_payload,
                  normalized_search_text, created_at
                ) values (
                  :content_version_id, :question_id, 'xingce', '{}'::jsonb,
                  :search_text, :created_at
                )
                """
            ),
            {
                "content_version_id": content_version_id,
                "question_id": question_id,
                "search_text": f"争议隔离测试 {question_id}",
                "created_at": NOW,
            },
        )
    head = session.execute(
        text(
            "select 1 from product.question_content_heads "
            "where question_id = :question_id"
        ),
        {"question_id": question_id},
    ).scalar_one_or_none()
    if head is None:
        session.execute(
            text(
                """
                insert into product.question_content_heads (
                  question_id, content_version_id, updated_at
                ) values (:question_id, :content_version_id, :created_at)
                """
            ),
            {
                "question_id": question_id,
                "content_version_id": content_version_id,
                "created_at": NOW,
            },
        )


def _session_owner(factory: sessionmaker[Session], *, session_id: str) -> str:
    with factory() as session:
        return str(
            session.execute(
                text(
                    "select user_id from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": session_id},
            ).scalar_one()
        )


def _xingce_patterns(
    factory: sessionmaker[Session],
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    with factory() as session:
        rows = LearningBrainRepository(session).error_patterns(user_id=user_id)
        return [dict(row) for row in rows if str(row["track"]) == "xingce"]


def _load_attempt_evidence(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    reflection_version_id = str(uuid4())
    with factory() as session:
        tools = CauseAgentTools(
            session=session,
            repository=CauseAgentRepository(),
            user_id=user_id,
            session_id=session_id,
            reflection_version_id=reflection_version_id,
        )
        return tools.load_attempt_evidence(
            {
                "userId": user_id,
                "sessionId": session_id,
                "reflectionVersionId": reflection_version_id,
            }
        )
