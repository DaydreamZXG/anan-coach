from __future__ import annotations

from app.domains.planning.engine import (
    PriorityBreakdown,
    TaskCandidate,
    select_daily_tasks,
)


def test_normal_day_selects_xingce_and_essay_within_one_budget() -> None:
    selection = select_daily_tasks(
        [
            _candidate("xingce-practice", "xingce", "objective_practice", 20, 0.70),
            _candidate("essay-baseline", "essay", "essay_drafting", 25, 0.74),
            _candidate("xingce-extra", "xingce", "objective_verification", 15, 0.68),
        ],
        available_minutes=45,
        recent_track_minutes={"xingce": 100, "essay": 20},
    )

    assert {item.candidate.track for item in selection.tasks} == {"xingce", "essay"}
    assert selection.scheduled_minutes == 45
    assert selection.dual_track_constraint_applied is True
    assert "xingce_essay_dual_track" in selection.explanation_codes


def test_dual_track_is_a_constraint_not_fixed_leftover_allocation() -> None:
    selection = select_daily_tasks(
        [
            _candidate("xingce", "xingce", "objective_practice", 20, 0.80),
            _candidate("essay", "essay", "essay_rewrite", 25, 0.79, urgency=0.95),
            _candidate("essay-extra", "essay", "essay_drafting", 30, 0.77),
        ],
        available_minutes=45,
    )

    assert [item.candidate.candidate_key for item in selection.tasks] == ["essay", "xingce"]
    assert selection.scheduled_minutes == 45


def test_small_capacity_chooses_highest_evidence_weighted_task() -> None:
    selection = select_daily_tasks(
        [
            _candidate("xingce", "xingce", "objective_practice", 20, 0.60),
            _candidate("essay", "essay", "essay_rewrite", 25, 0.75, urgency=0.95),
        ],
        available_minutes=25,
    )

    assert [item.candidate.candidate_key for item in selection.tasks] == ["essay"]
    assert selection.dual_track_constraint_applied is False


def test_recovery_day_only_schedules_due_review_without_new_learning() -> None:
    selection = select_daily_tasks(
        [
            _candidate("xingce-new", "xingce", "objective_practice", 15, 0.95),
            _candidate("essay-new", "essay", "essay_drafting", 15, 0.92),
            _candidate("essay-rewrite", "essay", "essay_rewrite", 20, 0.70, urgency=0.85),
        ],
        available_minutes=60,
        recovery_day=True,
    )

    assert [item.candidate.candidate_key for item in selection.tasks] == ["essay-rewrite"]
    assert selection.scheduled_minutes == 20
    assert selection.explanation_codes == ("recovery_day_no_new_learning",)


def test_mandatory_mock_keeps_priority_but_does_not_discard_remaining_capacity() -> None:
    selection = select_daily_tasks(
        [
            _candidate("mock", "mock", "weekly_mock", 120, 0.90, mandatory=True),
            _candidate("xingce", "xingce", "objective_practice", 20, 0.95),
            _candidate("essay", "essay", "essay_drafting", 25, 0.95),
        ],
        available_minutes=150,
    )

    assert [item.candidate.candidate_key for item in selection.tasks] == ["essay", "mock"]
    assert selection.scheduled_minutes == 145
    assert "mandatory_calibration_first" in selection.explanation_codes


def test_fulltime_capacity_scales_to_ten_hour_work_blocks() -> None:
    selection = select_daily_tasks(
        [
            _candidate(f"xingce-{index}", "xingce", "objective_practice", 30, 0.80 - index / 100)
            for index in range(16)
        ]
        + [_candidate("essay", "essay", "essay_drafting", 30, 0.84)],
        available_minutes=600,
    )

    assert len(selection.tasks) == 16
    assert selection.scheduled_minutes == 480
    assert {item.candidate.track for item in selection.tasks} == {"xingce", "essay"}


def test_unfit_mandatory_mock_does_not_erase_a_short_users_day() -> None:
    selection = select_daily_tasks(
        [
            _candidate("mock", "mock", "weekly_mock", 120, 0.90, mandatory=True),
            _candidate("xingce", "xingce", "objective_practice", 20, 0.75),
            _candidate("essay", "essay", "essay_material_reading", 15, 0.78),
        ],
        available_minutes=45,
    )

    assert {item.candidate.track for item in selection.tasks} == {"xingce", "essay"}
    assert selection.scheduled_minutes == 35
    assert "mandatory_task_capacity_insufficient" in selection.explanation_codes


def test_recent_track_imbalance_adds_auditable_bonus() -> None:
    selection = select_daily_tasks(
        [
            _candidate("xingce", "xingce", "objective_practice", 20, 0.70),
            _candidate("essay", "essay", "essay_drafting", 20, 0.70),
        ],
        available_minutes=20,
        recent_track_minutes={"xingce": 120, "essay": 0},
    )

    assert selection.tasks[0].candidate.candidate_key == "essay"
    assert selection.tasks[0].track_balance_bonus > 0
    assert "rolling_track_balance" in selection.explanation_codes


def _candidate(
    key: str,
    track: str,
    stage: str,
    minutes: int,
    gain: float,
    *,
    urgency: float = 0.4,
    mandatory: bool = False,
) -> TaskCandidate:
    return TaskCandidate(
        candidate_key=key,
        track=track,  # type: ignore[arg-type]
        stage=stage,
        kind="mock_exam" if track == "mock" else "essay" if track == "essay" else "practice",
        title=key,
        reason=f"{key} reason",
        estimated_minutes=minutes,
        completion_rule=f"finish {key}",
        start_url=f"/tasks/{key}",
        evidence_refs=({"owner": "test", "entityId": key},),
        priority=PriorityBreakdown(
            goal_gain=gain,
            ability_gain=gain,
            verification_urgency=urgency,
            time_fit=0.9,
            load_penalty=0.1,
        ),
        mandatory=mandatory,
    )
