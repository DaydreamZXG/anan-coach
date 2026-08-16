from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.ability_estimation.schemas import AbilityEstimateCurrent
from app.domains.ability_estimation.service import AbilityEstimateService
from app.domains.calibration.service import (
    project_knowledge_point_weaknesses,
)
from app.domains.learning_brain.repository import LearningBrainRepository, json_value
from app.domains.learning_brain.schemas import (
    BrainCandidateSummary,
    BrainDecisionSummary,
    BrainEvidenceRef,
    BrainEvidenceSummary,
    CoachRecognition,
    ErrorPatternDetail,
    ErrorPatternSummary,
    InferenceCandidateVersion,
    KnowledgePointWeakness,
    LearnerCoachContext,
    LearningBrainProfileVersion,
    ModuleAbility,
    StrategyCandidateCommand,
    StrategyCandidateResult,
    StrategyDecisionCommand,
    StrategyDecisionResult,
    StrategyDecisionVersion,
    StrategyDirective,
    StrategyDirectiveEntry,
)
from app.domains.xingce_learning.cause_agent.tools import CAUSES as XINGCE_CAUSE_TAXONOMY
from app.platform.outbox import OutboxRepository


PROFILE_ALGORITHM_VERSION = "learning-brain-profile@v1"
STRATEGY_POLICY_VERSION = "strategy-candidate@v1"
EVIDENCE_VALIDITY_DAYS = 365


class LearningBrainContractError(RuntimeError):
    pass


class LearningBrainNotFoundError(LookupError):
    pass


class LearningBrainService:
    def __init__(
        self,
        *,
        ability_service: AbilityEstimateService | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._ability_service = ability_service or AbilityEstimateService()
        self._outbox = outbox or OutboxRepository()

    def get_or_create_profile(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> LearningBrainProfileVersion:
        generated_at = _as_utc(now or datetime.now(UTC))
        repository = LearningBrainRepository(session)
        repository.lock_user(user_id=user_id)
        ability = self._ability_service.get_current(session, user_id=user_id, now=generated_at)
        input_state = repository.input_state(user_id=user_id, today=generated_at.date())
        modules = [
            *(_xingce_module(row, generated_at) for row in repository.xingce_modules(user_id=user_id)),
            *(_essay_module(row, generated_at) for row in repository.essay_modules(user_id=user_id)),
        ]
        modules.sort(key=lambda item: (item.track, item.module_key))
        patterns = [_error_pattern(row) for row in repository.error_patterns(user_id=user_id)]

        evidence_summary = BrainEvidenceSummary(
            scoreEvidenceCount=int(input_state["score_evidence_count"]),
            xingceAttemptCount=int(input_state["xingce_attempt_count"]),
            xingceMasteryCount=int(input_state["xingce_mastery_count"]),
            essayMasteryCount=int(input_state["essay_mastery_count"]),
            taskExecutionCount=int(input_state["task_execution_count"]),
            externalAnswerCount=int(input_state["external_answer_count"]),
            latestEvidenceAt=input_state["latest_evidence_at"],
        )
        candidate_summary = BrainCandidateSummary(
            awaitingConfirmation=int(input_state["candidate_awaiting_count"]),
            accepted=int(input_state["candidate_accepted_count"]),
            rejected=int(input_state["candidate_rejected_count"]),
            expired=int(input_state["candidate_expired_count"]),
        )
        decision_summary = BrainDecisionSummary(
            activeStrategyCount=int(input_state["active_strategy_count"]),
            reviewDueCount=int(input_state["review_due_count"]),
            currentPlanVersionId=input_state["current_plan_version_id"],
            currentWeeklyPlanVersionId=input_state["current_weekly_plan_version_id"],
        )
        conflicts = _conflicts(ability, modules)
        readiness = _readiness(ability, evidence_summary, conflicts)
        source_refs = _source_refs(
            ability=ability,
            modules=modules,
            patterns=patterns,
            plan_version_id=input_state["current_plan_version_id"],
            weekly_plan_version_id=input_state["current_weekly_plan_version_id"],
            latest_external_answer_fact_id=input_state["latest_external_answer_fact_id"],
            latest_external_answer_track=input_state["latest_external_answer_track"],
            latest_external_answer_at=input_state["latest_external_answer_at"],
        )
        input_versions = _input_versions(ability, input_state, generated_at=generated_at)
        fingerprint = _fingerprint(
            {
                "ability": ability.model_dump(by_alias=True, mode="json"),
                "evidenceSummary": evidence_summary.model_dump(by_alias=True, mode="json"),
                "candidateSummary": candidate_summary.model_dump(by_alias=True, mode="json"),
                "modules": [item.model_dump(by_alias=True, mode="json") for item in modules],
                "errorPatterns": [
                    item.model_dump(by_alias=True, mode="json") for item in patterns
                ],
                "decisionSummary": decision_summary.model_dump(by_alias=True, mode="json"),
                "conflicts": conflicts,
                "inputVersions": input_versions,
                "algorithmVersion": PROFILE_ALGORITHM_VERSION,
            }
        )
        knowledge_point_weaknesses = _knowledge_point_weaknesses(session, user_id=user_id)
        current = repository.current_profile(user_id=user_id)
        if current is not None and str(current["input_fingerprint"]) == fingerprint:
            return _profile_from_row(current).model_copy(
                update={"knowledge_point_weaknesses": knowledge_point_weaknesses}
            )

        profile_version_id = str(uuid4())
        valid_until = _profile_valid_until(ability, generated_at)
        row = repository.insert_profile(
            {
                "profile_version_id": profile_version_id,
                "user_id": user_id,
                "version_no": int(current["version_no"]) + 1 if current is not None else 1,
                "readiness": readiness,
                "ability_estimate_version_id": (
                    ability.estimate.estimate_version_id if ability.estimate is not None else None
                ),
                "ability_snapshot": json_value(
                    ability.model_dump(by_alias=True, mode="json")
                ),
                "evidence_summary": json_value(
                    evidence_summary.model_dump(by_alias=True, mode="json")
                ),
                "candidate_summary": json_value(
                    candidate_summary.model_dump(by_alias=True, mode="json")
                ),
                "module_abilities": json_value(
                    [item.model_dump(by_alias=True, mode="json") for item in modules]
                ),
                "error_patterns": json_value(
                    [item.model_dump(by_alias=True, mode="json") for item in patterns]
                ),
                "decision_summary": json_value(
                    decision_summary.model_dump(by_alias=True, mode="json")
                ),
                "conflicts": json_value(conflicts),
                "source_refs": json_value(
                    [item.model_dump(by_alias=True, mode="json") for item in source_refs]
                ),
                "input_versions": json_value(input_versions),
                "input_fingerprint": fingerprint,
                "supersedes_profile_version_id": (
                    str(current["profile_version_id"]) if current is not None else None
                ),
                "generated_at": generated_at,
                "valid_until": valid_until,
            }
        )
        self._outbox.append(
            session,
            deduplication_key=f"learning-brain-profile:{profile_version_id}",
            aggregate_type="learner_brain_profile",
            aggregate_id=user_id,
            event_type="learning_brain.profile.changed",
            payload={
                "profileVersionId": profile_version_id,
                "userId": user_id,
                "readiness": readiness,
                "inputFingerprint": fingerprint,
                "sourceVersionRefs": input_versions,
            },
        )
        return _profile_from_row(row).model_copy(
            update={"knowledge_point_weaknesses": knowledge_point_weaknesses}
        )

    def get_module(
        self,
        session: Session,
        *,
        user_id: str,
        track: str,
        module_key: str,
    ) -> ModuleAbility:
        profile = self.get_or_create_profile(session, user_id=user_id)
        module = next(
            (
                item
                for item in profile.modules
                if item.track == track and item.module_key == module_key
            ),
            None,
        )
        if module is None:
            raise LearningBrainNotFoundError("module ability is not available")
        return module

    def get_coach_context(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> LearnerCoachContext:
        """编译今日与岸岸对话共用的当前学员上下文。

        profile 仍由学习大脑拥有；本方法不创造另一份学习事实，
        strategy directive 也只是当前版本的只读投影。
        """
        generated_at = _as_utc(now or datetime.now(UTC))
        profile = self.get_or_create_profile(
            session,
            user_id=user_id,
            now=generated_at,
        )
        directive = self.current_strategy_directive(
            session,
            user_id=user_id,
            now=generated_at,
        )
        return compile_learner_coach_context(
            profile,
            strategy_directive=directive,
            now=generated_at,
        )

    def get_current_coach_context(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> LearnerCoachContext:
        """编译已存在的当前画像，不触发画像重建。

        Companion 路由在进入 repository 前已建立当前画像；此方法避免一次问答
        内重复计算或依赖发布状态再次改变。
        """
        generated_at = _as_utc(now or datetime.now(UTC))
        current = LearningBrainRepository(session).current_profile(user_id=user_id)
        if current is None:
            raise LearningBrainNotFoundError("learning-brain profile is not available")
        profile = _profile_from_row(current)
        directive = self.current_strategy_directive(
            session,
            user_id=user_id,
            now=generated_at,
        )
        return compile_learner_coach_context(
            profile,
            strategy_directive=directive,
            now=generated_at,
        )

    def get_error_pattern(
        self,
        session: Session,
        *,
        user_id: str,
        pattern_id: str,
    ) -> ErrorPatternDetail:
        profile = self.get_or_create_profile(session, user_id=user_id)
        pattern = next(
            (item for item in profile.error_patterns if item.pattern_id == pattern_id),
            None,
        )
        if pattern is None:
            raise LearningBrainNotFoundError("error pattern is not available")
        if pattern.status == "counterevidence":
            supporting: list[BrainEvidenceRef] = []
            counterevidence = pattern.evidence_refs
            interpretation = "该模式曾经成立，但已有独立迁移或延时验证反证，不能继续当作当前弱项。"
        else:
            supporting = pattern.evidence_refs
            counterevidence = []
            interpretation = "该模式来自用户确认或 canonical 评阅决定，仍需后续独立验证才能退出当前弱项。"
        return ErrorPatternDetail(
            profileVersionId=profile.profile_version_id,
            pattern=pattern,
            supportingEvidence=supporting,
            counterevidence=counterevidence,
            interpretation=interpretation,
            limitations=[
                "错误模式不等于人格标签",
                "单次正确不会自动推翻已确认模式",
                "题目失效或证据撤回后会重新生成画像版本",
            ],
        )

    def create_strategy_candidate(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: StrategyCandidateCommand,
        now: datetime | None = None,
        inference_type: str = "strategy_recommendation",
        producer_type: str = "rule",
        producer_id: str = "strategy-candidate@v1",
        policy_version: str = STRATEGY_POLICY_VERSION,
        model_id: str | None = None,
        prompt_version: str | None = None,
    ) -> StrategyCandidateResult:
        created_at = _as_utc(now or datetime.now(UTC))
        if command.review_at <= created_at or command.effective_until <= created_at:
            raise LearningBrainContractError("strategy review and expiry must be in the future")
        repository = LearningBrainRepository(session)
        repository.lock_user(user_id=user_id)
        profile = self.get_or_create_profile(session, user_id=user_id, now=created_at)
        if profile.profile_version_id != command.based_on_profile_version_id:
            raise LearningBrainContractError("learning-brain profile changed; reload before deciding")
        refs, benefit = _strategy_basis(profile, command)
        value: dict[str, object] = {
            "decisionType": command.decision_type,
            "scopeType": command.scope_type,
            "scopeKey": command.scope_key,
            "rationale": command.rationale,
            "benefitEvidence": benefit,
            "effectiveUntil": command.effective_until.isoformat(),
            "reviewAt": command.review_at.isoformat(),
            "basedOnProfileVersionId": str(profile.profile_version_id),
        }
        fingerprint = _fingerprint(value)
        replay = repository.candidate_by_command(user_id=user_id, command_id=command_id)
        if replay is not None:
            if str(replay["input_fingerprint"]) != fingerprint:
                raise LearningBrainContractError(
                    "idempotency key was already used with different strategy inputs"
                )
            return StrategyCandidateResult(candidate=_candidate_from_row(replay))
        candidate_id = str(uuid4())
        candidate_version_id = str(uuid4())
        row = repository.insert_candidate(
            {
                "candidate_version_id": candidate_version_id,
                "candidate_id": candidate_id,
                "command_id": command_id,
                "user_id": user_id,
                "version_no": 1,
                "inference_type": inference_type,
                "status": "awaiting_confirmation",
                "producer_type": producer_type,
                "producer_id": producer_id,
                "value": json_value(value),
                "evidence_refs": json_value(
                    [item.model_dump(by_alias=True, mode="json") for item in refs]
                ),
                "confidence": 0.5,
                "confirmation_status": "pending",
                "model_id": model_id,
                "prompt_version": prompt_version,
                "policy_version": policy_version,
                "valid_from": created_at,
                "expires_at": command.review_at,
                "input_fingerprint": fingerprint,
                "supersedes_candidate_version_id": None,
                "created_at": created_at,
            }
        )
        self._outbox.append(
            session,
            deduplication_key=f"strategy-candidate:{candidate_version_id}",
            aggregate_type="inference_candidate",
            aggregate_id=candidate_id,
            event_type="learning_brain.strategy_candidate.created",
            payload={
                "candidateVersionId": candidate_version_id,
                "userId": user_id,
                "status": "awaiting_confirmation",
                "profileVersionId": str(profile.profile_version_id),
            },
        )
        return StrategyCandidateResult(candidate=_candidate_from_row(row))

    def decide_strategy_candidate(
        self,
        session: Session,
        *,
        user_id: str,
        candidate_id: str,
        command_id: str,
        command: StrategyDecisionCommand,
        now: datetime | None = None,
    ) -> StrategyDecisionResult:
        created_at = _as_utc(now or datetime.now(UTC))
        repository = LearningBrainRepository(session)
        repository.lock_user(user_id=user_id)
        replay_decision = repository.decision_by_command(
            user_id=user_id,
            command_id=command_id,
        )
        replay_candidate = repository.candidate_by_command(
            user_id=user_id,
            command_id=f"{command_id}:candidate",
        )
        replay_fingerprint = _fingerprint(
            {
                "candidateId": candidate_id,
                "candidateVersionId": str(command.based_on_candidate_version_id),
                "action": command.action,
            }
        )
        if replay_decision is not None or replay_candidate is not None:
            if replay_decision is None or replay_candidate is None:
                raise LearningBrainContractError("strategy decision replay is incomplete")
            if str(replay_decision["input_fingerprint"]) != replay_fingerprint:
                raise LearningBrainContractError(
                    "idempotency key was already used with a different strategy decision"
                )
            return StrategyDecisionResult(
                candidate=_candidate_from_row(replay_candidate),
                decision=_decision_from_row(replay_decision),
            )

        current = repository.current_candidate(user_id=user_id, candidate_id=candidate_id)
        if current is None:
            raise LearningBrainNotFoundError("strategy candidate is not available")
        if str(current["candidate_version_id"]) != str(
            command.based_on_candidate_version_id
        ):
            raise LearningBrainContractError("strategy candidate changed; reload before deciding")
        if current["status"] != "awaiting_confirmation":
            raise LearningBrainContractError("strategy candidate is already terminal")
        if _as_utc(current["expires_at"]) <= created_at:
            raise LearningBrainContractError("strategy candidate expired")

        terminal_status = "accepted" if command.action == "confirm" else "rejected"
        terminal_confirmation = "confirmed" if command.action == "confirm" else "rejected"
        terminal_candidate_version_id = str(uuid4())
        terminal = repository.insert_candidate(
            {
                "candidate_version_id": terminal_candidate_version_id,
                "candidate_id": str(current["candidate_id"]),
                "command_id": f"{command_id}:candidate",
                "user_id": user_id,
                "version_no": int(current["version_no"]) + 1,
                "inference_type": str(current["inference_type"]),
                "status": terminal_status,
                "producer_type": str(current["producer_type"]),
                "producer_id": str(current["producer_id"]),
                "value": json_value(dict(current["value"])),
                "evidence_refs": json_value(list(current["evidence_refs"])),
                "confidence": float(current["confidence"]),
                "confirmation_status": terminal_confirmation,
                "model_id": (
                    str(current["model_id"]) if current["model_id"] is not None else None
                ),
                "prompt_version": (
                    str(current["prompt_version"])
                    if current["prompt_version"] is not None
                    else None
                ),
                "policy_version": str(current["policy_version"]),
                "valid_from": current["valid_from"],
                "expires_at": current["expires_at"],
                "input_fingerprint": replay_fingerprint,
                "supersedes_candidate_version_id": str(current["candidate_version_id"]),
                "created_at": created_at,
            }
        )
        value = dict(current["value"])
        decision_id = str(uuid4())
        decision_version_id = str(uuid4())
        decision_status = "active" if command.action == "confirm" else "rejected"
        decision = repository.insert_strategy_decision(
            {
                "decision_version_id": decision_version_id,
                "decision_id": decision_id,
                "command_id": command_id,
                "user_id": user_id,
                "candidate_version_id": terminal_candidate_version_id,
                "decision_type": value["decisionType"],
                "status": decision_status,
                "scope_type": value["scopeType"],
                "scope_key": value["scopeKey"],
                "rationale": value["rationale"],
                "benefit_evidence": json_value(value["benefitEvidence"]),
                "evidence_refs": json_value(list(current["evidence_refs"])),
                "effective_from": created_at,
                "effective_until": datetime.fromisoformat(str(value["effectiveUntil"])),
                "review_at": datetime.fromisoformat(str(value["reviewAt"])),
                "input_fingerprint": replay_fingerprint,
                "created_at": created_at,
            }
        )
        self._outbox.append(
            session,
            deduplication_key=f"strategy-decision:{decision_version_id}",
            aggregate_type="strategy_decision",
            aggregate_id=decision_id,
            event_type="learning_brain.strategy_decision.changed",
            payload={
                "decisionVersionId": decision_version_id,
                "candidateVersionId": terminal_candidate_version_id,
                "userId": user_id,
                "status": decision_status,
                "reviewAt": value["reviewAt"],
                "effectiveUntil": value["effectiveUntil"],
            },
        )
        return StrategyDecisionResult(
            candidate=_candidate_from_row(terminal),
            decision=_decision_from_row(decision),
        )

    def current_strategy_directive(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> StrategyDirective | None:
        """读当前已生效策略 + 待确认候选（只读投影），供岸岸引用 rationale。

        纯读不写：不锁用户、不重建画像、不进入 input_fingerprint，因此
        不会因查看而扰动版本化 profile。active 优先于 pending 返回；两者
        皆无时返回 None（岸岸据此直说数据不足）。
        """
        generated_at = _as_utc(now or datetime.now(UTC))
        repository = LearningBrainRepository(session)
        directive = repository.current_strategy_directive(
            user_id=user_id,
            now=generated_at,
        )
        active = (
            _active_strategy_entry(directive["activeStrategy"])
            if directive["activeStrategy"] is not None
            else None
        )
        pending = (
            _pending_strategy_entry(directive["pendingCandidate"])
            if directive["pendingCandidate"] is not None
            else None
        )
        if active is None and pending is None:
            return None
        return StrategyDirective(active=active, pending=pending)


def _knowledge_point_weaknesses(
    session: Session,
    *,
    user_id: str,
) -> list[KnowledgePointWeakness]:
    """知识点弱项 Top 投影：每模块最多 3 个、仅样本充足者。

    只读投影，不写库、不进 profile input_fingerprint。样本门槛、
    排序和指纹均由 calibration 中立投影拥有，避免与 planning 互依赖。
    样本不足（lowSample）与「未分类」标签不进榜单。
    """
    estimates, _fingerprint_value = project_knowledge_point_weaknesses(
        session,
        user_id=user_id,
    )
    return [
            KnowledgePointWeakness(
                module_name=item.module_name,
                tag_value=item.tag_value,
                accuracy=float(item.accuracy or 0.0),
                sample_size=item.sample_size,
            )
        for item in estimates
    ]


def compile_learner_coach_context(
    profile: LearningBrainProfileVersion,
    *,
    strategy_directive: StrategyDirective | None = None,
    now: datetime | None = None,
) -> LearnerCoachContext:
    """从一个已版本化 profile 纯编译岸岸上下文。"""
    generated_at = _as_utc(now or datetime.now(UTC))
    stable_mastery_count = sum(item.stable_mastery_count for item in profile.modules)
    recognition = _coach_recognition(
        profile.evidence_summary,
        stable_mastery_count=stable_mastery_count,
    )
    return LearnerCoachContext(
        profileVersionId=profile.profile_version_id,
        profile=profile,
        recognition=recognition,
        freshness=_coach_context_freshness(profile, now=generated_at),
        strategyDirective=strategy_directive or StrategyDirective(),
        generatedAt=generated_at,
        validUntil=profile.valid_until,
    )


def _coach_recognition(
    evidence: BrainEvidenceSummary,
    *,
    stable_mastery_count: int,
) -> CoachRecognition:
    evidence_count = evidence.total_count
    if stable_mastery_count > 0:
        return CoachRecognition(
            level="understands",
            label="比较了解你",
            observation=(
                f"已有 {stable_mastery_count} 个稳定掌握记录经过练习与复测确认。"
            ),
            evidenceCount=evidence_count,
            stableMasteryCount=stable_mastery_count,
        )
    mastery_count = evidence.xingce_mastery_count + evidence.essay_mastery_count
    if evidence.task_execution_count > 0 or mastery_count > 0:
        return CoachRecognition(
            level="verifying",
            label="正在验证",
            observation=(
                f"岸岸已经收到 {evidence.task_execution_count} 条任务执行记录和 "
                f"{mastery_count} 条掌握证据，当前判断还在复测。"
            ),
            evidenceCount=evidence_count,
            stableMasteryCount=0,
        )
    if evidence_count > 0:
        sources = [
            f"{evidence.score_evidence_count} 份成绩"
            if evidence.score_evidence_count > 0
            else "",
            f"{evidence.xingce_attempt_count} 次真实作答"
            if evidence.xingce_attempt_count > 0
            else "",
            f"{evidence.external_answer_count} 条外部作答"
            if evidence.external_answer_count > 0
            else "",
        ]
        source_copy = "、".join(item for item in sources if item) or f"{evidence_count} 条学习证据"
        return CoachRecognition(
            level="initial_hypothesis",
            label="形成初步判断",
            observation=f"岸岸目前看过{source_copy}；这些只够形成假设，还不能当定论。",
            evidenceCount=evidence_count,
            stableMasteryCount=0,
        )
    return CoachRecognition(
        level="just_met",
        label="刚认识你",
        observation="岸岸还没看过你的真实作答，现在只用目标和可用时间决定先从哪里开始。",
        evidenceCount=0,
        stableMasteryCount=0,
    )


def _coach_context_freshness(
    profile: LearningBrainProfileVersion,
    *,
    now: datetime,
) -> str:
    if profile.readiness == "conflicted":
        return "conflicted"
    if profile.readiness == "unavailable":
        return "unavailable"
    if profile.readiness == "stale" or _as_utc(profile.valid_until) <= now:
        return "stale"
    return "current"


def _xingce_module(row: RowMapping, now: datetime) -> ModuleAbility:
    attempt_count = int(row["attempt_count"])
    mastery_count = int(row["mastery_count"])
    external_evidence_count = int(row["external_answer_count"])
    evidence_count = attempt_count + mastery_count + external_evidence_count
    latest = row["latest_evidence_at"]
    stale = latest is not None and _as_utc(latest) + timedelta(days=EVIDENCE_VALIDITY_DAYS) <= now
    refs: list[BrainEvidenceRef] = []
    if row["latest_assessment_id"]:
        refs.append(
            BrainEvidenceRef(
                owner="xingce_learning",
                entityType="xingce_assessment",
                entityId=str(row["latest_assessment_id"]),
                versionId=str(row["latest_assessment_id"]),
                occurredAt=latest,
                role="module_performance",
            )
        )
    if row["latest_mastery_version_id"]:
        refs.append(
            BrainEvidenceRef(
                owner="xingce_mastery",
                entityType="xingce_mastery_state_version",
                entityId=str(row["latest_mastery_version_id"]),
                versionId=str(row["latest_mastery_version_id"]),
                occurredAt=latest,
                role="mastery_state",
            )
        )
    if row["latest_external_fact_id"]:
        refs.append(
            BrainEvidenceRef(
                owner="answer_ingestion",
                entityType="external_answer_fact",
                entityId=str(row["latest_external_fact_id"]),
                versionId=str(row["latest_external_fact_id"]),
                occurredAt=latest,
                role="user_confirmed_external_answer",
            )
        )
    readiness = (
        "unavailable"
        if evidence_count == 0
        else "stale"
        if stale
        else "reliable"
        if int(row["stable_mastery_count"]) > 0
        else "developing"
    )
    return ModuleAbility(
        track="xingce",
        moduleKey=str(row["module_name"]),
        displayName=str(row["module_name"]),
        readiness=readiness,
        scoreBand=None,
        evidenceCount=evidence_count,
        externalEvidenceCount=external_evidence_count,
        correctRate=(
            round(int(row["correct_count"]) / attempt_count, 4)
            if attempt_count > 0
            else None
        ),
        averageSeconds=(
            round(float(row["average_seconds"]), 2)
            if row["average_seconds"] is not None
            else None
        ),
        stableMasteryCount=int(row["stable_mastery_count"]),
        dueCount=int(row["due_count"]),
        confidence="medium" if int(row["stable_mastery_count"]) > 0 else "low",
        freshness="stale" if stale else "current" if latest is not None else "unavailable",
        latestEvidenceAt=latest,
        conflictCodes=[],
        evidenceRefs=refs,
    )


def _essay_module(row: RowMapping, now: datetime) -> ModuleAbility:
    evidence_count = int(row["mastery_count"])
    latest = row["latest_evidence_at"]
    stale = latest is not None and _as_utc(latest) + timedelta(days=EVIDENCE_VALIDITY_DAYS) <= now
    module_key = str(row["module_key"])
    display_names = {
        "point_finding": "找点与覆盖",
        "abstraction": "概括与归纳",
        "structure": "结构组织",
        "expression": "表达准确性",
        "format": "格式规范",
        "unsupported_claim": "材料依据",
        "unclassified": "待分类能力",
    }
    refs = [
        BrainEvidenceRef(
            owner="essay_mastery",
            entityType="essay_mastery_state_version",
            entityId=str(row["latest_mastery_version_id"]),
            versionId=str(row["latest_mastery_version_id"]),
            occurredAt=latest,
            role="mastery_state",
        )
    ]
    return ModuleAbility(
        track="essay",
        moduleKey=module_key,
        displayName=display_names.get(module_key, module_key),
        readiness=(
            "stale"
            if stale
            else "reliable"
            if int(row["stable_mastery_count"]) > 0
            else "developing"
        ),
        scoreBand=None,
        evidenceCount=evidence_count,
        externalEvidenceCount=0,
        correctRate=None,
        averageSeconds=None,
        stableMasteryCount=int(row["stable_mastery_count"]),
        dueCount=int(row["due_count"]),
        confidence="medium" if int(row["stable_mastery_count"]) > 0 else "low",
        freshness="stale" if stale else "current",
        latestEvidenceAt=latest,
        conflictCodes=[],
        evidenceRefs=refs,
    )


# 逐题候选行不携带会话级 cause_text，展示名回落到已发布的错因分类描述。
_XINGCE_CAUSE_DISPLAY_NAMES = dict(XINGCE_CAUSE_TAXONOMY)


def _error_pattern(row: RowMapping) -> ErrorPatternSummary:
    identity = {
        "track": str(row["track"]),
        "moduleKey": str(row["module_key"]),
        "causeCode": str(row["cause_code"]),
    }
    pattern_id = _fingerprint(identity)
    cause_code = str(row["cause_code"])
    display_name = (
        row["display_name"]
        or _XINGCE_CAUSE_DISPLAY_NAMES.get(cause_code)
        or cause_code
    )
    counterevidence = int(row["counterevidence_count"])
    occurrence_count = int(row["occurrence_count"])
    fully_countered = counterevidence > 0 and counterevidence == occurrence_count
    latest_version_id = (
        str(row["latest_counterevidence_version_id"])
        if fully_countered and row["latest_counterevidence_version_id"] is not None
        else str(row["latest_version_id"])
    )
    latest_observed_at = (
        row["latest_counterevidence_at"]
        if fully_countered and row["latest_counterevidence_at"] is not None
        else row["latest_observed_at"]
    )
    ref = BrainEvidenceRef(
        owner=(
            "xingce_mastery"
            if row["track"] == "xingce" and fully_countered
            else "xingce_learning"
            if row["track"] == "xingce"
            else "essay_mastery"
        ),
        entityType=(
            "xingce_mastery_state_version"
            if row["track"] == "xingce" and fully_countered
            else "xingce_confirmed_cause_version"
            if row["track"] == "xingce"
            else "essay_mastery_state_version"
        ),
        entityId=latest_version_id,
        versionId=latest_version_id,
        occurredAt=latest_observed_at,
        role="counterevidence" if fully_countered else "confirmed_pattern",
    )
    return ErrorPatternSummary(
        patternId=pattern_id,
        track=str(row["track"]),
        moduleKey=str(row["module_key"]),
        causeCode=cause_code,
        displayName=str(display_name),
        status="counterevidence" if fully_countered else "confirmed",
        occurrenceCount=occurrence_count,
        counterevidenceCount=counterevidence,
        confidence="medium",
        latestObservedAt=row["latest_observed_at"],
        evidenceRefs=[ref],
    )


def _strategy_basis(
    profile: LearningBrainProfileVersion,
    command: StrategyCandidateCommand,
) -> tuple[list[BrainEvidenceRef], dict[str, object]]:
    if command.decision_type == "baseline_guidance":
        # 引导型：新用户/脱敏用户没有模块证据，不要求 canonical module。
        # refs 用模块证据（若有）或能力估计引用（无模块时兜底），保证
        # evidence_refs 非空满足数据库约束；benefit 表达「先建立基线」。
        refs = _dedupe_refs(
            [
                ref
                for module in profile.modules
                for ref in module.evidence_refs
            ]
        )
        ability = profile.ability
        ability_estimate = ability.estimate if ability.estimate is not None else None
        if not refs and ability_estimate is not None:
            refs = [
                BrainEvidenceRef(
                    owner="ability_estimation",
                    entityType="ability_estimate_version",
                    entityId=ability_estimate.estimate_version_id,
                    versionId=ability_estimate.estimate_version_id,
                    occurredAt=ability_estimate.generated_at,
                    role="baseline_guidance_basis",
                )
            ]
        if not refs:
            # 完全无证据的新用户：用 profile 版本自身作引导引用，满足
            # evidence_refs 非空约束（数据库要求候选至少一条引用）。
            refs = [
                BrainEvidenceRef(
                    owner="learning_brain",
                    entityType="learner_brain_profile_version",
                    entityId=str(profile.profile_version_id),
                    versionId=str(profile.profile_version_id),
                    role="baseline_guidance_basis",
                )
            ]
        benefit: dict[str, object] = {
            "profileVersionId": str(profile.profile_version_id),
            "moduleCount": len(profile.modules),
            "evidenceCount": sum(item.evidence_count for item in profile.modules),
            "totalRecommended": (
                ability_estimate.total.recommended
                if ability_estimate is not None and ability_estimate.total is not None
                else None
            ),
            "claim": "证据不足，先建立能力基线后再给具体侧重。",
        }
        return refs, benefit
    if command.scope_type == "question_type":
        raise LearningBrainContractError(
            "question-type strategy requires a published question-type projection"
        )
    if command.scope_type == "track":
        if command.scope_key not in {"xingce", "essay"}:
            raise LearningBrainContractError("track strategy scope is invalid")
        if command.decision_type in {"exam_skip", "strategic_abandon"}:
            raise LearningBrainContractError(
                "exam skip and strategic abandon require a bounded module scope"
            )
        modules = [item for item in profile.modules if item.track == command.scope_key]
    else:
        modules = [item for item in profile.modules if item.module_key == command.scope_key]
    if not modules:
        raise LearningBrainContractError("strategy scope has no canonical learning evidence")
    refs = _dedupe_refs([ref for module in modules for ref in module.evidence_refs])
    if not refs:
        raise LearningBrainContractError("strategy scope has no versioned evidence reference")
    evidence_count = sum(item.evidence_count for item in modules)
    if command.decision_type == "strategic_abandon":
        if evidence_count < 5:
            raise LearningBrainContractError(
                "strategic abandon requires at least five canonical evidence records"
            )
        if any(item.freshness != "current" or item.conflict_codes for item in modules):
            raise LearningBrainContractError(
                "strategic abandon cannot use stale or conflicted module evidence"
            )
    benefit: dict[str, object] = {
        "profileVersionId": str(profile.profile_version_id),
        "moduleCount": len(modules),
        "evidenceCount": evidence_count,
        "dueCount": sum(item.due_count for item in modules),
        "stableMasteryCount": sum(item.stable_mastery_count for item in modules),
        "correctRates": {
            item.module_key: item.correct_rate
            for item in modules
            if item.correct_rate is not None
        },
        "averageSeconds": {
            item.module_key: item.average_seconds
            for item in modules
            if item.average_seconds is not None
        },
        "claim": "仅用于生成待确认的时间取舍候选，不改变能力证据。",
    }
    return refs, benefit


def _evidence_summary_or_empty(row: RowMapping) -> BrainEvidenceSummary:
    """容错脱敏/缺失的 evidence_summary。

    用户行使数据删除权后 evidence_summary 被擦为 ``{"erased": true}``；此时
    学习证据已清零，构造全 0 空摘要，让大脑把它当「无证据的新用户」处理，
    而不是 pydantic 校验失败。
    """
    raw = row["evidence_summary"]
    if not isinstance(raw, dict) or raw.get("erased") is True:
        return BrainEvidenceSummary(
            scoreEvidenceCount=0,
            xingceAttemptCount=0,
            xingceMasteryCount=0,
            essayMasteryCount=0,
            taskExecutionCount=0,
            externalAnswerCount=0,
        )
    return BrainEvidenceSummary.model_validate(raw)


def _profile_from_row(row: RowMapping) -> LearningBrainProfileVersion:
    return LearningBrainProfileVersion(
        profileVersionId=UUID(str(row["profile_version_id"])),
        versionNo=int(row["version_no"]),
        readiness=str(row["readiness"]),
        ability=AbilityEstimateCurrent.model_validate(row["ability_snapshot"]),
        evidenceSummary=_evidence_summary_or_empty(row),
        candidateSummary=BrainCandidateSummary.model_validate(row["candidate_summary"]),
        modules=[ModuleAbility.model_validate(item) for item in row["module_abilities"]],
        errorPatterns=[
            ErrorPatternSummary.model_validate(item) for item in row["error_patterns"]
        ],
        decisionSummary=BrainDecisionSummary.model_validate(row["decision_summary"]),
        conflicts=list(row["conflicts"]),
        sourceRefs=[BrainEvidenceRef.model_validate(item) for item in row["source_refs"]],
        inputVersions=dict(row["input_versions"]),
        inputFingerprint=str(row["input_fingerprint"]),
        supersedesProfileVersionId=(
            UUID(str(row["supersedes_profile_version_id"]))
            if row["supersedes_profile_version_id"] is not None
            else None
        ),
        generatedAt=row["generated_at"],
        validUntil=row["valid_until"],
    )


def _candidate_from_row(row: RowMapping) -> InferenceCandidateVersion:
    return InferenceCandidateVersion(
        candidateVersionId=UUID(str(row["candidate_version_id"])),
        candidateId=UUID(str(row["candidate_id"])),
        versionNo=int(row["version_no"]),
        inferenceType=str(row["inference_type"]),
        status=str(row["status"]),
        producerType=str(row["producer_type"]),
        producerId=str(row["producer_id"]),
        value=dict(row["value"]),
        evidenceRefs=[BrainEvidenceRef.model_validate(item) for item in row["evidence_refs"]],
        confidence=float(row["confidence"]),
        requiresUserConfirmation=bool(row["requires_user_confirmation"]),
        confirmationStatus=str(row["confirmation_status"]),
        validationStatus=str(row["validation_status"]),
        policyVersion=str(row["policy_version"]),
        validFrom=row["valid_from"],
        expiresAt=row["expires_at"],
        inputFingerprint=str(row["input_fingerprint"]),
        supersedesCandidateVersionId=(
            UUID(str(row["supersedes_candidate_version_id"]))
            if row["supersedes_candidate_version_id"] is not None
            else None
        ),
        createdAt=row["created_at"],
    )


def _decision_from_row(row: RowMapping) -> StrategyDecisionVersion:
    return StrategyDecisionVersion(
        decisionVersionId=UUID(str(row["decision_version_id"])),
        decisionId=UUID(str(row["decision_id"])),
        versionNo=int(row["version_no"]),
        candidateVersionId=UUID(str(row["candidate_version_id"])),
        decisionType=str(row["decision_type"]),
        status=str(row["status"]),
        scopeType=str(row["scope_type"]),
        scopeKey=str(row["scope_key"]),
        rationale=str(row["rationale"]),
        benefitEvidence=dict(row["benefit_evidence"]),
        evidenceRefs=[BrainEvidenceRef.model_validate(item) for item in row["evidence_refs"]],
        effectiveFrom=row["effective_from"],
        effectiveUntil=row["effective_until"],
        reviewAt=row["review_at"],
        inputFingerprint=str(row["input_fingerprint"]),
        createdAt=row["created_at"],
    )


def _active_strategy_entry(row: RowMapping) -> StrategyDirectiveEntry:
    return StrategyDirectiveEntry(
        status="active",
        decisionType=str(row["decision_type"]),
        scopeType=str(row["scope_type"]),
        scopeKey=str(row["scope_key"]),
        rationale=str(row["rationale"]),
        effectiveUntil=_as_utc(row["effective_until"]),
        generatedAt=_as_utc(row["created_at"]),
    )


def _pending_strategy_entry(row: RowMapping) -> StrategyDirectiveEntry | None:
    value = dict(row["value"])
    required = ("decisionType", "scopeType", "scopeKey", "rationale")
    if not all(isinstance(value.get(key), str) and value.get(key) for key in required):
        return None
    return StrategyDirectiveEntry(
        status="awaiting_confirmation",
        decisionType=str(value["decisionType"]),
        scopeType=str(value["scopeType"]),
        scopeKey=str(value["scopeKey"]),
        rationale=str(value["rationale"]),
        expiresAt=_as_utc(row["expires_at"]),
        generatedAt=_as_utc(row["created_at"]),
    )


def _readiness(
    ability: AbilityEstimateCurrent,
    evidence: BrainEvidenceSummary,
    conflicts: list[str],
) -> str:
    if conflicts:
        return "conflicted"
    if ability.readiness == "stale":
        return "stale"
    if evidence.total_count == 0:
        return "unavailable"
    if ability.readiness != "complete":
        return "partial"
    return "ready"


def _conflicts(
    ability: AbilityEstimateCurrent,
    modules: list[ModuleAbility],
) -> list[str]:
    values: list[str] = []
    if ability.estimate is not None:
        values.extend(
            f"ability:{component}"
            for component in ability.estimate.evidence_summary.conflict_components
        )
    values.extend(
        f"module:{module.track}:{module.module_key}:{code}"
        for module in modules
        for code in module.conflict_codes
    )
    return list(dict.fromkeys(values))


def _source_refs(
    *,
    ability: AbilityEstimateCurrent,
    modules: list[ModuleAbility],
    patterns: list[ErrorPatternSummary],
    plan_version_id: str | None,
    weekly_plan_version_id: str | None,
    latest_external_answer_fact_id: str | None,
    latest_external_answer_track: str | None,
    latest_external_answer_at: datetime | None,
) -> list[BrainEvidenceRef]:
    refs = [ref for module in modules for ref in module.evidence_refs]
    refs.extend(ref for pattern in patterns for ref in pattern.evidence_refs)
    if ability.estimate is not None:
        refs.append(
            BrainEvidenceRef(
                owner="ability_estimation",
                entityType="ability_estimate_version",
                entityId=ability.estimate.estimate_version_id,
                versionId=ability.estimate.estimate_version_id,
                occurredAt=ability.estimate.generated_at,
                role="overall_score_interval",
            )
        )
    if plan_version_id:
        refs.append(
            BrainEvidenceRef(
                owner="planning",
                entityType="daily_plan_version",
                entityId=plan_version_id,
                versionId=plan_version_id,
                role="current_daily_decision",
            )
        )
    if weekly_plan_version_id:
        refs.append(
            BrainEvidenceRef(
                owner="planning",
                entityType="weekly_plan_version",
                entityId=weekly_plan_version_id,
                versionId=weekly_plan_version_id,
                role="current_weekly_decision",
            )
        )
    if latest_external_answer_fact_id:
        refs.append(
            BrainEvidenceRef(
                owner="answer_ingestion",
                entityType="external_answer_fact",
                entityId=latest_external_answer_fact_id,
                versionId=latest_external_answer_fact_id,
                occurredAt=latest_external_answer_at,
                role=(
                    f"user_confirmed_external_answer:{latest_external_answer_track}"
                    if latest_external_answer_track
                    else "user_confirmed_external_answer"
                ),
            )
        )
    return _dedupe_refs(refs)


def _dedupe_refs(refs: list[BrainEvidenceRef]) -> list[BrainEvidenceRef]:
    result: list[BrainEvidenceRef] = []
    seen: set[tuple[str, str, str, str | None]] = set()
    for ref in refs:
        key = (ref.owner, ref.entity_type, ref.entity_id, ref.version_id)
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


def _input_versions(
    ability: AbilityEstimateCurrent,
    input_state: RowMapping,
    *,
    generated_at: datetime,
) -> dict[str, str]:
    result = {
        "scoreEvidenceCount": str(input_state["score_evidence_count"]),
        "xingceAttemptCount": str(input_state["xingce_attempt_count"]),
        "xingceMasteryCount": str(input_state["xingce_mastery_count"]),
        "essayMasteryCount": str(input_state["essay_mastery_count"]),
        "taskExecutionCount": str(input_state["task_execution_count"]),
        "externalAnswerCount": str(input_state["external_answer_count"]),
        "candidateAcceptedCount": str(input_state["candidate_accepted_count"]),
        "activeStrategyCount": str(input_state["active_strategy_count"]),
        "candidateVersions": str(input_state["candidate_version_fingerprint"]),
        "strategyDecisionVersions": str(input_state["strategy_decision_fingerprint"]),
        "freshnessDate": generated_at.date().isoformat(),
    }
    if ability.estimate is not None:
        result["abilityEstimateVersionId"] = ability.estimate.estimate_version_id
    if input_state["current_plan_version_id"]:
        result["dailyPlanVersionId"] = str(input_state["current_plan_version_id"])
    if input_state["current_weekly_plan_version_id"]:
        result["weeklyPlanVersionId"] = str(input_state["current_weekly_plan_version_id"])
    if input_state["latest_evidence_at"]:
        result["latestEvidenceAt"] = _as_utc(
            input_state["latest_evidence_at"]
        ).isoformat()
    if input_state["latest_external_answer_fact_id"]:
        result["latestExternalAnswerFactId"] = str(
            input_state["latest_external_answer_fact_id"]
        )
    return result


def _profile_valid_until(ability: AbilityEstimateCurrent, now: datetime) -> datetime:
    next_refresh = now + timedelta(days=1)
    if ability.estimate is None or ability.estimate.valid_until <= now:
        return next_refresh
    return min(next_refresh, ability.estimate.valid_until)


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LearningBrainContractError("learning-brain timestamps must include a timezone")
    return value.astimezone(UTC)
