"""中控大脑决策 agent（阶段 2-A）。

读全部学习状态（learning_brain profile + 目标分 + 已确认错因 + 能力估计 +
周节奏），用 LLM + strict JSON 推断「本周策略指令」，然后通过既有的
``create_strategy_candidate`` 落库为 ``producer_type=agent`` 的策略候选，
走用户确认流程（候选生效前 planning 不读取）。

设计落点（已和产品负责人确认）：
- 策略粒度：周级（每周侧重，不频繁变）
- 主动/被动：先被动（``suggest_strategy`` 由「错因确认后 / 每天首次进 app」
  或未来岸岸对话触发），后续再加主动
- 触发幂等：``maybe_suggest_strategy`` 用按天幂等的 suggestion 记录保证
  同一天不重复重算/重复写候选
- 用户裁决权最高：agent 只产出候选，确认才生效，用户也能 reject

尊重现有模式：prompt 输入参照 cause_agent 的冻结 evidence 方式（脱敏、
只读投影），输出接 ``create_strategy_candidate``（复用既有权重/证据校验）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domains.learning_brain.agent_contracts import (
    LearningBrainAgentEvidence,
    LearningBrainStrategyInstruction,
)
from app.domains.learning_brain.provider import (
    LearningBrainProvider,
    LearningBrainProviderConfigurationError,
    LearningBrainProviderError,
)
from app.domains.learning_brain.repository import LearningBrainRepository
from app.domains.learning_brain.schemas import (
    InferenceCandidateVersion,
    LearningBrainProfileVersion,
    StrategyCandidateCommand,
)
from app.domains.learning_brain.service import LearningBrainService
from app.domains.planning.repository import PlanningRepository
from app.domains.planning.service import goal_gap
from app.platform.outbox import OutboxRepository


LEARNING_BRAIN_AGENT_DIRECTIVE_INFERENCE_TYPE = "strategy_directive"
LEARNING_BRAIN_AGENT_PRODUCER_ID = "learning-brain-agent@v1"
LEARNING_BRAIN_AGENT_POLICY_VERSION = "weekly-strategy-directive@v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")

# 中控大脑的提示词版本 / 契约版本在 provider 上声明；这里只声明工作流层
# 的输入版本（evidence 投影版本），镜像 cause_agent 的 prompt/schema 分离。
AgentTriggerKind = Literal["daily_first_entry", "cause_confirmed", "on_demand"]


class LearningBrainAgentError(RuntimeError):
    pass


class LearningBrainAgentInsufficientEvidence(LearningBrainAgentError):
    pass


@dataclass(frozen=True)
class StrategySuggestionResult:
    candidate: InferenceCandidateVersion | None
    suggestion_id: str | None
    trigger_kind: str
    skipped_reason: str | None = None


class LearningBrainAgentService:
    """中控大脑决策 agent：读状态 → 推断策略指令 → 落库为待确认候选。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        brain: LearningBrainService | None = None,
        provider: LearningBrainProvider | None = None,
        planning_repository: PlanningRepository | None = None,
        repository: LearningBrainRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._brain = brain or LearningBrainService()
        self._provider = provider or LearningBrainProvider(self._settings)
        self._planning = planning_repository or PlanningRepository()
        self._repository = repository or LearningBrainRepository
        self._outbox = outbox or OutboxRepository()

    def suggest_strategy(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        trigger_kind: AgentTriggerKind,
        now: datetime | None = None,
    ) -> StrategySuggestionResult:
        """读取状态 → 调 LLM 推断策略指令 → 落库待确认候选。

        总是产生候选（假定证据充分），不做按天幂等；按天幂等由
        :meth:`maybe_suggest_strategy` 负责。候选进 awaiting_confirmation，
        用户确认后 planning 才能读到。
        """
        generated_at = _as_utc(now or datetime.now(UTC))
        brain_repository = self._repository(session)
        brain_repository.lock_user(user_id=user_id)
        profile = self._brain.get_or_create_profile(
            session, user_id=user_id, now=generated_at
        )
        evidence = self._build_evidence(
            session,
            profile=profile,
            user_id=user_id,
            now=generated_at,
        )
        directive = self._infer_directive(
            evidence=evidence,
            idempotency_key=f"learning-brain-agent:{command_id}",
        )
        week_start = generated_at.astimezone(SHANGHAI).date() - timedelta(
            days=generated_at.astimezone(SHANGHAI).weekday()
        )
        local_date = generated_at.astimezone(SHANGHAI).date()
        result = self._brain.create_strategy_candidate(
            session,
            user_id=user_id,
            command_id=command_id,
            command=self._candidate_command(directive, profile, week_start),
            now=generated_at,
            inference_type=LEARNING_BRAIN_AGENT_DIRECTIVE_INFERENCE_TYPE,
            producer_type="agent",
            producer_id=LEARNING_BRAIN_AGENT_PRODUCER_ID,
            policy_version=LEARNING_BRAIN_AGENT_POLICY_VERSION,
            model_id=self._provider.model,
            prompt_version=self._provider.prompt_version,
        )
        suggestion_id = self._record_suggestion(
            session,
            user_id=user_id,
            command_id=f"{command_id}:suggestion",
            local_date=local_date,
            trigger_kind=trigger_kind,
            profile_version_id=str(profile.profile_version_id),
            candidate_version_id=str(result.candidate.candidate_version_id),
            now=generated_at,
        )
        self._outbox.append(
            session,
            deduplication_key=f"learning-brain-agent-suggestion:{suggestion_id}",
            aggregate_type="learning_brain_agent",
            aggregate_id=user_id,
            event_type="learning_brain.agent_suggestion.created",
            payload={
                "suggestionId": suggestion_id,
                "userId": user_id,
                "localDate": local_date.isoformat(),
                "triggerKind": trigger_kind,
                "candidateVersionId": str(result.candidate.candidate_version_id),
                "profileVersionId": str(profile.profile_version_id),
            },
        )
        return StrategySuggestionResult(
            candidate=result.candidate,
            suggestion_id=suggestion_id,
            trigger_kind=trigger_kind,
        )

    def maybe_suggest_strategy(
        self,
        session: Session,
        *,
        user_id: str,
        trigger_kind: AgentTriggerKind,
        now: datetime | None = None,
    ) -> StrategySuggestionResult:
        """按天幂等的策略建议入口（每天首次进 app / 错因确认后触发）。

        同一天已建议过（不管候选是否确认）则直接返回，不重复调 LLM、
        不重复写候选。跨天之后再次触发才产生新候选。
        """
        generated_at = _as_utc(now or datetime.now(UTC))
        local_date = generated_at.astimezone(SHANGHAI).date()
        repository = self._repository(session)
        repository.lock_user(user_id=user_id)
        existing = repository.agent_suggestion_for_date(
            user_id=user_id,
            local_date=local_date,
        )
        if existing is not None:
            candidate = self._load_candidate(repository, existing)
            return StrategySuggestionResult(
                candidate=candidate,
                suggestion_id=str(existing["suggestion_id"]),
                trigger_kind=str(existing["trigger_kind"]),
                skipped_reason="already_suggested_today",
            )
        command_id = _suggestion_command_id(user_id, local_date)
        return self.suggest_strategy(
            session,
            user_id=user_id,
            command_id=command_id,
            trigger_kind=trigger_kind,
            now=generated_at,
        )

    def _infer_directive(
        self,
        *,
        evidence: LearningBrainAgentEvidence,
        idempotency_key: str,
    ) -> LearningBrainStrategyInstruction:
        try:
            result = self._provider.generate(
                evidence=evidence,
                idempotency_key=idempotency_key,
            )
        except (LearningBrainProviderConfigurationError, LearningBrainProviderError) as exc:
            raise LearningBrainAgentError(f"learning-brain agent provider failed: {exc}") from exc
        return result.output

    def _build_evidence(
        self,
        session: Session,
        *,
        profile: LearningBrainProfileVersion,
        user_id: str,
        now: datetime,
    ) -> LearningBrainAgentEvidence:
        repository = self._repository(session)
        today = now.astimezone(SHANGHAI).date()
        week_start = today - timedelta(days=today.weekday())
        ability = profile.ability.model_dump(by_alias=True, mode="json")
        modules = [item.model_dump(by_alias=True, mode="json") for item in profile.modules]
        error_patterns = [
            item.model_dump(by_alias=True, mode="json") for item in profile.error_patterns
        ]
        knowledge_point_weaknesses = [
            item.model_dump(by_alias=True, mode="json")
            for item in (profile.knowledge_point_weaknesses or [])
        ]
        goal = self._planning.goal_and_ability(session, user_id=user_id)
        gap = goal_gap(goal, now).model_dump(by_alias=True, mode="json")
        weekly_rhythm = repository.weekly_rhythm(user_id=user_id, today=today)
        week = {
            "weekStart": week_start.isoformat(),
            "weekEnd": (week_start + timedelta(days=6)).isoformat(),
            "localDate": today.isoformat(),
        }
        evidence = LearningBrainAgentEvidence(
            ability=ability,
            modules=modules,
            error_patterns=error_patterns,
            knowledge_point_weaknesses=knowledge_point_weaknesses,
            goal_gap=gap,
            weekly_rhythm=weekly_rhythm,
            week=week,
        )
        if not evidence.module_keys:
            raise LearningBrainAgentInsufficientEvidence(
                "学习画像没有可引用的模块证据，无法推断本周策略；先建立基线。"
            )
        return evidence

    def _candidate_command(
        self,
        directive: LearningBrainStrategyInstruction,
        profile: LearningBrainProfileVersion,
        week_start: date,
    ) -> StrategyCandidateCommand:
        # 周级策略：effective_until 覆盖本周（到下周一 0 点），review_at 对齐
        # 窗口末尾，保证候选有整个剩余周供用户确认，且到期自然失效。
        effective_until = datetime.combine(
            week_start + timedelta(days=7), datetime.min.time(), SHANGHAI
        ).astimezone(UTC)
        review_at = effective_until
        scope_type: str
        scope_key: str
        action = directive.action
        if action is None:
            raise LearningBrainAgentError(
                "learning-brain agent produced a directive without an actionable decision"
            )
        scope_type = action.scope_type
        scope_key = action.scope_key
        if scope_type == "track":
            if scope_key not in {"xingce", "essay"}:
                raise LearningBrainAgentError("learning-brain agent produced an invalid track scope")
            modules = [item for item in profile.modules if item.track == scope_key]
        else:
            modules = [item for item in profile.modules if item.module_key == scope_key]
        if not modules:
            raise LearningBrainAgentError(
                "learning-brain agent directive scope has no canonical learning evidence"
            )
        return StrategyCandidateCommand(
            based_on_profile_version_id=profile.profile_version_id,
            decision_type=action.decision_type,
            scope_type=scope_type,  # type: ignore[arg-type]
            scope_key=scope_key,
            rationale=directive.rationale,
            effective_until=effective_until,
            review_at=review_at,
        )

    def _record_suggestion(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        local_date: date,
        trigger_kind: AgentTriggerKind,
        profile_version_id: str,
        candidate_version_id: str,
        now: datetime,
    ) -> str:
        suggestion_id = str(uuid4())
        repository = self._repository(session)
        repository.insert_agent_suggestion(
            {
                "suggestion_id": suggestion_id,
                "command_id": command_id,
                "user_id": user_id,
                "local_date": local_date,
                "trigger_kind": trigger_kind,
                "profile_version_id": profile_version_id,
                "candidate_version_id": candidate_version_id,
                "input_fingerprint": _fingerprint(
                    {
                        "localDate": local_date.isoformat(),
                        "profileVersionId": profile_version_id,
                        "candidateVersionId": candidate_version_id,
                    }
                ),
                "created_at": now,
            }
        )
        return suggestion_id

    def _load_candidate(
        self,
        repository: LearningBrainRepository,
        suggestion: object,
    ) -> InferenceCandidateVersion | None:
        candidate_version_id = suggestion["candidate_version_id"]
        if candidate_version_id is None:
            return None
        row = repository.candidate_by_version(
            user_id=str(suggestion["user_id"]),
            candidate_version_id=str(candidate_version_id),
        )
        if row is None:
            return None
        from app.domains.learning_brain.service import _candidate_from_row

        return _candidate_from_row(row)


def _suggestion_command_id(user_id: str, local_date: date) -> str:
    return f"learning-brain-agent:{user_id}:{local_date.isoformat()}"


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
        raise ValueError("learning-brain agent timestamps must include a timezone")
    return value.astimezone(UTC)
