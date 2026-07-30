from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.knowledge.schemas import QuestionDetail
from app.domains.memory.schemas import LearnerProfile
from app.domains.study_budget import MAX_DAILY_MINUTES


class OnboardingBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


StudyMode = Literal["fulltime", "parttime"]
BaselineSource = Literal["self_report", "placement_test", "none"]


class OnboardingProfileUpdate(OnboardingBaseModel):
    education: str = Field(min_length=1, max_length=40)
    major: str = Field(min_length=1, max_length=120)
    fresh_graduate: bool = Field(alias="freshGraduate")
    political_status: str = Field(alias="politicalStatus", min_length=1, max_length=40)
    grassroots_years: int = Field(default=0, alias="grassrootsYears", ge=0, le=50)
    study_mode: StudyMode | None = Field(default=None, alias="studyMode")
    daily_minutes: int | None = Field(default=None, alias="dailyMinutes", ge=30, le=MAX_DAILY_MINUTES)
    target_position_id: str | None = Field(default=None, alias="targetPositionId")
    target_score: float | None = Field(default=None, alias="targetScore", ge=0, le=300)
    baseline_source: BaselineSource | None = Field(default=None, alias="baselineSource")
    baseline_xingce_score: float | None = Field(default=None, alias="baselineXingceScore", ge=0, le=100)
    baseline_shenlun_score: float | None = Field(default=None, alias="baselineShenlunScore", ge=0, le=100)
    baseline_weak_modules: list[str] = Field(default_factory=list, alias="baselineWeakModules")

    @field_validator("target_position_id")
    @classmethod
    def validate_target_position_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(UUID(value.strip()))
        except ValueError:
            raise ValueError("targetPositionId must be a valid UUID") from None

    @model_validator(mode="after")
    def validate_baseline(self) -> "OnboardingProfileUpdate":
        if self.target_score is not None and self.target_position_id is None:
            raise ValueError("targetScore requires targetPositionId")
        if self.baseline_source == "self_report" and (
            self.baseline_xingce_score is None and self.baseline_shenlun_score is None
        ):
            raise ValueError("self_report baseline requires at least one score")
        return self


class OnboardingCompleteResponse(OnboardingBaseModel):
    profile: LearnerProfile
    first_day_plan_url: str = Field(alias="firstDayPlanUrl")


class PlacementQuestionSpec(OnboardingBaseModel):
    module_name: str = Field(alias="moduleName")
    count: int


class PlacementTestTask(OnboardingBaseModel):
    task_id: str = Field(alias="taskId")
    title: str
    duration_minutes: int = Field(alias="durationMinutes")
    question_count: int = Field(alias="questionCount")
    module_plan: list[PlacementQuestionSpec] = Field(alias="modulePlan")
    questions: list[QuestionDetail]


class PlacementAnswer(OnboardingBaseModel):
    question_id: str = Field(alias="questionId")
    selected_answer: str = Field(alias="selectedAnswer", min_length=1, max_length=8)


class PlacementSubmitRequest(OnboardingBaseModel):
    answers: list[PlacementAnswer] = Field(min_length=1)


class PlacementModuleResult(OnboardingBaseModel):
    module_name: str = Field(alias="moduleName")
    correct: int
    total: int
    accuracy: float


class PlacementSubmitResponse(OnboardingBaseModel):
    task_id: str = Field(alias="taskId")
    estimated_xingce_score: float = Field(alias="estimatedXingceScore")
    baseline_source: BaselineSource = Field(alias="baselineSource")
    module_results: list[PlacementModuleResult] = Field(alias="moduleResults")
