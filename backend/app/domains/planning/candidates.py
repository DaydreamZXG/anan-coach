from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal
from uuid import UUID

from app.domains.planning.engine import PriorityBreakdown, TaskCandidate
from app.domains.planning.cause_training import ConfirmedCauseDirection
from app.domains.xingce_learning.contracts import (
    XINGCE_QUICK_QUESTION_BUNDLE_SIZE,
    XINGCE_MINUTES_PER_QUESTION,
    xingce_practice_minutes,
)


# Floor of `_essay_drafting_minutes`: summary/comprehensive drafting with a
# short word limit always lands on 25 minutes. A standard 45-minute parttime
# day must still fit both tracks, so xingce reserves this essay floor before
# taking its own block.
MIN_ESSAY_DRAFTING_MINUTES = 25

# Per-block ceiling for xingce targeted practice. `xingce_practice_minutes`
# sizes one focused block up to 60 minutes, but a 60-minute block is 30
# questions and too coarse for full-time study.  Capping each block at 30
# minutes keeps each task a checkable unit while letting full-time days
# surface more, finer-grained xingce tasks.  Small days are unaffected:
# their block is already under 30 minutes.
XINGCE_TASK_BLOCK_MINUTES = 30


AbilityReadiness = Literal["unavailable", "partial", "complete", "stale"]
ObjectiveClosureReadiness = Literal["available", "unavailable"]
EssayClosureReadiness = Literal["available", "unavailable"]
ActionableReflectionState = Literal[
    "reflection_due",
    "cause_followup_due",
    "cause_decision_due",
    "cause_evidence_insufficient",
    "cause_agent_terminated",
]
ACTIONABLE_REFLECTION_STATES: tuple[ActionableReflectionState, ...] = (
    "reflection_due",
    "cause_followup_due",
    "cause_decision_due",
    "cause_evidence_insufficient",
    "cause_agent_terminated",
)
EssayNextStage = Literal[
    "content_unavailable",
    "baseline_due",
    "point_extraction_due",
    "outline_due",
    "drafting_due",
    "evaluation_pending",
    "review_due",
    "rewrite_due",
    "transfer_due",
    "practice_due",
    "closure_unavailable",
]


@dataclass(frozen=True)
class GoalPlanningSnapshot:
    readiness: AbilityReadiness
    positive_gap: bool
    goal_version_id: str | None
    ability_estimate_version_id: str | None
    target_owner: str = "position_goal"
    target_entity_type: str = "position_goal_version"


@dataclass(frozen=True)
class ObjectivePlanningSnapshot:
    baseline_required: bool
    due_reflection_count: int | None
    due_verification_count: int | None
    weakest_module: str | None
    source_version_id: str | None
    closure_readiness: ObjectiveClosureReadiness
    closure_unavailable_reason: str | None
    due_reflection_session_id: str | None = None
    due_reflection_review_id: str | None = None
    due_reflection_version_id: str | None = None
    due_verification_mastery_id: str | None = None
    due_verification_version_id: str | None = None
    due_verification_decision_version_id: str | None = None
    baseline_question_count: int = 0
    targeted_question_count: int = 0
    verification_question_count: int = 0
    due_reflection_state: ActionableReflectionState | None = None
    # 已确认错因主导的针对性训练方向。有近期已确认错因时为该方向
    # （含错因码、方向 key、展示名），否则为 None，候选落到模块级最弱。
    caused_training_direction: ConfirmedCauseDirection | None = None

    def __post_init__(self) -> None:
        if (
            min(
                self.baseline_question_count,
                self.targeted_question_count,
                self.verification_question_count,
            )
            < 0
        ):
            raise ValueError("objective question supply counts cannot be negative")
        if self.due_verification_decision_version_id is not None and not _is_canonical_uuid(
            self.due_verification_decision_version_id
        ):
            raise ValueError("due verification decision version must be a canonical UUID")
        if self.closure_readiness == "available":
            if self.due_reflection_count is None or self.due_verification_count is None:
                raise ValueError("available objective closure requires canonical due counts")
            if self.closure_unavailable_reason is not None:
                raise ValueError("available objective closure cannot have an unavailable reason")
            if self.due_reflection_count > 0 and not all(
                (
                    self.due_reflection_session_id,
                    self.due_reflection_review_id,
                    self.due_reflection_version_id,
                )
            ):
                raise ValueError("due reflection requires a frozen review source")
            if self.due_verification_count > 0 and not all(
                (
                    self.due_verification_mastery_id,
                    self.due_verification_version_id,
                )
            ):
                raise ValueError("due verification requires a frozen mastery source")
            return
        if self.due_reflection_count is not None or self.due_verification_count is not None:
            raise ValueError("unavailable objective closure cannot publish synthetic due counts")
        if not self.closure_unavailable_reason:
            raise ValueError("unavailable objective closure requires an explicit reason")


@dataclass(frozen=True)
class EssayPlanningSnapshot:
    next_stage: EssayNextStage
    task_version_id: str | None
    material_bundle_version_id: str | None = None
    rubric_version_id: str | None = None
    question_type: str | None = None
    word_limit: int | None = None
    maximum_score: float | None = None
    attempt_id: str | None = None
    answer_version_id: str | None = None
    review_version_id: str | None = None
    rewrite_version_id: str | None = None
    transfer_verification_version_id: str | None = None
    material_annotation_version_id: str | None = None
    point_extraction_version_id: str | None = None
    outline_version_id: str | None = None
    review_acknowledgement_version_id: str | None = None
    weakest_dimension: str | None = None
    weakest_dimension_key: str | None = None
    pending_reason: str | None = None
    closure_readiness: EssayClosureReadiness = "available"
    closure_unavailable_reason: str | None = None
    mastery_state: Literal["rewrite_due", "transfer_due", "stable_mastered"] | None = None
    mastery_version_ids: tuple[str, ...] = ()
    mastery_ids: tuple[str, ...] = ()
    source_task_version_id: str | None = None
    source_material_bundle_version_id: str | None = None
    source_answer_version_id: str | None = None
    finding_decision_artifact_version_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.mastery_ids) != len(self.mastery_version_ids):
            raise ValueError("essay mastery IDs and version IDs must align")
        if self.closure_readiness == "unavailable":
            if not self.closure_unavailable_reason:
                raise ValueError("unavailable essay closure requires an explicit reason")
            if self.next_stage != "closure_unavailable":
                raise ValueError("unavailable essay closure cannot publish a writable stage")
        elif self.closure_unavailable_reason is not None:
            raise ValueError("available essay closure cannot carry an unavailable reason")


@dataclass(frozen=True)
class CandidateSet:
    candidates: tuple[TaskCandidate, ...]
    omitted_track_reasons: dict[str, str]
    due_reflection_count: int | None
    due_verification_count: int | None
    capability_gaps: dict[str, str]


def build_dual_track_candidates(
    *,
    goal: GoalPlanningSnapshot,
    objective: ObjectivePlanningSnapshot,
    essay: EssayPlanningSnapshot,
    available_minutes: int,
    preferred_xingce_module: str | None = None,
    xingce_only: bool = False,
) -> CandidateSet:
    candidates: list[TaskCandidate] = []
    omitted: dict[str, str] = {}
    objective_snapshot = objective
    if preferred_xingce_module is not None:
        clear_due = xingce_only and objective.closure_readiness == "available"
        objective_snapshot = replace(
            objective,
            baseline_required=False,
            weakest_module=preferred_xingce_module,
            due_reflection_count=0 if clear_due else objective.due_reflection_count,
            due_verification_count=0 if clear_due else objective.due_verification_count,
            due_reflection_session_id=None if xingce_only else objective.due_reflection_session_id,
            due_reflection_review_id=None if xingce_only else objective.due_reflection_review_id,
            due_reflection_version_id=None if xingce_only else objective.due_reflection_version_id,
            due_verification_mastery_id=(
                None if xingce_only else objective.due_verification_mastery_id
            ),
            due_verification_version_id=(
                None if xingce_only else objective.due_verification_version_id
            ),
            due_verification_decision_version_id=(
                None if xingce_only else objective.due_verification_decision_version_id
            ),
            due_reflection_state=None if xingce_only else objective.due_reflection_state,
        )
    candidates.extend(
        _objective_candidates(
            goal=goal,
            snapshot=objective_snapshot,
            available_minutes=available_minutes,
        )
    )
    essay_candidate = (
        None
        if xingce_only
        else _essay_candidate(
            goal=goal,
            snapshot=essay,
            available_minutes=available_minutes,
        )
    )
    if essay_candidate is not None:
        candidates.append(essay_candidate)
    elif not xingce_only:
        omitted["essay"] = essay.pending_reason or (
            "申论的材料和评分标准还没准备好，先不硬排，准备好了马上给你安排上。"
        )
    if not any(item.track == "xingce" for item in candidates):
        omitted["xingce"] = "今天暂时没有合适的行测任务，别急，随时回来看看。"
    capability_gaps: dict[str, str] = {}
    if objective.closure_readiness == "unavailable":
        assert objective.closure_unavailable_reason is not None
        capability_gaps["objectiveClosure"] = objective.closure_unavailable_reason
    if essay.closure_readiness == "unavailable":
        assert essay.closure_unavailable_reason is not None
        capability_gaps["essayClosure"] = essay.closure_unavailable_reason
    return CandidateSet(
        candidates=tuple(candidates),
        omitted_track_reasons=omitted,
        due_reflection_count=objective_snapshot.due_reflection_count,
        due_verification_count=objective_snapshot.due_verification_count,
        capability_gaps=capability_gaps,
    )


def _objective_candidates(
    *,
    goal: GoalPlanningSnapshot,
    snapshot: ObjectivePlanningSnapshot,
    available_minutes: int,
) -> list[TaskCandidate]:
    evidence = _shared_evidence(goal)
    if snapshot.source_version_id:
        evidence.append(
            {
                "owner": "objective_learning",
                "entityType": "objective_state",
                "entityId": snapshot.source_version_id,
                "versionId": snapshot.source_version_id,
            }
        )
    if snapshot.due_verification_mastery_id and snapshot.due_verification_version_id:
        evidence.append(
            {
                "owner": "objective_learning",
                "entityType": "xingce_mastery_state_version",
                "entityId": snapshot.due_verification_mastery_id,
                "versionId": snapshot.due_verification_version_id,
                "role": "due_verification",
            }
        )
        if snapshot.due_verification_decision_version_id:
            evidence.append(
                {
                    "owner": "objective_learning",
                    "entityType": "xingce_cause_decision_version",
                    "entityId": snapshot.due_verification_decision_version_id,
                    "versionId": snapshot.due_verification_decision_version_id,
                    "role": "immediate_verification_source",
                }
            )
    if snapshot.due_reflection_review_id and snapshot.due_reflection_version_id:
        evidence.append(
            {
                "owner": "objective_learning",
                "entityType": "xingce_attempt_review_version",
                "entityId": snapshot.due_reflection_review_id,
                "versionId": snapshot.due_reflection_version_id,
                "role": "due_reflection",
            }
        )
    candidates: list[TaskCandidate] = []
    if snapshot.baseline_required and not (
        (snapshot.due_verification_count or 0) > 0 or (snapshot.due_reflection_count or 0) > 0
    ):
        baseline_minutes = xingce_practice_minutes(
            available_minutes=available_minutes,
            question_supply=snapshot.baseline_question_count,
        )
        if baseline_minutes:
            candidates.append(
                _baseline_candidate(
                    goal,
                    evidence,
                    _cap_xingce_block(baseline_minutes, available_minutes),
                    available_minutes,
                )
            )
        return candidates

    if (
        snapshot.closure_readiness == "available"
        and (snapshot.due_verification_count or 0) > 0
        and snapshot.verification_question_count >= XINGCE_QUICK_QUESTION_BUNDLE_SIZE
    ):
        candidates.append(
            TaskCandidate(
                candidate_key="xingce-due-verification",
                track="xingce",
                stage="objective_verification",
                kind="verification",
                title="这几道错题该回炉啦",
                reason=f"有 {snapshot.due_verification_count} 道错题到了复测时间，趁还记得，再打一仗，赢了它们就是你的。",
                estimated_minutes=15,
                completion_rule="答完这几道题，说说你的判断，结果会自动存好。",
                start_url="/pages/xingce-task-intro/index?mode=verification",
                evidence_refs=tuple(evidence),
                priority=PriorityBreakdown(
                    goal_gain=0.65 if goal.positive_gap else 0.45,
                    ability_gain=0.90,
                    verification_urgency=1.0,
                    time_fit=_time_fit(15, available_minutes),
                    load_penalty=0.05,
                ),
            )
        )
    if snapshot.closure_readiness == "available" and (snapshot.due_reflection_count or 0) > 0:
        reflection_title, reflection_reason, reflection_completion_rule = (
            _reflection_candidate_copy(
                snapshot.due_reflection_state,
                snapshot.due_reflection_count or 0,
            )
        )
        candidates.append(
            TaskCandidate(
                candidate_key="xingce-due-reflection",
                track="xingce",
                stage="objective_reflection",
                kind="reflection",
                title=reflection_title,
                reason=reflection_reason,
                estimated_minutes=15,
                completion_rule=reflection_completion_rule,
                start_url=(
                    "/pages/reflection/index?sessionId="
                    f"{snapshot.due_reflection_session_id}&reviewId="
                    f"{snapshot.due_reflection_review_id}"
                ),
                evidence_refs=tuple(evidence),
                priority=PriorityBreakdown(
                    goal_gain=0.60 if goal.positive_gap else 0.42,
                    ability_gain=0.85,
                    verification_urgency=0.98,
                    time_fit=_time_fit(15, available_minutes),
                    load_penalty=0.05,
                ),
            )
        )
    if snapshot.baseline_required:
        baseline_minutes = xingce_practice_minutes(
            available_minutes=available_minutes,
            question_supply=snapshot.baseline_question_count,
        )
        if baseline_minutes:
            candidates.append(
                _baseline_candidate(goal, evidence, baseline_minutes, available_minutes)
            )
        return candidates
    targeted_minutes = xingce_practice_minutes(
        available_minutes=available_minutes,
        question_supply=snapshot.targeted_question_count,
    )
    targeted_minutes = _cap_xingce_block(targeted_minutes, available_minutes)
    if not targeted_minutes:
        return candidates
    module = snapshot.weakest_module or "国考高频模块"
    cause_direction = snapshot.caused_training_direction
    block_minutes = min(targeted_minutes, XINGCE_TASK_BLOCK_MINUTES)
    block_count = min(16, max(1, (available_minutes + block_minutes - 1) // block_minutes))
    for block_index in range(block_count):
        block_number = block_index + 1
        base_title = (
            f"行测提分练习：{module}（{cause_direction.display}）"
            if cause_direction is not None and cause_direction.display
            else f"行测提分练习：{module}"
        )
        candidates.append(
            TaskCandidate(
                candidate_key=(
                    f"xingce-practice:{module}"
                    if block_number == 1
                    else f"xingce-practice-block-{block_number}:{module}"
                ),
                track="xingce",
                stage="objective_practice",
                kind="practice",
                title=(
                    base_title
                    if block_number == 1
                    else f"{base_title}（第 {block_number} 组）"
                ),
                reason=(
                    f"你最近确认的错因方向是「{cause_direction.display}」，"
                    f"这块是你的提分宝地，咱们按这个方向练，重点放在「{module}」模块。"
                    if cause_direction is not None
                    else (
                        f"「{module}」这块是你的提分宝地，今天多练练，提分最快。"
                        "练完一组随时可以接着安排后面的时间。"
                    )
                ),
                estimated_minutes=block_minutes,
                completion_rule="做完这组题，把答案、用时和做题时的感觉记一下就行。",
                start_url="/pages/xingce-task-intro/index?mode=targeted",
                evidence_refs=tuple(evidence),
                priority=PriorityBreakdown(
                    goal_gain=max(0.45, (0.72 if goal.positive_gap else 0.50) - block_index * 0.02),
                    ability_gain=max(0.55, 0.78 - block_index * 0.015),
                    verification_urgency=max(0.25, 0.45 - block_index * 0.01),
                    time_fit=_time_fit(block_minutes, available_minutes),
                    load_penalty=min(0.30, 0.12 + block_index * 0.015),
                ),
                strategy_module_keys=(module,),
                strategy_question_type=(
                    cause_direction.focus if cause_direction is not None else None
                ),
            )
        )
    return candidates


def _reflection_candidate_copy(
    state: ActionableReflectionState | None,
    count: int,
) -> tuple[str, str, str]:
    if state == "cause_followup_due":
        return (
            "回答岸岸的一次追问",
            f"有 {count} 个错因还差一句关键信息，岸岸想再问你一句——别怕说错，把思路讲给我听。",
            "回答这轮追问就行，剩下的岸岸接着整理。",
        )
    if state in ("cause_decision_due", "cause_evidence_insufficient"):
        return (
            "看看岸岸整理的错因",
            f"岸岸帮你整理好了 {count} 个错因，来瞅一眼，想改随时改。",
            "看完岸岸的判断就行，想补充、想调整都顺手改一下。",
        )
    if state == "cause_agent_terminated":
        return (
            "补一下没整理完的错因",
            f"有 {count} 个错因刚才没整理完，没关系，重试、补充或先跳过都行。",
            "重试一次、补充一下，或者这次先跳过。",
        )
    return (
        "复盘这组错题",
        f"有 {count} 道错题还没弄明白错在哪，别急，复盘一次就能找到方向。",
        "写下你当时的思路就可以离开，岸岸会接着帮你整理。",
    )


def _baseline_candidate(
    goal: GoalPlanningSnapshot,
    evidence: list[dict[str, str]],
    estimated_minutes: int,
    available_minutes: int,
) -> TaskCandidate:
    return TaskCandidate(
        candidate_key="xingce-baseline",
        track="xingce",
        stage="objective_baseline",
        kind="practice",
        title="先来摸个底",
        reason="咱们先摸个底，不急着赶进度。答一组题，让我看看你现在的水平，后面才好帮你规划。",
        estimated_minutes=estimated_minutes,
        completion_rule="按真实水平答完这组题就行，用时和做题的感觉顺手记一下。",
        start_url="/pages/xingce-task-intro/index?mode=baseline",
        evidence_refs=tuple(evidence),
        priority=PriorityBreakdown(
            goal_gain=0.65 if goal.positive_gap else 0.45,
            ability_gain=0.95,
            verification_urgency=0.70,
            time_fit=_time_fit(estimated_minutes, available_minutes),
            load_penalty=0.35 if estimated_minutes < 20 else 0.15,
        ),
    )


def _essay_candidate(
    *,
    goal: GoalPlanningSnapshot,
    snapshot: EssayPlanningSnapshot,
    available_minutes: int,
) -> TaskCandidate | None:
    if (
        snapshot.next_stage in {"content_unavailable", "evaluation_pending", "closure_unavailable"}
        or snapshot.task_version_id is None
        or (
            snapshot.next_stage in {"baseline_due", "drafting_due"}
            and snapshot.rubric_version_id is None
        )
    ):
        return None
    evidence = _shared_evidence(goal)
    evidence.append(
        {
            "owner": "essay_intelligence",
            "entityType": "essay_task_version",
            "entityId": snapshot.task_version_id,
            "versionId": snapshot.task_version_id,
        }
    )
    for entity_type, entity_id in (
        ("essay_material_bundle_version", snapshot.material_bundle_version_id),
        ("essay_rubric_version", snapshot.rubric_version_id),
    ):
        if entity_id:
            evidence.append(
                {
                    "owner": "essay_intelligence",
                    "entityType": entity_type,
                    "entityId": entity_id,
                    "versionId": entity_id,
                }
            )
    for entity_type, entity_id in (
        ("essay_attempt", snapshot.attempt_id),
        ("essay_answer_version", snapshot.answer_version_id),
        ("essay_review_version", snapshot.review_version_id),
        ("essay_rewrite_version", snapshot.rewrite_version_id),
        ("essay_transfer_verification", snapshot.transfer_verification_version_id),
        (
            "essay_finding_decision",
            snapshot.finding_decision_artifact_version_id,
        ),
        ("essay_material_annotation", snapshot.material_annotation_version_id),
        ("essay_point_extraction", snapshot.point_extraction_version_id),
        ("essay_outline", snapshot.outline_version_id),
        (
            "essay_review_acknowledgement",
            snapshot.review_acknowledgement_version_id,
        ),
    ):
        if entity_id:
            evidence.append(
                {
                    "owner": "essay_intelligence",
                    "entityType": entity_type,
                    "entityId": entity_id,
                    "versionId": entity_id,
                }
            )
    for mastery_id, mastery_version_id in zip(
        snapshot.mastery_ids,
        snapshot.mastery_version_ids,
        strict=True,
    ):
        evidence.append(
            {
                "owner": "essay_intelligence",
                "entityType": "essay_mastery_state_version",
                "entityId": mastery_id,
                "versionId": mastery_version_id,
                "role": "canonical_head",
            }
        )
    for entity_type, entity_id in (
        ("essay_source_task_version", snapshot.source_task_version_id),
        (
            "essay_source_material_bundle_version",
            snapshot.source_material_bundle_version_id,
        ),
        ("essay_source_answer_version", snapshot.source_answer_version_id),
    ):
        if entity_id:
            evidence.append(
                {
                    "owner": "essay_intelligence",
                    "entityType": entity_type,
                    "entityId": entity_id,
                    "versionId": entity_id,
                }
            )

    definitions = {
        "baseline_due": _baseline_definition(snapshot, available_minutes),
        "point_extraction_due": (
            "essay_point_extraction",
            "把材料里的要点找出来",
            "材料你已经逐段读完了，评分标准也备好了，接下来把里面的要点捞出来，答题时用得上。",
            20,
            "每个要点标出它出自哪一段，存成找点记录。",
            "point-extraction",
            0.90,
            0.82,
        ),
        "outline_due": (
            "essay_outlining",
            "把要点组织成提纲",
            "要点都找齐了，接下来排个顺序、定好篇幅，写起来会顺很多。",
            20,
            "每个提纲段落引用对应的要点，并记下预计字数。",
            "outline",
            0.92,
            0.84,
        ),
        "drafting_due": (
            "essay_drafting",
            "完成申论作答",
            "题目、材料和评分标准都齐了，直接动笔写就行。材料地图、找点和提纲按需用，不用一次全做完。",
            _essay_drafting_minutes(snapshot.question_type, snapshot.word_limit),
            "提交你的答案，写完顺手记一下当时的想法。",
            "baseline",
            0.95,
            0.88,
        ),
        "review_due": (
            "essay_review",
            "看看评阅结果",
            "答案交上去，评阅也出来了，来看一下哪里要改、重写重点是什么。",
            15,
            "逐条看看评阅意见，挑出 1–3 个这次最该改的问题。",
            "review",
            0.82,
            0.92,
        ),
        "rewrite_due": (
            "essay_rewrite",
            "按评阅意见改写一遍",
            "这篇还有几个问题没改，别急着开新题，先把这篇改扎实，收获更大。",
            20,
            "改掉评阅点出的问题、引用材料作依据，再提交一版新答案。",
            "rewrite",
            0.88,
            0.96,
        ),
        "transfer_due": _transfer_definition(snapshot),
        "practice_due": _practice_definition(snapshot),
    }
    stage, title, reason, minutes, completion, mode, ability_gain, urgency = definitions[
        snapshot.next_stage
    ]
    start_url = (
        _essay_closure_start_url(snapshot, stage)
        if stage in {"essay_rewrite", "essay_transfer"}
        else (
            f"/pages/essay-task/index?mode={mode}&stage={stage}"
            f"&taskVersionId={snapshot.task_version_id}"
        )
    )
    return TaskCandidate(
        candidate_key=_essay_candidate_key(snapshot, stage),
        track="essay",
        stage=stage,
        kind="essay",
        title=title,
        reason=reason,
        estimated_minutes=minutes,
        completion_rule=completion,
        start_url=start_url,
        evidence_refs=tuple(evidence),
        priority=PriorityBreakdown(
            goal_gain=0.72 if goal.positive_gap else 0.50,
            ability_gain=ability_gain,
            verification_urgency=urgency,
            # Review, rewrite, and transfer are one evidence loop. Once the
            # learner has entered that loop, consuming the remaining study
            # block to close it is a good fit rather than a scheduling penalty.
            time_fit=(
                1.0
                if stage in {"essay_review", "essay_rewrite", "essay_transfer"}
                and minutes <= available_minutes
                else _time_fit(minutes, available_minutes)
            ),
            load_penalty=0.18 if minutes >= 25 else 0.10,
        ),
        strategy_module_keys=(
            (snapshot.weakest_dimension_key,) if snapshot.weakest_dimension_key else ()
        ),
        strategy_question_type=snapshot.question_type,
    )


def _baseline_definition(
    snapshot: EssayPlanningSnapshot,
    _available_minutes: int,
) -> tuple[str, str, str, int, str, str, float, float]:
    reading_minutes = min(15, max(10, _available_minutes)) if _available_minutes else 15
    return (
        "essay_material_reading",
        "先读懂申论材料",
        "第一次正式学申论，别紧张。咱们按「读材料 → 找要点 → 列提纲 → 作答」"
        "先完整走一遍，之后再以「作答 → 评阅」为主，哪里弱补哪里。",
        reading_minutes,
        "给材料分段标注，把每段的主旨、对象和能引用的句子标出来。",
        "material-reading",
        0.86,
        0.68,
    )


def _essay_candidate_key(snapshot: EssayPlanningSnapshot, stage: str) -> str:
    evidence_versions = (
        snapshot.rubric_version_id,
        snapshot.review_version_id,
        snapshot.rewrite_version_id,
        snapshot.transfer_verification_version_id,
        snapshot.material_annotation_version_id,
        snapshot.point_extraction_version_id,
        snapshot.outline_version_id,
        snapshot.review_acknowledgement_version_id,
        snapshot.finding_decision_artifact_version_id,
        *snapshot.mastery_version_ids,
    )
    suffix = ":".join(value for value in evidence_versions if value)
    base = f"essay:{stage}:{snapshot.task_version_id}"
    return f"{base}:{sha256(suffix.encode()).hexdigest()}" if suffix else base


def _essay_closure_start_url(snapshot: EssayPlanningSnapshot, stage: str) -> str:
    if stage == "essay_rewrite":
        required = {
            "attemptId": snapshot.attempt_id,
            "baseReviewVersionId": snapshot.review_version_id,
            "baseAnswerVersionId": snapshot.source_answer_version_id,
            "findingDecisionArtifactVersionId": (snapshot.finding_decision_artifact_version_id),
            "basedOnAnswerVersionId": snapshot.answer_version_id,
        }
        if any(value is None for value in required.values()):
            raise ValueError("essay rewrite candidate lacks a frozen writer source")
        query = "&".join(f"{key}={value}" for key, value in required.items())
        if snapshot.rewrite_version_id:
            query += f"&basedOnRewriteVersionId={snapshot.rewrite_version_id}"
        return (
            "/pages/essay-task/index?mode=rewrite&stage=essay_rewrite"
            f"&taskVersionId={snapshot.task_version_id}&{query}"
        )
    if (
        not snapshot.rewrite_version_id
        or not snapshot.finding_decision_artifact_version_id
        or not snapshot.material_bundle_version_id
        or not snapshot.source_task_version_id
        or not snapshot.source_material_bundle_version_id
    ):
        raise ValueError("essay transfer candidate lacks a frozen writer source")
    if (
        snapshot.task_version_id == snapshot.source_task_version_id
        or snapshot.material_bundle_version_id == snapshot.source_material_bundle_version_id
    ):
        raise ValueError("essay transfer candidate must bind distinct content")
    return (
        "/pages/essay-task/index?mode=transfer&stage=essay_transfer"
        f"&taskVersionId={snapshot.task_version_id}"
        f"&materialBundleVersionId={snapshot.material_bundle_version_id}"
        f"&sourceRewriteVersionId={snapshot.rewrite_version_id}"
        f"&sourceFindingDecisionArtifactVersionId="
        f"{snapshot.finding_decision_artifact_version_id}"
    )


def _essay_drafting_minutes(question_type: str | None, word_limit: int | None) -> int:
    base = {
        "summary": 25,
        "comprehensive_analysis": 35,
        "countermeasure": 30,
        "implementation": 40,
        "composition": 80,
    }.get(question_type or "", 35)
    if word_limit is None:
        return base
    if word_limit >= 800:
        return max(base, 80)
    if word_limit >= 500:
        return max(base, 55)
    if word_limit >= 350:
        return max(base, 40)
    if word_limit <= 250:
        return min(base, 25)
    return base


def _transfer_definition(
    snapshot: EssayPlanningSnapshot,
) -> tuple[str, str, str, int, str, str, float, float]:
    return (
        "essay_transfer",
        "用新题检验一下",
        "这篇改写已经完成，但还不敢说就稳了，用一篇没见过的材料检验一下最放心。",
        _essay_drafting_minutes(snapshot.question_type, snapshot.word_limit),
        "用新题目、新材料验证同一个评分维度，看能不能稳定发挥。",
        "transfer",
        0.92,
        0.90,
    )


def _practice_definition(
    snapshot: EssayPlanningSnapshot,
) -> tuple[str, str, str, int, str, str, float, float]:
    stage = _practice_stage(snapshot.weakest_dimension)
    minutes = {
        "essay_material_reading": 15,
        "essay_point_extraction": 20,
        "essay_outlining": 20,
        "essay_drafting": _essay_drafting_minutes(
            snapshot.question_type,
            snapshot.word_limit,
        ),
    }[stage]
    dimension = snapshot.weakest_dimension or "采点与结构"
    return (
        stage,
        f"申论专项：{dimension}",
        f"最近申论练习里，「{dimension}」是你的提分重点，今天多练练。",
        minutes,
        "完成材料标注和作答，再简单复盘一下就好。",
        "practice",
        0.80,
        0.62,
    )


def _practice_stage(weakest_dimension: str | None) -> str:
    value = weakest_dimension or ""
    if "找点" in value or "要点" in value:
        return "essay_point_extraction"
    if "结构" in value or "层次" in value:
        return "essay_outlining"
    if "材料" in value:
        return "essay_material_reading"
    return "essay_drafting"


def _shared_evidence(goal: GoalPlanningSnapshot) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    if goal.goal_version_id:
        evidence.append(
            {
                "owner": goal.target_owner,
                "entityType": goal.target_entity_type,
                "entityId": goal.goal_version_id,
                "versionId": goal.goal_version_id,
            }
        )
    if goal.ability_estimate_version_id:
        evidence.append(
            {
                "owner": "ability_estimation",
                "entityType": "ability_estimate_version",
                "entityId": goal.ability_estimate_version_id,
                "versionId": goal.ability_estimate_version_id,
            }
        )
    if not evidence:
        evidence.append(
            {
                "owner": "planning",
                "entityType": "evidence_collection_need",
                "entityId": "dual-track-baseline-required",
            }
        )
    return evidence


def _time_fit(task_minutes: int, available_minutes: int) -> float:
    if available_minutes <= 0 or task_minutes > available_minutes:
        return 0.0
    ideal_block = min(30, max(10, available_minutes // 2))
    distance = abs(task_minutes - ideal_block)
    return round(max(0.2, 1 - distance / max(ideal_block, 1)), 3)


def _cap_xingce_block(minutes: int, available_minutes: int) -> int:
    """Reserve the essay drafting floor so a short day still fits both tracks.

    ``xingce_practice_minutes`` sizes one focused block up to half the day,
    but half of a 45-minute parttime day (22 minutes) plus the 25-minute
    essay drafting floor overshoots the day, so the engine would schedule
    only essay and starve xingce entirely.  Cap the xingce block to the room
    that actually remains after reserving the essay floor, rounded down to a
    whole-question block.
    """

    reserved_room = available_minutes - MIN_ESSAY_DRAFTING_MINUTES
    minimum_block = XINGCE_QUICK_QUESTION_BUNDLE_SIZE * XINGCE_MINUTES_PER_QUESTION
    if reserved_room < minimum_block:
        return minutes
    capped = (reserved_room // XINGCE_MINUTES_PER_QUESTION) * XINGCE_MINUTES_PER_QUESTION
    return min(minutes, max(minimum_block, capped))


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False
