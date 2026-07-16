from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


MasteryQueueBucket = Literal[
    "pending_reflection",
    "due_verification",
    "stable_mastery",
    "invalidated",
]


class MasteryQueueModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class MasteryQueueSummary(MasteryQueueModel):
    pending_reflection_count: int = Field(alias="pendingReflectionCount", ge=0)
    due_verification_count: int = Field(alias="dueVerificationCount", ge=0)
    stable_mastery_count: int = Field(alias="stableMasteryCount", ge=0)
    invalidated_count: int = Field(alias="invalidatedCount", ge=0)


class MasteryQueueAction(MasteryQueueModel):
    state: Literal["ready", "awaiting_plan", "not_applicable"]
    start_url: str | None = Field(default=None, alias="startUrl")
    plan_task_id: UUID | None = Field(default=None, alias="planTaskId")

    @model_validator(mode="after")
    def validate_action(self) -> "MasteryQueueAction":
        if self.state == "ready":
            if self.start_url is None:
                raise ValueError("ready mastery action requires a URL")
        elif self.start_url is not None or self.plan_task_id is not None:
            raise ValueError("non-ready mastery action cannot carry a plan task")
        return self


class MasteryQueueItem(MasteryQueueModel):
    item_id: UUID = Field(alias="itemId")
    bucket: MasteryQueueBucket
    state: str
    verification_stage: str | None = Field(default=None, alias="verificationStage")
    module_name: str | None = Field(default=None, alias="moduleName")
    due_at: datetime | None = Field(default=None, alias="dueAt")
    created_at: datetime = Field(alias="createdAt")
    question_count: int = Field(alias="questionCount", ge=1)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)
    action: MasteryQueueAction
    session_id: UUID | None = Field(default=None, alias="sessionId")
    review_id: UUID | None = Field(default=None, alias="reviewId")
    mastery_id: UUID | None = Field(default=None, alias="masteryId")
    mastery_version_id: UUID | None = Field(default=None, alias="masteryVersionId")
    invalidation_reason: Literal["content_withdrawn"] | None = Field(
        default=None, alias="invalidationReason"
    )

    @model_validator(mode="after")
    def validate_identity(self) -> "MasteryQueueItem":
        if self.bucket == "pending_reflection":
            if self.session_id is None or self.review_id is None:
                raise ValueError("reflection queue item requires session and review")
            if self.mastery_id is not None or self.mastery_version_id is not None:
                raise ValueError("reflection queue item cannot carry mastery identity")
        else:
            if self.mastery_id is None or self.mastery_version_id is None:
                raise ValueError("mastery queue item requires mastery identity")
            if self.session_id is not None or self.review_id is not None:
                raise ValueError("mastery queue item cannot carry review identity")
        if (self.bucket == "invalidated") != (self.invalidation_reason is not None):
            raise ValueError("only invalidated items require an invalidation reason")
        return self


class MasteryQueuePage(MasteryQueueModel):
    contract_version: Literal["xingce-mastery-queue@v1"] = Field(
        default="xingce-mastery-queue@v1", alias="contractVersion"
    )
    bucket: MasteryQueueBucket
    summary: MasteryQueueSummary
    items: list[MasteryQueueItem]
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    generated_at: datetime = Field(alias="generatedAt")
