from __future__ import annotations

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
        handlers={tool.name: lambda arguments: {"ok": arguments} for tool in CAUSE_AGENT_DEFINITION.tools},
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
        handlers={tool.name: lambda arguments: {"value": arguments} for tool in CAUSE_AGENT_DEFINITION.tools},
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
