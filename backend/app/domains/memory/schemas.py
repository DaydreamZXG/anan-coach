from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.study_budget import MAX_DAILY_MINUTES


class MemoryBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class LearnerProfile(MemoryBaseModel):
    target_exam: str | None = Field(default=None, alias="targetExam")
    target_region: str | None = Field(default=None, alias="targetRegion")
    position_group: str | None = Field(default=None, alias="positionGroup")
    daily_minutes: int | None = Field(default=None, alias="dailyMinutes", ge=30, le=MAX_DAILY_MINUTES)
    education: str | None = None
    major: str | None = None
    fresh_graduate: bool | None = Field(default=None, alias="freshGraduate")
    political_status: str | None = Field(default=None, alias="politicalStatus")
    grassroots_years: int | None = Field(default=None, alias="grassrootsYears", ge=0, le=50)
    study_mode: Literal["fulltime", "parttime"] | None = Field(default=None, alias="studyMode")
    target_position_id: str | None = Field(default=None, alias="targetPositionId")
    target_score: float | None = Field(default=None, alias="targetScore")
    baseline_source: Literal["self_report", "placement_test", "none"] | None = Field(default=None, alias="baselineSource")
    baseline_xingce_score: float | None = Field(default=None, alias="baselineXingceScore")
    baseline_shenlun_score: float | None = Field(default=None, alias="baselineShenlunScore")
    baseline_weak_modules: list[str] = Field(default_factory=list, alias="baselineWeakModules")
    onboarding_complete: bool = Field(alias="onboardingComplete")


class LearnerProfileUpdate(MemoryBaseModel):
    target_exam: str = Field(alias="targetExam", min_length=2, max_length=80)
    target_region: str = Field(alias="targetRegion", min_length=2, max_length=80)
    position_group: str = Field(alias="positionGroup", min_length=2, max_length=120)
    daily_minutes: int = Field(alias="dailyMinutes", ge=30, le=MAX_DAILY_MINUTES)


class LearnerMemorySummary(MemoryBaseModel):
    practice_answers: int = Field(alias="practiceAnswers")
    wrong_answers: int = Field(alias="wrongAnswers")
    essay_attempts: int = Field(alias="essayAttempts")


class LearnerMemory(MemoryBaseModel):
    profile: LearnerProfile
    summary: LearnerMemorySummary
