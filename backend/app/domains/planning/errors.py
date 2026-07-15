from __future__ import annotations

from datetime import UTC, datetime


class PlanningContractError(RuntimeError):
    """The requested planning transition violates the durable plan contract."""


class PlanningTaskExpiredError(PlanningContractError):
    """A non-replay command targeted a daily-plan task past its validity window."""

    code = "plan_task_expired"

    def __init__(
        self,
        *,
        task_id: str,
        plan_version_id: str,
        expired_at: datetime,
    ) -> None:
        self.task_id = task_id
        self.plan_version_id = plan_version_id
        self.expired_at = _as_utc(expired_at)
        super().__init__(
            "The plan task is no longer executable because its plan expired"
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
