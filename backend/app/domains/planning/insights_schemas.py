from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domains.planning.schemas import (
    DayMode,
    PlanningContractModel,
    PlanningEvidenceRef,
    PlanTrack,
    PriorityDecomposition,
    Sha256,
)


class PriorityPolicy(PlanningContractModel):
    goal_gain_weight: float = Field(alias="goalGainWeight", ge=0, le=1)
    ability_gain_weight: float = Field(alias="abilityGainWeight", ge=0, le=1)
    verification_urgency_weight: float = Field(
        alias="verificationUrgencyWeight", ge=0, le=1
    )
    time_fit_weight: float = Field(alias="timeFitWeight", ge=0, le=1)
    load_penalty_weight: float = Field(alias="loadPenaltyWeight", ge=0, le=1)
    track_balance_max_bonus: float = Field(alias="trackBalanceMaxBonus", ge=0, le=1)
    algorithm_version: str = Field(alias="algorithmVersion", min_length=1)


class PlanBasisTask(PlanningContractModel):
    task_id: UUID = Field(alias="taskId")
    order: int = Field(ge=1)
    track: PlanTrack
    title: str
    estimated_minutes: int = Field(alias="estimatedMinutes", ge=1)
    priority_score: float = Field(alias="priorityScore", ge=0, le=1)
    priority_breakdown: PriorityDecomposition = Field(alias="priorityBreakdown")
    reason: str
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs", min_length=1)


class PlanBasisView(PlanningContractModel):
    contract_version: Literal["plan-basis@v1"] = Field(
        default="plan-basis@v1", alias="contractVersion"
    )
    plan_version_id: UUID = Field(alias="planVersionId")
    local_date: date = Field(alias="localDate")
    version_no: int = Field(alias="versionNo", ge=1)
    day_mode: DayMode = Field(alias="dayMode")
    headline: str
    rationale: str
    policy: PriorityPolicy
    tasks: list[PlanBasisTask]
    input_versions: dict[str, str] = Field(alias="inputVersions", min_length=1)
    invalidation_conditions: list[str] = Field(alias="invalidationConditions", min_length=1)
    replan_reason_codes: list[str] = Field(alias="replanReasonCodes")
    conflicts: list[str]
    generated_at: datetime = Field(alias="generatedAt")


class SettlementTask(PlanningContractModel):
    task_id: UUID = Field(alias="taskId")
    track: PlanTrack
    title: str
    status: Literal["ready", "in_progress", "complete", "abandoned"]
    planned_minutes: int = Field(alias="plannedMinutes", ge=1)
    elapsed_minutes: int = Field(alias="elapsedMinutes", ge=0)
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs")
    risk_code: str | None = Field(default=None, alias="riskCode")


class DailySettlementView(PlanningContractModel):
    contract_version: Literal["daily-settlement@v1"] = Field(
        default="daily-settlement@v1", alias="contractVersion"
    )
    plan_version_id: UUID = Field(alias="planVersionId")
    local_date: date = Field(alias="localDate")
    status: Literal["empty", "not_started", "in_progress", "partial", "complete"]
    scheduled_minutes: int = Field(alias="scheduledMinutes", ge=0)
    elapsed_minutes: int = Field(alias="elapsedMinutes", ge=0)
    completed_task_count: int = Field(alias="completedTaskCount", ge=0)
    abandoned_task_count: int = Field(alias="abandonedTaskCount", ge=0)
    open_task_count: int = Field(alias="openTaskCount", ge=0)
    carry_over_task_count: Literal[0] = Field(default=0, alias="carryOverTaskCount")
    tasks: list[SettlementTask]
    new_risks: list[str] = Field(alias="newRisks")
    summary: str
    generated_at: datetime = Field(alias="generatedAt")


class WeeklyPlanDay(PlanningContractModel):
    local_date: date = Field(alias="localDate")
    weekday: int = Field(ge=1, le=7)
    mode: Literal["training", "mock", "recovery"]
    planned_minutes: int = Field(alias="plannedMinutes", ge=0, le=1440)
    scheduled_minutes: int = Field(alias="scheduledMinutes", ge=0, le=1440)
    completed_task_count: int = Field(alias="completedTaskCount", ge=0)
    total_task_count: int = Field(alias="totalTaskCount", ge=0)
    status: Literal["past", "today", "upcoming"]
    explanation: str
    daily_plan_version_id: UUID | None = Field(default=None, alias="dailyPlanVersionId")


class WeeklyPlanVersion(PlanningContractModel):
    contract_version: Literal["weekly-plan@v1"] = Field(
        default="weekly-plan@v1", alias="contractVersion"
    )
    weekly_plan_version_id: UUID = Field(alias="weeklyPlanVersionId")
    week_start: date = Field(alias="weekStart")
    version_no: int = Field(alias="versionNo", ge=1)
    weekly_preference_version_id: UUID = Field(alias="weeklyPreferenceVersionId")
    rhythm: Literal["5+1+1"]
    days: list[WeeklyPlanDay] = Field(min_length=7, max_length=7)
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs", min_length=1)
    input_versions: dict[str, str] = Field(alias="inputVersions", min_length=1)
    replan_reason_codes: list[str] = Field(alias="replanReasonCodes")
    algorithm_version: Literal["weekly-rhythm@v1"] = Field(
        default="weekly-rhythm@v1", alias="algorithmVersion"
    )
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    supersedes_weekly_plan_version_id: UUID | None = Field(
        default=None, alias="supersedesWeeklyPlanVersionId"
    )
    generated_at: datetime = Field(alias="generatedAt")
    valid_until: datetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def validate_days(self) -> "WeeklyPlanVersion":
        expected = [date.fromordinal(self.week_start.toordinal() + index) for index in range(7)]
        if [item.local_date for item in self.days] != expected:
            raise ValueError("weekly plan days must be contiguous from weekStart")
        modes = [item.mode for item in self.days]
        if modes.count("mock") != 1 or modes.count("recovery") != 1:
            raise ValueError("5+1+1 requires one mock day and one recovery day")
        return self


class PlanChangeEntry(PlanningContractModel):
    plan_version_id: UUID = Field(alias="planVersionId")
    local_date: date = Field(alias="localDate")
    version_no: int = Field(alias="versionNo", ge=1)
    headline: str
    day_mode: DayMode = Field(alias="dayMode")
    scheduled_minutes: int = Field(alias="scheduledMinutes", ge=0)
    task_count: int = Field(alias="taskCount", ge=0)
    replan_reason_codes: list[str] = Field(alias="replanReasonCodes")
    changed_input_keys: list[str] = Field(alias="changedInputKeys")
    supersedes_plan_version_id: UUID | None = Field(
        default=None, alias="supersedesPlanVersionId"
    )
    generated_at: datetime = Field(alias="generatedAt")


class PlanChangeHistory(PlanningContractModel):
    items: list[PlanChangeEntry]
    next_cursor: str | None = Field(default=None, alias="nextCursor")
