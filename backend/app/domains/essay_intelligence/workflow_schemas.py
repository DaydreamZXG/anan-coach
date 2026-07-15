from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayAttempt,
    EssayContractModel,
    EssayReviewResponse,
    EssayThinkingSnapshot,
)


class EssayAttemptCreateCommand(EssayContractModel):
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    rubric_version_id: UUID = Field(alias="rubricVersionId")
    source_context: Literal["in_program", "paper", "external"] = Field(
        alias="sourceContext"
    )
    timed_status: Literal["timed", "untimed", "unknown"] = Field(
        alias="timedStatus"
    )
    elapsed_seconds: int | None = Field(default=None, alias="elapsedSeconds", ge=1)

    @model_validator(mode="after")
    def validate_timing(self) -> "EssayAttemptCreateCommand":
        if self.timed_status == "timed" and self.elapsed_seconds is None:
            raise ValueError("timed attempt requires elapsedSeconds")
        if self.timed_status != "timed" and self.elapsed_seconds is not None:
            raise ValueError("elapsedSeconds is only accepted for timed attempts")
        return self


class EssayAnswerCreateCommand(EssayContractModel):
    attempt_id: UUID = Field(alias="attemptId")
    source_type: Literal["typed", "ocr_confirmed"] = Field(alias="sourceType")
    answer_text: str = Field(alias="answerText", min_length=1, max_length=10000)
    thinking: EssayThinkingSnapshot | None = None
    based_on_answer_version_id: UUID | None = Field(
        default=None,
        alias="basedOnAnswerVersionId",
    )


class EssayReviewQueueCommand(EssayContractModel):
    attempt_id: UUID = Field(alias="attemptId")
    answer_version_id: UUID = Field(alias="answerVersionId")
    review_scope: Literal["grounded_full"] = Field(alias="reviewScope")


class EssayCurrentReviewEnsureCommand(EssayContractModel):
    task_version_id: UUID = Field(alias="taskVersionId", strict=False)
    material_bundle_version_id: UUID = Field(
        alias="materialBundleVersionId",
        strict=False,
    )
    rubric_version_id: UUID = Field(alias="rubricVersionId", strict=False)
    review_scope: Literal["grounded_full"] = Field(alias="reviewScope")


class EssayReviewQuota(EssayContractModel):
    feature: Literal["essay_review"]
    used: int = Field(ge=0)
    limit: int = Field(ge=1)
    usage_date: date = Field(alias="usageDate")


class EssayReviewIntentView(EssayContractModel):
    intent_id: UUID = Field(alias="intentId")
    intent_version_id: UUID = Field(alias="intentVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    status: Literal["pending", "waiting_quota", "queued"]
    attempt_id: UUID = Field(alias="attemptId")
    answer_version_id: UUID = Field(alias="answerVersionId")
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    rubric_version_id: UUID = Field(alias="rubricVersionId")
    review_scope: Literal["grounded_full"] = Field(alias="reviewScope")
    input_fingerprint: str = Field(alias="inputFingerprint", min_length=64, max_length=64)
    intent_fingerprint: str = Field(
        alias="intentFingerprint",
        min_length=64,
        max_length=64,
    )
    state_fingerprint: str = Field(
        alias="stateFingerprint",
        min_length=64,
        max_length=64,
    )
    job_id: UUID | None = Field(default=None, alias="jobId")
    job_status: Literal["queued", "running", "succeeded", "failed"] | None = Field(
        default=None,
        alias="jobStatus",
    )
    quota: EssayReviewQuota | None = None
    retry_after: AwareDatetime | None = Field(default=None, alias="retryAfter")
    supersedes_intent_version_id: UUID | None = Field(
        default=None,
        alias="supersedesIntentVersionId",
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_state_shape(self) -> "EssayReviewIntentView":
        if self.status == "pending":
            if any((self.job_id, self.job_status, self.quota, self.retry_after)):
                raise ValueError("pending review intent cannot carry queue or quota state")
        elif self.status == "waiting_quota":
            if self.job_id is not None or self.job_status is not None:
                raise ValueError("quota-waiting review intent cannot carry a job")
            if self.quota is None or self.retry_after is None:
                raise ValueError("quota-waiting review intent requires quota state")
        else:
            if self.job_id is None or self.job_status is None or self.quota is None:
                raise ValueError("queued review intent requires job and quota state")
            if self.retry_after is not None:
                raise ValueError("queued review intent cannot carry retryAfter")
        return self


class EssayReviewJobView(EssayContractModel):
    job_id: UUID = Field(alias="jobId")
    status: Literal["queued", "running", "succeeded", "failed"]
    attempt_id: UUID = Field(alias="attemptId")
    answer_version_id: UUID = Field(alias="answerVersionId")
    review_version_id: UUID = Field(alias="reviewVersionId")
    review_status: Literal["completed", "blocked", "failed"] | None = Field(
        default=None,
        alias="reviewStatus",
    )
    quota: EssayReviewQuota
    created_at: AwareDatetime = Field(alias="createdAt")
    completed_at: AwareDatetime | None = Field(default=None, alias="completedAt")
    review: EssayReviewResponse | None = None


class EssayCurrentReviewEnsureResult(EssayContractModel):
    intent: EssayReviewIntentView
    job: EssayReviewJobView | None = None


class EssayAttemptCreateResult(EssayContractModel):
    attempt: EssayAttempt


class EssayAnswerCreateResult(EssayContractModel):
    answer: EssayAnswerVersion
