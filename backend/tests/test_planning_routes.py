from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.essay_intelligence.learning_service import EssayLearningArtifactService
from app.domains.essay_intelligence.router import get_essay_learning_artifact_service
from app.domains.essay_intelligence.closure.service import EssayClosureService
from app.domains.essay_intelligence.router import get_essay_closure_service
from app.domains.planning.repository import (
    PlanningRepository,
    _review_is_learning_eligible,
    _weakest_dimension,
)
from app.domains.planning.candidates import (
    GoalPlanningSnapshot,
    ObjectivePlanningSnapshot,
    build_dual_track_candidates,
)
from app.domains.planning.router import get_planning_service
from app.domains.planning.schemas import DailyCapacityCommand
from app.domains.planning.service import PlanningService, _materialize_plan_task_start_url
from app.domains.xingce_learning.real_attempt.schemas import StartTaskSessionCommand
from app.domains.xingce_learning.real_attempt.service import XingceAttemptService
from app.main import create_app
from tests.factories import (
    assert_canonical_frozen_release,
    seed_planning_shared_frozen_release,
)


NOW = datetime(2026, 7, 13, 8, tzinfo=UTC)
TOKEN = "planning-v2-route-token"


def _make_material_reading_plan_task(
    factory: sessionmaker[Session],
    *,
    task_id: str,
) -> None:
    completion_contract = {
        "objective": "诊断后的申论材料地图特训",
        "successCriteria": ["完成材料分段标注，为每段保留主旨、对象与可引用证据。"],
        "terminalConditions": [
            "满足验收条件并提交证据",
            "用户主动结束并记录未完成原因",
        ],
        "requiresReflection": False,
        "completionEvidenceTypes": ["essay_material_annotation", "task_execution"],
    }
    with factory.begin() as session:
        session.execute(
            text(
                """
                update learning.daily_plan_tasks
                set stage = 'essay_material_reading',
                    title = '申论专项：材料理解',
                    reason = '最近作答证据显示材料理解需要专项训练。',
                    estimated_minutes = 15,
                    completion_contract = cast(:completion_contract as jsonb)
                where task_id = cast(:task_id as uuid)
                """
            ),
            {
                "task_id": task_id,
                "completion_contract": json.dumps(
                    completion_contract,
                    ensure_ascii=False,
                ),
            },
        )


def test_canonical_frozen_release_factory_satisfies_current_contract(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory.begin() as session:
        release = seed_planning_shared_frozen_release(session)
        assert_canonical_frozen_release(session, release)


def test_essay_planning_only_uses_exact_grounded_learning_evidence() -> None:
    findings = [
        {
            "findingId": "low-first",
            "type": "expression",
            "severity": "low",
        },
        {
            "findingId": "priority-structure",
            "type": "structure",
            "severity": "high",
        },
    ]
    review = {
        "review_status": "completed",
        "grounding_contract_version": "essay-grounding-v2",
        "grounding_report": {
            "verified": True,
            "eligibleForLearning": True,
        },
    }

    assert _review_is_learning_eligible(review) is True
    assert (
        _weakest_dimension(
            findings,
            {"priorityFindingIds": ["priority-structure"]},
        )
        == "结构层次"
    )

    for invalid in (
        {**review, "review_status": "blocked"},
        {**review, "grounding_contract_version": "essay-grounding-v1"},
        {
            **review,
            "grounding_report": {
                "verified": False,
                "eligibleForLearning": True,
            },
        },
        {
            **review,
            "grounding_report": {
                "verified": True,
                "eligibleForLearning": False,
            },
        },
    ):
        assert _review_is_learning_eligible(invalid) is False


def test_plan_task_start_urls_are_materialized_with_plan_task_id() -> None:
    task_id = str(uuid4())

    assert (
        _materialize_plan_task_start_url(
            "/pages/xingce-task-intro/index?mode=baseline",
            task_id=task_id,
        )
        == f"/pages/xingce-task-intro/index?mode=baseline&planTaskId={task_id}"
    )
    assert (
        _materialize_plan_task_start_url(
            f"/pages/xingce-task-intro/index?mode=targeted&planTaskId={task_id}",
            task_id=str(uuid4()),
        )
        == f"/pages/xingce-task-intro/index?mode=targeted&planTaskId={task_id}"
    )
    assert (
        _materialize_plan_task_start_url(
            "/pages/mock-exam/index",
            task_id=task_id,
        )
        == f"/pages/mock-exam/index?planTaskId={task_id}"
    )
    assert _materialize_plan_task_start_url(
        "/pages/essay-task/index?mode=material-reading"
        "&stage=essay_material_reading&taskVersionId=essay-task-v1",
        task_id=task_id,
    ) == (
        "/pages/essay-task/index?mode=material-reading"
        "&stage=essay_material_reading&taskVersionId=essay-task-v1"
        f"&planTaskId={task_id}"
    )


def test_dual_track_plan_persists_one_idempotent_xingce_and_essay_budget(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    service = PlanningService()

    with db_session_factory.begin() as session:
        first = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    with db_session_factory.begin() as session:
        replay = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )

    assert first.plan_version_id == replay.plan_version_id
    assert first.plan_basis == "evidence_priority_v2"
    assert first.day_mode == "normal"
    assert first.goal_gap.readiness == "unavailable"
    assert first.goal_gap.baseline_gap is None
    assert first.capacity.available_minutes == 45
    assert first.capacity.confirmed is False
    assert sum(task.estimated_minutes for task in first.tasks) == 37
    assert {task.track for task in first.tasks} == {"xingce", "essay"}
    assert {task.stage for task in first.tasks} == {
        "objective_baseline",
        "essay_drafting",
    }
    assert first.dual_track_explanation.dual_track_constraint_applied is True
    assert first.dual_track_explanation.xingce_minutes == 12
    assert first.dual_track_explanation.essay_minutes == 25
    assert all(task.evidence_refs for task in first.tasks)
    xingce_task = next(task for task in first.tasks if task.track == "xingce")
    xingce_query = parse_qs(urlsplit(xingce_task.start_url).query)
    assert xingce_query["mode"] == ["baseline"]
    assert xingce_query["planTaskId"] == [str(xingce_task.id)]

    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from learning.daily_plan_versions")).scalar_one()
            == 1
        )
        assert (
            session.execute(text("select count(*) from learning.daily_plan_tasks")).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text("select count(*) from learning.weekly_study_preference_versions")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("select count(*) from learning.daily_capacity_versions")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where event_type = 'daily_plan.changed'"
                )
            ).scalar_one()
            == 1
        )


def test_plan_omits_xingce_when_the_start_contract_has_no_three_question_bundle(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        include_objective_questions=False,
    )

    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )

    assert all(task.track != "xingce" for task in plan.tasks)
    assert plan.dual_track_explanation.omitted_track_reasons["xingce"] == (
        "当前没有满足内容与证据门禁的行测任务。"
    )


def test_started_question_bundles_never_repeat_and_exhausted_supply_is_not_planned(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    planning = PlanningService()
    attempts = XingceAttemptService()
    delivered: list[set[str]] = []

    for offset in range(1):
        current = NOW + timedelta(days=offset)
        with db_session_factory.begin() as session:
            plan = planning.get_or_create_today(
                session,
                user_id=user_id,
                now=current,
                local_date=current.date(),
            )
            task = next(item for item in plan.tasks if item.track == "xingce")
            started = attempts.start_or_resume(
                session,
                user_id=user_id,
                command_id=f"start-distinct-bundle-{offset}",
                command=StartTaskSessionCommand(planTaskId=task.id),
                now=current,
            )
            delivered.append({question.question_id for question in started.bundle.questions})

    assert len(delivered[0]) == 6
    expected_supply = delivered[0]
    assert len(expected_supply) == 6

    other_user_id = _seed_planning_learner(db_session_factory)
    other_delivered: list[set[str]] = []
    for offset in range(1):
        current = NOW + timedelta(days=offset)
        with db_session_factory.begin() as session:
            other_plan = planning.get_or_create_today(
                session,
                user_id=other_user_id,
                now=current,
                local_date=current.date(),
            )
            other_task = next(item for item in other_plan.tasks if item.track == "xingce")
            other_started = attempts.start_or_resume(
                session,
                user_id=other_user_id,
                command_id=f"start-distinct-bundle-other-user-{offset}",
                command=StartTaskSessionCommand(planTaskId=other_task.id),
                now=current,
            )
            other_delivered.append(
                {question.question_id for question in other_started.bundle.questions}
            )

    assert len(other_delivered[0]) == 6
    assert other_delivered[0] == expected_supply

    exhausted_day = NOW + timedelta(days=1)
    with db_session_factory.begin() as session:
        exhausted = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=exhausted_day,
            local_date=exhausted_day.date(),
        )

    assert all(task.track != "xingce" for task in exhausted.tasks)


def test_today_plan_route_requires_authenticated_learner() -> None:
    response = TestClient(create_app()).get("/api/v2/plans/today")
    assert response.status_code == 401


def test_planning_command_routes_require_authentication() -> None:
    client = TestClient(create_app())
    headers = {"Idempotency-Key": "planning-command-auth"}

    assert (
        client.post(
            "/api/v2/plans/today/capacity/preview",
            json={
                "basedOnCapacityVersionId": str(uuid4()),
                "availableMinutes": 45,
                "energy": 3,
                "stress": 3,
                "confidenceLevel": 3,
            },
        ).status_code
        == 401
    )
    assert (
        client.put(
            "/api/v2/plans/today/capacity",
            headers=headers,
            json={
                "basedOnCapacityVersionId": str(uuid4()),
                "availableMinutes": 45,
                "energy": 3,
                "stress": 3,
                "confidenceLevel": 3,
            },
        ).status_code
        == 401
    )
    assert (
        client.put(
            f"/api/v2/plans/tasks/{uuid4()}/execution",
            headers=headers,
            json={
                "status": "in_progress",
                "elapsedMinutes": 0,
                "evidenceRefs": [],
                "basedOnExecutionVersionId": None,
            },
        ).status_code
        == 401
    )


def test_today_plan_route_uses_the_authenticated_learner(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=TOKEN,
    )

    response = _client(db_session_factory).get(
        "/api/v2/plans/today",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert {task["track"] for task in body["tasks"]} == {"xingce", "essay"}
    assert body["dueReflectionCount"] == 0
    assert body["dueVerificationCount"] == 0
    assert body["inputVersions"]["objectiveClosure"] != "unavailable"
    assert body["dualTrackExplanation"]["capabilityGaps"] == {}
    assert not any(
        task["stage"] in {"objective_reflection", "objective_verification"}
        for task in body["tasks"]
    )
    assert body["inputVersions"]["essayMaterialBundle"] != "unavailable"
    assert body["inputVersions"]["essayRubric"] != "unavailable"


def test_short_capacity_mock_day_keeps_executable_dual_track_work(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)

    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=datetime(2026, 7, 18, 1, tzinfo=UTC),
            local_date=date(2026, 7, 18),
        )

    assert plan.day_mode == "normal"
    assert {task.track for task in plan.tasks} == {"xingce", "essay"}
    assert plan.dual_track_explanation.omitted_track_reasons["mock"].startswith(
        "周度标准卷需要连续 120 分钟"
    )
    assert "weekly_mock_capacity_insufficient" in plan.replan_reason_codes


def test_published_material_without_rubric_is_omitted_instead_of_faking_prerequisites(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        include_rubric=False,
    )

    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )

    assert not any(task.track == "essay" for task in plan.tasks)
    assert "评分规程尚未发布" in plan.dual_track_explanation.omitted_track_reasons[
        "essay"
    ]
    assert plan.input_versions["essayRubric"] == "unavailable"


@pytest.mark.parametrize("foreign_lineage", ["other_material", "legacy_unbound"])
def test_planning_keeps_the_exact_material_rubric_when_foreign_lineage_is_newer(
    db_session_factory: sessionmaker[Session],
    foreign_lineage: str,
) -> None:
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        material_version_no=2 if foreign_lineage == "other_material" else 1,
    )
    ids = _published_essay_content_ids(db_session_factory)
    _insert_foreign_rubric_successor(
        db_session_factory,
        ids=ids,
        foreign_lineage=foreign_lineage,
    )

    repository = PlanningRepository()
    with db_session_factory() as session:
        snapshot = repository.essay_snapshot(
            session,
            user_id=user_id,
            goal=repository.goal_and_ability(session, user_id=user_id),
        )

    assert snapshot.task_version_id == ids["task"]
    assert snapshot.material_bundle_version_id == ids["material"]
    assert snapshot.rubric_version_id == ids["rubric"]


def test_capacity_command_is_idempotent_and_creates_a_new_plan_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "planning-capacity-command-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    initial = client.get("/api/v2/plans/today", headers=auth).json()["data"]
    command = {
        "basedOnCapacityVersionId": initial["capacity"]["capacityVersionId"],
        "availableMinutes": 30,
        "energy": 4,
        "stress": 2,
        "confidenceLevel": 5,
    }
    headers = {**auth, "Idempotency-Key": "capacity-command-0001"}

    first = client.put(
        "/api/v2/plans/today/capacity",
        headers=headers,
        json=command,
    )
    replay = client.put(
        "/api/v2/plans/today/capacity",
        headers=headers,
        json=command,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200
    result = first.json()["data"]
    replay_result = replay.json()["data"]
    assert result["capacity"] == replay_result["capacity"]
    assert result["plan"]["planVersionId"] == replay_result["plan"]["planVersionId"]
    assert result["capacity"]["versionNo"] == 2
    assert result["capacity"]["confirmed"] is True
    assert result["capacity"]["availableMinutes"] == 30
    assert result["plan"]["planVersionId"] != initial["planVersionId"]
    assert "daily_capacity_changed" in result["plan"]["replanReasonCodes"]
    assert sum(task["estimatedMinutes"] for task in result["plan"]["tasks"]) <= 30

    changed_payload = {**command, "availableMinutes": 31}
    conflict = client.put(
        "/api/v2/plans/today/capacity",
        headers=headers,
        json=changed_payload,
    )
    assert conflict.status_code == 409

    with db_session_factory() as session:
        assert (
            session.execute(
                text("select count(*) from learning.daily_capacity_versions")
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where event_type = 'daily_capacity.changed'"
                )
            ).scalar_one()
            == 1
        )


def test_capacity_preview_uses_the_real_planner_without_persisting_the_proposal(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "planning-capacity-preview-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    initial = client.get("/api/v2/plans/today", headers=auth).json()["data"]
    command = {
        "basedOnCapacityVersionId": initial["capacity"]["capacityVersionId"],
        "availableMinutes": 30,
        "energy": 4,
        "stress": 2,
        "confidenceLevel": 5,
    }

    with db_session_factory() as session:
        counts_before = {
            "capacities": session.execute(
                text("select count(*) from learning.daily_capacity_versions")
            ).scalar_one(),
            "plans": session.execute(
                text("select count(*) from learning.daily_plan_versions")
            ).scalar_one(),
            "outbox": session.execute(
                text("select count(*) from platform.outbox_events")
            ).scalar_one(),
        }

    response = client.post(
        "/api/v2/plans/today/capacity/preview",
        headers=auth,
        json=command,
    )

    assert response.status_code == 200, response.text
    preview = response.json()["data"]
    assert preview["basedOnCapacityVersionId"] == command["basedOnCapacityVersionId"]
    assert preview["currentPlan"]["planVersionId"] == initial["planVersionId"]
    assert preview["proposedPlan"]["capacity"]["availableMinutes"] == 30
    assert preview["proposedPlan"]["capacity"]["energy"] == 4
    assert sum(task["estimatedMinutes"] for task in preview["proposedPlan"]["tasks"]) <= 30

    with db_session_factory() as session:
        counts_after = {
            "capacities": session.execute(
                text("select count(*) from learning.daily_capacity_versions")
            ).scalar_one(),
            "plans": session.execute(
                text("select count(*) from learning.daily_plan_versions")
            ).scalar_one(),
            "outbox": session.execute(
                text("select count(*) from platform.outbox_events")
            ).scalar_one(),
        }
    assert counts_after == counts_before


def test_capacity_preview_can_make_today_an_explicit_single_module_day(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "planning-module-focus-preview-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
        objective_canonical_module="资料分析",
        objective_raw_module="资料分析",
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    initial = client.get("/api/v2/plans/today", headers=auth).json()["data"]

    response = client.post(
        "/api/v2/plans/today/capacity/preview",
        headers=auth,
        json={
            "basedOnCapacityVersionId": initial["capacity"]["capacityVersionId"],
            "availableMinutes": 120,
            "energy": 3,
            "stress": 3,
            "confidenceLevel": 3,
            "preferredXingceModule": "资料分析",
            "xingceOnly": True,
        },
    )

    assert response.status_code == 200, response.text
    proposed = response.json()["data"]["proposedPlan"]
    assert proposed["capacity"]["preferredXingceModule"] == "资料分析"
    assert proposed["capacity"]["xingceOnly"] is True
    assert proposed["tasks"]
    assert all(task["track"] == "xingce" for task in proposed["tasks"])
    assert all("资料分析" in task["title"] for task in proposed["tasks"])


def test_positive_capacity_override_turns_scheduled_rest_day_into_normal_plan(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    _seed_today_as_regular_rest_day(db_session_factory, user_id=user_id)
    service = PlanningService()

    with db_session_factory.begin() as session:
        initial = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.astimezone(ZoneInfo("Asia/Shanghai")).date(),
        )
    assert initial.day_mode == "recovery"

    command = DailyCapacityCommand(
        based_on_capacity_version_id=str(initial.capacity.capacity_version_id),
        available_minutes=120,
        energy=4,
        stress=2,
        confidence_level=4,
    )
    with db_session_factory.begin() as session:
        preview = service.preview_today_capacity(
            session,
            user_id=user_id,
            command=command,
            now=NOW,
        )
    assert preview.current_plan.day_mode == "recovery"
    assert preview.proposed_plan.day_mode == "normal"
    assert {task.track for task in preview.proposed_plan.tasks} == {"xingce", "essay"}

    with db_session_factory.begin() as session:
        confirmed = service.set_today_capacity(
            session,
            user_id=user_id,
            command_id="scheduled-rest-positive-override",
            command=command,
            now=NOW,
        )

    assert confirmed.capacity.source_type == "user_override"
    assert confirmed.capacity.confirmed is True
    assert confirmed.plan.day_mode == "normal"
    assert confirmed.plan.is_recovery_day is False
    assert [
        (task.track, task.stage, task.title, task.estimated_minutes)
        for task in confirmed.plan.tasks
    ] == [
        (task.track, task.stage, task.title, task.estimated_minutes)
        for task in preview.proposed_plan.tasks
    ]


def test_scheduled_rest_override_does_not_bypass_zero_or_adaptive_recovery(
    db_session_factory: sessionmaker[Session],
) -> None:
    first_user_id = _seed_learner_and_published_essay_content(db_session_factory)
    for index, (suffix, minutes, energy, stress) in enumerate(
        (
            ("zero", 0, 4, 2),
            ("adaptive", 120, 1, 5),
        )
    ):
        user_id = first_user_id if index == 0 else _seed_planning_learner(db_session_factory)
        _seed_today_as_regular_rest_day(db_session_factory, user_id=user_id)
        service = PlanningService()
        with db_session_factory.begin() as session:
            initial = service.get_or_create_today(
                session,
                user_id=user_id,
                now=NOW,
                local_date=NOW.astimezone(ZoneInfo("Asia/Shanghai")).date(),
            )
        with db_session_factory.begin() as session:
            result = service.set_today_capacity(
                session,
                user_id=user_id,
                command_id=f"scheduled-rest-{suffix}-override",
                command=DailyCapacityCommand(
                    based_on_capacity_version_id=str(initial.capacity.capacity_version_id),
                    available_minutes=minutes,
                    energy=energy,
                    stress=stress,
                    confidence_level=4,
                ),
                now=NOW,
            )
        assert result.plan.day_mode == "recovery"
        assert result.plan.is_recovery_day is True
        assert "capacity_recovery_mode" in result.plan.replan_reason_codes
        if minutes == 0:
            assert result.plan.tasks == []


def test_confirmed_critical_energy_or_stress_switches_to_recovery_mode(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    service = PlanningService()

    with db_session_factory.begin() as session:
        initial = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    with db_session_factory.begin() as session:
        result = service.set_today_capacity(
            session,
            user_id=user_id,
            command_id="critical-load-recovery-command",
            command=DailyCapacityCommand(
                based_on_capacity_version_id=str(initial.capacity.capacity_version_id),
                available_minutes=initial.capacity.available_minutes,
                energy=1,
                stress=5,
                confidence_level=3,
            ),
            now=NOW,
        )

    assert result.plan.day_mode == "recovery"
    assert result.plan.is_recovery_day is True
    assert "capacity_recovery_mode" in result.plan.replan_reason_codes
    assert not any(
        task.stage in {"objective_baseline", "objective_practice", "essay_drafting"}
        for task in result.plan.tasks
    )


def test_confirmed_zero_capacity_is_an_intentional_recovery_day(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    service = PlanningService()

    with db_session_factory.begin() as session:
        initial = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    with db_session_factory.begin() as session:
        result = service.set_today_capacity(
            session,
            user_id=user_id,
            command_id="zero-capacity-recovery-command",
            command=DailyCapacityCommand(
                based_on_capacity_version_id=str(initial.capacity.capacity_version_id),
                available_minutes=0,
                energy=3,
                stress=3,
                confidence_level=3,
            ),
            now=NOW,
        )

    assert result.capacity.available_minutes == 0
    assert result.plan.plan_version_id != initial.plan_version_id
    assert result.plan.day_mode == "recovery"
    assert result.plan.is_recovery_day is True
    assert result.plan.headline == "今天恢复，不开新知识"
    assert result.plan.tasks == []
    assert "capacity_recovery_mode" in result.plan.replan_reason_codes


def test_public_task_execution_allows_progress_and_abandonment_but_not_completion(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "planning-task-command-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    answer_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.practice_answers (
                  answer_id, user_id, task_id, question_id,
                  selected_answer, correct_answer, correct
                ) values (
                  cast(:answer_id as uuid), cast(:user_id as uuid),
                  'planning-baseline', 'q-1', 'A', 'A', true
                )
                """
            ),
            {"answer_id": answer_id, "user_id": user_id},
        )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    initial = client.get("/api/v2/plans/today", headers=auth).json()["data"]
    xingce = next(task for task in initial["tasks"] if task["track"] == "xingce")
    start_headers = {**auth, "Idempotency-Key": "task-start-command-0001"}
    start_command = {
        "status": "in_progress",
        "elapsedMinutes": 5,
        "evidenceRefs": [],
        "basedOnExecutionVersionId": None,
    }

    started = client.put(
        f"/api/v2/plans/tasks/{xingce['id']}/execution",
        headers=start_headers,
        json=start_command,
    )
    replay = client.put(
        f"/api/v2/plans/tasks/{xingce['id']}/execution",
        headers=start_headers,
        json=start_command,
    )

    assert started.status_code == 200
    assert replay.status_code == 200
    started_data = started.json()["data"]
    assert started_data["replanned"] is True
    assert started_data["execution"]["status"] == "in_progress"
    assert (
        replay.json()["data"]["execution"]["executionVersionId"]
        == started_data["execution"]["executionVersionId"]
    )
    current_xingce = next(
        task for task in started_data["plan"]["tasks"] if task["track"] == "xingce"
    )
    assert current_xingce["status"] == "in_progress"
    assert (
        current_xingce["execution"]["executionVersionId"]
        == started_data["execution"]["executionVersionId"]
    )
    refreshed = client.get("/api/v2/plans/today", headers=auth).json()["data"]
    refreshed_xingce = next(task for task in refreshed["tasks"] if task["track"] == "xingce")
    assert refreshed_xingce["status"] == "in_progress"

    complete_headers = {**auth, "Idempotency-Key": "task-complete-command-0001"}
    complete_command = {
        "status": "complete",
        "elapsedMinutes": 20,
        "evidenceRefs": [
            {
                "owner": "objective_learning",
                "entityType": "question_attempt",
                "entityId": answer_id,
            }
        ],
        "basedOnExecutionVersionId": started_data["execution"]["executionVersionId"],
    }
    completed = client.put(
        f"/api/v2/plans/tasks/{current_xingce['id']}/execution",
        headers=complete_headers,
        json=complete_command,
    )
    completed_replay = client.put(
        f"/api/v2/plans/tasks/{current_xingce['id']}/execution",
        headers=complete_headers,
        json=complete_command,
    )

    assert completed.status_code == 409
    assert completed_replay.status_code == 409

    abandoned_xingce = client.put(
        f"/api/v2/plans/tasks/{current_xingce['id']}/execution",
        headers={**auth, "Idempotency-Key": "task-abandon-xingce-command-0001"},
        json={
            "status": "abandoned",
            "elapsedMinutes": 20,
            "evidenceRefs": [],
            "abandonmentReason": "当日受控行测 writer 尚未结算，主动结束当前工作项。",
            "basedOnExecutionVersionId": started_data["execution"]["executionVersionId"],
        },
    )
    assert abandoned_xingce.status_code == 200, abandoned_xingce.text
    completed_data = abandoned_xingce.json()["data"]
    assert completed_data["execution"]["status"] == "abandoned"
    assert completed_data["execution"]["versionNo"] == 2
    assert all(task["track"] != "xingce" for task in completed_data["plan"]["tasks"])
    assert sum(task["estimatedMinutes"] for task in completed_data["plan"]["tasks"]) <= 25
    assert "task_execution_recorded" in completed_data["plan"]["replanReasonCodes"]

    essay = next(task for task in completed_data["plan"]["tasks"] if task["track"] == "essay")
    abandoned = client.put(
        f"/api/v2/plans/tasks/{essay['id']}/execution",
        headers={**auth, "Idempotency-Key": "task-abandon-command-0001"},
        json={
            "status": "abandoned",
            "elapsedMinutes": 5,
            "evidenceRefs": [],
            "abandonmentReason": "今日时间已用完，主动结束而不生成任务债。",
            "basedOnExecutionVersionId": None,
        },
    )
    assert abandoned.status_code == 200, abandoned.text
    abandoned_data = abandoned.json()["data"]
    assert abandoned_data["execution"]["status"] == "abandoned"
    assert abandoned_data["plan"]["tasks"] == []
    assert abandoned_data["plan"]["dayMode"] == "unavailable"

    with db_session_factory() as session:
        assert (
            session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one()
            == 3
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where event_type = 'planning_task_execution.changed'"
                )
            ).scalar_one()
            == 3
        )


def test_expired_plan_task_route_returns_stable_conflict_without_execution_write(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "planning-expired-task-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    task = next(item for item in plan.tasks if item.track == "xingce")
    client = _client(db_session_factory)
    app = cast(Any, client.app)
    app.dependency_overrides[get_planning_service] = lambda: PlanningService(
        now=lambda: NOW + timedelta(days=1)
    )

    response = client.put(
        f"/api/v2/plans/tasks/{task.id}/execution",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "expired-task-execution-command-0001",
        },
        json={
            "status": "in_progress",
            "elapsedMinutes": 1,
            "evidenceRefs": [],
            "basedOnExecutionVersionId": None,
        },
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "plan_task_expired"
    assert body["details"]["taskId"] == str(task.id)
    assert body["details"]["planVersionId"] == str(plan.plan_version_id)
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from learning.task_execution_versions "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where event_type = 'planning_task_execution.changed'"
                )
            ).scalar_one()
            == 0
        )


def test_expired_essay_artifact_completion_uses_same_conflict_and_rolls_back(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "planning-expired-essay-artifact-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    task = next(item for item in plan.tasks if item.stage == "essay_drafting")
    _make_material_reading_plan_task(db_session_factory, task_id=str(task.id))
    task_version_id = next(
        item.entity_id for item in task.evidence_refs if item.entity_type == "essay_task_version"
    )
    material_version_id = next(
        item.entity_id
        for item in task.evidence_refs
        if item.entity_type == "essay_material_bundle_version"
    )
    client = _client(db_session_factory)
    app = cast(Any, client.app)
    app.dependency_overrides[get_essay_learning_artifact_service] = lambda: (
        EssayLearningArtifactService(now=lambda: NOW + timedelta(days=1))
    )

    response = client.post(
        "/api/v2/essay-intelligence/artifacts/complete-plan-task",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "expired-essay-artifact-command-0001",
        },
        json={
            "planTaskId": str(task.id),
            "elapsedMinutes": 15,
            "basedOnExecutionVersionId": None,
            "artifact": {
                "artifactType": "material_annotation",
                "taskVersionId": task_version_id,
                "materialBundleVersionId": material_version_id,
                "rubricVersionId": None,
                "reviewVersionId": None,
                "answerVersionId": None,
                "basedOnArtifactVersionId": None,
                "payload": {
                    "schemaType": "material_annotation",
                    "overallTheme": "基层治理问题",
                    "annotations": [
                        {
                            "segmentId": "segment-1",
                            "paragraphSummary": "材料说明基层治理问题",
                            "subjects": ["基层"],
                            "evidenceQuote": "基层治理材料。",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "plan_task_expired"
    assert body["details"]["taskId"] == str(task.id)
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from learning.essay_learning_artifact_versions "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learning.task_execution_versions "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where event_type in ("
                    "'planning_task_execution.changed', "
                    "'essay_learning_artifact.recorded'"
                    ")"
                )
            ).scalar_one()
            == 0
        )


def test_task_execution_rejects_cross_user_and_unowned_evidence(
    db_session_factory: sessionmaker[Session],
) -> None:
    first_token = "planning-owner-one-token"
    second_token = "planning-owner-two-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=first_token,
    )
    second_user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (:user_id, '另一个学习者')"
            ),
            {"user_id": second_user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  :session_id, :user_id, :token_hash, 'wechat-planning-owner-two',
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": second_user_id,
                "token_hash": token_hash_for_tests(second_token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id, daily_minutes, study_mode, onboarding_complete,
                  profile_version
                ) values (:user_id, 45, 'parttime', true, 1)
                """
            ),
            {"user_id": second_user_id},
        )
    client = _client(db_session_factory)
    first_auth = {"Authorization": f"Bearer {first_token}"}
    first_plan = client.get("/api/v2/plans/today", headers=first_auth).json()["data"]
    task = next(item for item in first_plan["tasks"] if item["track"] == "xingce")
    payload = {
        "status": "complete",
        "elapsedMinutes": 20,
        "evidenceRefs": [
            {
                "owner": "objective_learning",
                "entityType": "question_attempt",
                "entityId": str(uuid4()),
            }
        ],
        "basedOnExecutionVersionId": None,
    }

    cross_user = client.put(
        f"/api/v2/plans/tasks/{task['id']}/execution",
        headers={
            "Authorization": f"Bearer {second_token}",
            "Idempotency-Key": "cross-owner-task-command",
        },
        json=payload,
    )
    fake_evidence = client.put(
        f"/api/v2/plans/tasks/{task['id']}/execution",
        headers={**first_auth, "Idempotency-Key": "fake-evidence-task-command"},
        json=payload,
    )

    assert cross_user.status_code == 409
    assert fake_evidence.status_code == 409
    with db_session_factory() as session:
        assert (
            session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one()
            == 0
        )


def test_material_reading_completion_accepts_owned_canonical_artifact_and_replans(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "planning-material-command-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    plan = client.get("/api/v2/plans/today", headers=auth).json()["data"]
    essay = next(task for task in plan["tasks"] if task["track"] == "essay")
    _make_material_reading_plan_task(db_session_factory, task_id=essay["id"])
    task_id = next(
        ref["entityId"]
        for ref in essay["evidenceRefs"]
        if ref["entityType"] == "essay_task_version"
    )
    material_id = next(
        ref["entityId"]
        for ref in essay["evidenceRefs"]
        if ref["entityType"] == "essay_material_bundle_version"
    )
    artifact_command = {
        "artifactType": "material_annotation",
        "taskVersionId": task_id,
        "materialBundleVersionId": material_id,
        "rubricVersionId": None,
        "reviewVersionId": None,
        "answerVersionId": None,
        "basedOnArtifactVersionId": None,
        "payload": {
            "schemaType": "material_annotation",
            "overallTheme": "基层治理问题",
            "annotations": [
                {
                    "segmentId": "segment-1",
                    "paragraphSummary": "材料说明基层治理问题",
                    "subjects": ["基层"],
                    "evidenceQuote": "基层治理材料。",
                }
            ],
        },
    }
    rolled_back = client.post(
        "/api/v2/essay-intelligence/artifacts/complete-plan-task",
        headers={**auth, "Idempotency-Key": "material-atomic-invalid"},
        json={
            "artifact": artifact_command,
            "planTaskId": str(uuid4()),
            "elapsedMinutes": 15,
            "basedOnExecutionVersionId": None,
        },
    )
    assert rolled_back.status_code == 409
    with db_session_factory() as session:
        assert (
            session.execute(
                text("select count(*) from learning.essay_learning_artifact_versions")
            ).scalar_one()
            == 0
        )

    response = client.post(
        "/api/v2/essay-intelligence/artifacts/complete-plan-task",
        headers={**auth, "Idempotency-Key": "material-atomic-complete"},
        json={
            "artifact": artifact_command,
            "planTaskId": essay["id"],
            "elapsedMinutes": 15,
            "basedOnExecutionVersionId": None,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["completion"]["execution"]["status"] == "complete"
    assert result["artifact"]["artifactType"] == "material_annotation"
    assert any(task["stage"] == "essay_drafting" for task in result["completion"]["plan"]["tasks"])
    replay = client.post(
        "/api/v2/essay-intelligence/artifacts/complete-plan-task",
        headers={**auth, "Idempotency-Key": "material-atomic-complete"},
        json={
            "artifact": artifact_command,
            "planTaskId": essay["id"],
            "elapsedMinutes": 15,
            "basedOnExecutionVersionId": None,
        },
    )
    assert replay.status_code == 200
    assert replay.json()["data"] == result


def test_orphan_essay_attempt_remains_drafting_due_instead_of_evaluation_pending(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    ids = _published_essay_content_ids(db_session_factory)
    attempt_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.essay_attempts (
                  attempt_id, command_id, user_id, task_version_id, task_key,
                  material_bundle_version_id, rubric_version_id, source_context,
                  timed_status, input_fingerprint, submitted_at
                ) values (
                  cast(:attempt_id as uuid), :command_id, cast(:user_id as uuid),
                  cast(:task_id as uuid), 'guokao-summary-baseline',
                  cast(:material_id as uuid), cast(:rubric_id as uuid),
                  'in_program', 'untimed', :fingerprint, :submitted_at
                )
                """
            ),
            {
                "attempt_id": attempt_id,
                "command_id": f"orphan-attempt:{attempt_id}",
                "user_id": user_id,
                "task_id": ids["task"],
                "material_id": ids["material"],
                "rubric_id": ids["rubric"],
                "fingerprint": sha256(attempt_id.encode()).hexdigest(),
                "submitted_at": NOW,
            },
        )

    repository = PlanningRepository()
    with db_session_factory() as session:
        goal = repository.goal_and_ability(session, user_id=user_id)
        snapshot = repository.essay_snapshot(session, user_id=user_id, goal=goal)

    assert snapshot.next_stage == "drafting_due"
    assert snapshot.attempt_id == attempt_id
    assert snapshot.answer_version_id is None
    assert snapshot.review_version_id is None
    assert snapshot.pending_reason is None


def test_essay_planning_consumes_canonical_mastery_heads_and_distinct_transfer(
    db_session_factory: sessionmaker[Session],
) -> None:
    from tests.test_essay_closure_writers import (
        _insert_fresh_content,
        _post_decision,
        _seed_fresh_answer,
    )
    from tests.test_essay_learning_artifacts import (
        _client as essay_client,
        _content_ids,
        _insert_completed_review,
        _seed_answer_without_review,
    )

    token = "essay-planning-mastery-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    answer_id, attempt_id = _seed_answer_without_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    review_id = _insert_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
        answer_id=answer_id,
    )
    client = essay_client(db_session_factory)
    app = cast(Any, client.app)
    app.dependency_overrides[get_essay_closure_service] = lambda: EssayClosureService(
        now=lambda: NOW
    )
    auth = {"Authorization": f"Bearer {token}"}
    decision_id = _post_decision(
        client,
        auth,
        key="essay-planning-source-decision",
        ids=ids,
        review_id=review_id,
        decision="accepted",
    )
    repository = PlanningRepository()
    with db_session_factory() as session:
        goal = repository.goal_and_ability(session, user_id=user_id)
        rewrite_due = repository.essay_snapshot(
            session,
            user_id=user_id,
            goal=goal,
        )
    assert rewrite_due.next_stage == "rewrite_due"
    assert rewrite_due.mastery_state == "rewrite_due"
    assert rewrite_due.mastery_version_ids
    assert rewrite_due.source_answer_version_id == answer_id
    rewrite_candidate = next(
        item
        for item in build_dual_track_candidates(
            goal=GoalPlanningSnapshot(
                readiness="unavailable",
                positive_gap=True,
                goal_version_id=None,
                ability_estimate_version_id=None,
            ),
            objective=ObjectivePlanningSnapshot(False, 0, 0, None, None, "available", None),
            essay=rewrite_due,
            available_minutes=60,
        ).candidates
        if item.track == "essay"
    )
    assert rewrite_candidate.stage == "essay_rewrite"
    assert f"taskVersionId={ids['task']}" in rewrite_candidate.start_url
    assert f"findingDecisionArtifactVersionId={decision_id}" in (rewrite_candidate.start_url)
    with db_session_factory.begin() as session:
        plan = PlanningService(now=lambda: NOW).get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    rewrite_task = next(item for item in plan.tasks if item.stage == "essay_rewrite")

    # Fresh content exists before completion so the owner-committed rewrite can
    # atomically replan the next canonical transfer task.
    fresh_ids = _insert_fresh_content(db_session_factory)

    rewrite_payload = {
        "attemptId": attempt_id,
        "baseReviewVersionId": review_id,
        "baseAnswerVersionId": answer_id,
        "findingDecisionArtifactVersionId": decision_id,
        "basedOnAnswerVersionId": answer_id,
        "basedOnRewriteVersionId": None,
        "sourceType": "typed",
        "answerText": "将基层治理材料概括为责任与协同两类问题。",
        "thinking": None,
        "resolutions": [
            {
                "findingId": "finding-1",
                "status": "resolved",
                "learnerEvidence": "使用了可复用的上位概念",
            }
        ],
    }
    atomic_rewrite_payload = {
        "rewrite": rewrite_payload,
        "planTaskId": str(rewrite_task.id),
        "elapsedMinutes": 20,
        "basedOnExecutionVersionId": None,
    }
    with db_session_factory() as session:
        rewrite_counts_before = {
            "answers": session.execute(
                text("select count(*) from learning.essay_answer_versions")
            ).scalar_one(),
            "rewrites": session.execute(
                text("select count(*) from learning.essay_rewrite_versions")
            ).scalar_one(),
            "mastery": session.execute(
                text("select count(*) from learning.essay_mastery_state_versions")
            ).scalar_one(),
            "executions": session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one(),
            "outbox": session.execute(
                text("select count(*) from platform.outbox_events")
            ).scalar_one(),
        }
    rolled_back_rewrite = client.post(
        "/api/v2/essay-intelligence/rewrites/complete-plan-task",
        headers={**auth, "Idempotency-Key": "essay-atomic-rewrite-rollback"},
        json={**atomic_rewrite_payload, "planTaskId": str(uuid4())},
    )
    assert rolled_back_rewrite.status_code == 409, rolled_back_rewrite.text
    with db_session_factory() as session:
        assert {
            "answers": session.execute(
                text("select count(*) from learning.essay_answer_versions")
            ).scalar_one(),
            "rewrites": session.execute(
                text("select count(*) from learning.essay_rewrite_versions")
            ).scalar_one(),
            "mastery": session.execute(
                text("select count(*) from learning.essay_mastery_state_versions")
            ).scalar_one(),
            "executions": session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one(),
            "outbox": session.execute(
                text("select count(*) from platform.outbox_events")
            ).scalar_one(),
        } == rewrite_counts_before

    rewritten = client.post(
        "/api/v2/essay-intelligence/rewrites/complete-plan-task",
        headers={**auth, "Idempotency-Key": "essay-atomic-rewrite-complete"},
        json=atomic_rewrite_payload,
    )
    assert rewritten.status_code == 200, rewritten.text
    rewrite_result = rewritten.json()["data"]
    rewrite = rewrite_result["rewrite"]
    assert rewrite_result["completion"]["execution"]["status"] == "complete"
    assert rewrite_result["completion"]["execution"]["evidenceRefs"] == [
        {
            "owner": "essay_intelligence",
            "entityType": "essay_rewrite_version",
            "entityId": rewrite["rewriteVersionId"],
            "versionId": rewrite["rewriteVersionId"],
            "role": None,
        }
    ]
    rewrite_replay = client.post(
        "/api/v2/essay-intelligence/rewrites/complete-plan-task",
        headers={**auth, "Idempotency-Key": "essay-atomic-rewrite-complete"},
        json=atomic_rewrite_payload,
    )
    assert rewrite_replay.status_code == 200
    assert rewrite_replay.json()["data"] == rewrite_result
    rewrite_conflict = client.post(
        "/api/v2/essay-intelligence/rewrites/complete-plan-task",
        headers={**auth, "Idempotency-Key": "essay-atomic-rewrite-complete"},
        json={**atomic_rewrite_payload, "elapsedMinutes": 21},
    )
    assert rewrite_conflict.status_code == 409
    with db_session_factory() as session:
        assert repository.execution_evidence_exists(
            session,
            user_id=user_id,
            task=cast(
                Any,
                {
                    "stage": "essay_rewrite",
                    "evidence_refs": list(rewrite_candidate.evidence_refs),
                    "created_at": NOW,
                },
            ),
            evidence_type="essay_rewrite_version",
            owner="essay_intelligence",
            entity_id=rewrite["rewriteVersionId"],
            version_id=rewrite["rewriteVersionId"],
            submitted_evidence={},
        )

    with db_session_factory() as session:
        transfer_due = repository.essay_snapshot(
            session,
            user_id=user_id,
            goal=repository.goal_and_ability(session, user_id=user_id),
        )
    assert transfer_due.next_stage == "transfer_due"
    assert transfer_due.task_version_id == fresh_ids["task"]
    assert transfer_due.material_bundle_version_id == fresh_ids["material"]
    assert transfer_due.source_task_version_id == ids["task"]
    assert transfer_due.rewrite_version_id == rewrite["rewriteVersionId"]
    transfer_candidate = next(
        item
        for item in build_dual_track_candidates(
            goal=GoalPlanningSnapshot(
                readiness="unavailable",
                positive_gap=True,
                goal_version_id=None,
                ability_estimate_version_id=None,
            ),
            objective=ObjectivePlanningSnapshot(False, 0, 0, None, None, "available", None),
            essay=transfer_due,
            available_minutes=60,
        ).candidates
        if item.track == "essay"
    )
    assert "stage=essay_transfer" in transfer_candidate.start_url
    assert f"sourceRewriteVersionId={rewrite['rewriteVersionId']}" in (transfer_candidate.start_url)
    assert {item["entityType"] for item in transfer_candidate.evidence_refs} >= {
        "essay_mastery_state_version",
        "essay_source_task_version",
        "essay_source_answer_version",
        "essay_rewrite_version",
    }
    transfer_task = next(
        item
        for item in rewrite_result["completion"]["plan"]["tasks"]
        if item["stage"] == "essay_transfer"
    )

    transfer_answer_id, transfer_attempt_id = _seed_fresh_answer(
        db_session_factory,
        user_id=user_id,
        ids=fresh_ids,
    )
    transfer_review_id = _insert_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=fresh_ids,
        answer_id=transfer_answer_id,
    )
    transfer_decision_id = _post_decision(
        client,
        auth,
        key="essay-planning-transfer-decision",
        ids=fresh_ids,
        review_id=transfer_review_id,
        decision="rejected",
    )
    transfer_payload = {
        "sourceRewriteVersionId": rewrite["rewriteVersionId"],
        "sourceFindingDecisionArtifactVersionId": decision_id,
        "transferAttemptId": transfer_attempt_id,
        "transferAnswerVersionId": transfer_answer_id,
        "transferReviewVersionId": transfer_review_id,
        "transferFindingDecisionArtifactVersionId": transfer_decision_id,
        "basedOnTransferVerificationVersionId": None,
        "testedCriterionCodes": ["point_coverage"],
    }
    atomic_transfer_payload = {
        "transfer": transfer_payload,
        "planTaskId": transfer_task["id"],
        "elapsedMinutes": transfer_task["estimatedMinutes"],
        "basedOnExecutionVersionId": None,
    }
    with db_session_factory() as session:
        transfer_counts_before = {
            "transfers": session.execute(
                text("select count(*) from learning.essay_transfer_verification_versions")
            ).scalar_one(),
            "mastery": session.execute(
                text("select count(*) from learning.essay_mastery_state_versions")
            ).scalar_one(),
            "executions": session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one(),
            "outbox": session.execute(
                text("select count(*) from platform.outbox_events")
            ).scalar_one(),
        }
    rolled_back_transfer = client.post(
        "/api/v2/essay-intelligence/transfers/complete-plan-task",
        headers={**auth, "Idempotency-Key": "essay-atomic-transfer-rollback"},
        json={**atomic_transfer_payload, "planTaskId": str(uuid4())},
    )
    assert rolled_back_transfer.status_code == 409, rolled_back_transfer.text
    with db_session_factory() as session:
        assert {
            "transfers": session.execute(
                text("select count(*) from learning.essay_transfer_verification_versions")
            ).scalar_one(),
            "mastery": session.execute(
                text("select count(*) from learning.essay_mastery_state_versions")
            ).scalar_one(),
            "executions": session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one(),
            "outbox": session.execute(
                text("select count(*) from platform.outbox_events")
            ).scalar_one(),
        } == transfer_counts_before

    verified = client.post(
        "/api/v2/essay-intelligence/transfers/complete-plan-task",
        headers={**auth, "Idempotency-Key": "essay-atomic-transfer-complete"},
        json=atomic_transfer_payload,
    )
    assert verified.status_code == 200, verified.text
    transfer_result = verified.json()["data"]
    verification_id = transfer_result["transfer"]["verificationVersionId"]
    assert transfer_result["completion"]["execution"]["status"] == "complete"
    assert transfer_result["completion"]["execution"]["evidenceRefs"] == [
        {
            "owner": "essay_intelligence",
            "entityType": "essay_transfer_verification",
            "entityId": verification_id,
            "versionId": verification_id,
            "role": None,
        }
    ]
    transfer_replay = client.post(
        "/api/v2/essay-intelligence/transfers/complete-plan-task",
        headers={**auth, "Idempotency-Key": "essay-atomic-transfer-complete"},
        json=atomic_transfer_payload,
    )
    assert transfer_replay.status_code == 200
    assert transfer_replay.json()["data"] == transfer_result
    transfer_conflict = client.post(
        "/api/v2/essay-intelligence/transfers/complete-plan-task",
        headers={**auth, "Idempotency-Key": "essay-atomic-transfer-complete"},
        json={**atomic_transfer_payload, "elapsedMinutes": 1},
    )
    assert transfer_conflict.status_code == 409
    with db_session_factory() as session:
        assert repository.execution_evidence_exists(
            session,
            user_id=user_id,
            task=cast(
                Any,
                {
                    "stage": "essay_transfer",
                    "evidence_refs": list(transfer_candidate.evidence_refs),
                    "created_at": NOW,
                },
            ),
            evidence_type="essay_transfer_verification",
            owner="essay_intelligence",
            entity_id=verification_id,
            version_id=verification_id,
            submitted_evidence={},
        )
        stable = repository.essay_snapshot(
            session,
            user_id=user_id,
            goal=repository.goal_and_ability(session, user_id=user_id),
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learning.score_evidence "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learning.ability_estimate_versions "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )
    assert stable.next_stage == "content_unavailable"
    assert stable.mastery_state == "stable_mastered"
    assert "不重复生成复写或迁移训练" in str(stable.pending_reason)


def _seed_learner_and_published_essay_content(
    factory: sessionmaker[Session],
    *,
    access_token: str | None = None,
    include_rubric: bool = True,
    material_version_no: int = 1,
    include_objective_questions: bool = True,
    objective_canonical_module: str = "言语理解与表达",
    objective_raw_module: str = "言语理解",
) -> str:
    user_id = str(uuid4())
    task_version_id = str(uuid4())
    bundle_version_id = str(uuid4())
    segment_id = str(uuid4())
    rubric_version_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) values (:user_id, '双轨学习者')"
            ),
            {"user_id": user_id},
        )
        if include_objective_questions:
            _seed_shared_objective_question_bank(
                session,
                canonical_module=objective_canonical_module,
                raw_module=objective_raw_module,
            )
        if access_token:
            session.execute(
                text(
                    """
                    insert into learner.app_sessions (
                      session_id, user_id, session_token_hash, device_id, expires_at
                    ) values (
                      :session_id, :user_id, :token_hash, 'wechat-planning-test',
                      :expires_at
                    )
                    """
                ),
                {
                    "session_id": str(uuid4()),
                    "user_id": user_id,
                    "token_hash": token_hash_for_tests(access_token),
                    "expires_at": datetime.now(UTC) + timedelta(days=1),
                },
            )
        session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id, daily_minutes, study_mode, onboarding_complete,
                  profile_version
                ) values (
                  :user_id, 45, 'parttime', true, 1
                )
                """
            ),
            {"user_id": user_id},
        )
        if access_token:
            _seed_active_route_day_preference(session, user_id=user_id)
        session.execute(
            text(
                """
                insert into product.essay_task_versions (
                  task_version_id, task_key, version_no, question_type,
                  prompt, prompt_sha256, word_limit, maximum_score, state
                ) values (
                  :task_version_id, 'guokao-summary-baseline', 1, 'summary',
                  '根据给定资料概括主要问题。', :prompt_sha, 200, 20, 'published'
                )
                """
            ),
            {
                "task_version_id": task_version_id,
                "prompt_sha": sha256("根据给定资料概括主要问题。".encode("utf-8")).hexdigest(),
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_material_bundle_versions (
                  material_bundle_version_id, task_version_id, task_key,
                  version_no, completeness, source_kind, content_sha256
                ) values (
                  :bundle_version_id, :task_version_id,
                  'guokao-summary-baseline', :material_version_no,
                  'complete', 'own_bank', :content_sha
                )
                """
            ),
            {
                "bundle_version_id": bundle_version_id,
                "task_version_id": task_version_id,
                "material_version_no": material_version_no,
                "content_sha": "b" * 64,
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_material_segments (
                  material_segment_id, material_bundle_version_id, material_id,
                  segment_id, material_order, segment_order, text, text_sha256
                ) values (
                  :material_segment_id, :bundle_version_id, 'material-1',
                  'segment-1', 1, 1, '基层治理材料。', :text_sha
                )
                """
            ),
            {
                "material_segment_id": segment_id,
                "bundle_version_id": bundle_version_id,
                "text_sha": sha256("基层治理材料。".encode("utf-8")).hexdigest(),
            },
        )
        if include_rubric:
            session.execute(
                text(
                    """
                    insert into product.essay_rubric_versions (
                      rubric_version_id, task_version_id, task_key, version_no,
                      material_bundle_version_id, compilation_input_fingerprint,
                      source_kind, verification_status, state, total_maximum,
                      criteria, points, content_sha256, compilation_audit
                    ) values (
                      :rubric_version_id, :task_version_id,
                      'guokao-summary-baseline', 1, :bundle_version_id, :compile_fp,
                      'system_compiled', 'unverified',
                      'published', 20, cast(:criteria as jsonb), cast(:points as jsonb),
                      :rubric_sha, cast(:compilation_audit as jsonb)
                    )
                    """
                ),
                {
                    "rubric_version_id": rubric_version_id,
                    "task_version_id": task_version_id,
                    "bundle_version_id": bundle_version_id,
                    "compile_fp": "e" * 64,
                    "compilation_audit": json.dumps(
                        {
                            "contractVersion": "guokao-essay-rubric-v1",
                            "compilerVersion": "test-compiler-v1",
                            "promptVersion": "test-prompt-v1",
                            "provider": "test-provider",
                            "model": "test-model",
                            "inputFingerprint": "e" * 64,
                            "promptSha256": "a" * 64,
                            "materialContentSha256": "b" * 64,
                        },
                        ensure_ascii=False,
                    ),
                    "criteria": json.dumps(
                        [
                            {
                                "criterionCode": "point_coverage",
                                "label": "要点覆盖",
                                "maximum": 20,
                                "scoringRule": "按材料要点覆盖评分",
                                "materialEvidenceRequired": True,
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    "points": json.dumps(
                        [
                            {
                                "rubricPointId": "point-1",
                                "criterionCode": "point_coverage",
                                "category": "problem",
                                "label": "概括基层治理问题",
                                "maximumCredit": 20,
                                "materialSegmentIds": ["segment-1"],
                                "materialEvidenceRefs": [
                                    {
                                        "materialBundleVersionId": bundle_version_id,
                                        "materialId": "material-1",
                                        "segmentId": "segment-1",
                                        "startOffset": 0,
                                        "endOffset": 4,
                                        "quote": "基层治理",
                                        "quoteSha256": sha256(
                                            "基层治理".encode("utf-8")
                                        ).hexdigest(),
                                    }
                                ],
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    "rubric_sha": "d" * 64,
                },
            )
    return user_id


def _seed_shared_objective_question_bank(
    session: Session,
    *,
    count: int = 6,
    canonical_module: str = "言语理解与表达",
    raw_module: str = "言语理解",
) -> None:
    seed_planning_shared_frozen_release(
        session,
        count=count,
        canonical_module=canonical_module,
        raw_module=raw_module,
    )


def _seed_planning_learner(factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (:user_id, '隔离验收学习者')"
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id, daily_minutes, study_mode, onboarding_complete,
                  profile_version
                ) values (:user_id, 45, 'parttime', true, 1)
                """
            ),
            {"user_id": user_id},
        )
    return user_id


def _seed_active_route_day_preference(session: Session, *, user_id: str) -> None:
    route_local_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    route_rest_weekday = route_local_date.isoweekday() % 7 + 1
    route_mock_weekday = route_rest_weekday % 7 + 1
    session.execute(
        text(
            """
            insert into learning.weekly_study_preference_versions (
              preference_version_id, command_id, user_id, version_no,
              timezone, weekday_minutes, weekend_minutes, long_block_minutes,
              fragment_minutes, regular_rest_weekday, regular_mock_weekday,
              effective_from, source_type, confirmed, input_fingerprint,
              supersedes_preference_version_id, created_at
            ) values (
              cast(:preference_version_id as uuid), :command_id,
              cast(:user_id as uuid), 2, 'Asia/Shanghai', 45, 45, 45, 25,
              :rest_weekday, :mock_weekday, :effective_from,
              'user_confirmed', true, :input_fingerprint, null, :created_at
            )
            """
        ),
        {
            "preference_version_id": str(uuid4()),
            "command_id": f"test-active-route-day:{user_id}",
            "user_id": user_id,
            "rest_weekday": route_rest_weekday,
            "mock_weekday": route_mock_weekday,
            "effective_from": route_local_date,
            "input_fingerprint": sha256(f"test-active-route-day:{user_id}".encode()).hexdigest(),
            "created_at": datetime.now(UTC),
        },
    )


def _seed_today_as_regular_rest_day(
    factory: sessionmaker[Session],
    *,
    user_id: str,
) -> None:
    local_date = NOW.astimezone(ZoneInfo("Asia/Shanghai")).date()
    rest_weekday = local_date.isoweekday()
    mock_weekday = rest_weekday % 7 + 1
    fingerprint = sha256(f"test-rest-today:{user_id}".encode()).hexdigest()
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.weekly_study_preference_versions (
                  preference_version_id, command_id, user_id, version_no,
                  timezone, weekday_minutes, weekend_minutes, long_block_minutes,
                  fragment_minutes, regular_rest_weekday, regular_mock_weekday,
                  effective_from, source_type, confirmed, input_fingerprint,
                  supersedes_preference_version_id, created_at
                ) values (
                  cast(:preference_version_id as uuid), :command_id,
                  cast(:user_id as uuid), 1, 'Asia/Shanghai', 120, 120, 90, 25,
                  :rest_weekday, :mock_weekday, :effective_from,
                  'user_confirmed', true, :input_fingerprint, null, :created_at
                )
                """
            ),
            {
                "preference_version_id": str(uuid4()),
                "command_id": f"test-rest-today:{user_id}",
                "user_id": user_id,
                "rest_weekday": rest_weekday,
                "mock_weekday": mock_weekday,
                "effective_from": local_date,
                "input_fingerprint": fingerprint,
                "created_at": NOW,
            },
        )


def _published_essay_content_ids(
    factory: sessionmaker[Session],
) -> dict[str, str]:
    with factory() as session:
        row = (
            session.execute(
                text(
                    """
                select task.task_version_id::text as task,
                       material.material_bundle_version_id::text as material,
                       rubric.rubric_version_id::text as rubric
                  from product.essay_task_versions task
                  join product.essay_material_bundle_versions material
                    on material.task_version_id = task.task_version_id
                  join product.essay_rubric_versions rubric
                    on rubric.task_version_id = task.task_version_id
                   and rubric.material_bundle_version_id =
                       material.material_bundle_version_id
                   and rubric.version_no = 1
                 where task.task_key = 'guokao-summary-baseline'
                 order by material.version_no desc
                 limit 1
                """
                )
            )
            .mappings()
            .one()
        )
    return {key: str(value) for key, value in row.items()}


def _insert_foreign_rubric_successor(
    factory: sessionmaker[Session],
    *,
    ids: dict[str, str],
    foreign_lineage: str,
) -> None:
    if foreign_lineage not in {"other_material", "legacy_unbound"}:
        raise ValueError(f"Unsupported foreign rubric lineage: {foreign_lineage}")
    foreign_material_id = str(uuid4()) if foreign_lineage == "other_material" else None
    with factory.begin() as session:
        if foreign_material_id is not None:
            base_material_version = int(
                session.execute(
                    text(
                        "select version_no "
                        "from product.essay_material_bundle_versions "
                        "where material_bundle_version_id = "
                        "cast(:material_id as uuid)"
                    ),
                    {"material_id": ids["material"]},
                ).scalar_one()
            )
            foreign_material_version = (
                base_material_version - 1
                if base_material_version > 1
                else base_material_version + 1
            )
            foreign_material_completeness = (
                "complete" if base_material_version > 1 else "incomplete"
            )
            session.execute(
                text(
                    """
                    insert into product.essay_material_bundle_versions (
                      material_bundle_version_id, task_version_id, task_key,
                      version_no, completeness, source_kind, content_sha256
                    ) values (
                      cast(:material_id as uuid), cast(:task_id as uuid),
                      'guokao-summary-baseline', :version_no, :completeness,
                      'official',
                      repeat('7', 64)
                    )
                    """
                ),
                {
                    "material_id": foreign_material_id,
                    "task_id": ids["task"],
                    "version_no": foreign_material_version,
                    "completeness": foreign_material_completeness,
                },
            )
        session.execute(
            text(
                """
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  material_bundle_version_id, source_kind,
                  verification_status, state, total_maximum, criteria, points,
                  content_sha256, supersedes_rubric_version_id
                ) values (
                  cast(:rubric_id as uuid), cast(:task_id as uuid),
                  'guokao-summary-baseline', 2, cast(:material_id as uuid),
                  'official', 'verified', 'published', 20,
                  '[{}]'::jsonb, '[{}]'::jsonb, repeat('8', 64),
                  cast(:base_rubric_id as uuid)
                )
                """
            ),
            {
                "rubric_id": str(uuid4()),
                "task_id": ids["task"],
                "material_id": foreign_material_id,
                "base_rubric_id": ids["rubric"],
            },
        )


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    return TestClient(app)
