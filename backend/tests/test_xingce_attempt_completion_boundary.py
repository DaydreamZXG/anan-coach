from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_xingce_planning_closure import _submitted_attempt


def test_marked_session_missing_terminal_planning_fact_rolls_back_cause_decision(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="marked-completion-consistency-token",
        choices=["A", "B", "C"],
    )
    session_id = submitted["sessionId"]
    review = submitted["review"]
    with db_session_factory() as session:
        scoring_bundle = session.execute(
            text(
                "select scoring_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": session_id},
        ).scalar_one()
        assert scoring_bundle["sessionGeneration"] == (
            "xingce-plan-completion-at-submit@v1"
        )
        assert (
            session.execute(
                text(
                    "select status from learning.task_execution_versions "
                    "where task_id = cast(:task_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"task_id": task["id"]},
            ).scalar_one()
            == "complete"
        )

    reflected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "marked-consistency-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "新会话必须只认交卷时已经写入的计划完成事实。",
            "tags": ["一致性守卫"],
            "strategyNote": None,
            "doubtNote": None,
        },
    )
    assert reflected.status_code == 200, reflected.text
    reflected_data = reflected.json()["data"]
    candidate_id = reflected_data["review"]["aiCandidate"]["candidateVersionId"]

    with db_session_factory.begin() as session:
        session.execute(
            text(
                "delete from learning.task_execution_versions "
                "where task_id = cast(:task_id as uuid) and status = 'complete'"
            ),
            {"task_id": task["id"]},
        )
    with db_session_factory() as session:
        facts_before = {
            "reviews": session.execute(
                text("select count(*) from learning.xingce_attempt_review_versions")
            ).scalar_one(),
            "decisions": session.execute(
                text("select count(*) from learning.xingce_cause_decision_versions")
            ).scalar_one(),
            "outbox": session.execute(
                text("select count(*) from platform.outbox_events")
            ).scalar_one(),
        }

    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "marked-consistency-cause"},
        json={
            "expectedRevision": reflected_data["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "reject",
            "replacementCause": None,
        },
    )
    assert decided.status_code == 409, decided.text
    assert decided.json()["code"] == "xingce_planning_conflict"

    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": session_id},
            ).scalar_one()
            == "submitted"
        )
        assert (
            session.execute(
                text(
                    "select status from learning.task_execution_versions "
                    "where task_id = cast(:task_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"task_id": task["id"]},
            ).scalar_one()
            == "in_progress"
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "where session_id = cast(:session_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"session_id": session_id},
            ).scalar_one()
            == "pending_reflection"
        )
        assert {
            "reviews": session.execute(
                text("select count(*) from learning.xingce_attempt_review_versions")
            ).scalar_one(),
            "decisions": session.execute(
                text("select count(*) from learning.xingce_cause_decision_versions")
            ).scalar_one(),
            "outbox": session.execute(
                text("select count(*) from platform.outbox_events")
            ).scalar_one(),
        } == facts_before


@pytest.mark.parametrize("marker", ["future-generation@v2", None, 7])
def test_unknown_session_generation_fails_closed_without_historical_writes(
    db_session_factory: sessionmaker[Session],
    marker: object,
) -> None:
    token = f"unknown-generation-{type(marker).__name__}"
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token=token,
        choices=["A", "B", "C"],
    )
    session_id = submitted["sessionId"]
    review = submitted["review"]
    reflected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": f"{token}-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "未知代际不能被当作历史会话补偿。",
            "tags": ["代际守卫"],
            "strategyNote": None,
            "doubtNote": None,
        },
    )
    assert reflected.status_code == 200, reflected.text
    reflected_data = reflected.json()["data"]
    candidate_id = reflected_data["review"]["aiCandidate"]["candidateVersionId"]

    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update learning.xingce_task_sessions "
                "set scoring_bundle = scoring_bundle || cast(:patch as jsonb) "
                "where session_id = cast(:session_id as uuid)"
            ),
            {
                "session_id": session_id,
                "patch": json.dumps({"sessionGeneration": marker}),
            },
        )
    with db_session_factory() as session:
        facts_before = _cause_fact_counts(session)

    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": f"{token}-cause"},
        json={
            "expectedRevision": reflected_data["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "reject",
            "replacementCause": None,
        },
    )
    assert decided.status_code == 409, decided.text
    assert decided.json()["code"] == "xingce_planning_conflict"

    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": session_id},
            ).scalar_one()
            == "submitted"
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "where session_id = cast(:session_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"session_id": session_id},
            ).scalar_one()
            == "pending_reflection"
        )
        assert _cause_fact_counts(session) == facts_before


def _cause_fact_counts(session: Session) -> dict[str, int]:
    return {
        "reviews": session.execute(
            text("select count(*) from learning.xingce_attempt_review_versions")
        ).scalar_one(),
        "decisions": session.execute(
            text("select count(*) from learning.xingce_cause_decision_versions")
        ).scalar_one(),
        "evidence": session.execute(
            text("select count(*) from learning.xingce_plan_evidence_versions")
        ).scalar_one(),
        "outbox": session.execute(
            text("select count(*) from platform.outbox_events")
        ).scalar_one(),
    }
