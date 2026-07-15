from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.domains.essay_intelligence.learning_schemas import (
    EssayLearningArtifactVersion,
    ReflectionPayload,
    UuidText,
)
from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayAttempt,
    EssayContractModel,
    EssayThinkingSnapshot,
)
from app.domains.essay_intelligence.workflow_schemas import EssayReviewIntentView
from app.domains.planning.schemas import TaskExecutionCommandResult


class EssayDraftingAttemptCreate(EssayContractModel):
    mode: Literal["create"]
    source_context: Literal["in_program", "paper", "external"] = Field(
        alias="sourceContext"
    )
    timed_status: Literal["timed", "untimed", "unknown"] = Field(
        alias="timedStatus"
    )
    elapsed_seconds: int | None = Field(default=None, alias="elapsedSeconds", ge=1)

    @model_validator(mode="after")
    def validate_timing(self) -> "EssayDraftingAttemptCreate":
        if self.timed_status == "timed" and self.elapsed_seconds is None:
            raise ValueError("timed attempt requires elapsedSeconds")
        if self.timed_status != "timed" and self.elapsed_seconds is not None:
            raise ValueError("elapsedSeconds is only accepted for timed attempts")
        return self


class EssayDraftingAttemptExisting(EssayContractModel):
    mode: Literal["existing"]
    attempt_id: UuidText = Field(alias="attemptId")


EssayDraftingAttemptInput = Annotated[
    EssayDraftingAttemptCreate | EssayDraftingAttemptExisting,
    Field(discriminator="mode"),
]


class EssayDraftingAnswerCreate(EssayContractModel):
    mode: Literal["create"]
    source_type: Literal["typed", "ocr_confirmed"] = Field(alias="sourceType")
    answer_text: str = Field(alias="answerText", min_length=1, max_length=10000)
    thinking: EssayThinkingSnapshot
    based_on_answer_version_id: UuidText | None = Field(
        default=None,
        alias="basedOnAnswerVersionId",
    )


class EssayDraftingAnswerExisting(EssayContractModel):
    mode: Literal["existing"]
    answer_version_id: UuidText = Field(alias="answerVersionId")


EssayDraftingAnswerInput = Annotated[
    EssayDraftingAnswerCreate | EssayDraftingAnswerExisting,
    Field(discriminator="mode"),
]


class EssayDraftingCompletionCommand(EssayContractModel):
    plan_task_id: UuidText = Field(alias="planTaskId")
    task_version_id: UuidText = Field(alias="taskVersionId")
    material_bundle_version_id: UuidText = Field(alias="materialBundleVersionId")
    rubric_version_id: UuidText = Field(alias="rubricVersionId")
    elapsed_minutes: int = Field(alias="elapsedMinutes", ge=0, le=1440)
    based_on_execution_version_id: UuidText | None = Field(
        default=None,
        alias="basedOnExecutionVersionId",
    )
    attempt: EssayDraftingAttemptInput
    answer: EssayDraftingAnswerInput
    reflection: ReflectionPayload
    based_on_reflection_artifact_version_id: UuidText | None = Field(
        default=None,
        alias="basedOnReflectionArtifactVersionId",
    )

    @model_validator(mode="after")
    def validate_creation_chain(self) -> "EssayDraftingCompletionCommand":
        if self.attempt.mode == "create" and self.answer.mode != "create":
            raise ValueError("a new attempt requires a new answer version")
        if (
            self.attempt.mode == "create"
            and self.answer.mode == "create"
            and self.answer.based_on_answer_version_id is not None
        ):
            raise ValueError("a new attempt cannot supersede an existing answer")
        if self.attempt.mode == "create" and self.attempt.timed_status == "timed":
            assert self.attempt.elapsed_seconds is not None
            expected_minutes = (self.attempt.elapsed_seconds + 59) // 60
            if self.elapsed_minutes != expected_minutes:
                raise ValueError(
                    "elapsedMinutes must equal rounded-up elapsedSeconds for a timed attempt"
                )
        return self


class EssayDraftingCompletionResult(EssayContractModel):
    attempt: EssayAttempt
    answer: EssayAnswerVersion
    reflection: EssayLearningArtifactVersion
    completion: TaskExecutionCommandResult
    review_intent: EssayReviewIntentView = Field(alias="reviewIntent")
