from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.ability_estimation.schemas import AbilityEstimateVersion
from app.domains.positions.schemas import TargetScoreRange


class OnboardingV2Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


OnboardingNextStep = Literal[
    "preparation_experience",
    "qualification",
    "study_preference",
    "result",
    "workspace",
]
OnboardingResultNextAction = Literal[
    "enter_workspace",
    "record_baseline",
    "choose_position",
    "review_exam_briefing",
    "import_prior_evidence",
    "start_retake_recovery",
]
PositionLevel = Literal[
    "central",
    "provincial",
    "municipal",
    "county_or_below",
]
AbilityBaselineMode = Literal["unknown", "reported_score"]
Weekday = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


class OnboardingProgress(OnboardingV2Model):
    progress_version: int = Field(alias="progressVersion", ge=1)
    experience_ready: bool = Field(alias="experienceReady")
    experience_profile_version: int | None = Field(
        default=None,
        alias="experienceProfileVersion",
        ge=1,
    )
    qualification_ready: bool = Field(alias="qualificationReady")
    qualification_profile_version: int | None = Field(
        default=None,
        alias="qualificationProfileVersion",
        ge=1,
    )
    study_preference_ready: bool = Field(alias="studyPreferenceReady")
    study_preference_version: int | None = Field(
        default=None,
        alias="studyPreferenceVersion",
        ge=1,
    )
    result_ready: bool = Field(alias="resultReady")
    result_acknowledged: bool = Field(alias="resultAcknowledged")
    next_step: OnboardingNextStep = Field(alias="nextStep")
    completed: bool
    updated_at: datetime = Field(alias="updatedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")


class PreparationExperienceWrite(OnboardingV2Model):
    expected_experience_version: int = Field(alias="expectedExperienceVersion", ge=0)
    expected_progress_version: int = Field(alias="expectedProgressVersion", ge=1)
    first_systematic: bool = Field(alias="firstSystematic")
    external_learning: bool = Field(alias="externalLearning")
    prior_guokao_exam: bool = Field(alias="priorGuokaoExam")
    retake: bool
    prior_exam_years: list[int] = Field(
        default_factory=list,
        alias="priorExamYears",
        max_length=20,
    )
    current_status: Literal[
        "parttime",
        "fulltime",
        "student",
        "unspecified",
    ] = Field(default="unspecified", alias="currentStatus")

    @model_validator(mode="after")
    def validate_experience(self) -> "PreparationExperienceWrite":
        experienced = self.external_learning or self.prior_guokao_exam or self.retake
        if self.first_systematic == experienced:
            raise ValueError(
                "firstSystematic must be exclusive with experienced preparation modes"
            )
        if self.retake and not self.prior_guokao_exam:
            raise ValueError("retake requires priorGuokaoExam")
        if self.prior_exam_years and not self.prior_guokao_exam:
            raise ValueError("priorExamYears require priorGuokaoExam")
        if any(year < 2000 or year > 2100 for year in self.prior_exam_years):
            raise ValueError("priorExamYears must be between 2000 and 2100")
        if self.prior_exam_years != sorted(set(self.prior_exam_years)):
            raise ValueError("priorExamYears must be sorted and unique")
        return self


class PreparationExperienceProfile(OnboardingV2Model):
    experience_version_id: str = Field(alias="experienceVersionId")
    experience_version: int = Field(alias="experienceVersion", ge=1)
    declaration_status: Literal["declared", "legacy_not_collected"] = Field(
        alias="declarationStatus"
    )
    first_systematic: bool = Field(alias="firstSystematic")
    external_learning: bool = Field(alias="externalLearning")
    prior_guokao_exam: bool = Field(alias="priorGuokaoExam")
    retake: bool
    prior_exam_years: list[int] = Field(alias="priorExamYears")
    current_status: Literal[
        "parttime",
        "fulltime",
        "student",
        "unspecified",
    ] = Field(alias="currentStatus")
    source_type: Literal["user_confirmed", "user_revised", "migration"] = Field(
        alias="sourceType"
    )
    recommended_entry: Literal[
        "guided_first_start",
        "import_external_evidence",
        "retake_loss_recovery",
        "legacy_unspecified",
    ] = Field(alias="recommendedEntry")
    created_at: datetime = Field(alias="createdAt")
    supersedes_experience_version_id: str | None = Field(
        default=None,
        alias="supersedesExperienceVersionId",
    )


class PreparationExperienceResult(OnboardingV2Model):
    experience: PreparationExperienceProfile
    progress: OnboardingProgress


class QualificationMilestoneCommand(OnboardingV2Model):
    expected_progress_version: int = Field(alias="expectedProgressVersion", ge=1)
    qualification_profile_version: int = Field(alias="qualificationProfileVersion", ge=1)


class PositionPreferenceWrite(OnboardingV2Model):
    expected_preference_version: int = Field(
        alias="expectedPreferenceVersion",
        ge=0,
    )
    preferred_regions: list[str] = Field(
        alias="preferredRegions",
        min_length=1,
        max_length=5,
    )
    accepts_relocation: bool = Field(alias="acceptsRelocation")
    accepted_position_levels: list[PositionLevel] = Field(
        alias="acceptedPositionLevels",
        min_length=1,
        max_length=4,
    )

    @model_validator(mode="after")
    def validate_position_preference(self) -> "PositionPreferenceWrite":
        normalized_regions = [region.strip() for region in self.preferred_regions]
        if any(not region or len(region) > 40 for region in normalized_regions):
            raise ValueError("preferredRegions must contain valid region names")
        if len(normalized_regions) != len(set(normalized_regions)):
            raise ValueError("preferredRegions must be unique")
        if len(self.accepted_position_levels) != len(
            set(self.accepted_position_levels)
        ):
            raise ValueError("acceptedPositionLevels must be unique")
        self.preferred_regions = normalized_regions
        return self


class PositionPreferenceProfile(OnboardingV2Model):
    preference_version_id: str = Field(alias="preferenceVersionId")
    preference_version: int = Field(alias="preferenceVersion", ge=1)
    preferred_regions: list[str] = Field(alias="preferredRegions")
    accepts_relocation: bool = Field(alias="acceptsRelocation")
    accepted_position_levels: list[PositionLevel] = Field(
        alias="acceptedPositionLevels"
    )
    source_type: Literal["user_confirmed", "user_revised"] = Field(alias="sourceType")
    created_at: datetime = Field(alias="createdAt")
    supersedes_preference_version_id: str | None = Field(
        default=None,
        alias="supersedesPreferenceVersionId",
    )


class AbilityBaselineWrite(OnboardingV2Model):
    expected_baseline_version: int = Field(
        alias="expectedBaselineVersion",
        ge=0,
    )
    mode: AbilityBaselineMode
    xingce_score: float | None = Field(
        default=None,
        alias="xingceScore",
        ge=0,
        le=100,
    )
    shenlun_score: float | None = Field(
        default=None,
        alias="shenlunScore",
        ge=0,
        le=100,
    )

    @model_validator(mode="after")
    def validate_ability_baseline(self) -> "AbilityBaselineWrite":
        has_score = self.xingce_score is not None or self.shenlun_score is not None
        if self.mode == "unknown" and has_score:
            raise ValueError("unknown ability baseline cannot contain scores")
        if self.mode == "reported_score" and not has_score:
            raise ValueError("reported_score ability baseline requires a score")
        return self


class AbilityBaselineProfile(OnboardingV2Model):
    baseline_version_id: str = Field(alias="baselineVersionId")
    baseline_version: int = Field(alias="baselineVersion", ge=1)
    mode: AbilityBaselineMode
    xingce_score: float | None = Field(default=None, alias="xingceScore")
    shenlun_score: float | None = Field(default=None, alias="shenlunScore")
    score_evidence_id: str | None = Field(default=None, alias="scoreEvidenceId")
    source_type: Literal["user_confirmed", "user_revised"] = Field(
        alias="sourceType"
    )
    created_at: datetime = Field(alias="createdAt")
    supersedes_baseline_version_id: str | None = Field(
        default=None,
        alias="supersedesBaselineVersionId",
    )


class StudyPreferenceWrite(OnboardingV2Model):
    expected_preference_version: int = Field(alias="expectedPreferenceVersion", ge=0)
    expected_progress_version: int = Field(alias="expectedProgressVersion", ge=1)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    effective_from: date = Field(alias="effectiveFrom")
    weekday_minutes: int = Field(alias="weekdayMinutes", ge=0, le=720)
    weekend_minutes: int = Field(alias="weekendMinutes", ge=0, le=720)
    long_block_minutes: int = Field(alias="longBlockMinutes", ge=0, le=720)
    fragment_minutes: int = Field(alias="fragmentMinutes", ge=0, le=720)
    fixed_rest_day: Weekday = Field(alias="fixedRestDay")
    mock_exam_day: Weekday = Field(alias="mockExamDay")

    @model_validator(mode="after")
    def validate_schedule(self) -> "StudyPreferenceWrite":
        if self.fixed_rest_day == self.mock_exam_day:
            raise ValueError("fixedRestDay and mockExamDay must be different")
        if self.weekday_minutes == 0 and self.weekend_minutes == 0:
            raise ValueError("weekly study preference must retain study time")
        daily_max = max(self.weekday_minutes, self.weekend_minutes)
        if self.long_block_minutes > daily_max or self.fragment_minutes > daily_max:
            raise ValueError("study blocks cannot exceed the daily time budget")
        return self


class WeeklyStudyPreference(OnboardingV2Model):
    preference_version_id: str = Field(alias="preferenceVersionId")
    preference_version: int = Field(alias="preferenceVersion", ge=1)
    timezone: str
    effective_from: date = Field(alias="effectiveFrom")
    weekday_minutes: int = Field(alias="weekdayMinutes", ge=0, le=720)
    weekend_minutes: int = Field(alias="weekendMinutes", ge=0, le=720)
    long_block_minutes: int = Field(alias="longBlockMinutes", ge=0, le=720)
    fragment_minutes: int = Field(alias="fragmentMinutes", ge=0, le=720)
    fixed_rest_day: Weekday = Field(alias="fixedRestDay")
    mock_exam_day: Weekday = Field(alias="mockExamDay")
    source_type: Literal["user_confirmed", "user_revised"] = Field(alias="sourceType")
    created_at: datetime = Field(alias="createdAt")
    supersedes_preference_version_id: str | None = Field(
        default=None,
        alias="supersedesPreferenceVersionId",
    )
    temporary_adjustment_owner: Literal["daily_capacity"] = Field(
        default="daily_capacity",
        alias="temporaryAdjustmentOwner",
    )
    temporary_adjustment_endpoint: str = Field(
        default="/api/v2/plans/today/capacity",
        alias="temporaryAdjustmentEndpoint",
    )


class StudyPreferenceResult(OnboardingV2Model):
    preference: WeeklyStudyPreference
    progress: OnboardingProgress


class OnboardingResultAcknowledgement(OnboardingV2Model):
    expected_progress_version: int = Field(alias="expectedProgressVersion", ge=1)
    acknowledge_result: Literal[True] = Field(alias="acknowledgeResult")


class OnboardingPrimaryGoal(OnboardingV2Model):
    goal_version_id: str = Field(alias="goalVersionId")
    position_id: str = Field(alias="positionId")
    position_name: str = Field(alias="positionName")
    department_name: str = Field(alias="departmentName")
    target_score: TargetScoreRange = Field(alias="targetScore")


class OnboardingScoreGap(OnboardingV2Model):
    unit: Literal["points"]
    direction: Literal["positive_means_target_above_current"]
    lower: float
    recommended: float
    upper: float

    @model_validator(mode="after")
    def validate_gap(self) -> "OnboardingScoreGap":
        if not self.lower <= self.recommended <= self.upper:
            raise ValueError("score gap must be ordered")
        return self


class OnboardingResultProjection(OnboardingV2Model):
    status: Literal[
        "evidence_sufficient",
        "baseline_required",
        "position_selection_required",
        "no_eligible_position",
    ]
    reason_code: Literal[
        "ready",
        "ability_unavailable",
        "ability_partial",
        "ability_stale",
        "primary_goal_missing",
        "no_current_eligible_position",
    ] = Field(alias="reasonCode")
    progress_version: int = Field(alias="progressVersion", ge=1)
    qualification_profile_version: int = Field(
        alias="qualificationProfileVersion",
        ge=1,
    )
    preparation_experience: PreparationExperienceProfile = Field(
        alias="preparationExperience"
    )
    ability_estimate: AbilityEstimateVersion | None = Field(
        default=None,
        alias="abilityEstimate",
    )
    primary_goal: OnboardingPrimaryGoal | None = Field(
        default=None,
        alias="primaryGoal",
    )
    score_gap: OnboardingScoreGap | None = Field(default=None, alias="scoreGap")
    weekly_preference: WeeklyStudyPreference = Field(alias="weeklyPreference")
    next_action: OnboardingResultNextAction = Field(alias="nextAction")
    generated_at: datetime = Field(alias="generatedAt")
    valid_until: datetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def validate_projection(self) -> "OnboardingResultProjection":
        if self.status == "evidence_sufficient":
            if (
                self.reason_code != "ready"
                or self.ability_estimate is None
                or self.ability_estimate.status != "complete"
                or self.primary_goal is None
                or self.score_gap is None
                or self.next_action != "enter_workspace"
            ):
                raise ValueError("evidence-sufficient result requires complete evidence")
        elif self.score_gap is not None:
            raise ValueError("score gap is only available for complete dual-subject evidence")
        if self.valid_until <= self.generated_at:
            raise ValueError("result validUntil must be later than generatedAt")
        return self
