from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RetakeModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RetakeEnrollmentRequest(RetakeModel):
    source_score_evidence_id: UUID = Field(alias="sourceScoreEvidenceId")


class RetakeScoreEvidenceOption(RetakeModel):
    evidence_id: UUID = Field(alias="evidenceId")
    exam_year: int = Field(alias="examYear", ge=2000, le=2100)
    source_type: Literal["self_report", "official_exam"] = Field(alias="sourceType")
    xingce_score: float | None = Field(default=None, alias="xingceScore")
    essay_score: float | None = Field(default=None, alias="essayScore")
    total_score: float | None = Field(default=None, alias="totalScore")
    occurred_at: datetime = Field(alias="occurredAt")


class RetakeLossStructure(RetakeModel):
    xingce_score: float | None = Field(default=None, alias="xingceScore")
    essay_score: float | None = Field(default=None, alias="essayScore")
    available_tracks: list[Literal["xingce", "essay"]] = Field(alias="availableTracks")
    training_focus: list[str] = Field(alias="trainingFocus")
    limitation: str


class RetakeProgramView(RetakeModel):
    program_version_id: UUID = Field(alias="programVersionId")
    source_score_evidence_id: UUID = Field(alias="sourceScoreEvidenceId")
    source_exam_year: int = Field(alias="sourceExamYear")
    target_exam_year: int = Field(alias="targetExamYear")
    state: Literal["active", "completed", "withdrawn"]
    loss_structure: RetakeLossStructure = Field(alias="lossStructure")
    refund_uses_score: Literal[False] = Field(alias="refundUsesScore")
    reward_uses_score: Literal[False] = Field(alias="rewardUsesScore")
    training_uses_score: Literal[True] = Field(alias="trainingUsesScore")
    created_at: datetime = Field(alias="createdAt")


class RetakeWorkspace(RetakeModel):
    target_exam_year: int = Field(alias="targetExamYear", ge=2001, le=2100)
    eligible_score_evidence: list[RetakeScoreEvidenceOption] = Field(
        alias="eligibleScoreEvidence"
    )
    program: RetakeProgramView | None = None
    score_use_boundary: str = Field(alias="scoreUseBoundary")


class RetakeEnrollmentResult(RetakeModel):
    program: RetakeProgramView
