from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, UTC
from typing import Any

import pytest

from app.domains.ai.governance.contracts import GlobalAskContext
from app.domains.ai.governance.retrieval import (
    AuthorizedHybridRetriever,
    ObjectAuthorizationError,
)


USER_ID = "00000000-0000-4000-8000-00000000e101"
PROFILE_ID = "00000000-0000-4000-8000-00000000e201"
PLAN_ID = "00000000-0000-4000-8000-00000000e301"
PATTERN_ID = "a" * 64


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        if self.value is None:
            return None
        assert isinstance(self.value, dict)
        return self.value

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if isinstance(self.value, list):
            return iter(self.value)
        return iter(())


class _Session:
    def __init__(self, *values: object) -> None:
        self.values = iter(values)
        self.statements: list[str] = []
        self.parameters: list[dict[str, object]] = []

    def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> _Result:
        self.statements.append(str(statement))
        self.parameters.append(parameters or {})
        return _Result(next(self.values))


def _error_context() -> GlobalAskContext:
    return GlobalAskContext(
        routeId="reports/error-pattern/index",
        workspace="profile",
        objectRefs=[
            {"type": "error_pattern", "id": PATTERN_ID, "version": PROFILE_ID}
        ],
        learnerStateVersion=PROFILE_ID,
    )


def _plan_context() -> GlobalAskContext:
    return GlobalAskContext(
        routeId="plans/daily-settlement/index",
        workspace="today",
        objectRefs=[{"type": "plan", "id": PLAN_ID, "version": "3"}],
        learnerStateVersion=PROFILE_ID,
    )


def test_error_pattern_requires_exact_owned_profile_and_retrieves_named_pattern() -> None:
    pattern = {
        "patternId": PATTERN_ID,
        "displayName": "基期量放错位置",
        "track": "xingce",
        "moduleKey": "data_analysis",
        "causeCode": "base_period_misread",
        "status": "confirmed",
        "occurrenceCount": 4,
        "counterevidenceCount": 1,
        "confidence": 0.91,
        "latestObservedAt": "2026-08-10T08:00:00Z",
        "evidenceRefs": ["attempt-4"],
    }
    session = _Session(
        True,
        {"version_no": 7, "generated_at": datetime.now(UTC), "pattern": pattern},
        [],
    )
    bundle = AuthorizedHybridRetriever(
        session,  # type: ignore[arg-type]
        resolve_projection_from_environment=False,
    ).retrieve(
        user_id=USER_ID,
        question="这个错误模式是什么？",
        context=_error_context(),
    )

    source = next(item for item in bundle.sources if item.trust_scope == "authorized_error_pattern")
    assert source.reference.version == PROFILE_ID
    assert "基期量放错位置" in source.excerpt
    assert session.parameters[0] == {
        "user_id": USER_ID,
        "profile_version_id": PROFILE_ID,
        "pattern_id": PATTERN_ID,
    }

    with pytest.raises(ObjectAuthorizationError):
        AuthorizedHybridRetriever(
            _Session(False),  # type: ignore[arg-type]
            resolve_projection_from_environment=False,
        ).retrieve(
            user_id="00000000-0000-4000-8000-00000000e999",
            question="说说这个错误模式",
            context=_error_context(),
        )


def test_daily_plan_context_contains_current_task_execution_facts() -> None:
    daily = {
        "local_date": date(2026, 8, 10),
        "day_mode": "normal",
        "headline": "先收尾资料分析，再复测",
        "rationale": "错因连续出现",
        "goal_gap": {"total": 8},
        "scheduled_minutes": 55,
        "due_reflection_count": 1,
        "due_verification_count": 1,
        "track_explanation": {"xingce": "资料分析"},
        "invalidation_conditions": [],
        "replan_reason_codes": ["execution_changed"],
        "generated_at": datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        "valid_until": datetime(2026, 8, 11, 0, 0, tzinfo=UTC),
        "tasks": [
            {
                "order": 1,
                "title": "资料分析复测",
                "status": "complete",
                "elapsedMinutes": 24,
                "executionEvidenceRefs": [{"entityId": "attempt-24"}],
            }
        ],
    }
    session = _Session(True, daily, [])
    bundle = AuthorizedHybridRetriever(
        session,  # type: ignore[arg-type]
        resolve_projection_from_environment=False,
    ).retrieve(
        user_id=USER_ID,
        question="今天完成了什么？",
        context=_plan_context(),
    )

    source = next(item for item in bundle.sources if item.trust_scope == "authorized_plan")
    assert source.reference.source_id == f"daily-plan:{PLAN_ID}"
    assert "complete" in source.excerpt
    assert "24" in source.excerpt
    assert "attempt-24" in source.excerpt
    assert "task_execution_versions" in session.statements[1]
