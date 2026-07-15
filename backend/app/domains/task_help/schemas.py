from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


TaskHelpMode = Literal[
    "practice", "targeted", "retest", "baseline", "verification", "mock"
]


class TaskHelpModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class TaskHelpRequest(TaskHelpModel):
    session_id: UUID = Field(alias="sessionId")
    mode: TaskHelpMode
    scope: Literal["operation", "fault"]


class TaskHelpResponse(TaskHelpModel):
    session_id: str = Field(alias="sessionId")
    mode: TaskHelpMode
    scope: Literal["operation", "fault"]
    session_state: str = Field(alias="sessionState")
    policy_version: Literal["task-help-learner-safe-v1"] = Field(
        default="task-help-learner-safe-v1", alias="policyVersion"
    )
    title: str
    steps: list[str] = Field(min_length=1, max_length=8)
    permitted_tools: list[
        Literal[
            "navigation_help",
            "checkpoint_status",
            "timer_recovery",
            "exit_policy",
            "fault_retry",
            "dispute_entry",
        ]
    ] = Field(alias="permittedTools")
    blocked_capabilities: list[
        Literal[
            "current_question_answer",
            "answer_analysis",
            "formula_card",
            "solution_ai",
        ]
    ] = Field(alias="blockedCapabilities")
    content_lookup_performed: Literal[False] = Field(
        default=False, alias="contentLookupPerformed"
    )
