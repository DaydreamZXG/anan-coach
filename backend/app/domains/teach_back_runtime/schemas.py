from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.teaching_packages import SafeStructuredVisualProjection


class TeachBackModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class StartTeachBackCommand(TeachBackModel):
    command_id: str = Field(alias="commandId", min_length=8, max_length=260)
    source_question_id: str = Field(alias="sourceQuestionId", min_length=1, max_length=240)
    user_explanation: str = Field(alias="userExplanation", min_length=1, max_length=50_000)


class TeachBackTransferCommand(TeachBackModel):
    command_id: str = Field(alias="commandId", min_length=8, max_length=260)
    revision: int = Field(ge=1)
    selected_option_ids: list[str] = Field(
        alias="selectedOptionIds", min_length=1, max_length=12
    )
    confidence: Literal["low", "medium", "high"]

    @field_validator("selected_option_ids")
    @classmethod
    def unique_option_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(values) or len(set(normalized)) != len(normalized):
            raise ValueError("selectedOptionIds must be non-empty and unique")
        return normalized


class TeachBackStudentResult(TeachBackModel):
    state: Literal["blocked", "reproduced"]
    reproduced_steps: list[str] = Field(alias="reproducedSteps")
    attempted_option_id: str | None = Field(alias="attemptedOptionId", default=None)
    blocked_at: str | None = Field(alias="blockedAt", default=None)
    clarification_question: str | None = Field(
        alias="clarificationQuestion", default=None
    )


class TeachBackGroundingClaim(TeachBackModel):
    classification: Literal["supported", "omitted", "conflict", "unknown"]
    summary: str


class TeachBackOption(TeachBackModel):
    option_id: str = Field(alias="optionId")
    label: str
    text: str


class TeachBackQuestion(TeachBackModel):
    question_id: str = Field(alias="questionId")
    stem: str
    options: list[TeachBackOption]
    visual_evidence: list[SafeStructuredVisualProjection] = Field(
        alias="visualEvidence", default_factory=list
    )


class TeachBackSessionProjection(TeachBackModel):
    contract_version: Literal["grounded-teach-back@v1"] = Field(
        alias="contractVersion", default="grounded-teach-back@v1"
    )
    session_id: UUID = Field(alias="sessionId")
    revision: int = Field(ge=1)
    state: Literal["ready_for_transfer", "blocked", "provider_failed", "completed"]
    review_id: UUID = Field(alias="reviewId")
    source_question_id: str = Field(alias="sourceQuestionId")
    coach_message: str = Field(alias="coachMessage")
    student: TeachBackStudentResult | None = None
    grounding: list[TeachBackGroundingClaim] = Field(default_factory=list)
    transfer_question: TeachBackQuestion | None = Field(
        alias="transferQuestion", default=None
    )
    created_at: datetime = Field(alias="createdAt")


class TeachBackTransferReceipt(TeachBackModel):
    contract_version: Literal["teach-back-transfer-receipt@v1"] = Field(
        alias="contractVersion", default="teach-back-transfer-receipt@v1"
    )
    session_id: UUID = Field(alias="sessionId")
    revision: int = Field(ge=2)
    state: Literal["completed"] = "completed"
    correct: bool
    mastery_version_id: UUID = Field(alias="masteryVersionId")
    mastery_state: str = Field(alias="masteryState")
    verification_stage: str = Field(alias="verificationStage")
    due_at: datetime | None = Field(alias="dueAt", default=None)
    coach_message: str = Field(alias="coachMessage")
