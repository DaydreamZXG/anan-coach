from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
UuidText = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]
PlanTrack = Literal["xingce", "essay", "recovery", "mock"]
XingceStage = Literal[
    "objective_baseline",
    "objective_practice",
    "objective_reflection",
    "objective_verification",
]
EssayStage = Literal[
    "essay_material_reading",
    "essay_point_extraction",
    "essay_outlining",
    "essay_drafting",
    "essay_review",
    "essay_rewrite",
    "essay_transfer",
]
RecoveryStage = Literal["recovery_review"]
MockStage = Literal["weekly_mock"]
PlanStage = XingceStage | EssayStage | RecoveryStage | MockStage
PlanTaskKind = Literal["practice", "reflection", "verification", "mock_exam", "essay"]
PlanTaskStatus = Literal["ready", "in_progress", "complete", "abandoned"]
TaskExecutionStatus = Literal["in_progress", "complete", "abandoned"]
DayMode = Literal["normal", "mock", "recovery", "unavailable"]
EvidenceConfidence = Literal["unknown", "low", "medium", "high"]


_TRACK_STAGES: dict[str, set[str]] = {
    "xingce": {
        "objective_baseline",
        "objective_practice",
        "objective_reflection",
        "objective_verification",
    },
    "essay": {
        "essay_material_reading",
        "essay_point_extraction",
        "essay_outlining",
        "essay_drafting",
        "essay_review",
        "essay_rewrite",
        "essay_transfer",
    },
    "recovery": {"recovery_review"},
    "mock": {"weekly_mock"},
}

_TRACK_KINDS: dict[str, set[str]] = {
    "xingce": {"practice", "reflection", "verification"},
    "essay": {"essay"},
    "recovery": {"reflection", "verification"},
    "mock": {"mock_exam"},
}


class PlanningContractModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        strict=True,
    )


class PlanningEvidenceRef(PlanningContractModel):
    owner: str = Field(min_length=1, max_length=80)
    entity_type: str = Field(default="evidence", alias="entityType", min_length=1, max_length=80)
    entity_id: str = Field(alias="entityId", min_length=1, max_length=160)
    version_id: str | None = Field(default=None, alias="versionId", min_length=1, max_length=160)
    role: str | None = Field(default=None, min_length=1, max_length=120)


class PriorityDecomposition(PlanningContractModel):
    goal_gain: float = Field(alias="goalGain", ge=0, le=1)
    ability_gain: float = Field(alias="abilityGain", ge=0, le=1)
    verification_urgency: float = Field(alias="verificationUrgency", ge=0, le=1)
    time_fit: float = Field(alias="timeFit", ge=0, le=1)
    load_penalty: float = Field(alias="loadPenalty", ge=0, le=1)
    track_balance_bonus: float = Field(alias="trackBalanceBonus", ge=0, le=1)


class CompletionContract(PlanningContractModel):
    objective: str = Field(min_length=1, max_length=500)
    success_criteria: list[str] = Field(alias="successCriteria", min_length=1, max_length=12)
    terminal_conditions: list[str] = Field(
        alias="terminalConditions",
        min_length=1,
        max_length=12,
    )
    requires_reflection: bool = Field(alias="requiresReflection")
    completion_evidence_types: list[str] = Field(
        alias="completionEvidenceTypes",
        min_length=1,
        max_length=12,
    )

    @model_validator(mode="after")
    def validate_items(self) -> "CompletionContract":
        for name, values in (
            ("successCriteria", self.success_criteria),
            ("terminalConditions", self.terminal_conditions),
            ("completionEvidenceTypes", self.completion_evidence_types),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} cannot contain blank values")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} values must be unique")
        return self


class WeeklyStudyPreferenceVersion(PlanningContractModel):
    preference_version_id: UUID = Field(alias="preferenceVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    timezone: str = Field(min_length=1, max_length=80)
    weekday_minutes: int = Field(alias="weekdayMinutes", ge=0, le=1440)
    weekend_minutes: int = Field(alias="weekendMinutes", ge=0, le=1440)
    long_block_minutes: int = Field(alias="longBlockMinutes", ge=0, le=1440)
    fragment_minutes: int = Field(alias="fragmentMinutes", ge=0, le=1440)
    regular_rest_weekday: int = Field(alias="regularRestWeekday", ge=1, le=7)
    regular_mock_weekday: int = Field(alias="regularMockWeekday", ge=1, le=7)
    effective_from: date = Field(alias="effectiveFrom")
    source_type: Literal["system_default", "user_confirmed", "user_revised"] = Field(
        alias="sourceType"
    )
    confirmed: bool
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    supersedes_preference_version_id: UUID | None = Field(
        default=None,
        alias="supersedesPreferenceVersionId",
    )
    created_at: datetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_special_days(self) -> "WeeklyStudyPreferenceVersion":
        if self.regular_rest_weekday == self.regular_mock_weekday:
            raise ValueError("rest day and mock day must be different")
        if self.confirmed != (self.source_type != "system_default"):
            raise ValueError("only user-provided weekly preferences can be confirmed")
        _require_timezone(self.created_at, "createdAt")
        return self


class DailyCapacityVersion(PlanningContractModel):
    capacity_version_id: UUID = Field(alias="capacityVersionId")
    local_date: date = Field(alias="localDate")
    version_no: int = Field(alias="versionNo", ge=1)
    available_minutes: int = Field(alias="availableMinutes", ge=0, le=1440)
    energy: int = Field(ge=1, le=5)
    stress: int = Field(ge=1, le=5)
    confidence_level: int = Field(alias="confidenceLevel", ge=1, le=5)
    confirmed: bool
    source_type: Literal["weekly_preference", "user_override"] = Field(alias="sourceType")
    weekly_preference_version_id: UUID | None = Field(
        default=None,
        alias="weeklyPreferenceVersionId",
    )
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    supersedes_capacity_version_id: UUID | None = Field(
        default=None,
        alias="supersedesCapacityVersionId",
    )
    recorded_at: datetime = Field(alias="recordedAt")

    @model_validator(mode="after")
    def validate_source(self) -> "DailyCapacityVersion":
        if self.source_type == "weekly_preference":
            if self.weekly_preference_version_id is None or self.confirmed:
                raise ValueError(
                    "weekly-preference capacity requires its preference version and is unconfirmed"
                )
        elif not self.confirmed or self.weekly_preference_version_id is None:
            raise ValueError(
                "user override capacity must be confirmed and retain its preference basis"
            )
        _require_timezone(self.recorded_at, "recordedAt")
        return self


class DailyCapacityCommand(PlanningContractModel):
    based_on_capacity_version_id: UuidText = Field(alias="basedOnCapacityVersionId")
    available_minutes: int = Field(alias="availableMinutes", ge=0, le=1440)
    energy: int = Field(ge=1, le=5)
    stress: int = Field(ge=1, le=5)
    confidence_level: int = Field(alias="confidenceLevel", ge=1, le=5)


class TaskExecutionCommand(PlanningContractModel):
    status: TaskExecutionStatus
    elapsed_minutes: int = Field(alias="elapsedMinutes", ge=0, le=1440)
    evidence_refs: list[PlanningEvidenceRef] = Field(
        default_factory=list,
        alias="evidenceRefs",
        max_length=50,
    )
    abandonment_reason: str | None = Field(
        default=None,
        alias="abandonmentReason",
        min_length=1,
        max_length=500,
    )
    based_on_execution_version_id: UuidText | None = Field(
        default=None,
        alias="basedOnExecutionVersionId",
    )

    @model_validator(mode="after")
    def validate_terminal_evidence(self) -> "TaskExecutionCommand":
        if self.status == "complete" and not self.evidence_refs:
            raise ValueError("complete task execution requires evidenceRefs")
        if self.status == "abandoned" and not (self.abandonment_reason or "").strip():
            raise ValueError("abandoned task execution requires abandonmentReason")
        if self.status != "abandoned" and self.abandonment_reason is not None:
            raise ValueError("abandonmentReason is only valid for abandoned execution")
        return self


class TaskExecutionVersion(PlanningContractModel):
    execution_version_id: UUID = Field(alias="executionVersionId")
    task_id: UUID = Field(alias="taskId")
    plan_version_id: UUID = Field(alias="planVersionId")
    local_date: date = Field(alias="localDate")
    work_item_key: Sha256 = Field(alias="workItemKey")
    version_no: int = Field(alias="versionNo", ge=1)
    status: TaskExecutionStatus
    elapsed_minutes: int = Field(alias="elapsedMinutes", ge=0, le=1440)
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs")
    abandonment_reason: str | None = Field(default=None, alias="abandonmentReason")
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    supersedes_execution_version_id: UUID | None = Field(
        default=None,
        alias="supersedesExecutionVersionId",
    )
    supersedes_execution_version_no: int | None = Field(
        default=None,
        alias="supersedesExecutionVersionNo",
        ge=1,
    )
    recorded_at: datetime = Field(alias="recordedAt")

    @model_validator(mode="after")
    def validate_execution(self) -> "TaskExecutionVersion":
        if self.status == "complete" and not self.evidence_refs:
            raise ValueError("complete task execution requires evidenceRefs")
        if self.status == "abandoned" and not (self.abandonment_reason or "").strip():
            raise ValueError("abandoned task execution requires abandonmentReason")
        if self.status != "abandoned" and self.abandonment_reason is not None:
            raise ValueError("abandonmentReason is only valid for abandoned execution")
        if self.version_no == 1 and (
            self.supersedes_execution_version_id is not None
            or self.supersedes_execution_version_no is not None
        ):
            raise ValueError("first execution version cannot supersede another version")
        if self.version_no > 1 and (
            self.supersedes_execution_version_id is None
            or self.supersedes_execution_version_no != self.version_no - 1
        ):
            raise ValueError("execution version chain must be contiguous")
        _require_timezone(self.recorded_at, "recordedAt")
        return self


class ScoreRange(PlanningContractModel):
    lower: float = Field(ge=0, le=200)
    upper: float = Field(ge=0, le=200)
    confidence: EvidenceConfidence

    @model_validator(mode="after")
    def validate_order(self) -> "ScoreRange":
        if self.lower > self.upper:
            raise ValueError("score range must be ordered")
        return self


class GoalGap(PlanningContractModel):
    readiness: Literal["unavailable", "partial", "complete", "stale"]
    goal_id: str | None = Field(default=None, alias="goalId", min_length=1, max_length=160)
    position_name: str | None = Field(
        default=None,
        alias="positionName",
        min_length=1,
        max_length=300,
    )
    department_name: str | None = Field(
        default=None,
        alias="departmentName",
        min_length=1,
        max_length=300,
    )
    target_score: ScoreRange | None = Field(default=None, alias="targetScore")
    current_score: ScoreRange | None = Field(default=None, alias="currentScore")
    baseline_gap: float | None = Field(default=None, alias="baselineGap")
    evidence_summary: str = Field(alias="evidenceSummary", min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_readiness(self) -> "GoalGap":
        complete_values = (
            self.goal_id,
            self.position_name,
            self.department_name,
            self.target_score,
            self.current_score,
            self.baseline_gap,
        )
        if self.readiness == "complete" and any(value is None for value in complete_values):
            raise ValueError("complete goal gap requires target, current score, and gap")
        if self.readiness != "complete" and self.baseline_gap is not None:
            raise ValueError("incomplete or stale goal gap cannot expose baselineGap")
        return self


class TrackAllocation(PlanningContractModel):
    track: PlanTrack
    minutes: int = Field(gt=0, le=1440)
    reason: str = Field(min_length=1, max_length=1000)
    stages: list[PlanStage]
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs", min_length=1)

    @model_validator(mode="after")
    def validate_stages(self) -> "TrackAllocation":
        if not self.stages:
            raise ValueError("positive track allocation requires at least one stage")
        if any(stage not in _TRACK_STAGES[self.track] for stage in self.stages):
            raise ValueError("track allocation contains a stage from another track")
        if len(self.stages) != len(set(self.stages)):
            raise ValueError("track allocation stages must be unique")
        return self


class DualTrackExplanation(PlanningContractModel):
    mode: Literal[
        "balanced",
        "capacity_limited",
        "mock_day",
        "recovery_day",
        "evidence_missing",
    ]
    summary: str = Field(min_length=1, max_length=1200)
    xingce_minutes: int = Field(alias="xingceMinutes", ge=0, le=1440)
    essay_minutes: int = Field(alias="essayMinutes", ge=0, le=1440)
    recovery_minutes: int = Field(alias="recoveryMinutes", ge=0, le=1440)
    mock_minutes: int = Field(alias="mockMinutes", ge=0, le=1440)
    dual_track_constraint_applied: bool = Field(alias="dualTrackConstraintApplied")
    allocations: list[TrackAllocation]
    omitted_track_reasons: dict[PlanTrack, str] = Field(alias="omittedTrackReasons")
    capability_gaps: dict[str, str] = Field(
        default_factory=dict,
        alias="capabilityGaps",
    )

    @model_validator(mode="after")
    def validate_allocations(self) -> "DualTrackExplanation":
        tracks = [item.track for item in self.allocations]
        if len(tracks) != len(set(tracks)):
            raise ValueError("track allocations must be unique")
        expected = {
            "xingce": self.xingce_minutes,
            "essay": self.essay_minutes,
            "recovery": self.recovery_minutes,
            "mock": self.mock_minutes,
        }
        for allocation in self.allocations:
            if allocation.minutes != expected[allocation.track]:
                raise ValueError("track allocation minutes must match explanation totals")
        for track, minutes in expected.items():
            if minutes > 0 and track not in tracks:
                raise ValueError("positive track minutes require an allocation")
            if minutes == 0 and track not in tracks and track not in self.omitted_track_reasons:
                raise ValueError("omitted tracks require an explicit reason")
        if self.dual_track_constraint_applied and not (
            self.xingce_minutes > 0 and self.essay_minutes > 0
        ):
            raise ValueError("dual-track constraint requires both xingce and essay time")
        if any(not reason.strip() for reason in self.omitted_track_reasons.values()):
            raise ValueError("omitted track reasons cannot be blank")
        if any(
            not capability.strip() or not reason.strip()
            for capability, reason in self.capability_gaps.items()
        ):
            raise ValueError("capability gaps require non-blank keys and reasons")
        return self


class DailyPlanTask(PlanningContractModel):
    task_id: UUID = Field(alias="taskId")
    plan_version_id: UUID = Field(alias="planVersionId")
    local_date: date = Field(alias="localDate")
    plan_version_no: int = Field(alias="planVersionNo", ge=1)
    task_order: int = Field(alias="taskOrder", ge=1)
    track: PlanTrack
    stage: PlanStage
    kind: PlanTaskKind
    title: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=1200)
    estimated_minutes: int = Field(alias="estimatedMinutes", ge=1, le=1440)
    mandatory: bool
    start_url: str = Field(alias="startUrl", pattern=r"^/")
    priority_score: float = Field(alias="priorityScore", ge=0, le=1)
    priority_breakdown: PriorityDecomposition = Field(alias="priorityBreakdown")
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs", min_length=1)
    completion: CompletionContract
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    created_at: datetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_track_contract(self) -> "DailyPlanTask":
        if self.stage not in _TRACK_STAGES[self.track]:
            raise ValueError("task stage does not belong to task track")
        if self.kind not in _TRACK_KINDS[self.track]:
            raise ValueError("task kind does not belong to task track")
        _require_timezone(self.created_at, "createdAt")
        return self


class DailyPlanVersion(PlanningContractModel):
    plan_version_id: UUID = Field(alias="planVersionId")
    local_date: date = Field(alias="localDate")
    version_no: int = Field(alias="versionNo", ge=1)
    capacity_version_id: UUID = Field(alias="capacityVersionId")
    weekly_preference_version_id: UUID = Field(alias="weeklyPreferenceVersionId")
    day_mode: DayMode = Field(alias="dayMode")
    headline: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=2000)
    goal_gap: GoalGap = Field(alias="goalGap")
    scheduled_minutes: int = Field(alias="scheduledMinutes", ge=0, le=1440)
    due_reflection_count: int | None = Field(alias="dueReflectionCount", ge=0)
    due_verification_count: int | None = Field(alias="dueVerificationCount", ge=0)
    dual_track_explanation: DualTrackExplanation = Field(alias="dualTrackExplanation")
    input_versions: dict[str, str] = Field(alias="inputVersions", min_length=1)
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs", min_length=1)
    invalidation_conditions: list[str] = Field(alias="invalidationConditions", min_length=1)
    replan_reason_codes: list[str] = Field(alias="replanReasonCodes")
    plan_basis: Literal["evidence_priority_v2"] = Field(alias="planBasis")
    contract_version: str = Field(alias="contractVersion", min_length=1, max_length=80)
    algorithm_version: str = Field(alias="algorithmVersion", min_length=1, max_length=80)
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    supersedes_plan_version_id: UUID | None = Field(
        default=None,
        alias="supersedesPlanVersionId",
    )
    generated_at: datetime = Field(alias="generatedAt")
    valid_until: datetime = Field(alias="validUntil")
    tasks: list[DailyPlanTask]

    @model_validator(mode="after")
    def validate_plan(self) -> "DailyPlanVersion":
        _require_timezone(self.generated_at, "generatedAt")
        _require_timezone(self.valid_until, "validUntil")
        if self.valid_until <= self.generated_at:
            raise ValueError("validUntil must be later than generatedAt")
        orders = [task.task_order for task in self.tasks]
        if orders != list(range(1, len(self.tasks) + 1)):
            raise ValueError("task order must be contiguous and already sorted")
        if any(
            task.plan_version_id != self.plan_version_id
            or task.local_date != self.local_date
            or task.plan_version_no != self.version_no
            for task in self.tasks
        ):
            raise ValueError("tasks must use the same plan, owner date, and version")
        if sum(task.estimated_minutes for task in self.tasks) != self.scheduled_minutes:
            raise ValueError("scheduledMinutes must equal the sum of plan tasks")
        explanation_minutes = (
            self.dual_track_explanation.xingce_minutes
            + self.dual_track_explanation.essay_minutes
            + self.dual_track_explanation.recovery_minutes
            + self.dual_track_explanation.mock_minutes
        )
        if explanation_minutes != self.scheduled_minutes:
            raise ValueError("track explanation minutes must equal scheduledMinutes")
        if self.day_mode == "unavailable" and (self.tasks or self.scheduled_minutes != 0):
            raise ValueError("unavailable day cannot contain scheduled work")
        if self.day_mode == "mock" and not any(task.track == "mock" for task in self.tasks):
            raise ValueError("mock day requires a mock task")
        _require_unique_nonblank(self.invalidation_conditions, "invalidationConditions")
        _require_unique_nonblank(self.replan_reason_codes, "replanReasonCodes")
        if any(not key.strip() or not value.strip() for key, value in self.input_versions.items()):
            raise ValueError("inputVersions keys and values cannot be blank")
        return self


class TodayCapacity(PlanningContractModel):
    capacity_version_id: UUID = Field(alias="capacityVersionId")
    local_date: date = Field(alias="localDate")
    version_no: int = Field(alias="versionNo", ge=1)
    available_minutes: int = Field(alias="availableMinutes", ge=0, le=1440)
    energy: int = Field(ge=1, le=5)
    stress: int = Field(ge=1, le=5)
    confidence_level: int = Field(alias="confidenceLevel", ge=1, le=5)
    confirmed: bool
    source_type: Literal["weekly_preference", "user_override"] = Field(alias="sourceType")


class TodayTask(PlanningContractModel):
    id: UUID
    kind: PlanTaskKind
    title: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=1200)
    estimated_minutes: int = Field(alias="estimatedMinutes", ge=1, le=1440)
    completion_rule: str = Field(alias="completionRule", min_length=1, max_length=1000)
    status: PlanTaskStatus
    priority: int = Field(ge=1)
    track: PlanTrack
    stage: PlanStage
    mandatory: bool
    start_url: str = Field(alias="startUrl", pattern=r"^/")
    priority_score: float = Field(alias="priorityScore", ge=0, le=1)
    priority_breakdown: PriorityDecomposition = Field(alias="priorityBreakdown")
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs", min_length=1)
    completion: CompletionContract
    execution: TaskExecutionVersion | None = None

    @model_validator(mode="after")
    def validate_track_contract(self) -> "TodayTask":
        if self.stage not in _TRACK_STAGES[self.track]:
            raise ValueError("task stage does not belong to task track")
        if self.kind not in _TRACK_KINDS[self.track]:
            raise ValueError("task kind does not belong to task track")
        if self.execution is None and self.status != "ready":
            raise ValueError("non-ready task requires an execution version")
        if self.execution is not None and self.status != self.execution.status:
            raise ValueError("task status must match its execution version")
        return self


class TodayDashboard(PlanningContractModel):
    generated_at: datetime = Field(alias="generatedAt")
    plan_version: str = Field(alias="planVersion", min_length=1, max_length=160)
    plan_version_id: UUID = Field(alias="planVersionId")
    local_date: date = Field(alias="localDate")
    version_no: int = Field(alias="versionNo", ge=1)
    plan_basis: Literal["evidence_priority_v2"] = Field(alias="planBasis")
    day_mode: DayMode = Field(alias="dayMode")
    headline: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=2000)
    goal_gap: GoalGap = Field(alias="goalGap")
    capacity: TodayCapacity
    tasks: list[TodayTask]
    due_reflection_count: int | None = Field(alias="dueReflectionCount", ge=0)
    due_verification_count: int | None = Field(alias="dueVerificationCount", ge=0)
    is_recovery_day: bool = Field(alias="isRecoveryDay")
    dual_track_explanation: DualTrackExplanation = Field(alias="dualTrackExplanation")
    input_versions: dict[str, str] = Field(alias="inputVersions", min_length=1)
    evidence_refs: list[PlanningEvidenceRef] = Field(alias="evidenceRefs", min_length=1)
    invalidation_conditions: list[str] = Field(alias="invalidationConditions", min_length=1)
    replan_reason_codes: list[str] = Field(alias="replanReasonCodes")

    @model_validator(mode="after")
    def validate_dashboard(self) -> "TodayDashboard":
        _require_timezone(self.generated_at, "generatedAt")
        if self.capacity.local_date != self.local_date:
            raise ValueError("capacity date must match dashboard date")
        if self.is_recovery_day != (self.day_mode == "recovery"):
            raise ValueError("isRecoveryDay must match dayMode")
        priorities = [task.priority for task in self.tasks]
        if priorities != list(range(1, len(self.tasks) + 1)):
            raise ValueError("task priorities must be contiguous and already sorted")
        task_minutes = sum(task.estimated_minutes for task in self.tasks)
        explanation_minutes = (
            self.dual_track_explanation.xingce_minutes
            + self.dual_track_explanation.essay_minutes
            + self.dual_track_explanation.recovery_minutes
            + self.dual_track_explanation.mock_minutes
        )
        if task_minutes != explanation_minutes:
            raise ValueError("task minutes must match dual-track explanation")
        if task_minutes > self.capacity.available_minutes:
            raise ValueError("planned tasks cannot exceed daily capacity")
        if self.day_mode == "unavailable" and self.tasks:
            raise ValueError("unavailable dashboard cannot contain tasks")
        _require_unique_nonblank(self.invalidation_conditions, "invalidationConditions")
        _require_unique_nonblank(self.replan_reason_codes, "replanReasonCodes")
        return self


class DailyCapacityCommandResult(PlanningContractModel):
    capacity: TodayCapacity
    plan: TodayDashboard


class TaskExecutionCommandResult(PlanningContractModel):
    execution: TaskExecutionVersion
    plan: TodayDashboard
    replanned: bool


def _require_timezone(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def _require_unique_nonblank(values: list[str], field_name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} cannot contain blank values")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} values must be unique")
