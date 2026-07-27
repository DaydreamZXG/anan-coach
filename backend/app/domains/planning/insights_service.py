from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.engine import (
    ABILITY_GAIN_WEIGHT,
    GOAL_GAIN_WEIGHT,
    LOAD_PENALTY_WEIGHT,
    TIME_FIT_WEIGHT,
    TRACK_BALANCE_MAX_BONUS,
    VERIFICATION_URGENCY_WEIGHT,
)
from app.domains.planning.insights_repository import (
    PlanningInsightsRepository,
    json_value,
)
from app.domains.planning.insights_schemas import (
    DailySettlementView,
    PlanBasisTask,
    PlanBasisView,
    PlanChangeEntry,
    PlanChangeHistory,
    PriorityPolicy,
    SettlementTask,
    WeeklyPlanDay,
    WeeklyPlanVersion,
)
from app.domains.planning.repository import PlanningRepository
from app.domains.planning.schemas import PlanningEvidenceRef
from app.domains.planning.service import PLAN_ALGORITHM_VERSION, PlanningService
from app.platform.outbox import OutboxRepository


WEEKLY_ALGORITHM_VERSION = "weekly-rhythm@v1"


class PlanningInsightsService:
    def __init__(
        self,
        *,
        planning: PlanningService | None = None,
        planning_repository: PlanningRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._planning = planning or PlanningService()
        self._planning_repository = planning_repository or PlanningRepository()
        self._outbox = outbox or OutboxRepository()

    def plan_basis(self, session: Session, *, user_id: str) -> PlanBasisView:
        dashboard = self._planning.get_or_create_today(session, user_id=user_id)
        conflicts = [
            value
            for key, value in dashboard.dual_track_explanation.capability_gaps.items()
            if key and value
        ]
        return PlanBasisView(
            planVersionId=dashboard.plan_version_id,
            localDate=dashboard.local_date,
            versionNo=dashboard.version_no,
            dayMode=dashboard.day_mode,
            headline=dashboard.headline,
            rationale=dashboard.rationale,
            policy=PriorityPolicy(
                goalGainWeight=GOAL_GAIN_WEIGHT,
                abilityGainWeight=ABILITY_GAIN_WEIGHT,
                verificationUrgencyWeight=VERIFICATION_URGENCY_WEIGHT,
                timeFitWeight=TIME_FIT_WEIGHT,
                loadPenaltyWeight=LOAD_PENALTY_WEIGHT,
                trackBalanceMaxBonus=TRACK_BALANCE_MAX_BONUS,
                algorithmVersion=PLAN_ALGORITHM_VERSION,
            ),
            tasks=[
                PlanBasisTask(
                    taskId=task.id,
                    order=index,
                    track=task.track,
                    title=task.title,
                    estimatedMinutes=task.estimated_minutes,
                    priorityScore=task.priority_score,
                    priorityBreakdown=task.priority_breakdown,
                    reason=task.reason,
                    evidenceRefs=task.evidence_refs,
                )
                for index, task in enumerate(dashboard.tasks, start=1)
            ],
            inputVersions=dashboard.input_versions,
            invalidationConditions=dashboard.invalidation_conditions,
            replanReasonCodes=dashboard.replan_reason_codes,
            conflicts=conflicts,
            generatedAt=dashboard.generated_at,
        )

    def daily_settlement(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> DailySettlementView:
        dashboard = self._planning.get_or_create_today(session, user_id=user_id)
        task_facts = PlanningInsightsRepository(session).daily_settlement_task_facts(
            user_id=user_id,
            local_date=dashboard.local_date,
            current_plan_version_id=str(dashboard.plan_version_id),
        )
        tasks = [_settlement_task_from_fact(fact) for fact in task_facts]
        complete = sum(item.status == "complete" for item in tasks)
        abandoned = sum(item.status == "abandoned" for item in tasks)
        open_count = len(tasks) - complete - abandoned
        elapsed = sum(item.elapsed_minutes for item in tasks)
        if not tasks:
            status = "empty"
        elif complete == len(tasks):
            status = "complete"
        elif complete or abandoned:
            status = "partial"
        elif any(item.status == "in_progress" for item in tasks):
            status = "in_progress"
        else:
            status = "not_started"
        risks: list[str] = []
        if abandoned:
            risks.append("存在主动结束任务；原因只作为下一版计划输入，不形成任务债。")
        if dashboard.dual_track_explanation.capability_gaps:
            risks.extend(dashboard.dual_track_explanation.capability_gaps.values())
        if status == "complete":
            summary = "今天的学习与复盘已经完成；明天会按新的学习记录重新安排。"
        elif status in {"partial", "in_progress"}:
            summary = "今天只记录已经完成的内容；未完成部分不会机械顺延，明天会重新安排。"
        elif status == "empty":
            summary = "今天还没有可执行任务，请先补齐目标岗位和学习时间。"
        else:
            summary = "今天的任务还没有开始，完成后这里会汇总真实投入和学习结果。"
        return DailySettlementView(
            planVersionId=dashboard.plan_version_id,
            localDate=dashboard.local_date,
            status=status,
            scheduledMinutes=sum(item.planned_minutes for item in tasks),
            elapsedMinutes=elapsed,
            completedTaskCount=complete,
            abandonedTaskCount=abandoned,
            openTaskCount=open_count,
            carryOverTaskCount=0,
            tasks=tasks,
            newRisks=list(dict.fromkeys(risks)),
            summary=summary,
            generatedAt=datetime.now(UTC),
        )

    def weekly_plan(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> WeeklyPlanVersion:
        generated_at = _as_utc(now or datetime.now(UTC))
        self._planning_repository.lock_user(session, user_id=user_id)
        dashboard = self._planning.get_or_create_today(session, user_id=user_id, now=generated_at)
        repository = PlanningInsightsRepository(session)
        preference_id = dashboard.input_versions["weeklyPreference"]
        preference = repository.preference_by_id(
            user_id=user_id,
            preference_version_id=preference_id,
        )
        timezone = ZoneInfo(str(preference["timezone"]))
        local_today = generated_at.astimezone(timezone).date()
        week_start = local_today - timedelta(days=local_today.isoweekday() - 1)
        summaries = {
            row["local_date"]: row
            for row in repository.daily_plan_summaries(
                user_id=user_id,
                week_start=week_start,
            )
        }
        strategy_versions = repository.active_strategy_versions(user_id=user_id)
        days = [
            _weekly_day(
                week_start + timedelta(days=index),
                local_today=local_today,
                preference=preference,
                summary=summaries.get(week_start + timedelta(days=index)),
            )
            for index in range(7)
        ]
        input_versions: dict[str, str] = {
            "weeklyPreference": preference_id,
            "strategyDecisions": _fingerprint(strategy_versions),
        }
        for item_date, summary in sorted(summaries.items()):
            input_versions[f"dailyPlan:{item_date.isoformat()}"] = str(
                summary["plan_version_id"]
            )
            input_versions[f"execution:{item_date.isoformat()}"] = str(
                summary["execution_fingerprint"]
            )
        fingerprint = _fingerprint(
            {
                "weekStart": week_start.isoformat(),
                "preferenceVersionId": preference_id,
                "days": [item.model_dump(by_alias=True, mode="json") for item in days],
                "inputVersions": input_versions,
                "algorithmVersion": WEEKLY_ALGORITHM_VERSION,
            }
        )
        current = repository.current_weekly_plan(user_id=user_id, week_start=week_start)
        if current is not None and str(current["input_fingerprint"]) == fingerprint:
            return _weekly_from_row(current)
        reason_codes = _weekly_replan_reasons(current, input_versions)
        weekly_plan_version_id = str(uuid4())
        evidence_refs = [
            PlanningEvidenceRef(
                owner="planning",
                entityType="weekly_study_preference",
                entityId=preference_id,
                versionId=preference_id,
                role="5+1+1_rhythm",
            )
        ]
        evidence_refs.extend(
            PlanningEvidenceRef(
                owner="planning",
                entityType="daily_plan_version",
                entityId=str(summary["plan_version_id"]),
                versionId=str(summary["plan_version_id"]),
                role=f"day:{item_date.isoformat()}",
            )
            for item_date, summary in sorted(summaries.items())
        )
        next_week = week_start + timedelta(days=7)
        valid_until = datetime.combine(next_week, time.min, tzinfo=timezone).astimezone(UTC)
        row = repository.insert_weekly_plan(
            {
                "weekly_plan_version_id": weekly_plan_version_id,
                "command_id": f"weekly-plan:{weekly_plan_version_id}",
                "user_id": user_id,
                "week_start": week_start,
                "version_no": int(current["version_no"]) + 1 if current is not None else 1,
                "weekly_preference_version_id": preference_id,
                "days": json_value(
                    [item.model_dump(by_alias=True, mode="json") for item in days]
                ),
                "evidence_refs": json_value(
                    [item.model_dump(by_alias=True, mode="json") for item in evidence_refs]
                ),
                "input_versions": json_value(input_versions),
                "replan_reason_codes": json_value(reason_codes),
                "input_fingerprint": fingerprint,
                "supersedes_weekly_plan_version_id": (
                    str(current["weekly_plan_version_id"]) if current is not None else None
                ),
                "generated_at": generated_at,
                "valid_until": valid_until,
            }
        )
        self._outbox.append(
            session,
            deduplication_key=f"weekly-plan:{weekly_plan_version_id}",
            aggregate_type="weekly_plan",
            aggregate_id=user_id,
            event_type="weekly_plan.changed",
            payload={
                "weeklyPlanVersionId": weekly_plan_version_id,
                "userId": user_id,
                "weekStart": week_start.isoformat(),
                "inputFingerprint": fingerprint,
                "replanReasonCodes": reason_codes,
            },
        )
        return _weekly_from_row(row)

    def plan_history(self, session: Session, *, user_id: str) -> PlanChangeHistory:
        rows = PlanningInsightsRepository(session).plan_history(user_id=user_id)
        by_id = {str(row["plan_version_id"]): row for row in rows}
        items: list[PlanChangeEntry] = []
        for row in rows:
            previous = (
                by_id.get(str(row["supersedes_plan_version_id"]))
                if row["supersedes_plan_version_id"] is not None
                else None
            )
            current_inputs = dict(row["input_versions"])
            previous_inputs = dict(previous["input_versions"]) if previous is not None else {}
            changed_keys = sorted(
                key
                for key in set(current_inputs) | set(previous_inputs)
                if current_inputs.get(key) != previous_inputs.get(key)
            )
            items.append(
                PlanChangeEntry(
                    planVersionId=UUID(str(row["plan_version_id"])),
                    localDate=row["local_date"],
                    versionNo=int(row["version_no"]),
                    headline=str(row["headline"]),
                    dayMode=str(row["day_mode"]),
                    scheduledMinutes=int(row["scheduled_minutes"]),
                    taskCount=int(row["task_count"]),
                    replanReasonCodes=list(row["replan_reason_codes"]),
                    changedInputKeys=changed_keys,
                    supersedesPlanVersionId=(
                        UUID(str(row["supersedes_plan_version_id"]))
                        if row["supersedes_plan_version_id"] is not None
                        else None
                    ),
                    generatedAt=row["generated_at"],
                )
            )
        return PlanChangeHistory(items=items, nextCursor=None)


def _settlement_task_from_fact(fact: RowMapping) -> SettlementTask:
    status = str(fact["status"])
    risk_code = None
    if status == "abandoned":
        risk_code = "task_abandoned"
    elif status in {"ready", "in_progress"}:
        risk_code = "task_open"
    return SettlementTask(
        taskId=UUID(str(fact["task_id"])),
        track=str(fact["track"]),
        title=str(fact["title"]),
        status=status,
        plannedMinutes=int(fact["estimated_minutes"]),
        elapsedMinutes=int(fact["elapsed_minutes"]),
        evidenceRefs=[PlanningEvidenceRef.model_validate(item) for item in fact["evidence_refs"]],
        riskCode=risk_code,
    )


def _weekly_day(
    local_date: date,
    *,
    local_today: date,
    preference: RowMapping,
    summary: RowMapping | None,
) -> WeeklyPlanDay:
    weekday = local_date.isoweekday()
    if weekday == int(preference["regular_rest_weekday"]):
        mode = "recovery"
    elif weekday == int(preference["regular_mock_weekday"]):
        mode = "mock"
    else:
        mode = "training"
    base_minutes = int(
        preference["weekend_minutes"] if weekday >= 6 else preference["weekday_minutes"]
    )
    planned_minutes = min(base_minutes, 20) if mode == "recovery" else base_minutes
    if summary is not None:
        scheduled = int(summary["scheduled_minutes"])
        task_count = int(summary["task_count"])
        completed = int(summary["completed_count"])
        plan_version_id = UUID(str(summary["plan_version_id"]))
    else:
        scheduled = 0
        task_count = 0
        completed = 0
        plan_version_id = None
    status = "today" if local_date == local_today else "past" if local_date < local_today else "upcoming"
    explanation = {
        "training": "针对训练日；具体任务只在当天依据最新证据生成。",
        "mock": "整卷校准日；如已完成外部整卷，不重复强制自有整卷。",
        "recovery": "恢复日；不排新知识，只允许轻量到期闭环。",
    }[mode]
    return WeeklyPlanDay(
        localDate=local_date,
        weekday=weekday,
        mode=mode,
        plannedMinutes=planned_minutes,
        scheduledMinutes=scheduled,
        completedTaskCount=completed,
        totalTaskCount=task_count,
        status=status,
        explanation=explanation,
        dailyPlanVersionId=plan_version_id,
    )


def _weekly_replan_reasons(
    current: RowMapping | None,
    input_versions: dict[str, str],
) -> list[str]:
    if current is None:
        return ["weekly_rhythm_initialized"]
    previous = dict(current["input_versions"])
    reasons: list[str] = []
    if previous.get("weeklyPreference") != input_versions.get("weeklyPreference"):
        reasons.append("weekly_preference_changed")
    if previous.get("strategyDecisions") != input_versions.get("strategyDecisions"):
        reasons.append("strategy_decision_changed")
    if any(
        key.startswith("dailyPlan:") and previous.get(key) != value
        for key, value in input_versions.items()
    ):
        reasons.append("daily_plan_changed")
    if any(
        key.startswith("execution:") and previous.get(key) != value
        for key, value in input_versions.items()
    ):
        reasons.append("task_execution_changed")
    return reasons or ["weekly_inputs_reconciled"]


def _weekly_from_row(row: RowMapping) -> WeeklyPlanVersion:
    return WeeklyPlanVersion(
        weeklyPlanVersionId=UUID(str(row["weekly_plan_version_id"])),
        weekStart=row["week_start"],
        versionNo=int(row["version_no"]),
        weeklyPreferenceVersionId=UUID(str(row["weekly_preference_version_id"])),
        rhythm=str(row["rhythm"]),
        days=[_weekly_day_from_jsonb(item) for item in row["days"]],
        evidenceRefs=[PlanningEvidenceRef.model_validate(item) for item in row["evidence_refs"]],
        inputVersions=dict(row["input_versions"]),
        replanReasonCodes=list(row["replan_reason_codes"]),
        inputFingerprint=str(row["input_fingerprint"]),
        supersedesWeeklyPlanVersionId=(
            UUID(str(row["supersedes_weekly_plan_version_id"]))
            if row["supersedes_weekly_plan_version_id"] is not None
            else None
        ),
        generatedAt=row["generated_at"],
        validUntil=row["valid_until"],
    )


def _weekly_day_from_jsonb(item: object) -> WeeklyPlanDay:
    return WeeklyPlanDay.model_validate_json(
        json.dumps(item, ensure_ascii=False, default=str)
    )


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("planning insight timestamp must include a timezone")
    return value.astimezone(UTC)
