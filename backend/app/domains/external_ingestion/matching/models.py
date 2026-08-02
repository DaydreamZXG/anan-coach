from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.contracts.external_learning.models import LearningTrack, Sha256Hex
from app.domains.external_ingestion.recognition.models import VisualFeatureHash


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
AuthorizationPurpose = Literal[
    "owner_private_match", "cross_user_match", "reference_answer", "ai_grounding"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class IndexedContent(StrictModel):
    content_version_id: NonBlank = Field(alias="contentVersionId")
    track: LearningTrack
    prompt_text: Annotated[str, StringConstraints(min_length=1, max_length=200_000)] = Field(
        alias="promptText"
    )
    option_texts: tuple[Annotated[str, StringConstraints(min_length=1, max_length=20_000)], ...] = Field(
        default=(), alias="optionTexts"
    )
    material_texts: tuple[Annotated[str, StringConstraints(min_length=1, max_length=200_000)], ...] = Field(
        default=(), alias="materialTexts"
    )
    visual_fingerprints: tuple[Sha256Hex, ...] = Field(
        default=(), alias="visualFingerprints"
    )
    visual_feature_hashes: tuple[VisualFeatureHash, ...] = Field(
        default=(), alias="visualFeatureHashes"
    )
    reference_answer: str | None = Field(default=None, alias="referenceAnswer", max_length=200_000)
    solution_explanation: str | None = Field(
        default=None, alias="solutionExplanation", max_length=300_000
    )
    distribution_class: Literal["account_private", "licensed_transferable"] = Field(
        alias="distributionClass"
    )
    owner_user_id: UUID | None = Field(default=None, alias="ownerUserId")
    authorization_snapshot_ref: str | None = Field(
        default=None, alias="authorizationSnapshotRef"
    )
    cross_user_match_allowed: bool = Field(alias="crossUserMatchAllowed")

    @model_validator(mode="after")
    def privacy_shape(self) -> "IndexedContent":
        if self.distribution_class == "account_private":
            if self.owner_user_id is None or self.cross_user_match_allowed:
                raise ValueError("private indexed content must have one owner and deny cross-user matching")
        elif self.owner_user_id is not None:
            raise ValueError("transferable catalog content cannot masquerade as owner-private")
        return self


class MatchQuery(StrictModel):
    track: LearningTrack
    prompt_text: Annotated[str, StringConstraints(max_length=200_000)] = Field(
        alias="promptText"
    )
    option_texts: tuple[str, ...] = Field(default=(), alias="optionTexts")
    material_texts: tuple[str, ...] = Field(default=(), alias="materialTexts")
    visual_fingerprints: tuple[Sha256Hex, ...] = Field(
        default=(), alias="visualFingerprints"
    )
    visual_feature_hashes: tuple[VisualFeatureHash, ...] = Field(
        default=(), alias="visualFeatureHashes"
    )


class MatchingPolicy(StrictModel):
    policy_version: NonBlank = Field(alias="policyVersion")
    exact_text_threshold: Literal[1.0] = Field(default=1.0, alias="exactTextThreshold")
    similar_threshold: float = Field(alias="similarThreshold", ge=0.5, le=0.99)
    ambiguity_margin: float = Field(alias="ambiguityMargin", gt=0, le=0.20)
    text_weight: float = Field(alias="textWeight", gt=0, lt=1)
    visual_weight: float = Field(alias="visualWeight", gt=0, lt=1)
    essay_min_prompt_chars: int = Field(alias="essayMinPromptChars", ge=10)
    essay_require_material: bool = Field(alias="essayRequireMaterial")
    maximum_candidates: int = Field(alias="maximumCandidates", ge=1, le=20)

    @model_validator(mode="after")
    def weights_sum(self) -> "MatchingPolicy":
        if abs(self.text_weight + self.visual_weight - 1) > 1e-9:
            raise ValueError("matching weights must sum to one")
        if self.ambiguity_margin >= 1 - self.similar_threshold:
            raise ValueError("ambiguity margin must fit above the similarity threshold")
        return self


class AuthorizationDecision(StrictModel):
    allowed: bool
    reason: NonBlank
    snapshot_ref: str | None = Field(default=None, alias="snapshotRef")

    @model_validator(mode="after")
    def snapshot_when_allowed(self) -> "AuthorizationDecision":
        if self.allowed != (self.snapshot_ref is not None):
            raise ValueError("allowed authorization decisions require a snapshot")
        return self


class AIAnswerApproval(StrictModel):
    policy_version: NonBlank = Field(alias="policyVersion")
    provider: NonBlank
    provider_release: NonBlank = Field(alias="providerRelease")
    output_contract: Literal["external-ai-answer-output@v1"] = Field(
        default="external-ai-answer-output@v1", alias="outputContract"
    )
    data_region: Literal["mainland_china"] = Field(alias="dataRegion")
    dpa_evidence_ref: NonBlank = Field(alias="dpaEvidenceRef")
    security_review_ref: NonBlank = Field(alias="securityReviewRef")
    approval_receipt_ref: NonBlank = Field(alias="approvalReceiptRef")
    model_training_use: Literal["prohibited"] = Field(
        default="prohibited", alias="modelTrainingUse"
    )
    provider_retention_seconds: Literal[0] = Field(
        default=0, alias="providerRetentionSeconds"
    )
    valid_from: datetime = Field(alias="validFrom")
    valid_until: datetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def valid_window(self) -> "AIAnswerApproval":
        if self.valid_from.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("AI answer approval times must be timezone-aware")
        if self.valid_until <= self.valid_from:
            raise ValueError("AI answer approval window is empty")
        return self

    def permits(self, *, provider_release: str, at: datetime) -> bool:
        return (
            self.valid_from <= at < self.valid_until
            and self.provider_release == provider_release
        )


class VisualFingerprintApproval(StrictModel):
    policy_version: NonBlank = Field(alias="policyVersion")
    processor_release: NonBlank = Field(alias="processorRelease")
    output_contract: Literal["question-visual-fingerprint-output@v1"] = Field(
        default="question-visual-fingerprint-output@v1", alias="outputContract"
    )
    algorithm: Literal["perceptual-256"]
    data_region: Literal["mainland_china"] = Field(alias="dataRegion")
    approval_receipt_ref: NonBlank = Field(alias="approvalReceiptRef")
    security_review_ref: NonBlank = Field(alias="securityReviewRef")
    model_training_use: Literal["prohibited"] = Field(
        default="prohibited", alias="modelTrainingUse"
    )
    valid_from: datetime = Field(alias="validFrom")
    valid_until: datetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def valid_window(self) -> "VisualFingerprintApproval":
        if self.valid_from.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("visual-fingerprint approval times must be timezone-aware")
        if self.valid_until <= self.valid_from:
            raise ValueError("visual-fingerprint approval window is empty")
        return self


class VisualFingerprintResult(StrictModel):
    contract_version: Literal["question-visual-fingerprint-output@v1"] = Field(
        default="question-visual-fingerprint-output@v1", alias="contractVersion"
    )
    question_id: NonBlank = Field(alias="questionId")
    content_version_id: Sha256Hex = Field(alias="contentVersionId")
    fingerprint_sha256: Sha256Hex = Field(alias="fingerprintSha256")
    source_asset_ref: NonBlank = Field(alias="sourceAssetRef")
    processor_release: NonBlank = Field(alias="processorRelease")
    provider_request_id_hash: Sha256Hex = Field(alias="providerRequestIdHash")


class ScoredCandidate(StrictModel):
    content: IndexedContent
    match_kind: Literal["exact", "similar"] = Field(alias="matchKind")
    score: float = Field(ge=0, le=1)
    text_score: float = Field(alias="textScore", ge=0, le=1)
    visual_score: float = Field(alias="visualScore", ge=0, le=1)
    evidence_refs: tuple[NonBlank, ...] = Field(alias="evidenceRefs", min_length=1)


class MatchEvaluation(StrictModel):
    input_fingerprint: Sha256Hex = Field(alias="inputFingerprint")
    candidates: tuple[ScoredCandidate, ...]
    requires_user_confirmation: Literal[True] = Field(
        default=True, alias="requiresUserConfirmation"
    )
    ambiguity_reason: Literal[
        "single_exact", "single_similar", "score_tie", "no_eligible_candidate", "essay_incomplete"
    ] = Field(alias="ambiguityReason")


class AnswerLayer(StrictModel):
    layer: Literal["exact", "similar", "ai"]
    state: Literal["candidate", "confirmed"]
    answer_text: Annotated[str, StringConstraints(min_length=1, max_length=300_000)] = Field(
        alias="answerText"
    )
    content_version_id: str | None = Field(default=None, alias="contentVersionId")
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId")
    content_match_version_id: UUID | None = Field(
        default=None, alias="contentMatchVersionId"
    )
    authorization_snapshot_ref: NonBlank = Field(alias="authorizationSnapshotRef")
    processor_policy_version: str | None = Field(default=None, alias="processorPolicyVersion")
    output_sha256: Sha256Hex = Field(alias="outputSha256")

    @model_validator(mode="after")
    def layer_provenance(self) -> "AnswerLayer":
        if self.layer == "ai":
            if self.processor_policy_version is None:
                raise ValueError("AI answers require an approved processor policy")
        elif self.content_version_id is None or self.content_match_version_id is None:
            raise ValueError("stored answer layers require confirmed content provenance")
        return self


class EvidenceAdmission(StrictModel):
    allowed: bool
    reason: Literal[
        "confirmed_external_answer",
        "recognition_unconfirmed",
        "match_unconfirmed",
        "answer_unconfirmed",
        "lineage_mismatch",
        "authorization_not_current",
    ]
    answer_output_sha256: Sha256Hex = Field(alias="answerOutputSha256")
    may_affect_ability: bool = Field(alias="mayAffectAbility")
    may_affect_cause: bool = Field(alias="mayAffectCause")
    may_affect_plan: bool = Field(alias="mayAffectPlan")
