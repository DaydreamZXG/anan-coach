from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domains.essay_intelligence.mastery.schemas import EssayMasteryStateVersion
from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayContractModel,
    EssayThinkingSnapshot,
)
from app.domains.planning.schemas import TaskExecutionCommandResult


class EssayRewriteFindingResolution(EssayContractModel):
    finding_id: str = Field(alias="findingId", min_length=1, max_length=120)
    status: Literal["resolved", "unresolved"]
    learner_evidence: str = Field(alias="learnerEvidence", min_length=1, max_length=2000)


class EssayRewriteCommand(EssayContractModel):
    attempt_id: UUID = Field(alias="attemptId", strict=False)
    base_review_version_id: UUID = Field(alias="baseReviewVersionId", strict=False)
    base_answer_version_id: UUID = Field(alias="baseAnswerVersionId", strict=False)
    finding_decision_artifact_version_id: UUID = Field(
        alias="findingDecisionArtifactVersionId",
        strict=False,
    )
    based_on_answer_version_id: UUID = Field(
        alias="basedOnAnswerVersionId",
        strict=False,
    )
    based_on_rewrite_version_id: UUID | None = Field(
        default=None,
        alias="basedOnRewriteVersionId",
        strict=False,
    )
    source_type: Literal["typed", "ocr_confirmed"] = Field(alias="sourceType")
    source_artifact_version_id: UUID | None = Field(
        default=None,
        alias="sourceArtifactVersionId",
        strict=False,
    )
    recognition_revision_id: UUID | None = Field(
        default=None,
        alias="recognitionRevisionId",
        strict=False,
    )
    answer_text: str = Field(alias="answerText", min_length=1, max_length=10000)
    thinking: EssayThinkingSnapshot | None = None
    resolutions: list[EssayRewriteFindingResolution] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_resolutions(self) -> "EssayRewriteCommand":
        finding_ids = [item.finding_id for item in self.resolutions]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("rewrite finding resolutions must be unique")
        if self.source_type == "ocr_confirmed":
            if self.source_artifact_version_id is None or self.recognition_revision_id is None:
                raise ValueError("OCR rewrite requires confirmed recognition evidence")
        elif self.source_artifact_version_id is not None or self.recognition_revision_id is not None:
            raise ValueError("typed rewrite cannot carry OCR evidence")
        return self


class EssayRewriteRecord(EssayContractModel):
    contract_version: Literal["essay-rewrite-writer@v2"] = Field(
        alias="contractVersion"
    )
    policy_version: Literal["essay-mastery@v1"] = Field(alias="policyVersion")
    rewrite_version_id: UUID = Field(alias="rewriteVersionId")
    attempt_id: UUID = Field(alias="attemptId")
    task_version_id: UUID = Field(alias="taskVersionId")
    base_review_version_id: UUID = Field(alias="baseReviewVersionId")
    base_answer_version_id: UUID = Field(alias="baseAnswerVersionId")
    rewritten_answer_version_id: UUID = Field(alias="rewrittenAnswerVersionId")
    finding_decision_artifact_version_id: UUID = Field(
        alias="findingDecisionArtifactVersionId"
    )
    status: Literal["passed", "needs_revision"]
    addressed_finding_ids: list[str] = Field(alias="addressedFindingIds")
    unresolved_finding_ids: list[str] = Field(alias="unresolvedFindingIds")
    resolution_evidence: dict[str, str] = Field(alias="resolutionEvidence")
    input_fingerprint: str = Field(alias="inputFingerprint", pattern=r"^[0-9a-f]{64}$")
    output_fingerprint: str = Field(alias="outputFingerprint", pattern=r"^[0-9a-f]{64}$")
    supersedes_rewrite_version_id: UUID | None = Field(
        default=None,
        alias="supersedesRewriteVersionId",
    )
    created_at: AwareDatetime = Field(alias="createdAt")


class EssayRewriteResult(EssayContractModel):
    answer: EssayAnswerVersion
    rewrite: EssayRewriteRecord
    mastery_versions: list[EssayMasteryStateVersion] = Field(alias="masteryVersions")


class EssayAnswerDiffSegment(EssayContractModel):
    operation: Literal["equal", "delete", "insert"]
    text: str = Field(min_length=1, max_length=10000)


class EssayRewriteComparison(EssayContractModel):
    rewrite: EssayRewriteRecord
    original_answer: EssayAnswerVersion = Field(alias="originalAnswer")
    rewritten_answer: EssayAnswerVersion = Field(alias="rewrittenAnswer")
    diff_segments: list[EssayAnswerDiffSegment] = Field(alias="diffSegments")


class EssayRewritePlanTaskCompletionCommand(EssayContractModel):
    rewrite: EssayRewriteCommand
    plan_task_id: UUID = Field(alias="planTaskId", strict=False)
    elapsed_minutes: int = Field(alias="elapsedMinutes", ge=0, le=1440)
    based_on_execution_version_id: UUID | None = Field(
        default=None,
        alias="basedOnExecutionVersionId",
        strict=False,
    )


class EssayRewritePlanTaskCompletionResult(EssayRewriteResult):
    completion: TaskExecutionCommandResult


class EssayTransferCommand(EssayContractModel):
    source_rewrite_version_id: UUID = Field(
        alias="sourceRewriteVersionId",
        strict=False,
    )
    source_finding_decision_artifact_version_id: UUID = Field(
        alias="sourceFindingDecisionArtifactVersionId",
        strict=False,
    )
    transfer_attempt_id: UUID = Field(alias="transferAttemptId", strict=False)
    transfer_answer_version_id: UUID = Field(
        alias="transferAnswerVersionId",
        strict=False,
    )
    transfer_review_version_id: UUID = Field(
        alias="transferReviewVersionId",
        strict=False,
    )
    transfer_finding_decision_artifact_version_id: UUID = Field(
        alias="transferFindingDecisionArtifactVersionId",
        strict=False,
    )
    based_on_transfer_verification_version_id: UUID | None = Field(
        default=None,
        alias="basedOnTransferVerificationVersionId",
        strict=False,
    )
    tested_criterion_codes: list[str] = Field(
        alias="testedCriterionCodes",
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_criteria(self) -> "EssayTransferCommand":
        values = [item.strip() for item in self.tested_criterion_codes]
        if any(not item for item in values) or len(values) != len(set(values)):
            raise ValueError("tested criterion codes must be non-blank and unique")
        self.tested_criterion_codes = values
        return self


class EssayTransferRecord(EssayContractModel):
    contract_version: Literal["essay-transfer-writer@v2"] = Field(
        alias="contractVersion"
    )
    policy_version: Literal["essay-mastery@v1"] = Field(alias="policyVersion")
    verification_version_id: UUID = Field(alias="verificationVersionId")
    source_rewrite_version_id: UUID = Field(alias="sourceRewriteVersionId")
    source_review_version_id: UUID = Field(alias="sourceReviewVersionId")
    source_attempt_id: UUID = Field(alias="sourceAttemptId")
    source_task_version_id: UUID = Field(alias="sourceTaskVersionId")
    transfer_attempt_id: UUID = Field(alias="transferAttemptId")
    transfer_task_version_id: UUID = Field(alias="transferTaskVersionId")
    transfer_answer_version_id: UUID = Field(alias="transferAnswerVersionId")
    transfer_review_version_id: UUID = Field(alias="transferReviewVersionId")
    fresh_material_bundle_version_id: UUID = Field(alias="freshMaterialBundleVersionId")
    finding_decision_artifact_version_id: UUID = Field(
        alias="findingDecisionArtifactVersionId"
    )
    transfer_finding_decision_artifact_version_id: UUID = Field(
        alias="transferFindingDecisionArtifactVersionId"
    )
    status: Literal["verified", "not_verified", "inconclusive"]
    tested_criterion_codes: list[str] = Field(alias="testedCriterionCodes")
    evidence_payload: dict[str, object] = Field(alias="evidencePayload")
    input_fingerprint: str = Field(alias="inputFingerprint", pattern=r"^[0-9a-f]{64}$")
    output_fingerprint: str = Field(alias="outputFingerprint", pattern=r"^[0-9a-f]{64}$")
    supersedes_verification_version_id: UUID | None = Field(
        default=None,
        alias="supersedesVerificationVersionId",
    )
    generated_at: AwareDatetime = Field(alias="generatedAt")


class EssayTransferResult(EssayContractModel):
    transfer: EssayTransferRecord
    mastery_versions: list[EssayMasteryStateVersion] = Field(alias="masteryVersions")


class EssayTransferPlanTaskCompletionCommand(EssayContractModel):
    transfer: EssayTransferCommand
    plan_task_id: UUID = Field(alias="planTaskId", strict=False)
    elapsed_minutes: int = Field(alias="elapsedMinutes", ge=0, le=1440)
    based_on_execution_version_id: UUID | None = Field(
        default=None,
        alias="basedOnExecutionVersionId",
        strict=False,
    )


class EssayTransferPlanTaskCompletionResult(EssayTransferResult):
    completion: TaskExecutionCommandResult


class EssayTransferContentGap(EssayContractModel):
    code: Literal["essay_transfer_content_gap"]
    message: str
    source_task_version_id: UUID = Field(alias="sourceTaskVersionId")
    requires: Literal["different_published_task_and_material"]
