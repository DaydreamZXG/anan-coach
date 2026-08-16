from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from tests.factories import seed_route_frozen_release
from tests.position_release_helpers import seed_active_position_release
from tests.task_editing_helpers import claim_task_edit_headers
from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)


def test_real_quick_diagnostic_closes_cold_start_without_duplicate_facts(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "quick-diagnostic-golden-loop-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
        include_objective_questions=False,
    )
    seed_route_frozen_release(db_session_factory, count=60)
    with db_session_factory.begin() as session:
        now = datetime.now(UTC)
        seed_active_position_release(
            session,
            activated_at=now,
            database_now=now,
            rule_set_version="quick-diagnostic-rules",
            position_data_version="quick-diagnostic-positions",
        )
        session.execute(
            text(
                """
                update learning.weekly_study_preference_versions
                set weekday_minutes = 45, weekend_minutes = 45,
                    long_block_minutes = 45, fragment_minutes = 25
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        )

    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    before_profile_response = client.get("/api/v2/learning-brain/profile", headers=auth)
    assert before_profile_response.status_code == 200, before_profile_response.text
    before_profile = before_profile_response.json()["data"]
    assert before_profile["evidenceSummary"]["xingceAttemptCount"] == 0
    assert before_profile["evidenceSummary"]["taskExecutionCount"] == 0

    today_response = client.get("/api/v2/plans/today", headers=auth)
    assert today_response.status_code == 200, today_response.text
    baseline = next(
        task
        for task in today_response.json()["data"]["tasks"]
        if task["stage"] == "objective_baseline"
    )
    assert f"planTaskId={baseline['id']}" in baseline["startUrl"]

    start_headers = {**auth, "Idempotency-Key": "quick-diagnostic-start"}
    start_payload = {"planTaskId": baseline["id"]}
    started_response = client.post(
        "/api/v2/xingce/task-sessions",
        headers=start_headers,
        json=start_payload,
    )
    start_replay = client.post(
        "/api/v2/xingce/task-sessions",
        headers=start_headers,
        json=start_payload,
    )
    assert started_response.status_code == 200, started_response.text
    assert start_replay.status_code == 200, start_replay.text
    started = started_response.json()["data"]
    assert start_replay.json()["data"] == started
    assert started["mode"] == "baseline"
    questions = started["bundle"]["questions"]
    assert 3 <= len(questions) < 30

    edit_headers = claim_task_edit_headers(
        client,
        auth,
        resource_type="xingce_task_session",
        resource_id=started["sessionId"],
        revision=started["revision"],
    )
    with db_session_factory() as session:
        scoring_bundle = session.execute(
            text(
                """
                select scoring_bundle
                from learning.xingce_task_sessions
                where session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
    correct_by_question = {
        str(item["questionId"]): [str(option) for option in item["correctOptionIds"]]
        for item in scoring_bundle["questions"]
    }
    answers: list[dict[str, Any]] = []
    for index, question in enumerate(questions):
        correct_options = correct_by_question[question["questionId"]]
        selected = (
            next(option for option in ("A", "B", "C", "D") if option not in correct_options)
            if index == 0
            else correct_options[0]
        )
        answers.append(
            {
                "questionId": question["questionId"],
                "selectedOptionIds": [selected],
                "eliminatedOptionIds": [],
                "confidence": "medium" if index == 0 else "high",
                "marked": index == 0,
                "skipped": False,
                "elapsedSeconds": 75 if index == 0 else 40 + index,
                "draftText": "先定位限定词，再逐项排除。" if index == 0 else "",
                "materialPosition": None,
            }
        )
    total_elapsed_seconds = sum(int(item["elapsedSeconds"]) for item in answers)
    checkpoint_headers = {
        **auth,
        **edit_headers,
        "Idempotency-Key": "quick-diagnostic-checkpoint",
    }
    checkpoint_payload = {
        "expectedRevision": started["revision"],
        "currentQuestionId": questions[-1]["questionId"],
        "answers": answers,
        "totalElapsedSeconds": total_elapsed_seconds,
    }
    checkpoint_response = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers=checkpoint_headers,
        json=checkpoint_payload,
    )
    checkpoint_replay = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers=checkpoint_headers,
        json=checkpoint_payload,
    )
    assert checkpoint_response.status_code == 200, checkpoint_response.text
    assert checkpoint_replay.status_code == 200, checkpoint_replay.text
    checkpointed = checkpoint_response.json()["data"]
    assert checkpoint_replay.json()["data"] == checkpointed

    submit_headers = {
        **auth,
        **edit_headers,
        "Idempotency-Key": "quick-diagnostic-submit",
    }
    submit_payload = {
        "expectedRevision": checkpointed["revision"],
        "totalElapsedSeconds": total_elapsed_seconds + 3,
    }
    submit_response = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers=submit_headers,
        json=submit_payload,
    )
    submit_replay = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers=submit_headers,
        json=submit_payload,
    )
    assert submit_response.status_code == 200, submit_response.text
    assert submit_replay.status_code == 200, submit_replay.text
    submitted = submit_response.json()["data"]
    assert submit_replay.json()["data"] == submitted
    assert submitted["state"] == "reflection_required"
    completion_tasks = submitted["completion"]["plan"]["tasks"]
    assert all(task["stage"] != "objective_baseline" for task in completion_tasks)
    assert any(task["stage"] == "objective_reflection" for task in completion_tasks)

    fresh_today_response = client.get("/api/v2/plans/today", headers=auth)
    assert fresh_today_response.status_code == 200, fresh_today_response.text
    fresh_today = fresh_today_response.json()["data"]
    assert fresh_today["planVersionId"] == submitted["completion"]["plan"]["planVersionId"]
    assert all(task["stage"] != "objective_baseline" for task in fresh_today["tasks"])

    after_profile_response = client.get("/api/v2/learning-brain/profile", headers=auth)
    assert after_profile_response.status_code == 200, after_profile_response.text
    after_profile = after_profile_response.json()["data"]
    assert after_profile["profileVersionId"] != before_profile["profileVersionId"]
    assert after_profile["supersedesProfileVersionId"] == before_profile["profileVersionId"]
    assert after_profile["evidenceSummary"]["xingceAttemptCount"] == len(questions)
    assert after_profile["evidenceSummary"]["taskExecutionCount"] == 1

    with db_session_factory() as session:
        counts = (
            session.execute(
                text(
                    """
                select
                  (select count(*) from learning.xingce_task_sessions
                   where user_id = cast(:user_id as uuid)) as sessions,
                  (select count(*) from learning.xingce_answer_versions answer
                   join learning.xingce_task_sessions task_session
                     on task_session.session_id = answer.session_id
                   where task_session.user_id = cast(:user_id as uuid)) as answers,
                  (select count(*) from learning.xingce_attempt_records
                   where user_id = cast(:user_id as uuid)) as attempts,
                  (select count(*) from learning.xingce_assessments
                   where user_id = cast(:user_id as uuid)) as assessments,
                  (select count(*) from learning.task_execution_versions
                   where user_id = cast(:user_id as uuid)) as execution_versions,
                  (select count(*)
                   from learning.task_execution_versions execution
                   join learning.daily_plan_tasks task
                     on task.task_id = execution.task_id
                    and task.plan_version_id = execution.plan_version_id
                    and task.user_id = execution.user_id
                    and task.local_date = execution.local_date
                    and task.plan_version_no = execution.plan_version_no
                   where execution.user_id = cast(:user_id as uuid)
                     and execution.status = 'complete'
                     and task.stage = 'objective_baseline'
                     and not exists (
                       select 1 from learning.task_execution_versions successor
                       where successor.supersedes_execution_version_id =
                             execution.execution_version_id
                     )) as completed_baselines,
                  (select count(*) from learning.learner_brain_profile_versions
                   where user_id = cast(:user_id as uuid)) as profile_versions
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
    assert dict(counts) == {
        "sessions": 1,
        "answers": len(questions),
        "attempts": len(questions),
        "assessments": len(questions),
        "execution_versions": 2,
        "completed_baselines": 1,
        "profile_versions": 2,
    }

    other_token = "quick-diagnostic-other-account-token"
    with db_session_factory.begin() as session:
        other_user_id = str(uuid4())
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '隔离验收用户')"
            ),
            {"user_id": other_user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid), :token_hash,
                  'wechat-quick-diagnostic-isolation', :expires_at
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
    isolated = client.get(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert isolated.status_code == 404
    assert isolated.json()["code"] == "xingce_session_not_found"
