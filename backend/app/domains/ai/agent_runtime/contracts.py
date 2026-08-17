from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AGENT_DEFINITION_CONTRACT_VERSION = "cause-agent-definition@v1"
AGENT_RUN_CONTRACT_VERSION = "cause-agent-run@v1"
TOOL_LIFECYCLE_CONTRACT_VERSION = "agent-tool-lifecycle@v1"
TOOL_PERMISSION_POLICY_VERSION = "xingce-cause-read-only@v1"


class AgentContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class ToolDefinition(AgentContractModel):
    name: Literal[
        "load_attempt_evidence",
        "load_user_reflection",
        "load_published_question_explanation",
        "load_cause_taxonomy",
        "load_action_trace",
        "load_ink_formulas",
    ]
    schema_version: Literal["v1"] = Field(default="v1", alias="schemaVersion")
    behavior_version: Literal["v1"] = Field(default="v1", alias="behaviorVersion")
    permission_policy_version: Literal["xingce-cause-read-only@v1"] = Field(
        default=TOOL_PERMISSION_POLICY_VERSION,
        alias="permissionPolicyVersion",
    )
    read_only: Literal[True] = Field(default=True, alias="readOnly")


class AgentBudget(AgentContractModel):
    max_model_calls: Literal[6] = Field(default=6, alias="maxModelCalls")
    max_tool_calls: Literal[6] = Field(default=6, alias="maxToolCalls")
    max_followup_rounds: Literal[1] = Field(default=1, alias="maxFollowupRounds")
    max_consecutive_no_progress: Literal[2] = Field(
        default=2,
        alias="maxConsecutiveNoProgress",
    )


class AgentDefinition(AgentContractModel):
    # 审计惯例：定义内容（工具集/prompt）变更时发布新的 release id，
    # 旧 release 行在 ai_audit.agent_definition_releases 中保持不可变。
    definition_release_id: Literal["xingce-cause-agent-2026-08-17.1"] = Field(
        default="xingce-cause-agent-2026-08-17.1",
        alias="definitionReleaseId",
    )
    agent_key: Literal["xingce_cause"] = Field(default="xingce_cause", alias="agentKey")
    contract_version: Literal["cause-agent-definition@v1"] = Field(
        default=AGENT_DEFINITION_CONTRACT_VERSION,
        alias="contractVersion",
    )
    prompt_version: Literal["xingce-cause-prompt@v4"] = Field(
        default="xingce-cause-prompt@v4",
        alias="promptVersion",
    )
    schema_version: Literal["cause-candidate@v2"] = Field(
        default="cause-candidate@v2",
        alias="schemaVersion",
    )
    behavior_version: Literal["xingce-cause-behavior@v1"] = Field(
        default="xingce-cause-behavior@v1",
        alias="behaviorVersion",
    )
    completion_condition_version: Literal["cause-completion@v1"] = Field(
        default="cause-completion@v1",
        alias="completionConditionVersion",
    )
    tool_policy_version: Literal["xingce-cause-read-only@v1"] = Field(
        default=TOOL_PERMISSION_POLICY_VERSION,
        alias="toolPolicyVersion",
    )
    tools: tuple[ToolDefinition, ...]
    budget: AgentBudget = Field(default_factory=AgentBudget)

    @model_validator(mode="after")
    def validate_toolset(self) -> "AgentDefinition":
        names = [tool.name for tool in self.tools]
        expected = [
            "load_attempt_evidence",
            "load_user_reflection",
            "load_published_question_explanation",
            "load_cause_taxonomy",
            "load_action_trace",
            "load_ink_formulas",
        ]
        if names != expected:
            raise ValueError("cause agent toolset must match the frozen ordered allowlist")
        return self

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.model_dump(by_alias=True, mode="json"))


CAUSE_AGENT_DEFINITION = AgentDefinition(
    tools=tuple(
        ToolDefinition(name=name)
        for name in (
            "load_attempt_evidence",
            "load_user_reflection",
            "load_published_question_explanation",
            "load_cause_taxonomy",
            "load_action_trace",
            "load_ink_formulas",
        )
    )
)


@dataclass(frozen=True)
class ToolExecutionResult:
    call_id: str
    tool_name: str
    payload: dict[str, Any]
    input_fingerprint: str
    output_fingerprint: str
    progress_fingerprint: str


class AgentRuntimeViolation(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def fingerprint(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
