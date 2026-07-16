from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QualityModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class RecordQuestionQualityDecisionCommand(QualityModel):
    question_id: str = Field(alias="questionId", min_length=1, max_length=240)
    content_version_id: str = Field(
        alias="contentVersionId", pattern=r"^[0-9a-f]{64}$"
    )
    decision: Literal[
        "needs_more_evidence", "confirmed_valid", "confirmed_invalid"
    ]
    dispute_ids: list[UUID] = Field(alias="disputeIds", min_length=1, max_length=100)
    reason_code: str = Field(alias="reasonCode", min_length=2, max_length=120)
    rationale: str = Field(min_length=2, max_length=4000)
    authority_version: str = Field(alias="authorityVersion", min_length=3, max_length=160)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=100)

    @model_validator(mode="after")
    def normalize(self) -> "RecordQuestionQualityDecisionCommand":
        self.question_id = self.question_id.strip()
        self.reason_code = self.reason_code.strip()
        self.rationale = self.rationale.strip()
        self.authority_version = self.authority_version.strip()
        self.evidence_refs = [item.strip() for item in self.evidence_refs]
        if (
            not self.question_id
            or not self.reason_code
            or not self.rationale
            or not self.authority_version
            or any(not item for item in self.evidence_refs)
        ):
            raise ValueError("question-quality decision fields cannot be blank")
        if len(self.dispute_ids) != len(set(self.dispute_ids)):
            raise ValueError("question-quality dispute identifiers must be unique")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("question-quality evidence references must be unique")
        return self


class QuestionQualityDecisionProjection(QualityModel):
    decision_version_id: UUID = Field(alias="decisionVersionId")
    decision_id: UUID = Field(alias="decisionId")
    question_id: str = Field(alias="questionId")
    content_version_id: str = Field(alias="contentVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    decision: Literal[
        "needs_more_evidence", "confirmed_valid", "confirmed_invalid"
    ]
    reason_code: str = Field(alias="reasonCode")
    authority_version: str = Field(alias="authorityVersion")
    dispute_ids: list[UUID] = Field(alias="disputeIds")
    evidence_refs: list[str] = Field(alias="evidenceRefs")
    created_at: datetime = Field(alias="createdAt")
