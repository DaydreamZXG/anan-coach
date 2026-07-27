from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from app.domains.planning.engine import PriorityBreakdown, TaskCandidate
from app.domains.xingce_learning.contracts import XINGCE_QUESTION_BUNDLE_SIZE


AbilityReadiness = Literal["unavailable", "partial", "complete", "stale"]
ObjectiveClosureReadiness = Literal["available", "unavailable"]
EssayClosureReadiness = Literal["available", "unavailable"]
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
    baseline_question_count: int = 0
    targeted_question_count: int = 0
    verification_question_count: int = 0

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
) -> CandidateSet:
    candidates: list[TaskCandidate] = []
    omitted: dict[str, str] = {}
    candidates.extend(
        _objective_candidates(
            goal=goal,
            snapshot=objective,
            available_minutes=available_minutes,
        )
    )
    essay_candidate = _essay_candidate(
        goal=goal,
        snapshot=essay,
        available_minutes=available_minutes,
    )
    if essay_candidate is None:
        omitted["essay"] = essay.pending_reason or (
            "申论正式材料包与评分规程尚未发布，规划 Agent 不生成不可执行任务。"
        )
    else:
        candidates.append(essay_candidate)
    if not any(item.track == "xingce" for item in candidates):
        omitted["xingce"] = "当前没有满足内容与证据门禁的行测任务。"
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
        due_reflection_count=objective.due_reflection_count,
        due_verification_count=objective.due_verification_count,
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
    time_fit = _time_fit(20, available_minutes)
    candidates: list[TaskCandidate] = []
    if snapshot.baseline_required and not (
        (snapshot.due_verification_count or 0) > 0 or (snapshot.due_reflection_count or 0) > 0
    ):
        if snapshot.baseline_question_count >= XINGCE_QUESTION_BUNDLE_SIZE:
            candidates.append(_baseline_candidate(goal, evidence, time_fit))
        return candidates

    if (
        snapshot.closure_readiness == "available"
        and (snapshot.due_verification_count or 0) > 0
        and snapshot.verification_question_count >= XINGCE_QUESTION_BUNDLE_SIZE
    ):
        candidates.append(
            TaskCandidate(
                candidate_key="xingce-due-verification",
                track="xingce",
                stage="objective_verification",
                kind="verification",
                title="验证到期的行测错因",
                reason=f"有 {snapshot.due_verification_count} 项已到验证时间，继续拖延会降低结论可信度。",
                estimated_minutes=15,
                completion_rule="完成独立变式题并说明判断依据，验证结果写回掌握状态。",
                start_url="/pages/xingce-task-intro/index?mode=verification",
                evidence_refs=tuple(evidence),
                priority=PriorityBreakdown(
                    goal_gain=0.65 if goal.positive_gap else 0.45,
                    ability_gain=0.90,
                    verification_urgency=1.0,
                    time_fit=max(time_fit, _time_fit(15, available_minutes)),
                    load_penalty=0.05,
                ),
            )
        )
    if snapshot.closure_readiness == "available" and (snapshot.due_reflection_count or 0) > 0:
        candidates.append(
            TaskCandidate(
                candidate_key="xingce-due-reflection",
                track="xingce",
                stage="objective_reflection",
                kind="reflection",
                title="复盘高价值行测错题",
                reason=f"有 {snapshot.due_reflection_count} 项错误尚未形成可验证的修复动作。",
                estimated_minutes=15,
                completion_rule="确认原始思路、错因候选和下一次识别信号。",
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
                    time_fit=max(time_fit, _time_fit(15, available_minutes)),
                    load_penalty=0.05,
                ),
            )
        )
    if snapshot.baseline_required:
        if snapshot.baseline_question_count >= XINGCE_QUESTION_BUNDLE_SIZE:
            candidates.append(_baseline_candidate(goal, evidence, time_fit))
        return candidates
    if snapshot.targeted_question_count < XINGCE_QUESTION_BUNDLE_SIZE:
        return candidates
    module = snapshot.weakest_module or "国考高频模块"
    candidates.append(
        TaskCandidate(
            candidate_key=f"xingce-practice:{module}",
            track="xingce",
            stage="objective_practice",
            kind="practice",
            title=f"行测针对训练：{module}",
            reason=f"当前证据中「{module}」的改善收益最高，先做短题组再复盘。",
            estimated_minutes=20,
            completion_rule="完成题组并记录答案、用时、修改、跳题和信心。",
            start_url="/pages/xingce-task-intro/index?mode=targeted",
            evidence_refs=tuple(evidence),
            priority=PriorityBreakdown(
                goal_gain=0.72 if goal.positive_gap else 0.50,
                ability_gain=0.78,
                verification_urgency=0.45,
                time_fit=time_fit,
                load_penalty=0.12,
            ),
            strategy_module_keys=(module,),
        )
    )
    return candidates


def _baseline_candidate(
    goal: GoalPlanningSnapshot,
    evidence: list[dict[str, str]],
    time_fit: float,
) -> TaskCandidate:
    return TaskCandidate(
        candidate_key="xingce-baseline",
        track="xingce",
        stage="objective_baseline",
        kind="practice",
        title="完成一组行测受控基线",
        reason="当前行测能力证据缺失或已过期，先补可比较基线，再决定模块投入。",
        estimated_minutes=20,
        completion_rule="完成固定题组、记录用时与信心，并形成新的行测证据版本。",
        start_url="/pages/xingce-task-intro/index?mode=baseline",
        evidence_refs=tuple(evidence),
        priority=PriorityBreakdown(
            goal_gain=0.65 if goal.positive_gap else 0.45,
            ability_gain=0.95,
            verification_urgency=0.70,
            time_fit=time_fit,
            load_penalty=0.15,
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
            "从材料地图提炼申论要点",
            "材料已逐段读完，评分规程也已发布；先把材料证据提炼为可作答要点。",
            20,
            "每个要点引用材料段落，并保存结构化找点版本。",
            "point-extraction",
            0.90,
            0.82,
        ),
        "outline_due": (
            "essay_outlining",
            "把申论要点组织成提纲",
            "找点证据已完整，下一步先确定层次与篇幅分配，再进入正式作答。",
            20,
            "每个提纲段落引用已提炼要点，并记录预计字数。",
            "outline",
            0.92,
            0.84,
        ),
        "drafting_due": (
            "essay_drafting",
            "依据材料与提纲完成申论作答",
            "材料标注、要点与提纲均已形成，当前最需要补齐的是可评阅的原稿证据。",
            _essay_drafting_minutes(snapshot.question_type, snapshot.word_limit),
            "提交答案版本，并记录作答后的即时复盘。",
            "baseline",
            0.95,
            0.88,
        ),
        "review_due": (
            "essay_review",
            "查看并确认申论证据化评阅",
            "原稿已提交但评阅闭环未完成，先确认材料锚点、原句问题和重写重点。",
            15,
            "逐条查看 finding，并确定本次重写的 1–3 个优先问题。",
            "review",
            0.82,
            0.92,
        ),
        "rewrite_due": (
            "essay_rewrite",
            "按评阅证据完成申论重写",
            "本篇已有未解决 finding；重写比继续开新题更能形成完整学习证据。",
            20,
            "解决指定 finding、保留材料依据，并提交新的答案版本。",
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
            time_fit=_time_fit(minutes, available_minutes),
            load_penalty=0.18 if minutes >= 25 else 0.10,
        ),
        strategy_module_keys=(
            (snapshot.weakest_dimension_key,) if snapshot.weakest_dimension_key else ()
        ),
        strategy_question_type=snapshot.question_type,
    )


def _baseline_definition(
    snapshot: EssayPlanningSnapshot,
    available_minutes: int,
) -> tuple[str, str, str, int, str, str, float, float]:
    reading_minutes = min(15, max(10, available_minutes))
    reason = (
        "题目与完整材料已通过门禁，但可审计评分规程尚未发布；先完成不依赖假评分点的材料阅读。"
        if snapshot.rubric_version_id is None
        else "完整作答必须建立在逐段材料证据上；先完成可验收的材料阅读，不跳过找点依据。"
    )
    return (
        "essay_material_reading",
        "先建立申论材料地图",
        reason,
        reading_minutes,
        "完成材料分段标注，为每段保留主旨、对象与可引用证据。",
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
        "用新材料验证申论迁移",
        "本篇重写已完成，但还不能证明能力稳定；需要用未见材料做独立验证。",
        _essay_drafting_minutes(snapshot.question_type, snapshot.word_limit),
        "在不同题目与材料中验证同一评分维度，形成迁移结论。",
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
        f"最近申论证据显示「{dimension}」仍是主要改善点。",
        minutes,
        "完成对应材料标注、作答和证据化复盘。",
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
                "owner": "position_goal",
                "entityType": "position_goal_version",
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
