from __future__ import annotations

import importlib
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)
from tests.test_xingce_learning_routes import (
    _seed_objective_questions,
    _xingce_task,
)


def test_all_correct_reflection_then_skip_cause_completes_plan_and_replays(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-planning-complete-token",
        choices=["A", "B", "C"],
    )
    review = submitted["review"]
    payload = {
        "expectedRevision": review["revision"],
        "rawText": "这组三题我按题干限定词逐项排除，过程稳定。",
        "tags": ["审题稳定", "排除法"],
        "strategyNote": "继续保持先找限定词再判断。",
        "doubtNote": None,
    }
    headers = {**auth, "Idempotency-Key": "complete-reflection-planning"}

    response = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers=headers,
        json=payload,
    )
    replay = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 200, response.text
    assert replay.status_code == 200, replay.text
    reflected = response.json()["data"]
    assert replay.json()["data"] == reflected
    assert reflected["state"] == "cause_decision_required"
    assert reflected["review"]["state"] == "cause_decision_required"
    cause_headers = {**auth, "Idempotency-Key": "complete-cause-planning"}
    cause_payload = {
        "expectedRevision": reflected["review"]["revision"],
        "decision": "skip",
        "replacementCause": None,
    }
    cause = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers=cause_headers,
        json=cause_payload,
    )
    cause_replay = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers=cause_headers,
        json=cause_payload,
    )
    assert cause.status_code == 200, cause.text
    assert cause_replay.status_code == 200, cause_replay.text
    completed = cause.json()["data"]
    assert cause_replay.json()["data"] == completed
    assert completed["state"] == "completed"
    assert completed["review"]["state"] == "immediate_verification_due"
    assert completed["review"]["causeDecision"]["decision"] == "skip"
    assert completed["completion"]["receiptId"]
    assert all(
        planned["id"] != task["id"]
        for planned in completed["completion"]["plan"]["tasks"]
    )
    with db_session_factory() as session:
        execution = (
            session.execute(
                text(
                    """
                select status, evidence_refs
                from learning.task_execution_versions
                where task_id = cast(:task_id as uuid)
                order by version_no desc
                limit 1
                """
                ),
                {"task_id": task["id"]},
            )
            .mappings()
            .one()
        )
        assert execution["status"] == "complete"
        assert execution["evidence_refs"][0]["owner"] == "xingce_learning"
        assert (
            session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text("select count(*) from learning.xingce_reflection_versions")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("select count(*) from learning.xingce_cause_candidate_versions")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("select count(*) from learning.xingce_attempt_review_versions")
            ).scalar_one()
            == 4
        )
        assert (
            session.execute(
                text("select count(*) from learning.xingce_plan_evidence_versions")
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "order by version_no desc limit 1"
                )
            ).scalar_one()
            == "settled"
        )


def test_started_session_can_complete_after_source_plan_expires(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-planning-rollback-token",
        choices=["A", "B", "C"],
    )
    review = submitted["review"]
    with db_session_factory.begin() as session:
        plan_version_id = session.execute(
            text(
                "select plan_version_id::text from learning.daily_plan_tasks "
                "where task_id = cast(:task_id as uuid)"
            ),
            {"task_id": task["id"]},
        ).scalar_one()
        session.execute(
            text(
                "update learning.daily_plan_versions plan "
                "set valid_until = execution.recorded_at + interval '1 microsecond' "
                "from learning.task_execution_versions execution "
                "where plan.plan_version_id = cast(:plan_version_id as uuid) "
                "and execution.plan_version_id = plan.plan_version_id "
                "and execution.status = 'in_progress'"
            ),
            {"plan_version_id": plan_version_id},
        )

    response = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "rollback-reflection-planning"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "计划虽已跨过有效期，这仍是我完成本次作答后的真实复盘。",
            "tags": ["跨日完成"],
            "strategyNote": None,
            "doubtNote": None,
        },
    )

    assert response.status_code == 200, response.text
    reflected = response.json()["data"]
    assert reflected["state"] == "cause_decision_required"
    cause = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "late-cause-planning"},
        json={
            "expectedRevision": reflected["review"]["revision"],
            "decision": "skip",
            "replacementCause": None,
        },
    )
    assert cause.status_code == 200, cause.text
    completed = cause.json()["data"]
    assert completed["state"] == "completed"
    assert completed["completion"]["receiptId"]
    with db_session_factory() as session:
        assert (
            session.execute(
                text("select count(*) from learning.xingce_reflection_versions")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": submitted["sessionId"]},
            ).scalar_one()
            == "settled"
        )
        assert (
            session.execute(
                text("select count(*) from learning.xingce_attempt_review_versions")
            ).scalar_one()
            == 4
        )


def test_abandoned_plan_execution_is_preserved_and_late_evidence_is_linked(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-planning-late-link-token",
        choices=["D", "B", "C"],
    )
    with db_session_factory() as session:
        execution_id = session.execute(
            text(
                "select execution_version_id::text "
                "from learning.task_execution_versions "
                "where task_id = cast(:task_id as uuid) and status = 'in_progress'"
            ),
            {"task_id": task["id"]},
        ).scalar_one()
    abandoned = client.put(
        f"/api/v2/plans/tasks/{task['id']}/execution",
        headers={**auth, "Idempotency-Key": "abandon-started-xingce"},
        json={
            "status": "abandoned",
            "elapsedMinutes": 3,
            "evidenceRefs": [],
            "abandonmentReason": "当日计划结束，但保留已开始作答的复盘入口。",
            "basedOnExecutionVersionId": execution_id,
        },
    )
    assert abandoned.status_code == 200, abandoned.text

    review = submitted["review"]
    reflected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "late-link-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "计划结束后我仍补完复盘，主要是漏看了题干限定词。",
            "tags": ["题干审读"],
            "strategyNote": "下次先圈限定词。",
            "doubtNote": None,
        },
    )
    assert reflected.status_code == 200, reflected.text
    reflected_data = reflected.json()["data"]
    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "late-link-cause"},
        json={
            "expectedRevision": reflected_data["review"]["revision"],
            "decision": "replace",
            "replacementCause": "我漏看了题干限定词。",
        },
    )
    assert decided.status_code == 200, decided.text
    completed = decided.json()["data"]
    assert completed["state"] == "completed"
    assert completed["completion"]["settlementStatus"] == "late_evidence_linked"
    with db_session_factory() as session:
        latest_status = session.execute(
            text(
                "select status from learning.task_execution_versions "
                "where task_id = cast(:task_id as uuid) "
                "order by version_no desc limit 1"
            ),
            {"task_id": task["id"]},
        ).scalar_one()
        assert latest_status == "abandoned"
        assert (
            session.execute(
                text("select count(*) from learning.plan_task_late_evidence_versions")
            ).scalar_one()
            == 1
        )


def test_objective_verification_is_explicitly_outside_this_release_unit(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "xingce-verification-boundary-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    _seed_objective_questions(db_session_factory)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learning.daily_plan_tasks
                set stage = 'objective_verification', kind = 'verification'
                where task_id = cast(:task_id as uuid)
                """
            ),
            {"task_id": task["id"]},
        )

    response = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": "verification-outside-ru"},
        json={"planTaskId": task["id"]},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "xingce_plan_task_unavailable"
    with db_session_factory() as session:
        assert (
            session.execute(
                text("select count(*) from learning.xingce_task_sessions")
            ).scalar_one()
            == 0
        )


def test_xingce_migration_downgrade_guard_detects_personal_records(
    db_session_factory: sessionmaker[Session],
) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260715_19_xingce_learning_closure"
    )
    with db_session_factory.begin() as session:
        assert migration._has_xingce_learning_records(session.connection()) is False
        user_id = str(uuid4())
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '迁移守卫测试用户')"
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learning.xingce_commands (
                  user_id, command_id, command_type, target_id,
                  request_fingerprint, response_payload, created_at
                ) values (
                  cast(:user_id as uuid), 'migration-guard-command',
                  'session_start', null, repeat('a', 64), '{}'::jsonb, now()
                )
                """
            ),
            {"user_id": user_id},
        )
        assert migration._has_xingce_learning_records(session.connection()) is True


def _submitted_attempt(
    factory: sessionmaker[Session],
    *,
    token: str,
    choices: list[str],
) -> tuple[TestClient, dict[str, str], dict[str, Any], dict[str, Any]]:
    _seed_learner_and_published_essay_content(factory, access_token=token)
    _seed_objective_questions(factory)
    client = _client(factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)
    start = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": f"start-{token}"},
        json={"planTaskId": task["id"]},
    )
    assert start.status_code == 200, start.text
    started = start.json()["data"]
    questions = started["bundle"]["questions"]
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": f"checkpoint-{token}"},
        json={
            "expectedRevision": started["revision"],
            "currentQuestionId": questions[-1]["questionId"],
            "answers": [
                {
                    "questionId": question["questionId"],
                    "selectedOptionIds": [choice],
                    "eliminatedOptionIds": [],
                    "confidence": "high",
                    "marked": False,
                    "skipped": False,
                    "elapsedSeconds": 40 + index,
                    "draftText": "",
                    "materialPosition": None,
                }
                for index, (question, choice) in enumerate(
                    zip(questions, choices, strict=True)
                )
            ],
            "totalElapsedSeconds": 132,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    checkpointed = checkpoint.json()["data"]
    submit = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": f"submit-{token}"},
        json={
            "expectedRevision": checkpointed["revision"],
            "totalElapsedSeconds": 135,
        },
    )
    assert submit.status_code == 200, submit.text
    return client, auth, task, submit.json()["data"]
