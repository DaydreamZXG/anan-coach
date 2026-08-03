from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BriefingModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class XingceBriefingTiming(BriefingModel):
    behavior: Literal["advisory"] = "advisory"
    target_seconds: int = Field(alias="targetSeconds", ge=1)
    per_question_target_seconds: int = Field(alias="perQuestionTargetSeconds", ge=1)
    overtime_is_recorded: Literal[True] = Field(default=True, alias="overtimeIsRecorded")


class XingceBriefingCompletion(BriefingModel):
    evidence_type: str = Field(alias="evidenceType")
    required_metrics: list[
        Literal[
            "answer_verdict",
            "confidence_evidence",
            "timing_evidence",
            "reflection",
            "cause_decision",
        ]
    ] = Field(alias="requiredMetrics", min_length=1)
    stable_mastery_requires_delayed_verification: Literal[True] = Field(
        default=True, alias="stableMasteryRequiresDelayedVerification"
    )


class XingceBriefingAbandonment(BriefingModel):
    allowed: Literal[True] = True
    semantics: Literal["active_abandon_is_not_automatic_error"] = Field(
        default="active_abandon_is_not_automatic_error"
    )
    requires_explicit_action: Literal[True] = Field(
        default=True, alias="requiresExplicitAction"
    )


class XingceTaskBriefing(BriefingModel):
    contract_version: Literal["xingce-task-briefing@v1"] = Field(
        default="xingce-task-briefing@v1", alias="contractVersion"
    )
    plan_task_id: UUID = Field(alias="planTaskId")
    plan_version_id: UUID = Field(alias="planVersionId")
    task_definition_version: str = Field(alias="taskDefinitionVersion")
    mode: Literal["baseline", "targeted", "verification"]
    title: str
    reason: str
    module_name: str | None = Field(default=None, alias="moduleName")
    question_count: int = Field(alias="questionCount", ge=3, le=30)
    timing: XingceBriefingTiming
    completion: XingceBriefingCompletion
    abandonment: XingceBriefingAbandonment
    verification_stage: Literal["immediate", "first_review", "delayed_review"] | None = Field(
        default=None, alias="verificationStage"
    )
    source_mastery_version_id: UUID | None = Field(
        default=None, alias="sourceMasteryVersionId"
    )
    valid_until: datetime = Field(alias="validUntil")
