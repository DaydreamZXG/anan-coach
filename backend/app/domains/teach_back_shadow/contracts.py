from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.teaching_packages.structured_visual import (
    SafeStructuredVisualProjection,
)


class ShadowModel(BaseModel):
    """Closed immutable model used only by the offline shadow unit."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        str_strip_whitespace=True,
    )


class ShadowPreparationState(StrEnum):
    READY = "ready"
    NEEDS_EXPLANATION = "needs_explanation"


class ShadowReasonCode(StrEnum):
    EXPLANATION_REQUIRED = "EXPLANATION_REQUIRED"
    GROUNDING_REFERENCE_OUT_OF_SCOPE = "GROUNDING_REFERENCE_OUT_OF_SCOPE"


class ShadowPreparationInput(ShadowModel):
    """Authority and user wording used to prepare two isolated prompt candidates."""

    package_fingerprint: str = Field(
        alias="packageFingerprint", pattern=r"^[0-9a-f]{64}$"
    )
    method_card_ref: str = Field(alias="methodCardRef", min_length=1, max_length=240)
    method_steps: tuple[str, ...] = Field(alias="methodSteps", min_length=1, max_length=100)
    pitfalls: tuple[str, ...] = Field(default=(), max_length=100)
    student_question_ref: str = Field(
        alias="studentQuestionRef", min_length=1, max_length=240
    )
    student_stem: str = Field(alias="studentStem", min_length=1, max_length=20_000)
    student_options: tuple[str, ...] = Field(
        alias="studentOptions", min_length=2, max_length=20
    )
    student_answer_key: tuple[str, ...] = Field(
        alias="studentAnswerKey", min_length=1, max_length=20
    )
    student_explanation: str = Field(alias="studentExplanation", max_length=50_000)
    student_visual_evidence: tuple[SafeStructuredVisualProjection, ...] = Field(
        alias="studentVisualEvidence", default=(), max_length=100
    )
    verifier_visual_evidence: tuple[SafeStructuredVisualProjection, ...] = Field(
        alias="verifierVisualEvidence", default=(), max_length=100
    )
    user_explanation: str = Field(alias="userExplanation", max_length=50_000)

    @model_validator(mode="after")
    def validate_visual_question_binding(self) -> Self:
        if (
            any(
                item.question_ref != self.student_question_ref
                for item in (*self.student_visual_evidence, *self.verifier_visual_evidence)
            )
        ):
            raise ValueError("studentVisualEvidence questionRef must match Question A")
        return self


class StudentReproductionRequest(ShadowModel):
    """Answer-free request candidate. This is the entire student-model context."""

    prompt_version: Literal["student-anan-reproduction@v2"] = Field(
        alias="promptVersion", default="student-anan-reproduction@v2"
    )
    system_prompt: str = Field(alias="systemPrompt")
    user_explanation: str = Field(alias="userExplanation")
    question_ref: str = Field(alias="questionRef")
    stem: str
    options: tuple[str, ...]
    visual_evidence: tuple[SafeStructuredVisualProjection, ...] = Field(
        alias="visualEvidence", default=(), max_length=100
    )


class GroundingVerifierRequest(ShadowModel):
    """Handle for the isolated verifier and its complete evidence whitelist."""

    prompt_version: Literal["teach-back-grounding-verifier@v1"] = Field(
        alias="promptVersion", default="teach-back-grounding-verifier@v1"
    )
    package_fingerprint: str = Field(alias="packageFingerprint")
    allowed_evidence_refs: tuple[str, ...] = Field(
        alias="allowedEvidenceRefs", min_length=2
    )
    visual_evidence: tuple[SafeStructuredVisualProjection, ...] = Field(
        alias="visualEvidence", default=(), max_length=200
    )


class ShadowPreparationResult(ShadowModel):
    """Prepared isolated requests or an explicit pre-model failure."""

    state: ShadowPreparationState
    reason_codes: tuple[ShadowReasonCode, ...] = Field(alias="reasonCodes")
    student_requests: tuple[StudentReproductionRequest, ...] = Field(
        alias="studentRequests", max_length=1
    )
    verifier_requests: tuple[GroundingVerifierRequest, ...] = Field(
        alias="verifierRequests", max_length=1
    )

    @model_validator(mode="after")
    def validate_complete_state(self) -> Self:
        if self.state == ShadowPreparationState.READY:
            if self.reason_codes or len(self.student_requests) != 1 or len(self.verifier_requests) != 1:
                raise ValueError("ready preparation requires both isolated requests and no reasons")
        elif (
            self.reason_codes != (ShadowReasonCode.EXPLANATION_REQUIRED,)
            or self.student_requests
            or self.verifier_requests
        ):
            raise ValueError("needs_explanation requires one reason and no requests")
        return self


class StudentReproductionOutput(ShadowModel):
    """Synthetic or future provider output from the student-only context."""

    state: Literal["blocked", "reproduced"]
    reproduced_steps: tuple[str, ...] = Field(alias="reproducedSteps", default=())
    attempted_option_id: str | None = Field(alias="attemptedOptionId", default=None)
    blocked_at: str | None = Field(alias="blockedAt", default=None)
    clarification_question: str | None = Field(alias="clarificationQuestion", default=None)

    @model_validator(mode="after")
    def validate_state_shape(self) -> Self:
        if self.state == "blocked":
            if not self.blocked_at or not self.clarification_question:
                raise ValueError("blocked output requires a blocked point and one clarification")
            question = self.clarification_question
            if question.count("?") + question.count("？") != 1:
                raise ValueError("blocked output must ask exactly one question")
            if self.attempted_option_id is not None:
                raise ValueError("blocked output must stop before choosing an option")
        else:
            if not self.reproduced_steps or self.blocked_at or self.clarification_question:
                raise ValueError("reproduced output requires steps and no clarification")
            if not self.attempted_option_id:
                raise ValueError("reproduced output must record its attempted option")
        return self


class GroundingClaim(ShadowModel):
    """One verifier claim whose references must be package-scoped."""

    classification: Literal["supported", "omitted", "conflict", "unknown"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence_refs: tuple[str, ...] = Field(alias="evidenceRefs", min_length=1, max_length=100)


class GroundingVerificationOutput(ShadowModel):
    """Synthetic or future verifier output before whitelist validation."""

    claims: tuple[GroundingClaim, ...] = Field(min_length=1, max_length=100)
    next_action: Literal["ready_for_transfer", "needs_clarification", "unknown"] = Field(
        alias="nextAction"
    )


class GroundingShadowCandidate(ShadowModel):
    """Displayable shadow candidate; it is not a mastery or plan fact."""

    package_fingerprint: str = Field(alias="packageFingerprint")
    claims: tuple[GroundingClaim, ...]
    next_action: Literal["ready_for_transfer", "needs_clarification", "unknown"] = Field(
        alias="nextAction"
    )


class GroundingValidationResult(ShadowModel):
    """Whitelist-validation result with no side effect or partial candidate."""

    state: Literal["displayable", "undisplayable"]
    reason_codes: tuple[ShadowReasonCode, ...] = Field(alias="reasonCodes")
    candidates: tuple[GroundingShadowCandidate, ...] = Field(max_length=1)

    @model_validator(mode="after")
    def validate_complete_state(self) -> Self:
        if self.state == "displayable":
            if self.reason_codes or len(self.candidates) != 1:
                raise ValueError("displayable result requires one complete candidate")
        elif (
            self.reason_codes != (ShadowReasonCode.GROUNDING_REFERENCE_OUT_OF_SCOPE,)
            or self.candidates
        ):
            raise ValueError("undisplayable result requires an out-of-scope reason and no candidate")
        return self
