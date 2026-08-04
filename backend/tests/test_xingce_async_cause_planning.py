from __future__ import annotations

import pytest

from app.domains.planning.candidates import (
    ACTIONABLE_REFLECTION_STATES,
    ActionableReflectionState,
    EssayPlanningSnapshot,
    GoalPlanningSnapshot,
    ObjectivePlanningSnapshot,
    build_dual_track_candidates,
)


def test_processing_state_is_not_an_actionable_today_task() -> None:
    assert "cause_candidate_pending" not in ACTIONABLE_REFLECTION_STATES


@pytest.mark.parametrize(
    ("state", "expected_title"),
    (
        ("reflection_due", "复盘高价值行测错题"),
        ("cause_followup_due", "回答岸岸的一次追问"),
        ("cause_decision_due", "查看岸岸整理结果"),
        ("cause_evidence_insufficient", "查看岸岸整理结果"),
        ("cause_agent_terminated", "处理未完成的错因整理"),
    ),
)
def test_actionable_cause_state_returns_to_today_with_precise_copy(
    state: ActionableReflectionState,
    expected_title: str,
) -> None:
    result = build_dual_track_candidates(
        goal=GoalPlanningSnapshot(
            readiness="complete",
            positive_gap=True,
            goal_version_id="goal-v1",
            ability_estimate_version_id="ability-v1",
        ),
        objective=ObjectivePlanningSnapshot(
            baseline_required=False,
            due_reflection_count=1,
            due_verification_count=0,
            weakest_module=None,
            source_version_id="review-version-1",
            closure_readiness="available",
            closure_unavailable_reason=None,
            due_reflection_session_id="session-1",
            due_reflection_review_id="review-1",
            due_reflection_version_id="review-version-1",
            due_reflection_state=state,
        ),
        essay=EssayPlanningSnapshot("content_unavailable", None),
        available_minutes=60,
    )

    task = next(candidate for candidate in result.candidates if candidate.kind == "reflection")
    assert task.title == expected_title
    assert task.start_url == "/pages/reflection/index?sessionId=session-1&reviewId=review-1"
