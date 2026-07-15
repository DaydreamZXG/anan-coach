from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.domains.planning.errors import (
    PlanningContractError,
    PlanningTaskExpiredError,
)


def require_executable_plan_task(
    task: Mapping[str, Any],
    *,
    at: datetime,
) -> None:
    """Reject new transitions after the immutable daily-plan validity window.

    Idempotent replays are intentionally checked by callers before invoking this
    guard, so an already committed command remains replayable after midnight.
    """

    valid_until = task.get("plan_valid_until")
    if not isinstance(valid_until, datetime):
        raise PlanningContractError("Plan task is missing its validity boundary")
    checked_at = _as_utc(at)
    expires_at = _as_utc(valid_until)
    if checked_at >= expires_at:
        raise PlanningTaskExpiredError(
            task_id=str(task["task_id"]),
            plan_version_id=str(task["plan_version_id"]),
            expired_at=expires_at,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
