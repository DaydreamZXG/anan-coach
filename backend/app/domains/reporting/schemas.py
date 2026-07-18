from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.external_ingestion.recognition.models import RecognitionCandidatePayload


class ReportingContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ExternalScoreRecognitionStartCommand(ReportingContractModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId", strict=False)


class ExternalScoreRecognitionCorrectionCommand(ReportingContractModel):
    expected_revision_no: int = Field(alias="expectedRevisionNo", ge=1)
    corrected_payload: RecognitionCandidatePayload = Field(alias="correctedPayload")
    reason: str = Field(min_length=1, max_length=500)


class ExternalScoreRecognitionDecisionCommand(ReportingContractModel):
    expected_revision_no: int = Field(alias="expectedRevisionNo", ge=1)


class ExternalMockReportDraftCommand(ReportingContractModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId", strict=False)
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId", strict=False)
    platform_name: str = Field(alias="platformName", min_length=1, max_length=120)
    exam_name: str = Field(alias="examName", min_length=1, max_length=200)
    occurred_at: datetime = Field(alias="occurredAt")
    timed_status: Literal["timed", "untimed", "unknown"] = Field(alias="timedStatus")
    duration_minutes: int | None = Field(default=None, alias="durationMinutes", gt=0, le=1440)
    total_score: float | None = Field(default=None, alias="totalScore", ge=0, le=200)
    xingce_score: float | None = Field(default=None, alias="xingceScore", ge=0, le=100)
    shenlun_score: float | None = Field(default=None, alias="shenlunScore", ge=0, le=100)
    module_scores: dict[str, float] = Field(default_factory=dict, alias="moduleScores")

    @model_validator(mode="after")
    def validate_report_fields(self) -> "ExternalMockReportDraftCommand":
        self.platform_name = self.platform_name.strip()
        self.exam_name = self.exam_name.strip()
        if not self.platform_name or not self.exam_name:
            raise ValueError("platformName and examName cannot be blank")
        normalized: dict[str, float] = {}
        for key, value in self.module_scores.items():
            name = key.strip()
            if not name or name in normalized:
                raise ValueError("moduleScores keys must be non-blank and unique")
            if not 0 <= value <= 100:
                raise ValueError("moduleScores values must be between 0 and 100")
            normalized[name] = round(value, 2)
        self.module_scores = normalized
        if not any(
            value is not None
            for value in (self.total_score, self.xingce_score, self.shenlun_score)
        ) and not self.module_scores:
            raise ValueError("external report requires at least one score field")
        if (
            self.total_score is not None
            and self.xingce_score is not None
            and self.shenlun_score is not None
            and round(self.xingce_score + self.shenlun_score, 2)
            != round(self.total_score, 2)
        ):
            raise ValueError("totalScore must equal xingceScore plus shenlunScore")
        if self.timed_status == "timed" and self.duration_minutes is None:
            raise ValueError("timed reports require durationMinutes")
        if self.timed_status != "timed" and self.duration_minutes is not None:
            raise ValueError("durationMinutes is only valid for timed reports")
        return self


class ExternalMockReportConfirmCommand(ReportingContractModel):
    expected_report_version_id: UUID = Field(
        alias="expectedReportVersionId", strict=False
    )


class ExternalMockReportProjection(ReportingContractModel):
    report_version_id: UUID = Field(alias="reportVersionId")
    report_id: UUID = Field(alias="reportId")
    version_no: int = Field(alias="versionNo", ge=1)
    state: Literal["candidate", "confirmed", "rejected"]
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId")
    platform_name: str = Field(alias="platformName")
    exam_name: str = Field(alias="examName")
    occurred_at: datetime = Field(alias="occurredAt")
    timed_status: Literal["timed", "untimed", "unknown"] = Field(alias="timedStatus")
    duration_minutes: int | None = Field(alias="durationMinutes")
    total_score: float | None = Field(alias="totalScore")
    xingce_score: float | None = Field(alias="xingceScore")
    shenlun_score: float | None = Field(alias="shenlunScore")
    module_scores: dict[str, float] = Field(alias="moduleScores")
    evidence_grade: Literal[
        "candidate_unconfirmed",
        "ability_eligible",
        "trend_only_untimed",
        "trend_only_incomplete",
        "trend_only_stale",
    ] = Field(alias="evidenceGrade")
    score_evidence_id: UUID | None = Field(alias="scoreEvidenceId")
    estimate_version_id: UUID | None = Field(alias="estimateVersionId")
    ability_impact: Literal["blocked_until_confirmed", "entered_estimate", "trend_only"] = Field(
        alias="abilityImpact"
    )
    confirmation_message: str = Field(alias="confirmationMessage")
    confirmed_at: datetime | None = Field(alias="confirmedAt")
    created_at: datetime = Field(alias="createdAt")


class MockPaperSummary(ReportingContractModel):
    paper_version_id: UUID = Field(alias="paperVersionId")
    title: str
    duration_minutes: int = Field(alias="durationMinutes")
    question_count: int = Field(alias="questionCount")
    scoring_policy_version_id: str = Field(alias="scoringPolicyVersionId")
    metric_name: str = Field(alias="metricName")
    metric_unit: str = Field(alias="metricUnit")
    metric_definition: str = Field(alias="metricDefinition")
    status: Literal["available", "in_progress", "submitted"]
    session_id: UUID | None = Field(alias="sessionId")


class ReportCardSummary(ReportingContractModel):
    report_kind: Literal["single_set", "weekly", "mock", "position_gap", "global"] = Field(
        alias="reportKind"
    )
    title: str
    subtitle: str
    route: str
    state: Literal["ready", "partial", "empty"]


class ReportCenterProjection(ReportingContractModel):
    generated_at: datetime = Field(alias="generatedAt")
    standard_papers: list[MockPaperSummary] = Field(alias="standardPapers")
    latest_external_report: ExternalMockReportProjection | None = Field(
        alias="latestExternalReport"
    )
    report_cards: list[ReportCardSummary] = Field(alias="reportCards")


class ChartSpec(ReportingContractModel):
    chart_id: str = Field(alias="chartId")
    chart_type: Literal[
        "confidence_trend",
        "module_bullet",
        "speed_accuracy_quadrant",
        "error_heatmap",
        "score_gap_waterfall",
        "weekly_completion",
    ] = Field(alias="chartType")
    title: str
    definition: str
    period: str
    unit: str
    confidence: Literal["not_applicable", "low", "medium", "high"]
    summary: str
    evidence_refs: list[str] = Field(alias="evidenceRefs")
    data: list[dict[str, Any]]
    partial_reasons: list[str] = Field(default_factory=list, alias="partialReasons")


class ReportConclusion(ReportingContractModel):
    headline: str
    detail: str
    evidence_refs: list[str] = Field(alias="evidenceRefs")
    next_action: str = Field(alias="nextAction")
    next_plan_evidence_refs: list[str] = Field(alias="nextPlanEvidenceRefs")


class ReportProjection(ReportingContractModel):
    projection_version_id: UUID = Field(alias="projectionVersionId")
    report_kind: Literal["single_set", "weekly", "mock", "position_gap", "global"] = Field(
        alias="reportKind"
    )
    subject_ref: str = Field(alias="subjectRef")
    title: str
    generated_at: datetime = Field(alias="generatedAt")
    chart_contract_version: Literal["report-chart@v1"] = Field(
        default="report-chart@v1", alias="chartContractVersion"
    )
    state: Literal["ready", "partial", "empty"]
    conclusion: ReportConclusion
    charts: list[ChartSpec]
