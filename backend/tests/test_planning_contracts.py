from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.planning.schemas import (
    DailyPlanTask,
    DailyPlanVersion,
    GoalGap,
    TodayDashboard,
    WeeklyStudyPreferenceVersion,
)


NOW = datetime(2026, 7, 13, 8, tzinfo=UTC)
PLAN_ID = UUID("10000000-0000-0000-0000-000000000001")
CAPACITY_ID = UUID("20000000-0000-0000-0000-000000000001")
PREFERENCE_ID = UUID("30000000-0000-0000-0000-000000000001")
XINGCE_TASK_ID = UUID("40000000-0000-0000-0000-000000000001")
ESSAY_TASK_ID = UUID("40000000-0000-0000-0000-000000000002")
SHA = "a" * 64


def test_system_default_weekly_preference_is_explicitly_unconfirmed() -> None:
    preference = WeeklyStudyPreferenceVersion.model_validate(
        _weekly_preference(source_type="system_default", confirmed=False)
    )

    assert preference.source_type == "system_default"
    assert preference.confirmed is False

    invalid = _weekly_preference(source_type="system_default", confirmed=True)
    with pytest.raises(ValidationError, match="only user-provided"):
        WeeklyStudyPreferenceVersion.model_validate(invalid)


def test_user_weekly_preference_requires_distinct_rest_and_mock_days() -> None:
    payload = _weekly_preference(source_type="user_confirmed", confirmed=True)
    payload["regularMockWeekday"] = payload["regularRestWeekday"]

    with pytest.raises(ValidationError, match="rest day and mock day"):
        WeeklyStudyPreferenceVersion.model_validate(payload)


def test_goal_gap_only_exposes_numeric_gap_when_total_score_is_complete() -> None:
    complete = GoalGap.model_validate(_goal_gap())
    assert complete.readiness == "complete"
    assert complete.baseline_gap == 11

    partial = _goal_gap()
    partial.update(
        {
            "readiness": "partial",
            "currentScore": None,
            "baselineGap": None,
        }
    )
    assert GoalGap.model_validate(partial).baseline_gap is None

    partial["baselineGap"] = 11
    with pytest.raises(ValidationError, match="cannot expose baselineGap"):
        GoalGap.model_validate(partial)


def test_essay_task_stage_cannot_be_attached_to_xingce_track() -> None:
    payload = _plan_task(
        task_id=XINGCE_TASK_ID,
        order=1,
        track="xingce",
        stage="essay_point_extraction",
        kind="practice",
        minutes=20,
    )

    with pytest.raises(ValidationError, match="stage does not belong"):
        DailyPlanTask.model_validate(payload)


@pytest.mark.parametrize(
    "stage",
    [
        "essay_material_reading",
        "essay_point_extraction",
        "essay_outlining",
        "essay_drafting",
        "essay_review",
        "essay_rewrite",
        "essay_transfer",
    ],
)
def test_all_essay_learning_stages_are_first_class_plan_tasks(stage: str) -> None:
    payload = _plan_task(
        task_id=ESSAY_TASK_ID,
        order=1,
        track="essay",
        stage=stage,
        kind="essay",
        minutes=20,
    )

    assert DailyPlanTask.model_validate(payload).stage == stage


def test_objective_baseline_is_a_first_class_xingce_stage() -> None:
    payload = _plan_task(
        task_id=XINGCE_TASK_ID,
        order=1,
        track="xingce",
        stage="objective_baseline",
        kind="practice",
        minutes=20,
    )

    assert DailyPlanTask.model_validate(payload).stage == "objective_baseline"


def test_daily_plan_requires_same_date_version_and_auditable_minute_totals() -> None:
    payload = _daily_plan()
    plan = DailyPlanVersion.model_validate(payload)

    assert [task.track for task in plan.tasks] == ["xingce", "essay"]
    assert plan.dual_track_explanation.essay_minutes == 25
    assert plan.tasks[1].stage == "essay_point_extraction"
    assert plan.tasks[1].completion.success_criteria == ["提取 5 个材料要点"]

    mismatched = deepcopy(payload)
    mismatched["tasks"][1]["planVersionNo"] = 2
    with pytest.raises(ValidationError, match="same plan, owner date, and version"):
        DailyPlanVersion.model_validate(mismatched)

    wrong_total = deepcopy(payload)
    wrong_total["scheduledMinutes"] = 44
    with pytest.raises(ValidationError, match="sum of plan tasks"):
        DailyPlanVersion.model_validate(wrong_total)


def test_today_dashboard_preserves_existing_fields_and_adds_dual_track_basis() -> None:
    dashboard = TodayDashboard.model_validate(_today_dashboard())
    body = dashboard.model_dump(by_alias=True, mode="json")

    # Existing mini-program fields remain available.
    assert body["generatedAt"]
    assert body["planVersion"] == "planning-v2:1"
    assert body["headline"] == "行测校准与申论采点并行"
    assert body["rationale"]
    assert body["goalGap"]["baselineGap"] == 11
    assert body["capacity"]["availableMinutes"] == 45
    assert body["tasks"][1]["completionRule"] == "提取 5 个材料要点"
    assert body["dueReflectionCount"] == 1
    assert body["dueVerificationCount"] == 2
    assert body["isRecoveryDay"] is False

    # V2 explains both tracks with evidence instead of presenting essay as a generic task.
    assert body["planBasis"] == "evidence_priority_v2"
    assert body["dualTrackExplanation"]["xingceMinutes"] == 20
    assert body["dualTrackExplanation"]["essayMinutes"] == 25
    assert body["tasks"][1]["stage"] == "essay_point_extraction"
    assert body["tasks"][1]["evidenceRefs"][0]["versionId"] == "essay-review-v4"
    assert body["tasks"][1]["priorityBreakdown"]["verificationUrgency"] == 0.8


def _weekly_preference(*, source_type: str, confirmed: bool) -> dict[str, object]:
    return {
        "preferenceVersionId": PREFERENCE_ID,
        "versionNo": 1,
        "timezone": "Asia/Shanghai",
        "weekdayMinutes": 45,
        "weekendMinutes": 150,
        "longBlockMinutes": 120,
        "fragmentMinutes": 15,
        "regularRestWeekday": 7,
        "regularMockWeekday": 6,
        "effectiveFrom": date(2026, 7, 13),
        "sourceType": source_type,
        "confirmed": confirmed,
        "inputFingerprint": SHA,
        "supersedesPreferenceVersionId": None,
        "createdAt": NOW,
    }


def _goal_gap() -> dict[str, object]:
    return {
        "readiness": "complete",
        "goalId": "goal-1",
        "positionName": "中央机关综合管理岗",
        "departmentName": "目标单位",
        "targetScore": {"lower": 132.0, "upper": 136.0, "confidence": "medium"},
        "currentScore": {"lower": 121.0, "upper": 125.0, "confidence": "medium"},
        "baselineGap": 11.0,
        "evidenceSummary": "同版本行测与申论总分估计",
    }


def _evidence(*, entity_id: str, version_id: str) -> dict[str, object]:
    return {
        "owner": "learning",
        "entityType": "ability_evidence",
        "entityId": entity_id,
        "versionId": version_id,
        "role": "planning_input",
    }


def _priority(*, urgency: float) -> dict[str, float]:
    return {
        "goalGain": 0.7,
        "abilityGain": 0.8,
        "verificationUrgency": urgency,
        "timeFit": 0.9,
        "loadPenalty": 0.1,
        "trackBalanceBonus": 0.05,
    }


def _completion(rule: str, *, reflection: bool) -> dict[str, object]:
    return {
        "objective": rule,
        "successCriteria": [rule],
        "terminalConditions": ["达到成功标准或用完计划时长"],
        "requiresReflection": reflection,
        "completionEvidenceTypes": ["task_execution"],
    }


def _plan_task(
    *,
    task_id: UUID,
    order: int,
    track: str,
    stage: str,
    kind: str,
    minutes: int,
) -> dict[str, object]:
    essay = track == "essay"
    rule = "提取 5 个材料要点" if essay else "完成 10 道校准题"
    return {
        "taskId": task_id,
        "planVersionId": PLAN_ID,
        "localDate": date(2026, 7, 13),
        "planVersionNo": 1,
        "taskOrder": order,
        "track": track,
        "stage": stage,
        "kind": kind,
        "title": "申论材料采点" if essay else "行测资料分析校准",
        "reason": "最近一次评阅显示采点覆盖不足" if essay else "资料分析分差收益最高",
        "estimatedMinutes": minutes,
        "mandatory": False,
        "startUrl": "/pages/essay/task" if essay else "/pages/practice/task",
        "priorityScore": 0.78,
        "priorityBreakdown": _priority(urgency=0.8 if essay else 0.6),
        "evidenceRefs": [
            _evidence(
                entity_id="essay-review-4" if essay else "ability-estimate-7",
                version_id="essay-review-v4" if essay else "ability-v7",
            )
        ],
        "completion": _completion(rule, reflection=essay),
        "inputFingerprint": ("b" if essay else "c") * 64,
        "createdAt": NOW,
    }


def _track_explanation() -> dict[str, object]:
    return {
        "mode": "balanced",
        "summary": "45 分钟同时保留行测校准与申论采点，避免申论长期缺席。",
        "xingceMinutes": 20,
        "essayMinutes": 25,
        "recoveryMinutes": 0,
        "mockMinutes": 0,
        "dualTrackConstraintApplied": True,
        "allocations": [
            {
                "track": "xingce",
                "minutes": 20,
                "reason": "资料分析分差收益最高",
                "stages": ["objective_practice"],
                "evidenceRefs": [_evidence(entity_id="ability-estimate-7", version_id="ability-v7")],
            },
            {
                "track": "essay",
                "minutes": 25,
                "reason": "评阅证据显示采点覆盖不足",
                "stages": ["essay_point_extraction"],
                "evidenceRefs": [_evidence(entity_id="essay-review-4", version_id="essay-review-v4")],
            },
        ],
        "omittedTrackReasons": {
            "recovery": "今天不是恢复日",
            "mock": "今天不是模考日",
        },
    }


def _daily_plan() -> dict[str, object]:
    return {
        "planVersionId": PLAN_ID,
        "localDate": date(2026, 7, 13),
        "versionNo": 1,
        "capacityVersionId": CAPACITY_ID,
        "weeklyPreferenceVersionId": PREFERENCE_ID,
        "dayMode": "normal",
        "headline": "行测校准与申论采点并行",
        "rationale": "岗位分差需要双科总分，申论评阅证据已到下一次训练窗口。",
        "goalGap": _goal_gap(),
        "scheduledMinutes": 45,
        "dueReflectionCount": 1,
        "dueVerificationCount": 2,
        "dualTrackExplanation": _track_explanation(),
        "inputVersions": {
            "abilityEstimate": "ability-v7",
            "essayReview": "essay-review-v4",
            "dailyCapacity": str(CAPACITY_ID),
        },
        "evidenceRefs": [
            _evidence(entity_id="ability-estimate-7", version_id="ability-v7"),
            _evidence(entity_id="essay-review-4", version_id="essay-review-v4"),
        ],
        "invalidationConditions": ["daily_capacity_changed", "ability_estimate_changed"],
        "replanReasonCodes": [],
        "planBasis": "evidence_priority_v2",
        "contractVersion": "planning-v2",
        "algorithmVersion": "dual-track-priority-v1",
        "inputFingerprint": SHA,
        "supersedesPlanVersionId": None,
        "generatedAt": NOW,
        "validUntil": NOW + timedelta(hours=16),
        "tasks": [
            _plan_task(
                task_id=XINGCE_TASK_ID,
                order=1,
                track="xingce",
                stage="objective_practice",
                kind="practice",
                minutes=20,
            ),
            _plan_task(
                task_id=ESSAY_TASK_ID,
                order=2,
                track="essay",
                stage="essay_point_extraction",
                kind="essay",
                minutes=25,
            ),
        ],
    }


def _today_dashboard() -> dict[str, object]:
    plan = _daily_plan()
    return {
        "generatedAt": plan["generatedAt"],
        "planVersion": "planning-v2:1",
        "planVersionId": plan["planVersionId"],
        "localDate": plan["localDate"],
        "versionNo": plan["versionNo"],
        "planBasis": plan["planBasis"],
        "dayMode": plan["dayMode"],
        "headline": plan["headline"],
        "rationale": plan["rationale"],
        "goalGap": plan["goalGap"],
        "capacity": {
            "capacityVersionId": CAPACITY_ID,
            "localDate": plan["localDate"],
            "versionNo": 1,
            "availableMinutes": 45,
            "energy": 3,
            "stress": 2,
            "confidenceLevel": 3,
            "confirmed": True,
            "sourceType": "user_override",
        },
        "tasks": [
            {
                "id": task["taskId"],
                "kind": task["kind"],
                "title": task["title"],
                "reason": task["reason"],
                "estimatedMinutes": task["estimatedMinutes"],
                "completionRule": task["completion"]["successCriteria"][0],
                "status": "ready",
                "priority": task["taskOrder"],
                "track": task["track"],
                "stage": task["stage"],
                "mandatory": task["mandatory"],
                "startUrl": task["startUrl"],
                "priorityScore": task["priorityScore"],
                "priorityBreakdown": task["priorityBreakdown"],
                "evidenceRefs": task["evidenceRefs"],
                "completion": task["completion"],
            }
            for task in plan["tasks"]
        ],
        "dueReflectionCount": plan["dueReflectionCount"],
        "dueVerificationCount": plan["dueVerificationCount"],
        "isRecoveryDay": False,
        "dualTrackExplanation": plan["dualTrackExplanation"],
        "inputVersions": plan["inputVersions"],
        "evidenceRefs": plan["evidenceRefs"],
        "invalidationConditions": plan["invalidationConditions"],
        "replanReasonCodes": plan["replanReasonCodes"],
    }
