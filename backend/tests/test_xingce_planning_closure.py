from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.factories import seed_route_frozen_release
from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)
from tests.test_xingce_learning_routes import (
    _xingce_task,
)
from tests.task_editing_helpers import claim_task_edit_headers
from app.domains.task_runtime.xingce_attempt.question_repository import (
    XingceQuestionRepository,
)
from app.domains.xingce_learning.real_attempt.repository import (
    XingceAttemptRepository,
)


def test_planning_has_no_xingce_learning_imports() -> None:
    planning_root = Path(__file__).parents[1] / "app" / "domains" / "planning"
    violations: list[str] = []
    for source_path in sorted(planning_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (
                node.module or ""
            ).startswith("app.domains.xingce_learning"):
                violations.append(f"{source_path.name}:{node.lineno}:{node.module}")
            if isinstance(node, ast.Import):
                violations.extend(
                    f"{source_path.name}:{node.lineno}:{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("app.domains.xingce_learning")
                )
    assert violations == []


def test_xingce_attempt_repository_reuses_neutral_question_selector() -> None:
    assert issubclass(XingceAttemptRepository, XingceQuestionRepository)
    assert "select_frozen_questions" not in XingceAttemptRepository.__dict__
    assert "select_targeted_frozen_questions" not in XingceAttemptRepository.__dict__


def test_all_correct_reflection_rejects_unsupported_cause_and_completes_plan(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-planning-complete-token",
        choices=["A", "B", "C"],
    )
    assert submitted["completion"]["receiptId"]
    assert all(planned["id"] != task["id"] for planned in submitted["completion"]["plan"]["tasks"])
    with db_session_factory() as session:
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
    assert reflected["state"] == "cause_evidence_insufficient"
    assert reflected["review"]["state"] == "cause_evidence_insufficient"
    candidate_id = reflected["review"]["aiCandidate"]["candidateVersionId"]
    cause_headers = {**auth, "Idempotency-Key": "complete-cause-planning"}
    cause_payload = {
        "expectedRevision": reflected["review"]["revision"],
        "candidateVersionId": candidate_id,
        "decision": "reject",
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
    assert completed["review"]["causeDecision"]["decision"] == "reject"
    assert completed["completion"]["receiptId"]
    assert all(planned["id"] != task["id"] for planned in completed["completion"]["plan"]["tasks"])
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
            == 3
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
    assert reflected["state"] == "cause_evidence_insufficient"
    candidate_id = reflected["review"]["aiCandidate"]["candidateVersionId"]
    cause = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "late-cause-planning"},
        json={
            "expectedRevision": reflected["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "reject",
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
            == 3
        )


def test_abandoned_plan_execution_is_preserved_and_late_evidence_is_linked(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-planning-late-link-token",
        choices=["A", "B", "C"],
        abandon_before_submit=True,
    )
    review = submitted["review"]
    reflected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "late-link-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "计划结束后我仍补完复盘；虽然答对，但第一题判断过程并不稳定。",
            "tags": ["判断稳定性"],
            "strategyNote": "下次写出完整判断依据。",
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
            "candidateVersionId": reflected_data["review"]["aiCandidate"]["candidateVersionId"],
            "decision": "replace",
            "replacementCause": "第一题虽答对，但判断依据不完整。",
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


def test_due_mastery_version_runs_independent_verification_and_schedules_first_review(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="xingce-verification-runtime-token",
        choices=["A", "B", "C"],
    )
    review = submitted["review"]
    reflected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "mastery-initial-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "我已经确认这组题的判断依据，下一步需要独立新题验证。",
            "tags": ["判断依据"],
            "strategyNote": "新题仍先圈限定词。",
            "doubtNote": None,
        },
    )
    assert reflected.status_code == 200, reflected.text
    reflected_data = reflected.json()["data"]
    initial_candidate_id = reflected_data["review"]["aiCandidate"]["candidateVersionId"]
    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "mastery-initial-decision"},
        json={
            "expectedRevision": reflected_data["review"]["revision"],
            "candidateVersionId": initial_candidate_id,
            "decision": "reject",
            "replacementCause": None,
        },
    )
    assert decided.status_code == 200, decided.text
    decided_data = decided.json()["data"]
    verification_task = next(
        (
            task
            for task in decided_data["completion"]["plan"]["tasks"]
            if task["stage"] == "objective_verification"
        ),
        None,
    )
    assert verification_task is not None, {
        "stages": [task["stage"] for task in decided_data["completion"]["plan"]["tasks"]],
        "dueVerificationCount": decided_data["completion"]["plan"]["dueVerificationCount"],
        "inputVersions": decided_data["completion"]["plan"]["inputVersions"],
    }
    started = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": "mastery-verification-start"},
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
    assert verification["mode"] == "verification"
    question_ids = [question["questionId"] for question in verification["bundle"]["questions"]]
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
    source_question_ids = {str(item["questionId"]) for item in source_scoring_bundle["questions"]}
    correct_options_by_question = {
        str(item["questionId"]): [str(option) for option in item["correctOptionIds"]]
        for item in verification_scoring_bundle["questions"]
    }
    assert len(question_ids) == 3
    assert len(set(question_ids)) == 3
    assert set(question_ids).isdisjoint(source_question_ids)
    assert set(question_ids) | source_question_ids <= {
        f"xingce-route-question-{index}" for index in range(1, 61)
    }
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{verification['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "mastery-verification-checkpoint"},
        json={
            "expectedRevision": verification["revision"],
            "currentQuestionId": question_ids[-1],
            "answers": [
                {
                    "questionId": question_id,
                    "selectedOptionIds": [correct_options_by_question[question_id][0]],
                    "eliminatedOptionIds": [],
                    "confidence": "high",
                    "marked": False,
                    "skipped": False,
                    "elapsedSeconds": 45 + index,
                    "draftText": "",
                    "materialPosition": None,
                }
                for index, question_id in enumerate(question_ids)
            ],
            "totalElapsedSeconds": 150,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    submitted_verification = client.post(
        f"/api/v2/xingce/task-sessions/{verification['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": "mastery-verification-submit"},
        json={
            "expectedRevision": checkpoint.json()["data"]["revision"],
            "totalElapsedSeconds": 155,
        },
    )
    assert submitted_verification.status_code == 200, submitted_verification.text
    verification_review = submitted_verification.json()["data"]["review"]
    verification_reflection = client.put(
        f"/api/v2/xingce/attempt-reviews/{verification_review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "mastery-verification-reflection"},
        json={
            "expectedRevision": verification_review["revision"],
            "rawText": "三道独立新题都能说明判断依据，没有沿用原题答案。",
            "tags": ["独立验证"],
            "strategyNote": "首次间隔复测继续检查迁移。",
            "doubtNote": None,
        },
    )
    assert verification_reflection.status_code == 200, verification_reflection.text
    verification_reflected = verification_reflection.json()["data"]
    verification_candidate_id = verification_reflected["review"]["aiCandidate"][
        "candidateVersionId"
    ]
    completed = client.put(
        f"/api/v2/xingce/attempt-reviews/{verification_review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "mastery-verification-decision"},
        json={
            "expectedRevision": verification_reflected["review"]["revision"],
            "candidateVersionId": verification_candidate_id,
            "decision": "reject",
            "replacementCause": None,
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["review"]["state"] == "mastery_recorded"

    with db_session_factory() as session:
        versions = list(
            session.execute(
                text(
                    """
                    select version_no, state, verification_stage, due_at,
                           rating, used_question_ids,
                           source_session_id::text
                    from learning.xingce_mastery_state_versions
                    order by version_no
                    """
                )
            ).mappings()
        )
    assert len(versions) == 2
    assert versions[0]["state"] == "reflected"
    assert versions[0]["verification_stage"] == "immediate"
    assert versions[1]["state"] == "provisionally_mastered"
    assert versions[1]["verification_stage"] == "first_review"
    assert versions[1]["rating"] == 3
    assert set(versions[1]["used_question_ids"]) == source_question_ids | set(question_ids)
    assert versions[1]["source_session_id"] == verification["sessionId"]


def test_xingce_migration_downgrade_guard_detects_personal_records(
    db_session_factory: sessionmaker[Session],
) -> None:
    migration = importlib.import_module("migrations.versions.20260715_19_xingce_learning_closure")
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
    abandon_before_submit: bool = False,
) -> tuple[TestClient, dict[str, str], dict[str, Any], dict[str, Any]]:
    user_id = _seed_learner_and_published_essay_content(
        factory,
        access_token=token,
        include_objective_questions=False,
    )
    # Dynamic daily bundles can consume more than the historical fixed three
    # questions. Keep a separate supply available for the verification task
    # that these closure tests exercise.
    seed_route_frozen_release(factory, count=60)
    with factory.begin() as session:
        session.execute(
            text(
                """
                update learning.weekly_study_preference_versions
                set weekday_minutes = 180, weekend_minutes = 180,
                    long_block_minutes = 60, fragment_minutes = 30
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        )
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
    questions = started["bundle"]["questions"]
    with factory() as session:
        scoring_bundle = session.execute(
            text(
                "select scoring_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
    correct_options_by_question = {
        str(item["questionId"]): [str(option) for option in item["correctOptionIds"]]
        for item in scoring_bundle["questions"]
    }
    legacy_correct_options = ["A", "B", "C"]
    resolved_choices: list[str] = []
    if len(choices) > len(questions):
        raise AssertionError("test choices exceed the frozen question bundle")
    for index, question in enumerate(questions):
        correct_options = correct_options_by_question[question["questionId"]]
        if index >= len(choices):
            resolved_choices.append(correct_options[0])
            continue
        choice = choices[index]
        if index < len(legacy_correct_options) and choice == legacy_correct_options[index]:
            resolved_choices.append(correct_options[0])
            continue
        resolved_choices.append(
            next(option for option in ("A", "B", "C", "D") if option not in correct_options)
        )
    elapsed_seconds = [40 + index for index in range(len(questions))]
    total_elapsed_seconds = sum(elapsed_seconds)
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
                    "elapsedSeconds": elapsed_seconds[index],
                    "draftText": "",
                    "materialPosition": None,
                }
                for index, (question, choice) in enumerate(
                    zip(questions, resolved_choices, strict=True)
                )
            ],
            "totalElapsedSeconds": total_elapsed_seconds,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    checkpointed = checkpoint.json()["data"]
    if abandon_before_submit:
        with factory() as session:
            execution = (
                session.execute(
                    text(
                        """
                        select execution.execution_version_id::text,
                               execution.elapsed_minutes,
                               plan.valid_until > now() as plan_is_valid,
                               not exists (
                                 select 1 from learning.daily_plan_versions successor
                                 where successor.supersedes_plan_version_id =
                                       plan.plan_version_id
                               ) as plan_is_current
                        from learning.task_execution_versions execution
                        join learning.daily_plan_versions plan
                          on plan.plan_version_id = execution.plan_version_id
                        where execution.task_id = cast(:task_id as uuid)
                          and execution.status = 'in_progress'
                        """
                    ),
                    {"task_id": task["id"]},
                )
                .mappings()
                .one()
            )
        assert execution["plan_is_valid"] is True
        assert execution["plan_is_current"] is True
        abandoned = client.put(
            f"/api/v2/plans/tasks/{task['id']}/execution",
            headers={**auth, "Idempotency-Key": f"abandon-{token}"},
            json={
                "status": "abandoned",
                "elapsedMinutes": max(3, int(execution["elapsed_minutes"])),
                "evidenceRefs": [],
                "abandonmentReason": "当日计划结束，但保留已开始作答的复盘入口。",
                "basedOnExecutionVersionId": str(execution["execution_version_id"]),
            },
        )
        assert abandoned.status_code == 200, abandoned.text
    submit = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": f"submit-{token}"},
        json={
            "expectedRevision": checkpointed["revision"],
            "totalElapsedSeconds": total_elapsed_seconds + 3,
        },
    )
    assert submit.status_code == 200, submit.text
    return client, auth, task, submit.json()["data"]
