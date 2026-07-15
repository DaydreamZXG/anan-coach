from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AbilityBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


ScoreComponent = Literal["xingce", "shenlun"]
ScoreEvidenceSource = Literal[
    "self_report",
    "placement_test",
    "controlled_mock",
    "external_mock",
    "official_exam",
]
ReportedScoreContext = Literal["official_exam", "full_mock", "historical_estimate"]
TimedStatus = Literal["timed", "untimed", "unknown"]
EvidenceVerification = Literal["user_confirmed", "system_verified"]
EstimateConfidence = Literal["low", "medium", "high"]


class ReportedScoreEvidenceRequest(AbilityBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reported_context: ReportedScoreContext = Field(alias="reportedContext")
    xingce_score: float | None = Field(default=None, alias="xingceScore", ge=0, le=100)
    shenlun_score: float | None = Field(default=None, alias="shenlunScore", ge=0, le=100)
    timed_status: TimedStatus = Field(alias="timedStatus")
    occurred_at: datetime = Field(alias="occurredAt")
    supersedes_evidence_id: UUID | None = Field(default=None, alias="supersedesEvidenceId")

    @model_validator(mode="after")
    def validate_component(self) -> "ReportedScoreEvidenceRequest":
        if self.xingce_score is None and self.shenlun_score is None:
            raise ValueError("at least one score component is required")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurredAt must include a timezone")
        return self


class ScoreEvidence(AbilityBaseModel):
    evidence_id: str = Field(alias="evidenceId")
    exam_code: str = Field(alias="examCode")
    exam_year: int = Field(alias="examYear")
    source_type: ScoreEvidenceSource = Field(alias="sourceType")
    reported_context: ReportedScoreContext | None = Field(
        default=None,
        alias="reportedContext",
    )
    xingce_score: float | None = Field(default=None, alias="xingceScore")
    shenlun_score: float | None = Field(default=None, alias="shenlunScore")
    total_score: float | None = Field(default=None, alias="totalScore")
    timed_status: TimedStatus = Field(alias="timedStatus")
    verification_status: EvidenceVerification = Field(alias="verificationStatus")
    occurred_at: datetime = Field(alias="occurredAt")
    supersedes_evidence_id: str | None = Field(default=None, alias="supersedesEvidenceId")
    created_at: datetime = Field(alias="createdAt")


class ScoreEvidenceHistoryItem(ScoreEvidence):
    superseded_by_evidence_id: str | None = Field(
        default=None,
        alias="supersededByEvidenceId",
    )
    active: bool


class ScoreEvidenceHistory(AbilityBaseModel):
    items: list[ScoreEvidenceHistoryItem]


class ScoreRange(AbilityBaseModel):
    lower: float
    recommended: float
    upper: float

    @model_validator(mode="after")
    def validate_order(self) -> "ScoreRange":
        if not self.lower <= self.recommended <= self.upper:
            raise ValueError("score range must be ordered")
        return self


class AbilityEvidenceSummary(AbilityBaseModel):
    evidence_count: int = Field(alias="evidenceCount", ge=1)
    xingce_evidence_count: int = Field(alias="xingceEvidenceCount", ge=0)
    shenlun_evidence_count: int = Field(alias="shenlunEvidenceCount", ge=0)
    source_counts: dict[str, int] = Field(alias="sourceCounts")
    latest_evidence_at: datetime = Field(alias="latestEvidenceAt")
    missing_components: list[ScoreComponent] = Field(alias="missingComponents")
    conflict_components: list[ScoreComponent] = Field(alias="conflictComponents")


class AbilityEstimateVersion(AbilityBaseModel):
    estimate_version_id: str = Field(alias="estimateVersionId")
    exam_code: str = Field(alias="examCode")
    exam_year: int = Field(alias="examYear")
    status: Literal["partial", "complete"]
    xingce: ScoreRange | None = None
    shenlun: ScoreRange | None = None
    total: ScoreRange | None = None
    confidence: EstimateConfidence
    source_evidence_ids: list[str] = Field(alias="sourceEvidenceIds")
    evidence_summary: AbilityEvidenceSummary = Field(alias="evidenceSummary")
    algorithm_version: str = Field(alias="algorithmVersion")
    input_fingerprint: str = Field(alias="inputFingerprint", min_length=64, max_length=64)
    supersedes_estimate_version_id: str | None = Field(
        default=None,
        alias="supersedesEstimateVersionId",
    )
    generated_at: datetime = Field(alias="generatedAt")
    valid_until: datetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def validate_status(self) -> "AbilityEstimateVersion":
        if self.status == "complete" and (
            self.xingce is None or self.shenlun is None or self.total is None
        ):
            raise ValueError("complete estimate requires both components and total")
        if self.status == "partial" and (
            self.total is not None or ((self.xingce is None) == (self.shenlun is None))
        ):
            raise ValueError("partial estimate requires exactly one score component")
        return self


class AbilityEstimateCurrent(AbilityBaseModel):
    estimate: AbilityEstimateVersion | None
    readiness: Literal["unavailable", "partial", "complete", "stale"]
    missing_components: list[ScoreComponent] = Field(alias="missingComponents")


class ReportedScoreEvidenceResult(AbilityBaseModel):
    evidence: ScoreEvidence
    estimate: AbilityEstimateVersion
