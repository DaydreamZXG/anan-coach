from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domains.essay_intelligence.schemas import EssayContractModel


EssayMasteryState = Literal[
    "rewrite_due",
    "transfer_due",
    "stable_mastered",
    "withdrawn",
]


class EssayMasteryEvidenceRef(EssayContractModel):
    owner: Literal["essay_intelligence"] = "essay_intelligence"
    entity_type: str = Field(alias="entityType", min_length=1, max_length=120)
    entity_id: str = Field(alias="entityId", min_length=1, max_length=160)
    version_id: str = Field(alias="versionId", min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=120)


class EssayMasteryStateVersion(EssayContractModel):
    mastery_version_id: UUID = Field(alias="masteryVersionId")
    mastery_id: UUID = Field(alias="masteryId")
    finding_id: str = Field(alias="findingId", min_length=1, max_length=120)
    version_no: int = Field(alias="versionNo", ge=1)
    state: EssayMasteryState
    due_at: AwareDatetime | None = Field(default=None, alias="dueAt")
    source_task_version_id: UUID = Field(alias="sourceTaskVersionId")
    source_attempt_id: UUID = Field(alias="sourceAttemptId")
    source_answer_version_id: UUID = Field(alias="sourceAnswerVersionId")
    source_review_version_id: UUID = Field(alias="sourceReviewVersionId")
    finding_decision_artifact_version_id: UUID = Field(
        alias="findingDecisionArtifactVersionId"
    )
    rewrite_version_id: UUID | None = Field(default=None, alias="rewriteVersionId")
    transfer_verification_version_id: UUID | None = Field(
        default=None,
        alias="transferVerificationVersionId",
    )
    evidence_refs: list[EssayMasteryEvidenceRef] = Field(alias="evidenceRefs", min_length=4)
    policy_version: Literal["essay-mastery@v1"] = Field(alias="policyVersion")
    input_fingerprint: str = Field(alias="inputFingerprint", pattern=r"^[0-9a-f]{64}$")
    supersedes_mastery_version_id: UUID | None = Field(
        default=None,
        alias="supersedesMasteryVersionId",
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_state_shape(self) -> "EssayMasteryStateVersion":
        if self.state in {"stable_mastered", "withdrawn"}:
            if self.due_at is not None:
                raise ValueError("terminal essay mastery state cannot be due")
        elif self.due_at is None:
            raise ValueError("non-terminal essay mastery state requires dueAt")
        if self.state == "transfer_due" and self.rewrite_version_id is None:
            raise ValueError("transfer_due requires rewrite evidence")
        if self.state == "stable_mastered" and (
            self.rewrite_version_id is None
            or self.transfer_verification_version_id is None
        ):
            raise ValueError("stable mastery requires rewrite and transfer evidence")
        return self


class EssayMasteryProjection(EssayContractModel):
    contract_version: Literal["essay-mastery@v1"] = Field(alias="contractVersion")
    due_count: int = Field(alias="dueCount", ge=0)
    stable_count: int = Field(alias="stableCount", ge=0)
    items: list[EssayMasteryStateVersion]
