from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domains.ai.agent_runtime.contracts import (
    CAUSE_AGENT_DEFINITION,
    AgentRuntimeViolation,
)
from app.domains.ai.agent_runtime.runtime import BoundedToolRuntime


def test_duplicate_successful_tool_signature_is_terminated() -> None:
    events: list[dict] = []
    runtime = BoundedToolRuntime(
        definition=CAUSE_AGENT_DEFINITION,
        handlers={
            tool.name: lambda arguments: {"ok": arguments} for tool in CAUSE_AGENT_DEFINITION.tools
        },
        event_sink=events.append,
    )
    arguments = {"scope": "one"}
    runtime.execute(tool_name="load_attempt_evidence", arguments=arguments)
    with pytest.raises(AgentRuntimeViolation, match="duplicate_tool_call"):
        runtime.execute(tool_name="load_attempt_evidence", arguments=arguments)
    assert events[-1]["eventType"] == "denied"
    assert events[-1]["errorCode"] == "duplicate_tool_call"


def test_unknown_tool_and_two_consecutive_no_progress_steps_terminate() -> None:
    events: list[dict] = []
    runtime = BoundedToolRuntime(
        definition=CAUSE_AGENT_DEFINITION,
        handlers={
            tool.name: lambda arguments: {"value": arguments}
            for tool in CAUSE_AGENT_DEFINITION.tools
        },
        event_sink=events.append,
    )
    with pytest.raises(AgentRuntimeViolation, match="tool_not_allowlisted"):
        runtime.execute(tool_name="write_memory", arguments={})

    runtime.execute(
        tool_name="load_attempt_evidence",
        arguments={"scope": "a"},
        progress_scope={"progress": "same"},
    )
    runtime.execute(
        tool_name="load_user_reflection",
        arguments={"scope": "b"},
        progress_scope={"progress": "same"},
    )
    with pytest.raises(AgentRuntimeViolation, match="no_progress_budget_exhausted"):
        runtime.execute(
            tool_name="load_published_question_explanation",
            arguments={"scope": "c"},
            progress_scope={"progress": "same"},
        )


def test_runtime_accepts_an_independent_agent_definition_protocol() -> None:
    @dataclass(frozen=True)
    class Tool:
        name: str = "inspect"
        schema_version: str = "v1"
        behavior_version: str = "v1"
        permission_policy_version: str = "test-read-only@v1"
        read_only: bool = True

    @dataclass(frozen=True)
    class Budget:
        max_tool_calls: int = 1
        max_consecutive_no_progress: int = 1

    @dataclass(frozen=True)
    class Definition:
        tools: tuple = (Tool(),)
        budget: Budget = Budget()
        tool_policy_version: str = "test-read-only@v1"

    runtime = BoundedToolRuntime(
        definition=Definition(),  # type: ignore[arg-type]
        handlers={"inspect": lambda arguments: {"seen": arguments}},
        event_sink=lambda _event: None,
    )

    result = runtime.execute(tool_name="inspect", arguments={"scope": "one"})

    assert result.payload == {"seen": {"scope": "one"}}
