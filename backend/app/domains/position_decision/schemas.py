from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.qualification.schemas import EligibilityEvaluation
from app.domains.positions.schemas import PositionSummary, TargetScoreRange


class PositionDecisionBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


RiskPreference = Literal["conservative", "balanced", "aggressive"]
DecisionBucket = Literal["safe", "balanced", "stretch"]
DecisionRiskLevel = Literal["low", "medium", "high", "unknown"]
DecisionScoreBasisType = Literal["position_target_score", "regional_major_prediction"]
DecisionScoreConfidence = Literal["high", "medium", "low", "unknown"]


class PositionGoal(PositionDecisionBaseModel):
    goal_version_id: str = Field(alias="goalVersionId")
    position_id: str = Field(alias="positionId")
    role: Literal["primary", "balanced", "backup"]
    status: Literal["candidate", "confirmed", "retired"]
    target_score: TargetScoreRange = Field(alias="targetScore")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    eligibility_snapshot_id: str = Field(alias="eligibilitySnapshotId")
    position_data_version: str = Field(alias="positionDataVersion")
    created_at: datetime = Field(alias="createdAt")


class PrimaryGoalPlanImpact(PositionDecisionBaseModel):
    requires_plan_rebuild: bool = Field(alias="requiresPlanRebuild")
    change_type: Literal["goal_selected", "goal_changed"] = Field(alias="changeType")
    position_changed: bool = Field(alias="positionChanged")
    previous_target_score: float | None = Field(default=None, alias="previousTargetScore")
    next_target_score: float = Field(alias="nextTargetScore", ge=0, le=300)
    target_score_delta: float | None = Field(default=None, alias="targetScoreDelta")
    invalidates: list[Literal["daily_plan", "weekly_plan", "score_gap"]]


class PrimaryGoalPreviewRequest(PositionDecisionBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    position_id: UUID = Field(alias="positionId")
    eligibility_snapshot_id: UUID = Field(alias="eligibilitySnapshotId")


class PrimaryGoalPreview(PositionDecisionBaseModel):
    preview_id: str = Field(alias="previewId")
    position_id: str = Field(alias="positionId")
    eligibility_snapshot_id: str = Field(alias="eligibilitySnapshotId")
    current_goal_version_id: str | None = Field(default=None, alias="currentGoalVersionId")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    position_data_version: str = Field(alias="positionDataVersion")
    target_score: TargetScoreRange = Field(alias="targetScore")
    plan_impact: PrimaryGoalPlanImpact = Field(alias="planImpact")
    expires_at: datetime = Field(alias="expiresAt")
    created_at: datetime = Field(alias="createdAt")


class PositionFit(PositionDecisionBaseModel):
    position: PositionSummary
    eligibility: EligibilityEvaluation
    current_score_lower: float | None = Field(default=None, alias="currentScoreLower")
    current_score_upper: float | None = Field(default=None, alias="currentScoreUpper")
    target_score: TargetScoreRange = Field(alias="targetScore")
    score_gap_lower: float | None = Field(default=None, alias="scoreGapLower")
    score_gap_upper: float | None = Field(default=None, alias="scoreGapUpper")
    evidence_codes: list[str] = Field(alias="evidenceCodes")
    pending_confirmation_codes: list[str] = Field(alias="pendingConfirmationCodes")


class PrimaryGoalConfirmRequest(PositionDecisionBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    preview_id: UUID = Field(alias="previewId")
    change_reason: str = Field(alias="changeReason", min_length=2, max_length=240)


class PrimaryGoalConfirmResponse(PositionDecisionBaseModel):
    goal: PositionGoal
    plan_impact: PrimaryGoalPlanImpact = Field(alias="planImpact")


class PositionDecisionScoreBasis(PositionDecisionBaseModel):
    basis_type: DecisionScoreBasisType = Field(alias="basisType")
    target_score: float = Field(alias="targetScore")
    sample_count: int = Field(alias="sampleCount")
    sample_position_count: int = Field(alias="samplePositionCount")
    latest_score_year: int | None = Field(default=None, alias="latestScoreYear")
    average_entry_score: float | None = Field(default=None, alias="averageEntryScore")
    safety_margin: float = Field(alias="safetyMargin")
    confidence: DecisionScoreConfidence
    matched_dimensions: list[str] = Field(alias="matchedDimensions")
    evidence_summary: str = Field(alias="evidenceSummary")


class PositionDecisionItem(PositionDecisionBaseModel):
    position: PositionSummary
    bucket: DecisionBucket
    decision_label: str = Field(alias="decisionLabel")
    decision_target_score: float | None = Field(default=None, alias="decisionTargetScore")
    decision_score_basis: PositionDecisionScoreBasis | None = Field(
        default=None,
        alias="decisionScoreBasis",
    )
    score_gap: float | None = Field(default=None, alias="scoreGap")
    admission_chance_score: int = Field(alias="admissionChanceScore")
    chance_label: str = Field(alias="chanceLabel")
    risk_level: DecisionRiskLevel = Field(alias="riskLevel")
    risk_tags: list[str] = Field(alias="riskTags")
    reasons: list[str]
    next_action: str = Field(alias="nextAction")


class PositionDecisionBucket(PositionDecisionBaseModel):
    bucket: DecisionBucket
    title: str
    subtitle: str
    items: list[PositionDecisionItem]


class PositionDecisionResponse(PositionDecisionBaseModel):
    generated_at: datetime = Field(alias="generatedAt")
    current_score: float = Field(alias="currentScore")
    risk_preference: RiskPreference = Field(alias="riskPreference")
    summary: str
    recommendation_buckets: list[PositionDecisionBucket] = Field(
        alias="recommendationBuckets"
    )
    unverified_conditions: list[str] = Field(alias="unverifiedConditions")
    next_actions: list[str] = Field(alias="nextActions")
