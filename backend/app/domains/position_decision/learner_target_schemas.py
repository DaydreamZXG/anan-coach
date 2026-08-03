from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LearnerTargetBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class LearnerTargetScoreRange(LearnerTargetBaseModel):
    lower: float = Field(ge=0, le=300)
    recommended: float = Field(ge=0, le=300)
    upper: float = Field(ge=0, le=300)

    @model_validator(mode="after")
    def validate_order(self) -> "LearnerTargetScoreRange":
        if not self.lower <= self.recommended <= self.upper:
            raise ValueError("target score range must be ordered")
        return self


class ReferencePositionProjection(LearnerTargetBaseModel):
    position_id: str = Field(alias="positionId")
    name: str
    department: str
    region: str
    score_year: int = Field(alias="scoreYear")
    entry_score: float = Field(alias="entryScore")
    matched_reasons: list[str] = Field(alias="matchedReasons")


class LearnerTargetScoreProjection(LearnerTargetBaseModel):
    status: Literal["available", "unavailable"]
    reason_code: Literal[
        "historical_cohort_ready",
        "profile_incomplete",
        "insufficient_historical_samples",
    ] = Field(alias="reasonCode")
    version_id: str | None = Field(default=None, alias="versionId")
    model_version: str = Field(alias="modelVersion")
    profile_version: int = Field(alias="profileVersion", ge=1)
    reference_year: int = Field(alias="referenceYear")
    target_score: LearnerTargetScoreRange | None = Field(alias="targetScore")
    fallback_level: str | None = Field(alias="fallbackLevel")
    sample_count: int = Field(alias="sampleCount", ge=0)
    reference_positions: list[ReferencePositionProjection] = Field(alias="referencePositions")
    limitations: list[str]
