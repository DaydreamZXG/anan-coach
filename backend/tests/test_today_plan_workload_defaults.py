from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.domains.planning.repository import CapacitySnapshot
from app.domains.planning.schemas import GoalGap
from app.domains.planning.service import (
    PLAN_ALGORITHM_VERSION,
    PlanningService,
    _needs_adaptive_recovery,
    _plan_copy,
)
from tests import test_planning_routes as planning_fixtures


def _capacity(
    *,
    available_minutes: int = 480,
    energy: int = 3,
    stress: int = 3,
    source_type: str = "weekly_preference",
    confirmed: bool = False,
) -> CapacitySnapshot:
    return CapacitySnapshot(
        capacity_version_id="00000000-0000-4000-8000-000000000806",
        version_no=1,
        available_minutes=available_minutes,
        energy=energy,
        stress=stress,
        confidence_level=3,
        preferred_xingce_module=None,
        xingce_only=False,
        preferred_track="balanced",
        confirmed=confirmed,
        source_type=source_type,
        input_fingerprint="weekly-default-capacity",
    )


def test_weekly_capacity_is_an_effective_default_without_confirmation(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = planning_fixtures._seed_learner_and_published_essay_content(db_session_factory)

    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=planning_fixtures.NOW,
            local_date=planning_fixtures.NOW.date(),
        )

    assert plan.capacity.source_type == "weekly_preference"
    assert plan.capacity.confirmed is False
    assert plan.capacity.available_minutes == 45
    assert plan.tasks
    assert "按每周计划" in plan.rationale
    assert "等待你确认" not in plan.rationale
    assert "weekly_preference_pending_confirmation" not in plan.replan_reason_codes
    assert "daily_capacity_pending_confirmation" not in plan.replan_reason_codes
    assert PLAN_ALGORITHM_VERSION.endswith("v8")


def test_unconfirmed_weekly_capacity_still_drives_recovery_and_empty_copy() -> None:
    capacity = _capacity(available_minutes=0)
    gap = GoalGap.model_validate(
        {
            "readiness": "complete",
            "goalId": "goal-workload-default",
            "targetScore": {"lower": 130, "upper": 135, "confidence": "medium"},
            "currentScore": {"lower": 118, "upper": 122, "confidence": "medium"},
            "baselineGap": 13,
            "evidenceSummary": "同口径目标分与最近一次两科成绩。",
        }
    )

    assert _needs_adaptive_recovery(capacity) is True
    headline, rationale = _plan_copy(
        day_mode="normal",
        tasks=(),
        capacity=capacity,
        goal_gap=gap,
        essay_available=True,
    )
    assert headline == "今天的正式安排还没就绪"
    assert "已经生效" in rationale
    assert "确认" not in rationale
