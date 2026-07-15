from __future__ import annotations

from app.domains.planning.candidates import (
    EssayPlanningSnapshot,
    GoalPlanningSnapshot,
    ObjectivePlanningSnapshot,
    build_dual_track_candidates,
)


def test_missing_component_evidence_creates_both_baseline_tracks() -> None:
    candidates = build_dual_track_candidates(
        goal=_goal(readiness="partial"),
        objective=ObjectivePlanningSnapshot(True, 0, 0, None, None, "available", None),
        essay=EssayPlanningSnapshot(
            "baseline_due",
            "essay-task-v1",
            material_bundle_version_id="essay-material-v1",
            rubric_version_id="essay-rubric-v1",
            question_type="summary",
            word_limit=200,
            maximum_score=20,
        ),
        available_minutes=45,
    )

    assert {item.stage for item in candidates.candidates} == {
        "objective_baseline",
        "essay_material_reading",
    }
    assert all(item.evidence_refs for item in candidates.candidates)
    essay = next(item for item in candidates.candidates if item.track == "essay")
    assert {
        ref["entityType"] for ref in essay.evidence_refs
    } >= {"essay_task_version", "essay_material_bundle_version", "essay_rubric_version"}


def test_unresolved_essay_review_becomes_rewrite_instead_of_fixed_daily_prompt() -> None:
    candidates = build_dual_track_candidates(
        goal=_goal(),
        objective=ObjectivePlanningSnapshot(
            False, 0, 0, "资料分析", "objective-v3", "available", None
        ),
        essay=EssayPlanningSnapshot(
            "rewrite_due",
            "essay-task-v2",
            attempt_id="attempt-1",
            review_version_id="review-1",
        ),
        available_minutes=60,
    )

    essay = next(item for item in candidates.candidates if item.track == "essay")
    assert essay.stage == "essay_rewrite"
    assert essay.estimated_minutes == 20
    assert "未解决 finding" in essay.reason
    assert any(ref["entityType"] == "essay_review_version" for ref in essay.evidence_refs)


def test_finished_rewrite_requires_fresh_material_transfer() -> None:
    candidates = build_dual_track_candidates(
        goal=_goal(),
        objective=ObjectivePlanningSnapshot(
            False, 0, 1, "判断推理", "objective-v4", "available", None
        ),
        essay=EssayPlanningSnapshot(
            "transfer_due",
            "essay-task-v3",
            attempt_id="attempt-2",
            review_version_id="review-2",
            rewrite_version_id="rewrite-2",
        ),
        available_minutes=60,
    )

    essay = next(item for item in candidates.candidates if item.track == "essay")
    assert essay.stage == "essay_transfer"
    assert "不能证明能力稳定" in essay.reason


def test_missing_published_essay_content_is_explicitly_omitted() -> None:
    candidates = build_dual_track_candidates(
        goal=_goal(),
        objective=ObjectivePlanningSnapshot(False, 0, 0, None, None, "available", None),
        essay=EssayPlanningSnapshot("content_unavailable", None),
        available_minutes=45,
    )

    assert all(item.track != "essay" for item in candidates.candidates)
    assert "不生成不可执行任务" in candidates.omitted_track_reasons["essay"]


def test_short_capacity_gets_a_real_material_stage_instead_of_starving_essay() -> None:
    candidates = build_dual_track_candidates(
        goal=_goal(readiness="partial"),
        objective=ObjectivePlanningSnapshot(True, 0, 0, None, None, "available", None),
        essay=EssayPlanningSnapshot(
            "baseline_due",
            "essay-composition-v1",
            material_bundle_version_id="material-v1",
            rubric_version_id="rubric-v1",
            question_type="composition",
            word_limit=1000,
            maximum_score=40,
        ),
        available_minutes=20,
    )

    essay = next(item for item in candidates.candidates if item.track == "essay")
    assert essay.stage == "essay_material_reading"
    assert essay.estimated_minutes == 15
    assert "逐段材料证据" in essay.reason


def test_unpublished_rubric_allows_reading_but_never_fakes_scored_drafting() -> None:
    candidates = build_dual_track_candidates(
        goal=_goal(),
        objective=ObjectivePlanningSnapshot(
            False, 0, 0, "资料分析", "objective-v1", "available", None
        ),
        essay=EssayPlanningSnapshot(
            "baseline_due",
            "essay-task-v1",
            material_bundle_version_id="material-v1",
            rubric_version_id=None,
            question_type="summary",
            word_limit=200,
            maximum_score=20,
        ),
        available_minutes=60,
    )

    essay = next(item for item in candidates.candidates if item.track == "essay")
    assert essay.stage == "essay_material_reading"
    assert "可审计评分规程尚未发布" in essay.reason
    assert all(ref["entityType"] != "essay_rubric_version" for ref in essay.evidence_refs)


def test_unavailable_objective_closure_has_no_synthetic_counts_or_candidates() -> None:
    reason = "canonical objective reflection and verification writers are unavailable"
    candidates = build_dual_track_candidates(
        goal=_goal(),
        objective=ObjectivePlanningSnapshot(
            False,
            None,
            None,
            "资料分析",
            "objective-v1",
            "unavailable",
            reason,
        ),
        essay=EssayPlanningSnapshot("content_unavailable", None),
        available_minutes=45,
    )

    assert candidates.due_reflection_count is None
    assert candidates.due_verification_count is None
    assert candidates.capability_gaps == {"objectiveClosure": reason}
    assert not any(
        item.stage in {"objective_reflection", "objective_verification"}
        for item in candidates.candidates
    )


def _goal(readiness: str = "complete") -> GoalPlanningSnapshot:
    return GoalPlanningSnapshot(
        readiness=readiness,  # type: ignore[arg-type]
        positive_gap=True,
        goal_version_id="goal-v1",
        ability_estimate_version_id="ability-v1",
    )
