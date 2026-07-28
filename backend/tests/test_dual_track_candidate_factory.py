from __future__ import annotations

import pytest

from app.domains.planning.candidates import (
    EssayPlanningSnapshot,
    GoalPlanningSnapshot,
    ObjectivePlanningSnapshot,
    build_dual_track_candidates,
)
from app.domains.planning.repository import _strict_cause_decision_version_id


def test_missing_component_evidence_creates_both_baseline_tracks() -> None:
    candidates = build_dual_track_candidates(
        goal=_goal(readiness="partial"),
        objective=ObjectivePlanningSnapshot(
            True,
            0,
            0,
            None,
            None,
            "available",
            None,
            baseline_question_count=3,
        ),
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
            material_bundle_version_id="essay-material-v2",
            rubric_version_id="essay-rubric-v2",
            attempt_id="attempt-1",
            answer_version_id="current-answer-1",
            review_version_id="review-1",
            source_task_version_id="essay-task-v2",
            source_material_bundle_version_id="essay-material-v2",
            source_answer_version_id="original-answer-1",
            finding_decision_artifact_version_id="decision-1",
            mastery_state="rewrite_due",
            mastery_ids=("mastery-1",),
            mastery_version_ids=("mastery-version-1",),
        ),
        available_minutes=60,
    )

    essay = next(item for item in candidates.candidates if item.track == "essay")
    assert essay.stage == "essay_rewrite"
    assert essay.estimated_minutes == 20
    assert "未解决 finding" in essay.reason
    assert any(ref["entityType"] == "essay_review_version" for ref in essay.evidence_refs)
    assert "stage=essay_rewrite" in essay.start_url
    assert "attemptId=attempt-1" in essay.start_url
    assert "baseAnswerVersionId=original-answer-1" in essay.start_url
    assert "basedOnAnswerVersionId=current-answer-1" in essay.start_url
    assert {
        ref["entityType"] for ref in essay.evidence_refs
    } >= {
        "essay_mastery_state_version",
        "essay_source_answer_version",
        "essay_finding_decision",
    }


def test_finished_rewrite_requires_fresh_material_transfer() -> None:
    candidates = build_dual_track_candidates(
        goal=_goal(),
        objective=ObjectivePlanningSnapshot(
            False,
            0,
            1,
            "判断推理",
            "objective-v4",
            "available",
            None,
            due_verification_mastery_id="00000000-0000-4000-8000-000000000001",
            due_verification_version_id="00000000-0000-4000-8000-000000000002",
        ),
        essay=EssayPlanningSnapshot(
            "transfer_due",
            "essay-target-task-v3",
            material_bundle_version_id="essay-target-material-v3",
            rubric_version_id="essay-target-rubric-v3",
            attempt_id="attempt-2",
            answer_version_id="current-answer-2",
            review_version_id="review-2",
            rewrite_version_id="rewrite-2",
            source_task_version_id="essay-source-task-v2",
            source_material_bundle_version_id="essay-source-material-v2",
            source_answer_version_id="source-answer-2",
            finding_decision_artifact_version_id="decision-2",
            mastery_state="transfer_due",
            mastery_ids=("mastery-2",),
            mastery_version_ids=("mastery-version-2",),
        ),
        available_minutes=60,
    )

    essay = next(item for item in candidates.candidates if item.track == "essay")
    assert essay.stage == "essay_transfer"
    assert "不能证明能力稳定" in essay.reason
    assert "taskVersionId=essay-target-task-v3" in essay.start_url
    assert "sourceRewriteVersionId=rewrite-2" in essay.start_url
    assert any(
        ref["entityType"] == "essay_source_task_version"
        and ref["entityId"] == "essay-source-task-v2"
        for ref in essay.evidence_refs
    )


def test_due_verification_projects_only_the_frozen_cause_decision_version() -> None:
    decision_version_id = "00000000-0000-4000-8000-000000000003"
    candidates = build_dual_track_candidates(
        goal=_goal(),
        objective=ObjectivePlanningSnapshot(
            False,
            0,
            1,
            "判断推理",
            "00000000-0000-4000-8000-000000000002",
            "available",
            None,
            due_verification_mastery_id="00000000-0000-4000-8000-000000000001",
            due_verification_version_id="00000000-0000-4000-8000-000000000002",
            due_verification_decision_version_id=decision_version_id,
            verification_question_count=3,
        ),
        essay=EssayPlanningSnapshot("content_unavailable", None),
        available_minutes=45,
    )

    verification = next(
        item for item in candidates.candidates if item.stage == "objective_verification"
    )
    assert [
        ref
        for ref in verification.evidence_refs
        if ref.get("role") == "immediate_verification_source"
    ] == [
        {
            "owner": "objective_learning",
            "entityType": "xingce_cause_decision_version",
            "entityId": decision_version_id,
            "versionId": decision_version_id,
            "role": "immediate_verification_source",
        }
    ]


def test_due_verification_rejects_noncanonical_decision_version() -> None:
    with pytest.raises(ValueError, match="canonical UUID"):
        ObjectivePlanningSnapshot(
            False,
            0,
            1,
            "判断推理",
            "objective-v4",
            "available",
            None,
            due_verification_mastery_id="00000000-0000-4000-8000-000000000001",
            due_verification_version_id="00000000-0000-4000-8000-000000000002",
            due_verification_decision_version_id="not-a-uuid",
        )


def test_mastery_evidence_requires_exactly_one_canonical_cause_decision_version() -> None:
    decision_version_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"
    other_version_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4"
    assert (
        _strict_cause_decision_version_id(
            [
                "review-version:00000000-0000-4000-8000-000000000001",
                f"cause-decision-version:{decision_version_id}",
                "unknown-evidence:ignored",
            ]
        )
        == decision_version_id
    )
    for refs in (
        [],
        [f"cause-decision-version:{decision_version_id}"] * 2,
        [
            f"cause-decision-version:{decision_version_id}",
            f"cause-decision-version:{other_version_id}",
        ],
        ["cause-decision-version:not-a-uuid"],
        [f"cause-decision-version:{decision_version_id.upper()}"],
        {"cause-decision-version": decision_version_id},
    ):
        assert _strict_cause_decision_version_id(refs) is None


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


def test_unavailable_essay_closure_is_a_capability_gap_not_fake_drafting() -> None:
    reason = "transfer_due head lacks a distinct published task and material"
    candidates = build_dual_track_candidates(
        goal=_goal(),
        objective=ObjectivePlanningSnapshot(
            False, 0, 0, "资料分析", "objective-v1", "available", None
        ),
        essay=EssayPlanningSnapshot(
            "closure_unavailable",
            None,
            pending_reason=reason,
            closure_readiness="unavailable",
            closure_unavailable_reason=reason,
        ),
        available_minutes=45,
    )

    assert candidates.capability_gaps["essayClosure"] == reason
    assert candidates.omitted_track_reasons["essay"] == reason
    assert not any(item.track == "essay" for item in candidates.candidates)


def _goal(readiness: str = "complete") -> GoalPlanningSnapshot:
    return GoalPlanningSnapshot(
        readiness=readiness,  # type: ignore[arg-type]
        positive_gap=True,
        goal_version_id="goal-v1",
        ability_estimate_version_id="ability-v1",
    )
