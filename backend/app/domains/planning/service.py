from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.candidates import (
    GoalPlanningSnapshot,
    build_dual_track_candidates,
)
from app.domains.planning.engine import (
    PriorityBreakdown,
    RankedCandidate,
    TaskCandidate,
    select_daily_tasks,
)
from app.domains.planning.lifecycle import require_executable_plan_task
from app.domains.planning.repository import (
    ActiveStrategyDecision,
    CapacitySnapshot,
    GoalAbilitySnapshot,
    PlanningContractError,
    PlanningRepository,
    PreferenceSnapshot,
)
from app.domains.planning.schemas import (
    CompletionContract,
    DailyCapacityCommand,
    DailyCapacityCommandResult,
    DailyCapacityPreview,
    DualTrackExplanation,
    GoalGap,
    PlanningEvidenceRef,
    PriorityDecomposition,
    ScoreRange,
    TaskExecutionCommand,
    TaskExecutionCommandResult,
    TaskExecutionVersion,
    TodayCapacity,
    TodayDashboard,
    TodayTask,
    TrackAllocation,
)
from app.domains.position_decision.learner_target_runtime import LearnerTargetScoreRuntime
from app.platform.outbox import OutboxRepository


PLAN_CONTRACT_VERSION = "daily-plan-v3"
PLAN_ALGORITHM_VERSION = "dual-track-direct-essay-v8"
PLAN_BASIS = "evidence_priority_v2"
SHANGHAI = ZoneInfo("Asia/Shanghai")
STRATEGY_ADJUSTABLE_STAGES = {
    "objective_baseline",
    "objective_practice",
    "essay_material_reading",
    "essay_point_extraction",
    "essay_outlining",
    "essay_drafting",
}
STRATEGY_PRIORITY_FACTOR = 0.35


class PlanningService:
    def __init__(
        self,
        *,
        repository: PlanningRepository | None = None,
        outbox: OutboxRepository | None = None,
        learner_target: LearnerTargetScoreRuntime | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository or PlanningRepository()
        self._outbox = outbox or OutboxRepository()
        self._learner_target = learner_target or LearnerTargetScoreRuntime()
        self._now = now or (lambda: datetime.now(UTC))

    def _with_learner_target(
        self,
        session: Session,
        *,
        user_id: str,
        snapshot: GoalAbilitySnapshot,
    ) -> GoalAbilitySnapshot:
        if snapshot.target_lower is not None and snapshot.target_upper is not None:
            return snapshot
        projection = self._learner_target.projection(session, user_id=user_id)
        if projection.status != "available" or projection.target_score is None:
            return snapshot
        target_basis = (
            f"面向 {projection.target_year} 年备考：引用 {projection.reference_year} 年"
            f"同口径岗位 {projection.sample_count} 个，历史 P80 "
            f"{projection.historical_p80:g} 分、P95 {projection.historical_p95:g} 分、"
            f"偏好地区最高 {projection.cohort_maximum:g} 分；"
            f"全国硬条件可报同专业大类 {projection.coverage_sample_count} 个岗位，"
            f"最高 {projection.historical_maximum:g} 分。"
        )
        if projection.trend_reference_year is not None:
            target_basis += (
                f"对比 {projection.trend_reference_year} 年同口径"
                f" {projection.trend_sample_count} 个样本，下一届参考线保守预留"
                f" {projection.trend_uplift:g} 分备考余量（一年同比只采纳一半，"
                "且最多预留 2 分）。"
            )
        else:
            target_basis += "没有可比的上年度同口径样本，本次不增加备考余量。"
        return replace(
            snapshot,
            goal_version_id=projection.version_id,
            position_name="历史同类岗位目标",
            department_name=None,
            target_lower=projection.target_score.lower,
            target_recommended=projection.target_score.recommended,
            target_upper=projection.target_score.upper,
            target_evidence_level="historical_cohort",
            target_valid_until=None,
            target_owner="learner_target_score",
            target_entity_type="learner_target_score_version",
            target_evidence_summary=target_basis,
        )

    def set_today_capacity(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: DailyCapacityCommand,
        now: datetime | None = None,
    ) -> DailyCapacityCommandResult:
        recorded_at = _as_utc(now or self._now())
        day = recorded_at.astimezone(SHANGHAI).date()
        self._repository.lock_user(session, user_id=user_id)
        input_fingerprint = _fingerprint(
            {
                "userId": user_id,
                "localDate": day.isoformat(),
                "command": command.model_dump(by_alias=True, mode="json"),
            }
        )
        replay = self._repository.capacity_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            if replay["input_fingerprint"] != input_fingerprint:
                raise PlanningContractError(
                    "Idempotency key already belongs to a different capacity command"
                )
            replay_plan = self._repository.plan_for_capacity(
                session,
                user_id=user_id,
                capacity_version_id=str(replay["capacity_version_id"]),
            )
            if replay_plan is None:
                raise PlanningContractError("Capacity command replay is missing its plan version")
            replay_capacity = _capacity_from_row(replay)
            return DailyCapacityCommandResult(
                capacity=_today_capacity(replay_capacity, replay_plan["local_date"]),
                plan=_dashboard_from_rows(
                    replay_plan,
                    self._repository.plan_tasks(
                        session,
                        plan_version_id=str(replay_plan["plan_version_id"]),
                    ),
                    replay_capacity,
                ),
            )

        current = self._repository.current_capacity(
            session,
            user_id=user_id,
            local_date=day,
            for_update=True,
        )
        if current is None:
            raise PlanningContractError(
                "Load today's plan before confirming or revising daily capacity"
            )
        if str(current["capacity_version_id"]) != str(command.based_on_capacity_version_id):
            raise PlanningContractError(
                "Daily capacity changed; reload the current version before submitting"
            )
        row = self._repository.insert_user_capacity(
            session,
            values={
                "capacity_version_id": str(uuid4()),
                "command_id": command_id,
                "user_id": user_id,
                "local_date": day,
                "version_no": int(current["version_no"]) + 1,
                "available_minutes": command.available_minutes,
                "energy": command.energy,
                "stress": command.stress,
                "confidence_level": command.confidence_level,
                "preferred_xingce_module": command.preferred_xingce_module,
                "xingce_only": command.xingce_only,
                "preferred_track": command.preferred_track,
                "weekly_preference_version_id": str(current["weekly_preference_version_id"]),
                "input_fingerprint": input_fingerprint,
                "supersedes_capacity_version_id": str(current["capacity_version_id"]),
                "recorded_at": recorded_at,
            },
        )
        self._outbox.append(
            session,
            deduplication_key=f"daily-capacity:{row['capacity_version_id']}",
            aggregate_type="daily_capacity",
            aggregate_id=str(row["capacity_version_id"]),
            event_type="daily_capacity.changed",
            payload={
                "capacityVersionId": str(row["capacity_version_id"]),
                "userId": user_id,
                "localDate": day.isoformat(),
                "availableMinutes": command.available_minutes,
                "preferredXingceModule": command.preferred_xingce_module,
                "xingceOnly": command.xingce_only,
                "preferredTrack": command.preferred_track,
                "inputFingerprint": input_fingerprint,
            },
        )
        plan = self.get_or_create_today(
            session,
            user_id=user_id,
            now=recorded_at,
            local_date=day,
        )
        return DailyCapacityCommandResult(capacity=plan.capacity, plan=plan)

    def preview_today_capacity(
        self,
        session: Session,
        *,
        user_id: str,
        command: DailyCapacityCommand,
        now: datetime | None = None,
    ) -> DailyCapacityPreview:
        recorded_at = _as_utc(now or self._now())
        day = recorded_at.astimezone(SHANGHAI).date()
        current_plan = self.get_or_create_today(
            session,
            user_id=user_id,
            now=recorded_at,
            local_date=day,
        )
        preview_transaction = session.begin_nested()
        try:
            proposed = self.set_today_capacity(
                session,
                user_id=user_id,
                command_id=f"{user_id}:capacity-preview:{uuid4()}",
                command=command,
                now=recorded_at,
            )
            proposed_plan = proposed.plan.model_copy(deep=True)
        finally:
            preview_transaction.rollback()
            session.expire_all()
        return DailyCapacityPreview(
            based_on_capacity_version_id=current_plan.capacity.capacity_version_id,
            current_plan=current_plan,
            proposed_plan=proposed_plan,
        )

    def record_task_execution(
        self,
        session: Session,
        *,
        user_id: str,
        task_id: str,
        command_id: str,
        command: TaskExecutionCommand,
        now: datetime | None = None,
    ) -> TaskExecutionCommandResult:
        if command.status == "complete":
            raise PlanningContractError(
                "Task completion must be committed by the canonical learning owner "
                "in the same transaction as its evidence"
            )
        return self._record_task_execution(
            session,
            user_id=user_id,
            task_id=task_id,
            command_id=command_id,
            command=command,
            completion_owner=None,
            now=now,
        )

    def complete_task_from_owner(
        self,
        session: Session,
        *,
        user_id: str,
        task_id: str,
        command_id: str,
        command: TaskExecutionCommand,
        owner: Literal["essay_intelligence"],
        now: datetime | None = None,
    ) -> TaskExecutionCommandResult:
        if owner != "essay_intelligence":
            raise PlanningContractError("Unsupported canonical task completion owner")
        if command.status != "complete":
            raise PlanningContractError(
                "Canonical task completion owner may only record complete executions"
            )
        return self._record_task_execution(
            session,
            user_id=user_id,
            task_id=task_id,
            command_id=command_id,
            command=command,
            completion_owner=owner,
            now=now,
        )

    def _record_task_execution(
        self,
        session: Session,
        *,
        user_id: str,
        task_id: str,
        command_id: str,
        command: TaskExecutionCommand,
        completion_owner: str | None,
        now: datetime | None = None,
    ) -> TaskExecutionCommandResult:
        recorded_at = _as_utc(now or self._now())
        self._repository.lock_user(session, user_id=user_id)
        input_fingerprint = _fingerprint(
            {
                "userId": user_id,
                "taskId": task_id,
                "command": command.model_dump(by_alias=True, mode="json"),
            }
        )
        replay = self._repository.task_execution_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            if (
                str(replay["task_id"]) != task_id
                or replay["input_fingerprint"] != input_fingerprint
            ):
                raise PlanningContractError(
                    "Idempotency key already belongs to a different task execution command"
                )
            replay_execution = _execution_from_row(replay)
            replay_plan = self._repository.plan_for_execution(
                session,
                user_id=user_id,
                execution_version_id=str(replay_execution.execution_version_id),
            )
            if replay_plan is None:
                raise PlanningContractError(
                    "Task execution replay is missing its replanned plan version"
                )
            replay_capacity_row = self._repository.capacity_version(
                session,
                user_id=user_id,
                capacity_version_id=str(replay_plan["capacity_version_id"]),
            )
            if replay_capacity_row is None:
                raise PlanningContractError("Task execution replay is missing its capacity version")
            return TaskExecutionCommandResult(
                execution=replay_execution,
                plan=_dashboard_from_rows(
                    replay_plan,
                    self._repository.plan_tasks(
                        session,
                        plan_version_id=str(replay_plan["plan_version_id"]),
                    ),
                    _capacity_from_row(replay_capacity_row),
                ),
                replanned=str(replay_plan["plan_version_id"]) != str(replay["plan_version_id"]),
            )

        task = self._repository.current_task_for_execution(
            session,
            user_id=user_id,
            task_id=task_id,
        )
        if task is None:
            raise PlanningContractError("Current plan task does not exist")
        require_executable_plan_task(task, at=recorded_at)
        current = self._repository.current_task_execution(
            session,
            user_id=user_id,
            work_item_key=str(task["work_item_key"]),
        )
        based_on = command.based_on_execution_version_id
        if current is None and based_on is not None:
            raise PlanningContractError("Task execution has no version to supersede")
        if current is not None:
            if based_on is None or str(current["execution_version_id"]) != str(based_on):
                raise PlanningContractError(
                    "Task execution changed; reload the current version before submitting"
                )
            if current["status"] in {"complete", "abandoned"}:
                raise PlanningContractError("Terminal task execution cannot be changed")
            if command.elapsed_minutes < int(current["elapsed_minutes"]):
                raise PlanningContractError("Task execution elapsedMinutes cannot decrease")
        if command.status == "complete":
            if completion_owner != "essay_intelligence" or task["track"] != "essay":
                raise PlanningContractError(
                    "Task completion owner does not own this plan task track"
                )
            completion = CompletionContract.model_validate(task["completion_contract"])
            required_types = set(completion.completion_evidence_types) - {"task_execution"}
            if completion.requires_reflection:
                required_types.add("reflection_artifact")
            submitted_types = [item.entity_type for item in command.evidence_refs]
            if set(submitted_types) != required_types or len(submitted_types) != len(
                required_types
            ):
                raise PlanningContractError(
                    "Task completion evidence must exactly match its completion contract"
                )
            for evidence_type in required_types:
                item = next(
                    item for item in command.evidence_refs if item.entity_type == evidence_type
                )
                valid = self._repository.execution_evidence_exists(
                    session,
                    user_id=user_id,
                    task=task,
                    evidence_type=evidence_type,
                    owner=item.owner,
                    entity_id=item.entity_id,
                    version_id=item.version_id,
                    submitted_evidence={
                        evidence.entity_type: evidence.entity_id
                        for evidence in command.evidence_refs
                    },
                )
                if not valid:
                    raise PlanningContractError(
                        f"Task completion requires owned canonical {evidence_type} evidence"
                    )
        row = self._repository.insert_task_execution(
            session,
            values={
                "execution_version_id": str(uuid4()),
                "command_id": command_id,
                "user_id": user_id,
                "local_date": task["local_date"],
                "task_id": task_id,
                "plan_version_id": str(task["plan_version_id"]),
                "plan_version_no": int(task["plan_version_no"]),
                "candidate_key": str(task["candidate_key"]),
                "work_item_key": str(task["work_item_key"]),
                "version_no": int(current["version_no"]) + 1 if current is not None else 1,
                "status": command.status,
                "elapsed_minutes": command.elapsed_minutes,
                "evidence_refs": _json(
                    [item.model_dump(by_alias=True, mode="json") for item in command.evidence_refs]
                ),
                "abandonment_reason": command.abandonment_reason,
                "input_fingerprint": input_fingerprint,
                "supersedes_execution_version_id": (
                    str(current["execution_version_id"]) if current is not None else None
                ),
                "supersedes_execution_version_no": (
                    int(current["version_no"]) if current is not None else None
                ),
                "recorded_at": recorded_at,
            },
        )
        execution = _execution_from_row(row)
        self._outbox.append(
            session,
            deduplication_key=f"task-execution:{execution.execution_version_id}",
            aggregate_type="planning_task_execution",
            aggregate_id=str(execution.execution_version_id),
            event_type="planning_task_execution.changed",
            payload={
                "executionVersionId": str(execution.execution_version_id),
                "taskId": task_id,
                "planVersionId": str(task["plan_version_id"]),
                "userId": user_id,
                "localDate": task["local_date"].isoformat(),
                "status": execution.status,
                "inputFingerprint": input_fingerprint,
            },
        )
        plan = self.get_or_create_today(
            session,
            user_id=user_id,
            now=recorded_at,
            local_date=task["local_date"],
        )
        return TaskExecutionCommandResult(
            execution=execution,
            plan=plan,
            replanned=str(plan.plan_version_id) != str(task["plan_version_id"]),
        )

    def get_or_create_today(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
        local_date: date | None = None,
    ) -> TodayDashboard:
        generated_at = _as_utc(now or self._now())
        day = local_date or generated_at.astimezone(SHANGHAI).date()
        self._repository.lock_user(session, user_id=user_id)
        profile = self._repository.profile(session, user_id=user_id)
        preference = self._repository.current_or_create_preference(
            session,
            user_id=user_id,
            profile=profile,
            local_date=day,
            now=generated_at,
        )
        capacity = self._repository.current_or_create_capacity(
            session,
            user_id=user_id,
            local_date=day,
            preference=preference,
            now=generated_at,
        )
        execution_inputs = self._repository.execution_inputs(
            session,
            user_id=user_id,
            local_date=day,
        )
        elapsed_minutes = sum(int(item["elapsed_minutes"]) for item in execution_inputs)
        committed_minutes = sum(_committed_capacity_minutes(item) for item in execution_inputs)
        remaining_minutes = max(0, capacity.available_minutes - committed_minutes)
        goal_ability = self._repository.goal_and_ability(session, user_id=user_id)
        goal_ability = self._with_learner_target(
            session,
            user_id=user_id,
            snapshot=goal_ability,
        )
        gap = goal_gap(goal_ability, generated_at)
        planning_goal = GoalPlanningSnapshot(
            readiness=gap.readiness,
            positive_gap=(gap.baseline_gap or 0) > 0,
            goal_version_id=goal_ability.goal_version_id,
            ability_estimate_version_id=goal_ability.estimate_version_id,
            target_owner=goal_ability.target_owner,
            target_entity_type=goal_ability.target_entity_type,
        )
        objective = self._repository.objective_snapshot(
            session,
            user_id=user_id,
            goal=goal_ability,
            now=generated_at,
            preferred_xingce_module=capacity.preferred_xingce_module,
        )
        essay = self._repository.essay_snapshot(
            session,
            user_id=user_id,
            goal=goal_ability,
        )
        candidates = build_dual_track_candidates(
            goal=planning_goal,
            objective=objective,
            essay=essay,
            available_minutes=remaining_minutes,
            preferred_xingce_module=capacity.preferred_xingce_module,
            xingce_only=capacity.xingce_only,
            preferred_track=capacity.preferred_track,
        )
        strategy_decisions = self._repository.active_strategy_decisions(
            session,
            user_id=user_id,
            now=generated_at,
        )
        recent_minutes = self._repository.recent_track_minutes(
            session,
            user_id=user_id,
            before_date=day,
        )
        user_chose_to_study_today = (
            capacity.source_type == "user_override"
            and capacity.confirmed
            and capacity.available_minutes > 0
        )
        first_diagnostic_due = objective.baseline_required and objective.baseline_question_count > 0
        scheduled_recovery_day = (
            day.isoweekday() == preference.regular_rest_weekday
            and not user_chose_to_study_today
            and not first_diagnostic_due
        )
        adaptive_recovery_day = _needs_adaptive_recovery(capacity)
        recovery_day = scheduled_recovery_day or adaptive_recovery_day
        mock_day = day.isoweekday() == preference.regular_mock_weekday
        all_candidates = list(candidates.candidates)
        mock_due = mock_day and self._repository.weekly_mock_due(
            session,
            user_id=user_id,
            week_start=day - timedelta(days=day.weekday()),
        )
        if mock_due:
            all_candidates.append(_mock_candidate(preference, remaining_minutes))
        all_candidates, strategy_omissions, strategy_reason_codes = _apply_strategy_decisions(
            all_candidates, strategy_decisions
        )
        execution_by_work_item = {str(item["work_item_key"]): item for item in execution_inputs}
        candidate_work_items = {
            item.candidate_key: _candidate_work_item_key(day, item) for item in all_candidates
        }
        terminal_work_items = {
            key
            for key, item in execution_by_work_item.items()
            if item["status"] in {"complete", "abandoned"}
        }
        removed_terminal = [
            item
            for item in all_candidates
            if candidate_work_items[item.candidate_key] in terminal_work_items
        ]
        all_candidates = [
            _remaining_candidate(
                item,
                execution_by_work_item.get(candidate_work_items[item.candidate_key]),
            )
            for item in all_candidates
            if candidate_work_items[item.candidate_key] not in terminal_work_items
        ]
        for track in {item.track for item in removed_terminal}:
            if not any(item.track == track for item in all_candidates):
                candidates.omitted_track_reasons.setdefault(
                    track,
                    "该轨道今日工作项已完成或已主动结束，不重复生成 ready 任务。",
                )
        selection = select_daily_tasks(
            all_candidates,
            available_minutes=remaining_minutes,
            recent_track_minutes=recent_minutes,  # type: ignore[arg-type]
            recovery_day=recovery_day,
        )
        day_mode = _day_mode(
            recovery_day=recovery_day,
            selection=selection.tasks,
        )
        omitted_track_reasons = {
            **candidates.omitted_track_reasons,
            **strategy_omissions,
        }
        if mock_due and remaining_minutes < 120:
            omitted_track_reasons["mock"] = (
                "周度标准卷需要连续 120 分钟；今日容量不足，保留正常双轨训练并等待重新安排模考时段。"
            )
        explanation = _dual_track_explanation(
            selection.tasks,
            selection.dual_track_constraint_applied,
            omitted=omitted_track_reasons,
            capability_gaps=candidates.capability_gaps,
            day_mode=day_mode,
        )
        plan_evidence = _plan_evidence_refs(
            selection.tasks,
            preference=preference,
            capacity=replace(capacity, available_minutes=remaining_minutes),
            goal=goal_ability,
        )
        plan_evidence.extend(
            PlanningEvidenceRef(
                owner="planning_execution",
                entity_type="task_execution_version",
                entity_id=str(item["execution_version_id"]),
                version_id=str(item["execution_version_id"]),
                role=str(item["status"]),
            )
            for item in execution_inputs
        )
        plan_evidence.extend(
            PlanningEvidenceRef(
                owner="learning_brain",
                entity_type="strategy_decision_version",
                entity_id=item.decision_id,
                version_id=item.decision_version_id,
                role=item.decision_type,
            )
            for item in strategy_decisions
        )
        plan_evidence.extend(
            PlanningEvidenceRef.model_validate(ref)
            for item in strategy_decisions
            for ref in item.evidence_refs
        )
        plan_evidence = [
            PlanningEvidenceRef.model_validate(item)
            for item in _unique_refs(
                [ref.model_dump(by_alias=True, mode="json") for ref in plan_evidence]
            )
        ]
        execution_state_fingerprint = _fingerprint(
            {
                "executions": [
                    {
                        "executionVersionId": str(item["execution_version_id"]),
                        "workItemKey": str(item["work_item_key"]),
                        "status": str(item["status"]),
                        "inputFingerprint": str(item["input_fingerprint"]),
                    }
                    for item in execution_inputs
                ]
            }
        )
        strategy_state = [
            {
                "decisionVersionId": item.decision_version_id,
                "decisionType": item.decision_type,
                "scopeType": item.scope_type,
                "scopeKey": item.scope_key,
                "effectiveFrom": item.effective_from.isoformat(),
                "effectiveUntil": item.effective_until.isoformat(),
                "reviewAt": item.review_at.isoformat(),
                "inputFingerprint": item.input_fingerprint,
            }
            for item in strategy_decisions
        ]
        strategy_state_fingerprint = _fingerprint({"decisions": strategy_state})
        input_versions = {
            "profile": str(profile.profile_version),
            "weeklyPreference": preference.preference_version_id,
            "dailyCapacity": capacity.capacity_version_id,
            "positionGoal": (
                goal_ability.goal_version_id or "unavailable"
                if goal_ability.target_owner == "position_goal"
                else "unavailable"
            ),
            "learnerTargetScore": (
                goal_ability.goal_version_id or "unavailable"
                if goal_ability.target_owner == "learner_target_score"
                else "unavailable"
            ),
            "abilityEstimate": goal_ability.estimate_version_id or "unavailable",
            "objectiveClosure": (objective.source_version_id or objective.closure_readiness),
            "essayClosure": (
                essay.mastery_version_ids[0]
                if essay.mastery_version_ids
                else essay.closure_readiness
            ),
            "essayTask": essay.task_version_id or "unavailable",
            "essayMaterialBundle": essay.material_bundle_version_id or "unavailable",
            "essayRubric": essay.rubric_version_id or "unavailable",
            "taskExecutionState": execution_state_fingerprint,
            "strategyDecisions": strategy_state_fingerprint,
        }
        input_payload = {
            "algorithmVersion": PLAN_ALGORITHM_VERSION,
            "date": day.isoformat(),
            "inputVersions": input_versions,
            "goalGap": gap.model_dump(by_alias=True, mode="json"),
            "objective": asdict(objective),
            "essay": asdict(essay),
            "capacity": asdict(capacity),
            "elapsedMinutes": elapsed_minutes,
            "committedMinutes": committed_minutes,
            "remainingMinutes": remaining_minutes,
            "preference": asdict(preference),
            "recentTrackMinutes": recent_minutes,
            "candidateKeys": [item.candidate_key for item in all_candidates],
            "candidateWorkItems": candidate_work_items,
            "taskExecutions": [
                {
                    "executionVersionId": str(item["execution_version_id"]),
                    "workItemKey": str(item["work_item_key"]),
                    "status": str(item["status"]),
                    "inputFingerprint": str(item["input_fingerprint"]),
                }
                for item in execution_inputs
            ],
            "strategyDecisions": strategy_state,
            "dayMode": day_mode,
        }
        input_fingerprint = _fingerprint(input_payload)
        existing = self._repository.current_plan_for_fingerprint(
            session,
            user_id=user_id,
            local_date=day,
            input_fingerprint=input_fingerprint,
        )
        if existing is not None:
            return _dashboard_from_rows(
                existing,
                self._repository.plan_tasks(
                    session,
                    plan_version_id=str(existing["plan_version_id"]),
                ),
                capacity,
            )

        previous = self._repository.current_plan(
            session,
            user_id=user_id,
            local_date=day,
        )
        plan_version_id = str(uuid4())
        version_no = int(previous["version_no"]) + 1 if previous is not None else 1
        headline, rationale = _plan_copy(
            day_mode=day_mode,
            tasks=selection.tasks,
            capacity=capacity,
            goal_gap=gap,
            essay_available=essay.next_stage != "content_unavailable",
        )
        invalidation_conditions = [
            "position_goal_changed",
            "ability_estimate_changed",
            "daily_capacity_changed",
            "essay_review_state_changed",
            "task_execution_recorded",
            "strategy_decision_changed",
        ]
        valid_until = datetime.combine(day + timedelta(days=1), time.min, SHANGHAI).astimezone(UTC)
        replan_codes = [*selection.explanation_codes, *strategy_reason_codes]
        if previous is not None:
            previous_inputs = dict(previous["input_versions"])
            if previous_inputs.get("dailyCapacity") != capacity.capacity_version_id:
                replan_codes.append("daily_capacity_changed")
            if previous_inputs.get("taskExecutionState") != execution_state_fingerprint:
                replan_codes.append("task_execution_recorded")
            if previous_inputs.get("strategyDecisions") != strategy_state_fingerprint:
                replan_codes.append("strategy_decision_changed")
        if adaptive_recovery_day:
            replan_codes.append("capacity_recovery_mode")
        if mock_due and remaining_minutes < 120:
            replan_codes.append("weekly_mock_capacity_insufficient")
        replan_codes = list(dict.fromkeys(replan_codes))
        row = self._repository.insert_plan(
            session,
            values={
                "plan_version_id": plan_version_id,
                "command_id": (f"daily-plan:{user_id}:{day}:v{version_no}:{input_fingerprint}"),
                "user_id": user_id,
                "local_date": day,
                "version_no": version_no,
                "capacity_version_id": capacity.capacity_version_id,
                "weekly_preference_version_id": preference.preference_version_id,
                "day_mode": day_mode,
                "headline": headline,
                "rationale": rationale,
                "goal_gap": _json(gap.model_dump(by_alias=True, mode="json")),
                "scheduled_minutes": selection.scheduled_minutes,
                "due_reflection_count": candidates.due_reflection_count,
                "due_verification_count": candidates.due_verification_count,
                "track_explanation": _json(explanation.model_dump(by_alias=True, mode="json")),
                "input_versions": _json(input_versions),
                "evidence_refs": _json(
                    [item.model_dump(by_alias=True, mode="json") for item in plan_evidence]
                ),
                "invalidation_conditions": _json(invalidation_conditions),
                "replan_reason_codes": _json(replan_codes),
                "contract_version": PLAN_CONTRACT_VERSION,
                "algorithm_version": PLAN_ALGORITHM_VERSION,
                "input_fingerprint": input_fingerprint,
                "supersedes_plan_version_id": (
                    str(previous["plan_version_id"]) if previous is not None else None
                ),
                "generated_at": generated_at,
                "valid_until": valid_until,
            },
        )
        task_rows: list[RowMapping] = []
        for order, ranked in enumerate(selection.tasks, start=1):
            task_id = str(uuid4())
            completion = _completion_contract(ranked.candidate)
            task_fingerprint = _fingerprint(
                {
                    "planInputFingerprint": input_fingerprint,
                    "candidateKey": ranked.candidate.candidate_key,
                    "taskOrder": order,
                    "selectionIntent": ranked.candidate.selection_intent,
                }
            )
            task_rows.append(
                self._repository.insert_plan_task(
                    session,
                    values={
                        "task_id": task_id,
                        "plan_version_id": plan_version_id,
                        "user_id": user_id,
                        "local_date": day,
                        "plan_version_no": version_no,
                        "candidate_key": ranked.candidate.candidate_key,
                        "work_item_key": candidate_work_items[ranked.candidate.candidate_key],
                        "task_order": order,
                        "track": ranked.candidate.track,
                        "stage": ranked.candidate.stage,
                        "kind": ranked.candidate.kind,
                        "title": ranked.candidate.title,
                        "reason": ranked.candidate.reason,
                        "estimated_minutes": ranked.candidate.estimated_minutes,
                        "mandatory": ranked.candidate.mandatory,
                        "start_url": _materialize_plan_task_start_url(
                            ranked.candidate.start_url,
                            task_id=task_id,
                        ),
                        "priority_score": ranked.priority_score,
                        "goal_gain": ranked.candidate.priority.goal_gain,
                        "ability_gain": ranked.candidate.priority.ability_gain,
                        "verification_urgency": (ranked.candidate.priority.verification_urgency),
                        "time_fit": ranked.candidate.priority.time_fit,
                        "load_penalty": ranked.candidate.priority.load_penalty,
                        "track_balance_bonus": ranked.track_balance_bonus,
                        "evidence_refs": _json(list(ranked.candidate.evidence_refs)),
                        "completion_contract": _json(
                            completion.model_dump(by_alias=True, mode="json")
                        ),
                        "selection_intent": (
                            _json(ranked.candidate.selection_intent)
                            if ranked.candidate.selection_intent is not None
                            else None
                        ),
                        "input_fingerprint": task_fingerprint,
                        "created_at": generated_at,
                    },
                )
            )
        self._outbox.append(
            session,
            deduplication_key=f"daily-plan:{plan_version_id}",
            aggregate_type="daily_plan",
            aggregate_id=plan_version_id,
            event_type="daily_plan.changed",
            payload={
                "planVersionId": plan_version_id,
                "userId": user_id,
                "localDate": day.isoformat(),
                "dayMode": day_mode,
                "xingceMinutes": explanation.xingce_minutes,
                "essayMinutes": explanation.essay_minutes,
                "spentMinutes": elapsed_minutes,
                "taskExecutionState": execution_state_fingerprint,
                "inputFingerprint": input_fingerprint,
            },
        )
        return _dashboard_from_rows(
            row,
            self._repository.plan_tasks(
                session,
                plan_version_id=plan_version_id,
            ),
            capacity,
        )


def _materialize_plan_task_start_url(start_url: str, *, task_id: str) -> str:
    parts = urlsplit(start_url)
    if parts.path not in {
        "/pages/essay-task/index",
        "/xingce/xingce-task-intro/index",
        "/pages/mock-exam/index",
    }:
        return start_url
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(key == "planTaskId" for key, _ in query):
        return start_url
    query.append(("planTaskId", task_id))
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))


def _candidate_work_item_key(local_date: date, candidate: TaskCandidate) -> str:
    completion = _completion_contract(candidate)
    return _fingerprint(
        {
            "localDate": local_date.isoformat(),
            "candidateKey": candidate.candidate_key,
            "track": candidate.track,
            "stage": candidate.stage,
            "kind": candidate.kind,
            "title": candidate.title,
            "startUrl": candidate.start_url,
            "evidenceRefs": list(candidate.evidence_refs),
            "completion": completion.model_dump(by_alias=True, mode="json"),
            "selectionIntent": candidate.selection_intent,
        }
    )


def _remaining_candidate(
    candidate: TaskCandidate,
    execution: RowMapping | None,
) -> TaskCandidate:
    if execution is None or execution["status"] != "in_progress":
        return candidate
    remaining = max(1, candidate.estimated_minutes - int(execution["elapsed_minutes"]))
    return replace(candidate, estimated_minutes=remaining)


def _apply_strategy_decisions(
    candidates: list[TaskCandidate],
    decisions: list[ActiveStrategyDecision],
) -> tuple[list[TaskCandidate], dict[str, str], list[str]]:
    if not decisions:
        return candidates, {}, []

    adjusted: list[TaskCandidate] = []
    removed_tracks: set[str] = set()
    reason_codes: list[str] = []
    for candidate in candidates:
        matching = [
            decision for decision in decisions if _strategy_matches_candidate(decision, candidate)
        ]
        if not matching:
            adjusted.append(candidate)
            continue

        decision_refs = tuple(
            {
                "owner": "learning_brain",
                "entityType": "strategy_decision_version",
                "entityId": decision.decision_id,
                "versionId": decision.decision_version_id,
                "role": decision.decision_type,
            }
            for decision in matching
        )
        if candidate.stage not in STRATEGY_ADJUSTABLE_STAGES:
            adjusted.append(candidate)
            reason_codes.append("strategy_preserved_required_closure")
            continue

        if any(decision.decision_type == "strategic_abandon" for decision in matching):
            removed_tracks.add(candidate.track)
            reason_codes.append("strategy_abandon_applied")
            continue

        guidance_decisions = [
            decision for decision in matching if decision.decision_type == "baseline_guidance"
        ]
        if guidance_decisions:
            # 引导型：证据不足时大脑建议先建立基线。把匹配的摸底/基础练习
            # 候选提为高优先级（能力收益提升），reason 附大脑 rationale。
            priority = candidate.priority
            guidance_reason = guidance_decisions[0].rationale
            candidate = replace(
                candidate,
                reason=(f"{candidate.reason} 中控大脑建议先建立基线：{guidance_reason}"),
                evidence_refs=tuple(_unique_refs([*candidate.evidence_refs, *decision_refs])),
                priority=replace(
                    priority,
                    ability_gain=round(
                        min(1.0, priority.ability_gain / STRATEGY_PRIORITY_FACTOR),
                        4,
                    ),
                    goal_gain=round(
                        min(1.0, priority.goal_gain / STRATEGY_PRIORITY_FACTOR),
                        4,
                    ),
                ),
            )
            reason_codes.append("strategy_baseline_guidance_applied")

        phase_decisions = [
            decision for decision in matching if decision.decision_type == "phase_deprioritize"
        ]
        if phase_decisions:
            priority = candidate.priority
            candidate = replace(
                candidate,
                reason=(
                    f"{candidate.reason} 已确认对该范围阶段降权；"
                    "保留证据收益，但让位于当前更高优先级闭环。"
                ),
                evidence_refs=tuple(_unique_refs([*candidate.evidence_refs, *decision_refs])),
                priority=replace(
                    priority,
                    goal_gain=round(
                        priority.goal_gain * STRATEGY_PRIORITY_FACTOR,
                        4,
                    ),
                    ability_gain=round(
                        priority.ability_gain * STRATEGY_PRIORITY_FACTOR,
                        4,
                    ),
                ),
            )
            reason_codes.append("strategy_phase_deprioritize_applied")

        if any(decision.decision_type == "exam_skip" for decision in matching):
            candidate = replace(
                candidate,
                reason=(
                    f"{candidate.reason} 已确认考场跳过策略；"
                    "该策略只约束考场取舍，不自动删除学习证据或到期闭环。"
                ),
                evidence_refs=tuple(_unique_refs([*candidate.evidence_refs, *decision_refs])),
            )
            reason_codes.append("strategy_exam_skip_active")
        adjusted.append(candidate)

    omissions: dict[str, str] = {}
    for track in removed_tracks:
        if not any(candidate.track == track for candidate in adjusted):
            omissions[track] = (
                "这条轨道当前唯一的新练习落在你已确认先放下的范围里；"
                "到期的复盘、复测和重写还是会照常安排。"
            )
    return adjusted, omissions, list(dict.fromkeys(reason_codes))


def _strategy_matches_candidate(
    decision: ActiveStrategyDecision,
    candidate: TaskCandidate,
) -> bool:
    if decision.scope_type == "track":
        return decision.scope_key == candidate.track
    if decision.scope_type == "module":
        return decision.scope_key in candidate.strategy_module_keys
    if decision.scope_type == "question_type":
        return decision.scope_key == candidate.strategy_question_type
    return False


def _capacity_from_row(row: RowMapping) -> CapacitySnapshot:
    return CapacitySnapshot(
        capacity_version_id=str(row["capacity_version_id"]),
        version_no=int(row["version_no"]),
        available_minutes=int(row["available_minutes"]),
        energy=int(row["energy_level"]),
        stress=int(row["stress_level"]),
        confidence_level=int(row["confidence_level"]),
        preferred_xingce_module=(
            str(row["preferred_xingce_module"])
            if row["preferred_xingce_module"] is not None
            else None
        ),
        xingce_only=bool(row["xingce_only"]),
        preferred_track=str(row["preferred_track"]),
        confirmed=bool(row["confirmed"]),
        source_type=str(row["source_type"]),
        input_fingerprint=str(row["input_fingerprint"]),
    )


def _today_capacity(capacity: CapacitySnapshot, local_date: date) -> TodayCapacity:
    return TodayCapacity(
        capacity_version_id=UUID(capacity.capacity_version_id),
        local_date=local_date,
        version_no=capacity.version_no,
        available_minutes=capacity.available_minutes,
        energy=capacity.energy,
        stress=capacity.stress,
        confidence_level=capacity.confidence_level,
        preferred_xingce_module=capacity.preferred_xingce_module,
        xingce_only=capacity.xingce_only,
        preferred_track=capacity.preferred_track,
        confirmed=capacity.confirmed,
        source_type=capacity.source_type,
    )


def _execution_from_row(row: RowMapping, *, prefixed: bool = False) -> TaskExecutionVersion:
    prefix = "execution_" if prefixed else ""
    return TaskExecutionVersion(
        execution_version_id=UUID(str(row["execution_version_id"])),
        task_id=UUID(str(row[f"{prefix}task_id"])),
        plan_version_id=UUID(str(row[f"{prefix}plan_version_id"])),
        local_date=row[f"{prefix}local_date"],
        work_item_key=str(row[f"{prefix}work_item_key"]),
        version_no=int(row[f"{prefix}version_no"]),
        status=row[f"{prefix}status"],
        elapsed_minutes=int(row[f"{prefix}elapsed_minutes"]),
        evidence_refs=[
            PlanningEvidenceRef.model_validate(item) for item in row[f"{prefix}evidence_refs"]
        ],
        abandonment_reason=row[f"{prefix}abandonment_reason"],
        input_fingerprint=str(row[f"{prefix}input_fingerprint"]),
        supersedes_execution_version_id=(
            UUID(str(row["supersedes_execution_version_id"]))
            if row["supersedes_execution_version_id"] is not None
            else None
        ),
        supersedes_execution_version_no=(
            int(row["supersedes_execution_version_no"])
            if row["supersedes_execution_version_no"] is not None
            else None
        ),
        recorded_at=row[f"{prefix}recorded_at"],
    )


def goal_gap(snapshot: GoalAbilitySnapshot, now: datetime) -> GoalGap:
    target = (
        ScoreRange(
            lower=snapshot.target_lower,
            upper=snapshot.target_upper,
            confidence=_target_confidence(snapshot.target_evidence_level),
        )
        if snapshot.target_lower is not None and snapshot.target_upper is not None
        else None
    )
    current = (
        ScoreRange(
            lower=snapshot.total_lower,
            upper=snapshot.total_upper,
            confidence=snapshot.estimate_confidence or "unknown",
        )
        if snapshot.total_lower is not None and snapshot.total_upper is not None
        else None
    )
    if snapshot.goal_version_id is None:
        readiness = "unavailable"
        summary = "历史目标分尚未生成；规划继续采集双科能力并安排学习，不以岗位选择阻断任务。"
    elif snapshot.target_evidence_level in {None, "unavailable"}:
        readiness = "partial"
        summary = "目标参照已建立，但目标分区间缺少可引用样本，暂不计算分差。"
    elif snapshot.estimate_version_id is None or snapshot.estimate_status == "partial":
        readiness = "partial"
        summary = "目标分已生成，但行测与申论尚未形成同版本完整总分估计。"
    elif (
        snapshot.estimate_valid_until is None
        or _as_utc(snapshot.estimate_valid_until) <= now
        or (snapshot.target_valid_until is not None and _as_utc(snapshot.target_valid_until) <= now)
    ):
        readiness = "stale"
        summary = "目标分或双科能力证据已过期，旧区间仅供回看，不用于分差。"
    elif target is None or current is None or snapshot.total_recommended is None:
        readiness = "partial"
        summary = "目标分或总分区间缺少完整证据，暂不计算岗位分差。"
    else:
        readiness = "complete"
        count = (snapshot.evidence_summary or {}).get("evidenceCount", 0)
        if snapshot.target_owner == "learner_target_score":
            summary = snapshot.target_evidence_summary or (
                "引用历史同类岗位目标分版本与能力估计版本。"
            )
            if snapshot.target_recommended is not None and snapshot.total_recommended is not None:
                summary += (
                    f" 当前差距按稳妥线 {snapshot.target_recommended:g} 分减去"
                    f"当前推荐估计 {snapshot.total_recommended:g} 分计算；"
                    f"能力区间共引用 {count} 条当前成绩证据。"
                )
        else:
            summary = f"引用岗位目标版本与能力估计版本，共 {count} 条当前成绩证据。"
    baseline_gap = (
        round(snapshot.target_recommended - snapshot.total_recommended, 2)
        if readiness == "complete"
        and snapshot.target_recommended is not None
        and snapshot.total_recommended is not None
        else None
    )
    return GoalGap(
        readiness=readiness,
        goal_id=snapshot.goal_version_id,
        position_name=snapshot.position_name,
        department_name=snapshot.department_name,
        target_score=target,
        current_score=current,
        baseline_gap=baseline_gap,
        evidence_summary=summary,
    )


def _mock_candidate(
    preference: PreferenceSnapshot,
    available_minutes: int,
) -> TaskCandidate:
    return TaskCandidate(
        candidate_key="weekly-guokao-xingce-mock",
        track="mock",
        stage="weekly_mock",
        kind="mock_exam",
        title="国考行测周度标准卷",
        reason="今天是已配置的模考日，本周尚无标准卷记录，先完成高置信校准。",
        estimated_minutes=120,
        completion_rule="严格限时完成整卷，交卷后查看复盘，过程中不显示反馈。",
        start_url="/pages/mock-exam/index",
        evidence_refs=(
            {
                "owner": "planning",
                "entityType": "weekly_study_preference",
                "entityId": preference.preference_version_id,
                "versionId": preference.preference_version_id,
            },
        ),
        priority=PriorityBreakdown(
            goal_gain=0.75,
            ability_gain=0.95,
            verification_urgency=0.90,
            time_fit=1.0 if available_minutes >= 120 else 0.0,
            load_penalty=0.45,
        ),
        mandatory=True,
    )


def _dual_track_explanation(
    selected: tuple[RankedCandidate, ...],
    constraint_applied: bool,
    *,
    omitted: dict[str, str],
    capability_gaps: dict[str, str],
    day_mode: str,
) -> DualTrackExplanation:
    tracks = ("xingce", "essay", "recovery", "mock")
    minutes = {
        track: sum(
            item.candidate.estimated_minutes for item in selected if item.candidate.track == track
        )
        for track in tracks
    }
    allocations: list[TrackAllocation] = []
    for track in tracks:
        tasks = [item for item in selected if item.candidate.track == track]
        if not tasks:
            continue
        refs = _unique_refs([ref for item in tasks for ref in item.candidate.evidence_refs])
        allocations.append(
            TrackAllocation(
                track=track,
                minutes=minutes[track],
                reason="；".join(dict.fromkeys(item.candidate.reason for item in tasks)),
                stages=list(dict.fromkeys(item.candidate.stage for item in tasks)),
                evidence_refs=[PlanningEvidenceRef.model_validate(ref) for ref in refs],
            )
        )
    omitted_reasons = dict(omitted)
    for track in tracks:
        if minutes[track] > 0:
            continue
        omitted_reasons.setdefault(
            track,
            "今天的容量与更高优先级闭环不允许安排该轨道。",
        )
    if day_mode == "mock":
        mode = "mock_day"
        summary = "今天执行周度标准卷，双轨平衡由其余训练日保证。"
    elif day_mode == "recovery":
        mode = "recovery_day"
        summary = "今天是恢复日，只保留到期闭环，不开启新知识。"
    elif "essay" in omitted:
        mode = "evidence_missing"
        summary = "申论内容版本未通过门禁，计划明确缺口，不用固定占位任务冒充双轨。"
    elif constraint_applied:
        mode = "balanced"
        summary = "行测和申论分别产生候选，再在同一时间预算内选择收益最高的双轨组合。"
    else:
        mode = "capacity_limited"
        summary = "今天容量不足以同时完成双轨，按证据收益和近七天投入选择优先任务。"
    return DualTrackExplanation(
        mode=mode,
        summary=summary,
        xingce_minutes=minutes["xingce"],
        essay_minutes=minutes["essay"],
        recovery_minutes=minutes["recovery"],
        mock_minutes=minutes["mock"],
        dual_track_constraint_applied=constraint_applied,
        allocations=allocations,
        omitted_track_reasons=omitted_reasons,
        capability_gaps=capability_gaps,
    )


def _plan_evidence_refs(
    tasks: tuple[RankedCandidate, ...],
    *,
    preference: PreferenceSnapshot,
    capacity: CapacitySnapshot,
    goal: GoalAbilitySnapshot,
) -> list[PlanningEvidenceRef]:
    refs: list[dict[str, str]] = [
        {
            "owner": "planning",
            "entityType": "weekly_study_preference",
            "entityId": preference.preference_version_id,
            "versionId": preference.preference_version_id,
        },
        {
            "owner": "planning",
            "entityType": "daily_capacity",
            "entityId": capacity.capacity_version_id,
            "versionId": capacity.capacity_version_id,
        },
    ]
    refs.extend(ref for item in tasks for ref in item.candidate.evidence_refs)
    if goal.goal_version_id:
        refs.append(
            {
                "owner": goal.target_owner,
                "entityType": goal.target_entity_type,
                "entityId": goal.goal_version_id,
                "versionId": goal.goal_version_id,
            }
        )
    return [PlanningEvidenceRef.model_validate(ref) for ref in _unique_refs(refs)]


def _completion_contract(candidate: TaskCandidate) -> CompletionContract:
    evidence_type_by_stage = {
        "objective_baseline": "question_attempt",
        "objective_practice": "question_attempt",
        "objective_reflection": "reflection_artifact",
        "objective_verification": "question_attempt",
        "essay_material_reading": "essay_material_annotation",
        "essay_point_extraction": "essay_point_extraction",
        "essay_outlining": "essay_outline",
        "essay_drafting": "essay_answer_version",
        "essay_review": "essay_review_acknowledgement",
        "essay_rewrite": "essay_rewrite_version",
        "essay_transfer": "essay_transfer_verification",
        "weekly_mock": "assessment_attempt",
    }
    evidence_type = evidence_type_by_stage.get(candidate.stage)
    if evidence_type is None:
        raise PlanningContractError(
            f"Stage {candidate.stage} has no published canonical completion writer"
        )
    evidence_types = [evidence_type, "task_execution"]
    return CompletionContract(
        objective=candidate.title,
        success_criteria=[candidate.completion_rule],
        terminal_conditions=[
            "满足验收条件并提交证据",
            "用户主动结束并记录未完成原因",
        ],
        requires_reflection=candidate.stage
        in {
            "objective_reflection",
            "essay_drafting",
        },
        completion_evidence_types=evidence_types,
    )


def _plan_copy(
    *,
    day_mode: str,
    tasks: tuple[RankedCandidate, ...],
    capacity: CapacitySnapshot,
    goal_gap: GoalGap,
    essay_available: bool,
) -> tuple[str, str]:
    if day_mode == "mock":
        return (
            "今天用标准卷重新校准，不追加零散训练",
            "本周尚无标准卷，模考是最高置信能力证据；申论由其余训练日保持覆盖。",
        )
    if day_mode == "recovery":
        return (
            "今天恢复，不开新知识",
            "恢复日只处理到期闭环，未完成任务不会累积成明日债务。",
        )
    if not tasks:
        if goal_gap.readiness != "complete":
            return (
                "今天先把训练起点准备好",
                "今天的可用时间已按每周计划生效；先完成起点诊断，目标岗位可以稍后再选，不影响开始学习。",
            )
        return (
            "今天的正式安排还没就绪",
            "今天的可用时间已经生效，还差能安全派发的训练内容；稍后回来重新读取即可，岸岸不会硬塞不可执行的任务。",
        )
    xingce = sum(
        item.candidate.estimated_minutes for item in tasks if item.candidate.track == "xingce"
    )
    essay = sum(
        item.candidate.estimated_minutes for item in tasks if item.candidate.track == "essay"
    )
    headline = (
        f"今天双轨推进：行测 {xingce} 分钟，申论 {essay} 分钟"
        if xingce and essay
        else f"今天先完成最高收益的 {sum(item.candidate.estimated_minutes for item in tasks)} 分钟闭环"
    )
    capacity_note = "按你今天的调整" if capacity.source_type == "user_override" else "按每周计划"
    gap_note = (
        f"当前岗位总分差约 {goal_gap.baseline_gap:g} 分"
        if goal_gap.readiness == "complete" and goal_gap.baseline_gap is not None
        else "岗位分差尚不可用"
    )
    essay_note = "申论按当前闭环阶段进入排序" if essay_available else "申论内容版本缺口已明确记录"
    return (
        headline,
        f"当日容量 {capacity.available_minutes} 分钟（{capacity_note}）；{gap_note}；{essay_note}。",
    )


def _dashboard_from_rows(
    plan: RowMapping,
    tasks: list[RowMapping],
    capacity: CapacitySnapshot,
) -> TodayDashboard:
    today_tasks = []
    for row in tasks:
        priority = PriorityDecomposition(
            goal_gain=float(row["goal_gain"]),
            ability_gain=float(row["ability_gain"]),
            verification_urgency=float(row["verification_urgency"]),
            time_fit=float(row["time_fit"]),
            load_penalty=float(row["load_penalty"]),
            track_balance_bonus=float(row["track_balance_bonus"]),
        )
        completion = CompletionContract.model_validate(row["completion_contract"])
        execution = (
            _execution_from_row(row, prefixed=True)
            if row["execution_version_id"] is not None
            else None
        )
        evidence_refs = [PlanningEvidenceRef.model_validate(item) for item in row["evidence_refs"]]
        if execution is not None:
            evidence_refs.append(
                PlanningEvidenceRef(
                    owner="planning_execution",
                    entity_type="task_execution_version",
                    entity_id=str(execution.execution_version_id),
                    version_id=str(execution.execution_version_id),
                    role=execution.status,
                )
            )
        today_tasks.append(
            TodayTask(
                id=UUID(str(row["task_id"])),
                kind=row["kind"],
                title=row["title"],
                reason=row["reason"],
                estimated_minutes=int(row["estimated_minutes"]),
                completion_rule=completion.success_criteria[0],
                status=execution.status if execution is not None else "ready",
                priority=int(row["task_order"]),
                track=row["track"],
                stage=row["stage"],
                mandatory=bool(row["mandatory"]),
                start_url=_materialize_plan_task_start_url(
                    row["start_url"],
                    task_id=str(row["task_id"]),
                ),
                priority_score=float(row["priority_score"]),
                priority_breakdown=priority,
                evidence_refs=evidence_refs,
                completion=completion,
                execution=execution,
            )
        )
    local_date = plan["local_date"]
    return TodayDashboard(
        generated_at=plan["generated_at"],
        plan_version=f"plan-v{int(plan['version_no'])}-{plan['plan_version_id']}",
        plan_version_id=UUID(str(plan["plan_version_id"])),
        local_date=local_date,
        version_no=int(plan["version_no"]),
        plan_basis=plan["plan_basis"],
        day_mode=plan["day_mode"],
        headline=plan["headline"],
        rationale=plan["rationale"],
        goal_gap=GoalGap.model_validate(plan["goal_gap"]),
        capacity=_today_capacity(capacity, local_date),
        tasks=today_tasks,
        due_reflection_count=(
            int(plan["due_reflection_count"]) if plan["due_reflection_count"] is not None else None
        ),
        due_verification_count=(
            int(plan["due_verification_count"])
            if plan["due_verification_count"] is not None
            else None
        ),
        is_recovery_day=plan["day_mode"] == "recovery",
        dual_track_explanation=DualTrackExplanation.model_validate(plan["track_explanation"]),
        input_versions=dict(plan["input_versions"]),
        evidence_refs=[PlanningEvidenceRef.model_validate(item) for item in plan["evidence_refs"]],
        invalidation_conditions=list(plan["invalidation_conditions"]),
        replan_reason_codes=list(plan["replan_reason_codes"]),
    )


def _day_mode(
    *,
    recovery_day: bool,
    selection: tuple[RankedCandidate, ...],
) -> str:
    if recovery_day:
        return "recovery"
    if any(item.candidate.track == "mock" for item in selection):
        return "mock"
    if not selection:
        return "unavailable"
    return "normal"


def _committed_capacity_minutes(execution: RowMapping) -> int:
    elapsed = int(execution["elapsed_minutes"])
    if str(execution["status"]) not in {"complete", "abandoned"}:
        return elapsed
    return max(elapsed, int(execution["estimated_minutes"]))


def _needs_adaptive_recovery(capacity: CapacitySnapshot) -> bool:
    return (
        capacity.available_minutes == 0
        or capacity.energy == 1
        or capacity.stress == 5
        or (capacity.energy <= 2 and capacity.stress >= 4)
    )


def _target_confidence(evidence_level: str | None) -> str:
    return {
        "direct": "high",
        "strong": "high",
        "limited": "medium",
        "historical_cohort": "medium",
        "unavailable": "unknown",
    }.get(evidence_level or "", "unknown")


def _unique_refs(refs: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: dict[tuple[str, str, str, str | None], dict[str, str]] = {}
    for ref in refs:
        key = (
            ref["owner"],
            ref.get("entityType", "evidence"),
            ref["entityId"],
            ref.get("versionId"),
        )
        unique[key] = ref
    return list(unique.values())


def _fingerprint(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
