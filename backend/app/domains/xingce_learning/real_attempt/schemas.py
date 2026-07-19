from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.planning.schemas import TodayDashboard
from app.domains.task_content.schemas import (
    MaterialPosition,
    SafeQuestionBundle,
)


class XingceModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class StartTaskSessionCommand(XingceModel):
    plan_task_id: UUID = Field(alias="planTaskId")


class AnswerCheckpoint(XingceModel):
    question_id: str = Field(alias="questionId", min_length=1, max_length=240)
    selected_option_ids: list[str] = Field(
        default_factory=list,
        alias="selectedOptionIds",
        max_length=12,
    )
    eliminated_option_ids: list[str] = Field(
        default_factory=list,
        alias="eliminatedOptionIds",
        max_length=12,
    )
    confidence: Literal["low", "medium", "high"] | None = None
    marked: bool = False
    skipped: bool = False
    elapsed_seconds: int = Field(alias="elapsedSeconds", ge=0, le=86_400)
    draft_text: str = Field(default="", alias="draftText", max_length=4_000)
    material_position: MaterialPosition | None = Field(
        default=None, alias="materialPosition"
    )

    @model_validator(mode="after")
    def validate_answer_shape(self) -> "AnswerCheckpoint":
        selected = [item.strip().upper() for item in self.selected_option_ids]
        eliminated = [item.strip().upper() for item in self.eliminated_option_ids]
        if any(not item for item in selected + eliminated):
            raise ValueError("option identifiers cannot be blank")
        if len(set(selected)) != len(selected) or len(set(eliminated)) != len(
            eliminated
        ):
            raise ValueError("option identifiers must be unique")
        if set(selected) & set(eliminated):
            raise ValueError("selected and eliminated options cannot overlap")
        if self.skipped and selected:
            raise ValueError("a skipped answer cannot select an option")
        self.selected_option_ids = selected
        self.eliminated_option_ids = eliminated
        self.draft_text = self.draft_text.strip()
        return self


class SaveCheckpointCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    current_question_id: str = Field(
        alias="currentQuestionId", min_length=1, max_length=240
    )
    answers: list[AnswerCheckpoint] = Field(min_length=1, max_length=100)
    total_elapsed_seconds: int = Field(alias="totalElapsedSeconds", ge=0, le=86_400)

    @model_validator(mode="after")
    def unique_questions(self) -> "SaveCheckpointCommand":
        ids = [answer.question_id for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("checkpoint answers must contain unique question IDs")
        return self


class SubmitTaskSessionCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    total_elapsed_seconds: int = Field(alias="totalElapsedSeconds", ge=0, le=86_400)


class ItemInterpretation(XingceModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    question_id: str = Field(alias="questionId", min_length=1, max_length=240)
    interpretation: str = Field(min_length=1, max_length=80)


class ReflectionCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    raw_text: str = Field(alias="rawText", min_length=1, max_length=4_000)
    tags: list[str] = Field(min_length=1, max_length=12)
    strategy_note: str | None = Field(
        default=None, alias="strategyNote", max_length=2_000
    )
    doubt_note: str | None = Field(default=None, alias="doubtNote", max_length=2_000)
    voice_artifact_version_ids: list[UUID] = Field(
        default_factory=list,
        alias="voiceArtifactVersionIds",
        max_length=3,
    )
    item_interpretations: list[ItemInterpretation] = Field(
        default_factory=list,
        alias="itemInterpretations",
    )

    @model_validator(mode="after")
    def normalize_user_voice(self) -> "ReflectionCommand":
        self.raw_text = self.raw_text.strip()
        tags = [tag.strip() for tag in self.tags]
        if (
            not self.raw_text
            or any(not tag for tag in tags)
            or len(tags) != len(set(tags))
        ):
            raise ValueError("reflection text and unique non-blank tags are required")
        self.tags = tags
        self.strategy_note = (self.strategy_note or "").strip() or None
        self.doubt_note = (self.doubt_note or "").strip() or None
        if len(self.voice_artifact_version_ids) != len(
            set(self.voice_artifact_version_ids)
        ):
            raise ValueError("voice artifact version identifiers must be unique")
        return self


class CauseFollowupCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    candidate_version_id: UUID = Field(alias="candidateVersionId")
    answer: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def normalize_answer(self) -> "CauseFollowupCommand":
        self.answer = self.answer.strip()
        if not self.answer:
            raise ValueError("followup answer cannot be blank")
        return self


class CauseDecisionCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    candidate_version_id: UUID | None = Field(default=None, alias="candidateVersionId")
    decision: Literal["confirm", "reject", "replace", "defer", "skip"]
    replacement_cause: str | None = Field(
        default=None, alias="replacementCause", max_length=2_000
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "CauseDecisionCommand":
        self.replacement_cause = (self.replacement_cause or "").strip() or None
        if self.decision == "replace" and self.replacement_cause is None:
            raise ValueError("replace requires replacementCause")
        if self.decision != "replace" and self.replacement_cause is not None:
            raise ValueError("only replace can include replacementCause")
        if self.decision == "skip" and self.candidate_version_id is not None:
            raise ValueError("legacy skip cannot bind a candidate")
        if self.decision != "skip" and self.candidate_version_id is None:
            raise ValueError("cause decision must bind candidateVersionId")
        return self


class ReportQuestionDisputeCommand(XingceModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    question_id: str = Field(alias="questionId", min_length=1, max_length=240)
    content_version_id: str = Field(
        alias="contentVersionId", min_length=64, max_length=64
    )
    category: Literal[
        "stem_error",
        "option_error",
        "answer_error",
        "material_mismatch",
        "image_unreadable",
        "other",
    ]
    description: str = Field(min_length=2, max_length=2_000)

    @model_validator(mode="after")
    def normalize_description(self) -> "ReportQuestionDisputeCommand":
        self.description = self.description.strip()
        if len(self.description) < 2:
            raise ValueError("description must contain at least two characters")
        return self


class QuestionDisputeReceipt(XingceModel):
    dispute_id: UUID = Field(alias="disputeId")
    session_id: UUID = Field(alias="sessionId")
    question_id: str = Field(alias="questionId")
    content_version_id: str = Field(alias="contentVersionId")
    category: Literal[
        "stem_error",
        "option_error",
        "answer_error",
        "material_mismatch",
        "image_unreadable",
        "other",
    ]
    description: str
    evidence_attached: Literal[True] = Field(default=True, alias="evidenceAttached")
    created_at: datetime = Field(alias="createdAt")


class XingceResultItem(XingceModel):
    question_id: str = Field(alias="questionId")
    result_code: Literal[
        "correct", "incorrect", "timeout", "guessed_correct", "skipped"
    ] = Field(alias="resultCode")
    selected_option_ids: list[str] = Field(alias="selectedOptionIds")
    correct_option_ids: list[str] = Field(alias="correctOptionIds")
    fact_contract_version: Literal["xingce-result-facts@v1"] | None = Field(
        default=None,
        alias="factContractVersion",
    )
    objective_verdict: Literal["correct", "incorrect", "unanswered"] | None = Field(
        default=None,
        alias="objectiveVerdict",
    )
    disposition: Literal["answered", "active_abandon"] | None = None
    confidence_evidence: Literal[
        "low", "medium", "high", "unreported"
    ] | None = Field(default=None, alias="confidenceEvidence")
    timing_evidence: "XingceTimingEvidence | None" = Field(
        default=None,
        alias="timingEvidence",
    )
    explanation: str | None = None
    explanation_source: str | None = Field(default=None, alias="explanationSource")


class XingceTimingEvidence(XingceModel):
    status: Literal["within_advisory", "advisory_exceeded"]
    elapsed_seconds: int = Field(alias="elapsedSeconds", ge=0, le=86_400)
    advisory_seconds: int = Field(alias="advisorySeconds", ge=1, le=86_400)


class XingceGroupSummary(XingceModel):
    outcome: Literal["attained", "not_attained", "interrupted"]
    genuine_correct: int = Field(alias="genuineCorrect", ge=0)
    guessed_correct: int = Field(alias="guessedCorrect", ge=0)
    method_correct_calculation_error: int = Field(
        alias="methodCorrectCalculationError",
        ge=0,
    )
    advisory_exceeded: int = Field(alias="advisoryExceeded", ge=0)
    active_abandon: int = Field(alias="activeAbandon", ge=0)
    pending_reflection: bool = Field(alias="pendingReflection")
    pending_cause: bool = Field(alias="pendingCause")
    immediate_verification: bool = Field(alias="immediateVerification")
    scheduled: bool


class XingceAttemptResult(XingceModel):
    contract_version: Literal["xingce-attempt-result@v2"] | None = Field(
        default=None,
        alias="contractVersion",
    )
    attempt_id: UUID = Field(alias="attemptId")
    result_code: Literal[
        "correct", "incorrect", "timeout", "guessed_correct", "skipped", "mixed"
    ] = Field(alias="resultCode")
    correct_count: int = Field(alias="correctCount", ge=0)
    total_count: int = Field(alias="totalCount", ge=1)
    items: list[XingceResultItem]
    group_summary: XingceGroupSummary | None = Field(
        default=None,
        alias="groupSummary",
    )


class ReflectionVersionProjection(XingceModel):
    reflection_version_id: UUID = Field(alias="reflectionVersionId")
    raw_text: str = Field(alias="rawText")
    tags: list[str]
    strategy_note: str | None = Field(default=None, alias="strategyNote")
    doubt_note: str | None = Field(default=None, alias="doubtNote")
    voice_artifact_version_ids: list[UUID] = Field(
        default_factory=list,
        alias="voiceArtifactVersionIds",
    )
    item_interpretations: list[ItemInterpretation] = Field(
        default_factory=list,
        alias="itemInterpretations",
    )
    version_no: int = Field(alias="versionNo", ge=1)


class CauseDecisionVersionProjection(XingceModel):
    decision_version_id: UUID = Field(alias="decisionVersionId")
    candidate_version_id: UUID | None = Field(default=None, alias="candidateVersionId")
    confirmed_cause_version_id: UUID | None = Field(
        default=None,
        alias="confirmedCauseVersionId",
    )
    decision: Literal["confirm", "reject", "replace", "defer", "skip"]
    replacement_cause: str | None = Field(default=None, alias="replacementCause")
    version_no: int = Field(alias="versionNo", ge=1)


class AICauseCandidateState(XingceModel):
    state: Literal[
        "unavailable",
        "processing",
        "candidate_ready",
        "followup_required",
        "evidence_insufficient",
        "terminated",
    ]
    candidate_version_id: UUID | None = Field(
        default=None,
        alias="candidateVersionId",
    )
    reason: str | None = None
    cause_code: str | None = Field(default=None, alias="causeCode")
    summary: str | None = None
    improvement: str | None = None
    question: str | None = None
    rationale: str | None = None
    explanation: str | None = None
    missing_evidence: list[str] = Field(default_factory=list, alias="missingEvidence")
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    limitations: list[str] = Field(default_factory=list)
    quota_state: Literal["reserved", "consumed", "not_charged"] = Field(
        default="reserved",
        alias="quotaState",
    )
    entitlement_policy_version: Literal["metered-model-round@v1"] = Field(
        default="metered-model-round@v1",
        alias="entitlementPolicyVersion",
    )


class AttemptReviewProjection(XingceModel):
    review_id: UUID = Field(alias="reviewId")
    revision: int = Field(ge=1)
    state: Literal[
        "reflection_required",
        "cause_candidate_pending",
        "cause_followup_required",
        "cause_decision_required",
        "cause_evidence_insufficient",
        "cause_agent_terminated",
        "immediate_verification_due",
        "mastery_recorded",
    ]
    ai_candidate: AICauseCandidateState = Field(alias="aiCandidate")
    reflection: ReflectionVersionProjection | None = None
    cause_decision: CauseDecisionVersionProjection | None = Field(
        default=None,
        alias="causeDecision",
    )


class XingceCompletionReceipt(XingceModel):
    receipt_id: UUID = Field(alias="receiptId")
    settlement_status: Literal["completed", "late_evidence_linked"] = Field(
        alias="settlementStatus"
    )
    plan: TodayDashboard


class VerificationOutcomeProjection(XingceModel):
    outcome: Literal["passed", "not_passed", "disputed_or_invalidated"]
    evidence_state: Literal["valid", "pending_dispute", "confirmed_invalidated"] = (
        Field(alias="evidenceState")
    )
    source_mastery_version_id: UUID = Field(alias="sourceMasteryVersionId")
    verification_stage: Literal["immediate", "first_review", "delayed_review"] = (
        Field(alias="verificationStage")
    )


class TaskSessionProjection(XingceModel):
    session_id: UUID = Field(alias="sessionId")
    plan_task_id: UUID = Field(alias="planTaskId")
    revision: int = Field(ge=1)
    state: Literal[
        "answering",
        "submitted",
        "reflection_required",
        "cause_candidate_processing",
        "cause_followup_required",
        "cause_decision_required",
        "cause_evidence_insufficient",
        "cause_agent_terminated",
        "completed",
    ]
    mode: Literal["practice", "targeted", "baseline", "verification"]
    bundle: SafeQuestionBundle
    current_question_id: str = Field(alias="currentQuestionId")
    answers: list[AnswerCheckpoint]
    total_elapsed_seconds: int = Field(alias="totalElapsedSeconds", ge=0)
    result: XingceAttemptResult | None = None
    verification_outcome: VerificationOutcomeProjection | None = Field(
        default=None,
        alias="verificationOutcome",
    )
    review: AttemptReviewProjection | None = None
    completion: XingceCompletionReceipt | None = None
