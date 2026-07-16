from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommunityModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Track(StrEnum):
    XINGCE = "xingce"
    ESSAY = "essay"


class SourceType(StrEnum):
    TEXT = "text"
    VOICE_TRANSCRIPT = "voice_transcript"
    HANDWRITING_EXTRACTION = "handwriting_extraction"
    REFLECTION = "reflection"


class RightsDeclaration(StrEnum):
    ORIGINAL = "original"
    AUTHORIZED_DERIVATIVE = "authorized_derivative"
    PRIVATE_THIRD_PARTY = "private_third_party"


class CardState(StrEnum):
    PRIVATE_DRAFT = "private_draft"
    AUTOMATED_REVIEW = "automated_review"
    TRIAL = "trial"
    COMMUNITY_VALIDATED = "community_validated"
    REVISION_REQUIRED = "revision_required"
    REJECTED = "rejected"
    RESTRICTED = "restricted"
    RETIRED = "retired"


class CandidateStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class ReviewDimension(StrEnum):
    DUPLICATE = "duplicate"
    COPYRIGHT = "copyright"
    SAFETY = "safety"
    KNOWLEDGE = "knowledge"
    ANSWER_CONSISTENCY = "answer_consistency"


class ReviewCheckOutcome(StrEnum):
    PASSED = "passed"
    NEEDS_REVISION = "needs_revision"
    FAILED = "failed"


class DraftCreate(CommunityModel):
    source_type: SourceType = Field(alias="sourceType")
    source_text: str = Field(alias="sourceText", min_length=1)
    track: Track
    applicability: list[str]
    source_artifact_id: str | None = Field(default=None, alias="sourceArtifactId")
    reflection_version_id: str | None = Field(
        default=None, alias="reflectionVersionId"
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1)

    @field_validator("source_text")
    @classmethod
    def reject_blank_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sourceText cannot be blank")
        return value

    @model_validator(mode="after")
    def require_version_bound_source(self) -> "DraftCreate":
        if self.source_type == SourceType.TEXT:
            if self.source_artifact_id is not None or self.reflection_version_id is not None:
                raise ValueError("direct text cannot bind an artifact or reflection")
        elif self.source_type == SourceType.VOICE_TRANSCRIPT:
            if self.source_artifact_id is None or self.reflection_version_id is None:
                raise ValueError(
                    "voice transcript requires sourceArtifactId and reflectionVersionId"
                )
        elif self.source_type == SourceType.HANDWRITING_EXTRACTION:
            if self.source_artifact_id is None or self.reflection_version_id is not None:
                raise ValueError("handwriting extraction requires sourceArtifactId")
        elif self.source_type == SourceType.REFLECTION:
            if self.reflection_version_id is None or self.source_artifact_id is not None:
                raise ValueError("reflection requires reflectionVersionId")
        return self


class DraftReceipt(CommunityModel):
    card_id: str = Field(alias="cardId")
    version_id: str = Field(alias="versionId")
    status: Literal[CardState.PRIVATE_DRAFT] = CardState.PRIVATE_DRAFT
    candidate_status: Literal[
        CandidateStatus.PROCESSING,
        CandidateStatus.READY,
        CandidateStatus.FAILED,
    ] = Field(alias="candidateStatus")


class StructureCandidate(CommunityModel):
    candidate_id: str = Field(alias="candidateId")
    base_version_id: str = Field(alias="baseVersionId")
    title: str
    recognition: str
    method_steps: list[str] = Field(alias="methodSteps")
    source_span_refs: list[str] = Field(alias="sourceSpanRefs")
    status: CandidateStatus = CandidateStatus.READY
    failure_code: str | None = Field(default=None, alias="failureCode")


class CandidateConfirm(CommunityModel):
    card_id: str = Field(alias="cardId")
    base_version_id: str = Field(alias="baseVersionId")
    candidate_id: str = Field(alias="candidateId")
    title: str = Field(min_length=1)
    recognition: str = Field(min_length=1)
    method_steps: list[str] = Field(alias="methodSteps", min_length=1)
    applicability: list[str]
    exclusions: list[str]
    pitfalls: list[str]
    rights_declaration: RightsDeclaration = Field(alias="rightsDeclaration")

    @field_validator("title", "recognition")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("confirmed text cannot be blank")
        return value

    @field_validator("method_steps")
    @classmethod
    def reject_blank_steps(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("methodSteps cannot contain blank values")
        return values


class ConfirmReceipt(CommunityModel):
    card_id: str = Field(alias="cardId")
    version_id: str = Field(alias="versionId")
    version_number: int = Field(alias="versionNumber", ge=1)
    author_confirmed_at: datetime = Field(alias="authorConfirmedAt")


class CardSubmit(CommunityModel):
    card_id: str = Field(alias="cardId")
    version_id: str = Field(alias="versionId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1)


class ReviewCheck(CommunityModel):
    dimension: ReviewDimension
    outcome: ReviewCheckOutcome
    reason_code: str = Field(alias="reasonCode", min_length=1)
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")

    @property
    def code(self) -> str:
        return f"{self.dimension.value}:{self.outcome.value}:{self.reason_code}"


class ReviewOutcome(CommunityModel):
    card_id: str = Field(alias="cardId")
    version_id: str = Field(alias="versionId")
    status: Literal[
        CardState.TRIAL,
        CardState.REVISION_REQUIRED,
        CardState.REJECTED,
    ]
    check_codes: list[str] = Field(alias="checkCodes", min_length=5, max_length=5)
    review_id: str = Field(alias="reviewId")
    policy_version: str = Field(alias="policyVersion")


class ExposureCreate(CommunityModel):
    card_version_id: str = Field(alias="cardVersionId")
    applicability_snapshot: list[str] = Field(alias="applicabilitySnapshot")
    ranking_policy_version: str = Field(alias="rankingPolicyVersion")


class ExposureReceipt(CommunityModel):
    exposure_id: str = Field(alias="exposureId")
    card_id: str = Field(alias="cardId")
    version_id: str = Field(alias="versionId")
    opted_in_at: datetime = Field(alias="optedInAt")


class TrialComplete(CommunityModel):
    exposure_id: str = Field(alias="exposureId")
    question_attempt_id: str = Field(alias="questionAttemptId", min_length=1)
    question_version_id: str = Field(alias="questionVersionId", min_length=1)


class TrialReceipt(CommunityModel):
    trial_id: str = Field(alias="trialId")
    feedback_eligible: bool = Field(alias="feedbackEligible")
    outcome_code: str = Field(alias="outcomeCode")
    delayed_verification_due_at: datetime | None = Field(
        default=None, alias="delayedVerificationDueAt"
    )


class DelayedVerificationRecord(CommunityModel):
    trial_id: str = Field(alias="trialId")
    question_attempt_id: str = Field(alias="questionAttemptId", min_length=1)
    question_version_id: str = Field(alias="questionVersionId", min_length=1)


class DelayedVerificationReceipt(CommunityModel):
    verification_id: str = Field(alias="verificationId")
    recorded: bool


class FeedbackRecord(CommunityModel):
    trial_id: str = Field(alias="trialId")
    vote: Literal["helpful", "not_helpful"]
    reason_code: Literal[
        "clear",
        "transferable",
        "too_specific",
        "unclear",
        "incorrect",
        "unsafe",
    ] = Field(alias="reasonCode")


class FeedbackReceipt(CommunityModel):
    vote_id: str = Field(alias="voteId")
    recorded: bool


class ReportCreate(CommunityModel):
    card_version_id: str = Field(alias="cardVersionId")
    category: Literal[
        "copyright",
        "incorrect",
        "unsafe",
        "advertising",
        "contact_information",
        "other_fixed",
    ]
    evidence_ref: str = Field(alias="evidenceRef", min_length=1)


class ReportReceipt(CommunityModel):
    report_id: str = Field(alias="reportId")
    card_status: Literal[
        CardState.TRIAL,
        CardState.COMMUNITY_VALIDATED,
        CardState.RESTRICTED,
    ] = Field(alias="cardStatus")
    decision: Literal["pending", "confirmed", "rejected"] = "pending"


class ReportDecisionReceipt(CommunityModel):
    report_id: str = Field(alias="reportId")
    report_decision_id: str = Field(alias="reportDecisionId")
    decision: Literal["confirmed", "rejected"]
    card_status: Literal[
        CardState.TRIAL,
        CardState.COMMUNITY_VALIDATED,
        CardState.RESTRICTED,
    ] = Field(alias="cardStatus")
    beneficiary_user_id: str | None = Field(alias="beneficiaryUserId")
    policy_version: str = Field(alias="policyVersion")


class RetireCard(CommunityModel):
    card_id: str = Field(alias="cardId")
    head_version_id: str = Field(alias="headVersionId")
    reason_code: str = Field(alias="reasonCode", min_length=1)


class RetireReceipt(CommunityModel):
    card_id: str = Field(alias="cardId")
    status: Literal[CardState.RETIRED] = CardState.RETIRED


class CardVersionView(CommunityModel):
    version_id: str = Field(alias="versionId")
    card_id: str = Field(alias="cardId")
    version_number: int = Field(alias="versionNumber")
    base_version_id: str | None = Field(alias="baseVersionId")
    title: str | None
    recognition: str | None
    method_steps: list[str] = Field(alias="methodSteps")
    applicability: list[str]
    exclusions: list[str]
    pitfalls: list[str]
    rights_declaration: RightsDeclaration | None = Field(alias="rightsDeclaration")
    author_confirmed_at: datetime | None = Field(alias="authorConfirmedAt")


class Workspace(CommunityModel):
    card_id: str = Field(alias="cardId")
    status: CardState
    head_version_id: str = Field(alias="headVersionId")
    version_ids: list[str] = Field(alias="versionIds")
    candidate_status: CandidateStatus = Field(alias="candidateStatus")
    review_status: str = Field(alias="reviewStatus")
    versions: list[CardVersionView] = Field(default_factory=list)


class DiscoveryRequest(CommunityModel):
    track: Track
    applicability: list[str]
    cursor: str | None = None


class DiscoveryCard(CommunityModel):
    card_version_id: str = Field(alias="cardVersionId")
    title: str
    recognition: str
    method_steps: list[str] = Field(alias="methodSteps")
    applicability: list[str]
    exclusions: list[str]
    pitfalls: list[str]
    conservative_score: float | None = Field(alias="conservativeScore")
    evidence_count: int = Field(alias="evidenceCount", ge=0)
    risk_flags: list[str] = Field(alias="riskFlags")
    evidence_policy_version: str = Field(alias="evidencePolicyVersion")
    is_author: Literal[False] = Field(default=False, alias="isAuthor")


class PublicCardDetail(CommunityModel):
    card_version_id: str = Field(alias="cardVersionId")
    title: str
    recognition: str
    method_steps: list[str] = Field(alias="methodSteps")
    applicability: list[str]
    exclusions: list[str]
    pitfalls: list[str]
    status: Literal[CardState.TRIAL, CardState.COMMUNITY_VALIDATED]
    evidence_count: int = Field(alias="evidenceCount", ge=0)
    delayed_verified_count: int = Field(alias="delayedVerifiedCount", ge=0)
    conservative_score: float | None = Field(alias="conservativeScore")
    risk_flags: list[str] = Field(alias="riskFlags")
    policy_version: str = Field(alias="policyVersion")


class DiscoveryResponse(CommunityModel):
    card_version_ids: list[str] = Field(alias="cardVersionIds")
    ranking_policy_version: str = Field(alias="rankingPolicyVersion")
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    cards: list[DiscoveryCard] = Field(default_factory=list)


class ContributionsResponse(CommunityModel):
    card_ids: list[str] = Field(alias="cardIds")
    reward_entry_ids: list[str] = Field(alias="rewardEntryIds")
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class MilestoneQualified(CommunityModel):
    event_id: str = Field(alias="eventId")
    author_id: str = Field(alias="authorId")
    card_id: str = Field(alias="cardId")
    version_id: str = Field(alias="versionId")
    milestone: Literal[
        "automated_review_passed",
        "independent_evidence_sufficient",
        "conservative_effectiveness_reached",
        "report_confirmed",
        "official_derivation",
    ]
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)
    occurred_at: datetime = Field(alias="occurredAt")
