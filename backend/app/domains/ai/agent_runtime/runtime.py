from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.domains.ai.agent_runtime.contracts import (
    AgentDefinition,
    AgentRuntimeViolation,
    ToolExecutionResult,
    fingerprint,
)


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
ToolEventSink = Callable[[dict[str, Any]], None]


@dataclass
class BoundedToolRuntime:
    definition: AgentDefinition
    handlers: dict[str, ToolHandler]
    event_sink: ToolEventSink
    _successful_signatures: set[str] = field(default_factory=set)
    _tool_call_count: int = 0
    _last_progress: str | None = None
    _consecutive_no_progress: int = 0

    @property
    def tool_call_count(self) -> int:
        return self._tool_call_count

    @property
    def consecutive_no_progress(self) -> int:
        return self._consecutive_no_progress

    def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        progress_scope: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        definitions = {tool.name: tool for tool in self.definition.tools}
        tool = definitions.get(tool_name)
        call_id = str(uuid4())
        input_fingerprint = fingerprint(arguments)
        sequence_no = self._tool_call_count + 1
        base_event = {
            "callId": call_id,
            "sequenceNo": sequence_no,
            "toolName": tool_name,
            "toolSchemaVersion": tool.schema_version if tool else "unknown",
            "toolBehaviorVersion": tool.behavior_version if tool else "unknown",
            "permissionPolicyVersion": (
                tool.permission_policy_version if tool else self.definition.tool_policy_version
            ),
            "inputFingerprint": input_fingerprint,
        }
        self.event_sink({**base_event, "eventType": "requested"})
        if tool is None or tool_name not in self.handlers:
            self.event_sink(
                {
                    **base_event,
                    "eventType": "denied",
                    "errorCode": "tool_not_allowlisted",
                }
            )
            raise AgentRuntimeViolation("tool_not_allowlisted")
        if not tool.read_only:
            self.event_sink(
                {
                    **base_event,
                    "eventType": "denied",
                    "errorCode": "tool_write_forbidden",
                }
            )
            raise AgentRuntimeViolation("tool_write_forbidden")
        if self._tool_call_count >= self.definition.budget.max_tool_calls:
            self.event_sink(
                {
                    **base_event,
                    "eventType": "denied",
                    "errorCode": "tool_call_budget_exhausted",
                }
            )
            raise AgentRuntimeViolation("tool_call_budget_exhausted")
        signature = fingerprint(
            {
                "toolName": tool_name,
                "schemaVersion": tool.schema_version,
                "behaviorVersion": tool.behavior_version,
                "arguments": arguments,
            }
        )
        if signature in self._successful_signatures:
            self.event_sink(
                {
                    **base_event,
                    "eventType": "denied",
                    "errorCode": "duplicate_tool_call",
                }
            )
            raise AgentRuntimeViolation("duplicate_tool_call")
        self.event_sink({**base_event, "eventType": "allowed"})
        try:
            payload = self.handlers[tool_name](arguments)
        except Exception:
            self.event_sink(
                {
                    **base_event,
                    "eventType": "failed",
                    "errorCode": "tool_execution_failed",
                }
            )
            raise
        self._tool_call_count += 1
        self._successful_signatures.add(signature)
        output_fingerprint = fingerprint(payload)
        progress_fingerprint = fingerprint(progress_scope or payload)
        if progress_fingerprint == self._last_progress:
            self._consecutive_no_progress += 1
        else:
            self._last_progress = progress_fingerprint
            self._consecutive_no_progress = 0
        self.event_sink(
            {
                **base_event,
                "eventType": "succeeded",
                "outputFingerprint": output_fingerprint,
                "progressFingerprint": progress_fingerprint,
            }
        )
        if (
            self._consecutive_no_progress
            >= self.definition.budget.max_consecutive_no_progress
        ):
            raise AgentRuntimeViolation("no_progress_budget_exhausted")
        return ToolExecutionResult(
            call_id=call_id,
            tool_name=tool_name,
            payload=payload,
            input_fingerprint=input_fingerprint,
            output_fingerprint=output_fingerprint,
            progress_fingerprint=progress_fingerprint,
        )
