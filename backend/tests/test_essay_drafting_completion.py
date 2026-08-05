from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.auth.repository import token_hash_for_tests
from app.db.session import get_db_session
from app.domains.commerce.deps import get_commerce_service
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.essay_intelligence.drafting_service import (
    EssayDraftingCompletionService,
)
from app.domains.essay_intelligence.router import (
    get_essay_drafting_completion_service,
    get_essay_review_workflow_service,
)
from app.domains.essay_intelligence.multi_model_runtime import (
    EssayReviewExecutionIdentity,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROVIDER_NAME,
)
from app.domains.essay_intelligence.workflow_service import (
    EssayReviewWorkflowService,
)
from app.domains.planning.schemas import TaskExecutionCommand
from app.domains.planning.service import PlanningService
from tests.test_essay_learning_artifacts import (
    _client as _base_client,
    _content_ids,
    _material_command,
    _outline_command,
    _point_command,
    _post,
    _seed_answer_without_review,
    _seed_other_learner,
)
from tests.test_planning_routes import _seed_learner_and_published_essay_content


NOW = datetime(2026, 7, 13, 8, tzinfo=UTC)


def test_public_review_writes_fail_closed_when_worker_is_disabled(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-review-worker-disabled-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    client = _base_client(db_session_factory)
    app = cast(Any, client.app)
    app.dependency_overrides[get_essay_review_workflow_service] = lambda: (
        EssayReviewWorkflowService(
            settings=Settings(essay_review_worker_enabled=False),
        )
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "essay-worker-disabled-0001",
    }
    queue_payload = {
        "attemptId": str(uuid4()),
        "answerVersionId": str(uuid4()),
        "reviewScope": "grounded_full",
    }
    ensure_payload = {
        "taskVersionId": str(uuid4()),
        "materialBundleVersionId": str(uuid4()),
        "rubricVersionId": str(uuid4()),
        "reviewScope": "grounded_full",
    }

    responses = (
        client.post(
            "/api/v2/essay-intelligence/reviews",
            headers=headers,
            json=queue_payload,
        ),
        client.post(
            "/api/v2/essay-intelligence/reviews/current/ensure",
            headers=headers,
            json=ensure_payload,
        ),
        client.post(
            f"/api/v2/essay-intelligence/review-jobs/{uuid4()}/regenerate",
            headers=headers,
        ),
    )

    for response in responses:
        assert response.status_code == 503, response.json()
    assert {response.json()["code"] for response in responses} == {"service_unavailable"}
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from platform.background_jobs "
                    "where job_type = 'essay_grounded_review_v2'"
                )
            ).scalar_one()
            == 0
        )


def test_atomic_drafting_completion_is_owned_versioned_and_strongly_idempotent(
    db_session_factory: sessionmaker[Session],
) -> None:
    context = _drafting_context(
        db_session_factory,
        token="essay-drafting-atomic-token",
    )
    command = _create_command(context)

    response = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-atomic-command-0001"},
        json=command,
    )

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["attempt"]["taskVersionId"] == context["ids"]["task"]
    assert result["answer"]["attemptId"] == result["attempt"]["attemptId"]
    assert result["reflection"]["answerVersionId"] == result["answer"]["answerVersionId"]
    assert result["completion"]["execution"]["status"] == "complete"
    assert not any(item["track"] == "essay" for item in result["completion"]["plan"]["tasks"])
    assert (
        "评阅还在路上"
        in result["completion"]["plan"]["dualTrackExplanation"]["omittedTrackReasons"]["essay"]
    )
    assert {item["entityType"] for item in result["completion"]["execution"]["evidenceRefs"]} == {
        "essay_answer_version",
        "reflection_artifact",
    }
    answer_ref = next(
        item
        for item in result["completion"]["execution"]["evidenceRefs"]
        if item["entityType"] == "essay_answer_version"
    )
    assert answer_ref["role"].startswith("drafting_command:")
    intent = result["reviewIntent"]
    assert intent["status"] == "pending"
    assert intent["versionNo"] == 1
    assert intent["attemptId"] == result["attempt"]["attemptId"]
    assert intent["answerVersionId"] == result["answer"]["answerVersionId"]
    assert intent["taskVersionId"] == context["ids"]["task"]
    assert intent["materialBundleVersionId"] == context["ids"]["material"]
    assert intent["rubricVersionId"] == context["ids"]["rubric"]
    assert intent["jobId"] is None
    assert intent["quota"] is None
    assert len(intent["inputFingerprint"]) == 64
    assert len(intent["intentFingerprint"]) == 64
    assert len(intent["stateFingerprint"]) == 64

    _set_drafting_now(context["client"], NOW + timedelta(days=1))
    replay = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-atomic-command-0001"},
        json=command,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == result

    changed = {
        **command,
        "reflection": {
            **command["reflection"],
            "whatIWillChange": "改用另一个复盘动作",
        },
    }
    conflict = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-atomic-command-0001"},
        json=changed,
    )
    assert conflict.status_code == 409

    same_rows_different_command_shape = {
        **command,
        "attempt": {
            "mode": "existing",
            "attemptId": result["attempt"]["attemptId"],
        },
        "answer": {
            "mode": "existing",
            "answerVersionId": result["answer"]["answerVersionId"],
        },
    }
    composite_conflict = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-atomic-command-0001"},
        json=same_rows_different_command_shape,
    )
    assert composite_conflict.status_code == 409

    with db_session_factory() as session:
        assert _count(session, "learning.essay_attempts", context["user_id"]) == 1
        assert _count(session, "learning.essay_answer_versions", context["user_id"]) == 1
        assert (
            _count(
                session,
                "learning.essay_learning_artifact_versions",
                context["user_id"],
                "and artifact_type = 'reflection'",
            )
            == 1
        )
        assert _count(session, "learning.task_execution_versions", context["user_id"]) == 1
        assert (
            _count(
                session,
                "learning.essay_review_intent_versions",
                context["user_id"],
            )
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) from commerce.feature_usage_reservations "
                    "where user_id = cast(:user_id as uuid) and feature = 'essay_review'"
                ),
                {"user_id": context["user_id"]},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where aggregate_type = 'essay_review_intent'"
                )
            ).scalar_one()
            == 1
        )


def test_expired_drafting_task_returns_stable_conflict_without_partial_writes(
    db_session_factory: sessionmaker[Session],
) -> None:
    context = _drafting_context(
        db_session_factory,
        token="essay-drafting-expired-token",
    )
    _set_drafting_now(context["client"], NOW + timedelta(days=1))

    response = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-expired-command-0001"},
        json=_create_command(context),
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "plan_task_expired"
    assert body["message"] == "该任务所属的每日计划已过期，请刷新今日计划后继续。"
    assert body["details"]["taskId"] == str(context["task"].id)
    assert body["details"]["planVersionId"] == str(context["plan_version_id"])
    assert body["details"]["expiredAt"]

    with db_session_factory() as session:
        assert _count(session, "learning.essay_attempts", context["user_id"]) == 0
        assert _count(session, "learning.essay_answer_versions", context["user_id"]) == 0
        assert (
            _count(
                session,
                "learning.essay_learning_artifact_versions",
                context["user_id"],
                "and artifact_type = 'reflection'",
            )
            == 0
        )
        assert _count(session, "learning.task_execution_versions", context["user_id"]) == 0
        assert (
            _count(
                session,
                "learning.essay_review_intent_versions",
                context["user_id"],
            )
            == 0
        )


def test_late_planning_version_conflict_rolls_back_attempt_answer_and_reflection(
    db_session_factory: sessionmaker[Session],
) -> None:
    context = _drafting_context(
        db_session_factory,
        token="essay-drafting-rollback-token",
    )
    task = context["task"]
    with db_session_factory.begin() as session:
        in_progress = PlanningService().record_task_execution(
            session,
            user_id=context["user_id"],
            task_id=str(task.id),
            command_id="setup-in-progress-drafting",
            command=TaskExecutionCommand(
                status="in_progress",
                elapsed_minutes=1,
            ),
            now=NOW,
        )
    current_task = next(item for item in in_progress.plan.tasks if item.stage == "essay_drafting")
    context["task"] = current_task
    command = {
        **_create_command(context),
        "basedOnExecutionVersionId": str(uuid4()),
    }

    response = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-rollback-command-0001"},
        json=command,
    )

    assert response.status_code == 409, response.text
    with db_session_factory() as session:
        assert _count(session, "learning.essay_attempts", context["user_id"]) == 0
        assert _count(session, "learning.essay_answer_versions", context["user_id"]) == 0
        assert (
            _count(
                session,
                "learning.essay_learning_artifact_versions",
                context["user_id"],
                "and artifact_type = 'reflection'",
            )
            == 0
        )
        assert _count(session, "learning.task_execution_versions", context["user_id"]) == 1
        assert (
            _count(
                session,
                "learning.essay_review_intent_versions",
                context["user_id"],
            )
            == 0
        )


def test_current_review_ensure_is_cross_device_and_business_idempotent(
    db_session_factory: sessionmaker[Session],
) -> None:
    context = _drafting_context(
        db_session_factory,
        token="essay-drafting-device-one-token",
    )
    second_token = "essay-drafting-device-two-token"
    _add_device_session(
        db_session_factory,
        user_id=context["user_id"],
        access_token=second_token,
        device_id="wechat-device-two",
    )
    completed = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-cross-device-0001"},
        json=_create_command(context),
    )
    assert completed.status_code == 200, completed.text
    pending = completed.json()["data"]["reviewIntent"]
    ensure_payload = {
        "taskVersionId": context["ids"]["task"],
        "materialBundleVersionId": context["ids"]["material"],
        "rubricVersionId": context["ids"]["rubric"],
        "reviewScope": "grounded_full",
    }

    device_two = context["client"].post(
        "/api/v2/essay-intelligence/reviews/current/ensure",
        headers={
            "Authorization": f"Bearer {second_token}",
            "Idempotency-Key": "ensure-current-device-two-0001",
        },
        json=ensure_payload,
    )
    assert device_two.status_code == 202, device_two.text
    queued = device_two.json()["data"]
    assert queued["intent"]["intentId"] == pending["intentId"]
    assert queued["intent"]["status"] == "queued"
    assert queued["intent"]["versionNo"] == 2
    assert queued["intent"]["jobId"] == queued["job"]["jobId"]
    assert queued["intent"]["jobStatus"] == "queued"

    replay = context["client"].post(
        "/api/v2/essay-intelligence/reviews/current/ensure",
        headers={
            "Authorization": f"Bearer {second_token}",
            "Idempotency-Key": "ensure-current-device-two-0001",
        },
        json=ensure_payload,
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["data"] == queued

    other_device_key = context["client"].post(
        "/api/v2/essay-intelligence/reviews/current/ensure",
        headers={**context["auth"], "Idempotency-Key": "ensure-current-device-one-0002"},
        json=ensure_payload,
    )
    assert other_device_key.status_code == 202, other_device_key.text
    assert other_device_key.json()["data"]["job"]["jobId"] == queued["job"]["jobId"]
    assert other_device_key.json()["data"]["intent"] == queued["intent"]

    workspace = context["client"].get(
        f"/api/v2/essay-intelligence/tasks/{context['ids']['task']}/workspace",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert workspace.status_code == 200, workspace.text
    progression = workspace.json()["data"]["progression"]
    assert progression["reviewIntent"] == queued["intent"]
    assert progression["reviewJobId"] == queued["job"]["jobId"]

    other_token = "essay-drafting-unowned-current-token"
    _seed_other_learner(db_session_factory, access_token=other_token)
    unowned = context["client"].post(
        "/api/v2/essay-intelligence/reviews/current/ensure",
        headers={
            "Authorization": f"Bearer {other_token}",
            "Idempotency-Key": "ensure-current-unowned-0001",
        },
        json=ensure_payload,
    )
    assert unowned.status_code == 404

    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from platform.background_jobs "
                    "where job_type = 'essay_grounded_review_v2'"
                )
            ).scalar_one()
            == 1
        )
        assert (
            _count(
                session,
                "learning.essay_review_intent_versions",
                context["user_id"],
            )
            == 2
        )


def test_quota_waiting_is_durable_without_rolling_back_drafting_facts(
    db_session_factory: sessionmaker[Session],
) -> None:
    context = _drafting_context(
        db_session_factory,
        token="essay-drafting-quota-waiting-token",
    )
    _exhaust_review_quota(db_session_factory, user_id=context["user_id"])
    completed = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-quota-waiting-0001"},
        json=_create_command(context),
    )
    assert completed.status_code == 200, completed.text
    result = completed.json()["data"]
    assert result["reviewIntent"]["status"] == "pending"
    _set_metered_commerce(context["client"])
    ensure_payload = {
        "taskVersionId": context["ids"]["task"],
        "materialBundleVersionId": context["ids"]["material"],
        "rubricVersionId": context["ids"]["rubric"],
        "reviewScope": "grounded_full",
    }

    waiting_response = context["client"].post(
        "/api/v2/essay-intelligence/reviews/current/ensure",
        headers={**context["auth"], "Idempotency-Key": "ensure-quota-waiting-0001"},
        json=ensure_payload,
    )
    assert waiting_response.status_code == 202, waiting_response.text
    waiting = waiting_response.json()["data"]
    assert waiting["job"] is None
    assert waiting["intent"]["status"] == "waiting_quota"
    assert waiting["intent"]["versionNo"] == 2
    assert waiting["intent"]["jobId"] is None
    assert waiting["intent"]["quota"]["feature"] == "essay_review"
    assert waiting["intent"]["quota"]["used"] == 1
    assert waiting["intent"]["quota"]["limit"] == 1
    assert waiting["intent"]["retryAfter"] is not None

    replay = context["client"].post(
        "/api/v2/essay-intelligence/reviews/current/ensure",
        headers={**context["auth"], "Idempotency-Key": "ensure-quota-waiting-0001"},
        json=ensure_payload,
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["data"] == waiting

    workspace = context["client"].get(
        f"/api/v2/essay-intelligence/tasks/{context['ids']['task']}/workspace",
        headers=context["auth"],
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["data"]["progression"]["reviewIntent"] == waiting["intent"]

    with db_session_factory() as session:
        assert _count(session, "learning.essay_attempts", context["user_id"]) == 1
        assert _count(session, "learning.essay_answer_versions", context["user_id"]) == 1
        assert _count(session, "learning.task_execution_versions", context["user_id"]) == 1
        assert (
            _count(
                session,
                "learning.essay_review_intent_versions",
                context["user_id"],
            )
            == 2
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.background_jobs "
                    "where job_type = 'essay_grounded_review_v2'"
                )
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "select count(*) from commerce.feature_usage_reservations "
                    "where user_id = cast(:user_id as uuid) and feature = 'essay_review'"
                ),
                {"user_id": context["user_id"]},
            ).scalar_one()
            == 1
        )


def test_existing_attempt_and_answer_must_be_current_and_owned(
    db_session_factory: sessionmaker[Session],
) -> None:
    context = _drafting_context(
        db_session_factory,
        token="essay-drafting-owner-token",
    )
    other_user_id = _seed_other_learner(
        db_session_factory,
        access_token="essay-drafting-other-token",
    )
    answer_id, attempt_id = _seed_answer_without_review(
        db_session_factory,
        user_id=other_user_id,
        ids=context["ids"],
    )
    command = _create_command(context)
    command["attempt"] = {"mode": "existing", "attemptId": attempt_id}
    command["answer"] = {"mode": "existing", "answerVersionId": answer_id}

    response = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**context["auth"], "Idempotency-Key": "drafting-cross-owner-0001"},
        json=command,
    )

    assert response.status_code == 404, response.text
    with db_session_factory() as session:
        assert _count(session, "learning.essay_attempts", context["user_id"]) == 0
        assert _count(session, "learning.task_execution_versions", context["user_id"]) == 0


def test_drafting_command_rejects_wrong_stage_and_content_versions_before_writing(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-drafting-stage-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    material = _post(
        client, auth, "stage-material", _material_command(ids["task"], ids["material"])
    )
    point = _post(
        client,
        auth,
        "stage-point",
        _point_command(ids, material["artifactVersionId"]),
    )
    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    outline_task = next(item for item in plan.tasks if item.stage == "essay_outlining")
    context = {
        "client": client,
        "auth": auth,
        "user_id": user_id,
        "ids": ids,
        "task": outline_task,
    }
    wrong_stage = client.post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**auth, "Idempotency-Key": "drafting-wrong-stage-0001"},
        json=_create_command(context),
    )
    assert wrong_stage.status_code == 409

    _post(client, auth, "stage-outline", _outline_command(ids, point["artifactVersionId"]))
    with db_session_factory.begin() as session:
        drafting_plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    context["task"] = next(item for item in drafting_plan.tasks if item.stage == "essay_drafting")
    mismatch = {
        **_create_command(context),
        "materialBundleVersionId": str(uuid4()),
    }
    wrong_content = client.post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={**auth, "Idempotency-Key": "drafting-wrong-content-0001"},
        json=mismatch,
    )
    assert wrong_content.status_code == 409
    with db_session_factory() as session:
        assert _count(session, "learning.essay_attempts", user_id) == 0
        assert _count(session, "learning.essay_answer_versions", user_id) == 0
        assert _count(session, "learning.task_execution_versions", user_id) == 0


def _drafting_context(
    factory: sessionmaker[Session],
    *,
    token: str,
) -> dict[str, object]:
    user_id = _seed_learner_and_published_essay_content(
        factory,
        access_token=token,
    )
    ids = _content_ids(factory)
    client = _client(factory)
    auth = {"Authorization": f"Bearer {token}"}
    material = _post(
        client,
        auth,
        f"{token}-material",
        _material_command(ids["task"], ids["material"]),
    )
    point = _post(
        client,
        auth,
        f"{token}-point",
        _point_command(ids, material["artifactVersionId"]),
    )
    _post(
        client,
        auth,
        f"{token}-outline",
        _outline_command(ids, point["artifactVersionId"]),
    )
    with factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    return {
        "client": client,
        "auth": auth,
        "user_id": user_id,
        "ids": ids,
        "plan_version_id": plan.plan_version_id,
        "task": next(item for item in plan.tasks if item.stage == "essay_drafting"),
    }


def _client(factory: sessionmaker[Session]) -> TestClient:
    client = _base_client(factory)
    _set_drafting_now(client, NOW)
    app = cast(Any, client.app)
    app.dependency_overrides[get_essay_review_workflow_service] = lambda: (
        EssayReviewWorkflowService(
            execution_identity_override=EssayReviewExecutionIdentity(
                provider=GROUNDED_REVIEW_PROVIDER_NAME,
                model="gemini-3.5-flash",
            )
        )
    )
    return client


def _set_drafting_now(client: object, now: datetime) -> None:
    app = cast(Any, cast(TestClient, client).app)
    app.dependency_overrides[get_essay_drafting_completion_service] = lambda: (
        EssayDraftingCompletionService(now=lambda: now)
    )


def _set_metered_commerce(client: object) -> None:
    def override(
        session: Annotated[Session, Depends(get_db_session)],
    ) -> CommerceService:
        return CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=cast(Any, object()),
            commercial_mode="paid",
            usage_access_mode="entitlement_quota",
        )

    app = cast(Any, cast(TestClient, client).app)
    app.dependency_overrides[get_commerce_service] = override


def _create_command(context: dict[str, object]) -> dict[str, object]:
    ids = context["ids"]
    task = context["task"]
    return {
        "planTaskId": str(task.id),
        "taskVersionId": ids["task"],
        "materialBundleVersionId": ids["material"],
        "rubricVersionId": ids["rubric"],
        "elapsedMinutes": 25,
        "basedOnExecutionVersionId": None,
        "attempt": {
            "mode": "create",
            "sourceContext": "in_program",
            "timedStatus": "timed",
            "elapsedSeconds": 1500,
        },
        "answer": {
            "mode": "create",
            "sourceType": "typed",
            "answerText": "基层治理需要先归纳共性问题，再提出协同处置机制。",
            "thinking": {
                "userThought": "我先圈出主体和问题，再按同类项合并。",
                "pointFindingText": "从材料中的执行主体和矛盾表现找点。",
                "outlineText": "问题、原因、对策。",
            },
        },
        "reflection": {
            "schemaType": "reflection",
            "causeCodes": ["wrong_abstraction", "weak_structure"],
            "whatIThought": "我担心漏点，所以保留了太多材料原词。",
            "whatIWillChange": "先写上位概念，再用材料词校验覆盖范围。",
            "verificationPlan": "下一道概括题逐点检查概念层级和并列关系。",
        },
        "basedOnReflectionArtifactVersionId": None,
    }


def _count(
    session: Session,
    table: str,
    user_id: str,
    extra_sql: str = "",
) -> int:
    return int(
        session.execute(
            text(
                f"select count(*) from {table} where user_id = cast(:user_id as uuid) {extra_sql}"
            ),
            {"user_id": user_id},
        ).scalar_one()
    )


def _add_device_session(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    access_token: str,
    device_id: str,
) -> None:
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  :session_id, cast(:user_id as uuid), :token_hash, :device_id,
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(access_token),
                "device_id": device_id,
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )


def _exhaust_review_quota(
    factory: sessionmaker[Session],
    *,
    user_id: str,
) -> None:
    with factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=cast(Any, object()),
            commercial_mode="paid",
            usage_access_mode="entitlement_quota",
            now=lambda: datetime.now(UTC),
        )
        service.reserve_feature_usage(
            user_id=user_id,
            feature="essay_review",
            reservation_key=f"test-exhaust-essay-review:{user_id}",
            business_key=f"test-exhaust-essay-review:{user_id}",
            commit=False,
        )
        session.commit()
