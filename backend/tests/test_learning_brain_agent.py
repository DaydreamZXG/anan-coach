from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.learning_brain.agent import LearningBrainAgentService
from app.domains.learning_brain.agent_contracts import (
    LearningBrainStrategyAction,
    LearningBrainStrategyEnvelope,
    LearningBrainStrategyInstruction,
)
from app.domains.learning_brain.provider import (
    LearningBrainProviderConfigurationError,
    LearningBrainProviderResult,
)
from tests.position_release_helpers import seed_active_position_release
from tests.test_xingce_cause_agent_workflow import SequenceProvider, _reflect, _run_one
from tests.test_xingce_planning_closure import _submitted_attempt
from tests.test_xingce_question_cause_candidates import _session_owner


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)  # Monday


class _DirectiveProvider:
    """Fake learning-brain provider returning one fixed strategy directive."""

    provider_name = "learning_brain_agent@local-unreleased"
    provider_release_id = "local-unreleased"
    model = "gemini-3.5-flash"
    prompt_version = "learning-brain-strategy@v1"
    schema_version = "weekly-strategy-directive@v1"

    def __init__(self, directive: LearningBrainStrategyInstruction) -> None:
        self._directive = directive
        self.calls: list[str] = []

    def validate_configuration(self) -> None:
        return None

    def generate(
        self,
        *,
        evidence,
        idempotency_key: str,
    ) -> LearningBrainProviderResult:
        del evidence
        self.calls.append(idempotency_key)
        return LearningBrainProviderResult(
            output=self._directive,
            usage=ModelTokenUsage(
                input_tokens=100,
                cached_input_tokens=0,
                output_tokens=40,
            ),
            output_fingerprint="a" * 64,
            latency_ms=12,
        )


def _directive(scope_key: str = "言语理解") -> LearningBrainStrategyInstruction:
    return LearningBrainStrategyInstruction(
        focus=scope_key,
        cause_direction="calculation_error",
        essay_emphasis="作答-评阅",
        mock_schedule="周三一次模考",
        rationale="言语理解正确率偏低，近14天错因集中在计算，本周侧重提升。",
        action=LearningBrainStrategyAction(
            decision_type="phase_deprioritize",
            scope_type="module",
            scope_key=scope_key,
        ),
    )


def _seed_user_and_learning(factory: sessionmaker[Session]) -> str:
    """造一个拥有双轨学习证据 + 已确认错因 + 完整能力估计的用户。

    复用小程序的真实链路：先完成一次行测 task session 并走错因确认
    （落库 xingce_confirmed_cause_versions），再补申论内容与能力估计。
    """
    client, auth, _task, submitted = _submitted_attempt(
        factory,
        token="central-brain-directive-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="central-brain-reflection")
    _run_one(factory, SequenceProvider(["candidate_ready"]))
    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    ready = current.json()["data"]
    assert ready["state"] == "cause_decision_required"
    candidate_id = ready["review"]["aiCandidate"]["candidateVersionId"]
    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "central-brain-cause-decision"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "replace",
            "replacementCause": "数量关系这题是跳步算错了。",
            "replacementCode": "calculation_error",
        },
    )
    assert decided.status_code == 200, decided.text
    user_id = _session_owner(factory, session_id=submitted["sessionId"])
    with factory.begin() as session:
        seed_active_position_release(
            session,
            activated_at=NOW,
            database_now=datetime.now(UTC),
            rule_set_version="central-brain-rules",
            position_data_version="central-brain-positions",
        )
        _insert_complete_ability_estimate(session, user_id=user_id, now=NOW)
    return user_id


def _insert_complete_ability_estimate(
    session: Session,
    *,
    user_id: str,
    now: datetime,
) -> None:
    session.execute(
        text(
            """
            insert into learning.ability_estimate_versions (
              estimate_version_id, user_id, exam_code, exam_year, status,
              xingce_lower, xingce_recommended, xingce_upper,
              shenlun_lower, shenlun_recommended, shenlun_upper,
              total_lower, total_recommended, total_upper,
              confidence, source_evidence_ids, evidence_summary,
              algorithm_version, input_fingerprint, generated_at, valid_until
            ) values (
              cast(:estimate_id as uuid), cast(:user_id as uuid),
              'guokao', 2026, 'complete', 58, 62, 66, 52, 56, 60,
              110, 118, 126, 'medium', '[]'::jsonb,
              cast(:evidence_summary as jsonb),
              'central-brain-test-v1', :fingerprint,
              :generated_at, :valid_until
            )
            """
        ),
        {
            "estimate_id": str(uuid4()),
            "user_id": user_id,
            "fingerprint": sha256(f"estimate:{user_id}".encode()).hexdigest(),
            "generated_at": now - timedelta(minutes=5),
            "valid_until": now + timedelta(days=30),
            "evidence_summary": json.dumps(
                {
                    "evidenceCount": 1,
                    "xingceEvidenceCount": 1,
                    "shenlunEvidenceCount": 1,
                    "sourceCounts": {"controlled_mock": 1},
                    "latestEvidenceAt": (now - timedelta(minutes=5)).isoformat(),
                    "missingComponents": [],
                    "conflictComponents": [],
                }
            ),
        },
    )


def _agent(provider) -> LearningBrainAgentService:
    return LearningBrainAgentService(
        settings=Settings(),
        provider=provider,
    )


def test_directive_contract_is_strict_json_object() -> None:
    """策略指令是可被 strict json_schema 冻结的结构。"""

    schema = LearningBrainStrategyEnvelope.model_json_schema(by_alias=True)
    assert "output" in schema["properties"]
    # nested output model is emitted as a $defs reference; the output itself
    # must carry the five intent fields plus an action decision.
    defs = schema["$defs"]
    assert any("action" in definition.get("properties", {}) for definition in defs.values())
    assert any("rationale" in definition.get("properties", {}) for definition in defs.values())
    envelope = LearningBrainStrategyEnvelope(output=_directive())
    assert envelope.contract_version == "weekly-strategy-directive@v1"


def test_suggest_strategy_persists_agent_candidate_awaiting_confirmation(
    db_session_factory: sessionmaker[Session],
) -> None:
    """读状态 → 调 LLM → 落库为 producer_type=agent 的待确认候选。"""

    user_id = _seed_user_and_learning(db_session_factory)
    provider = _DirectiveProvider(_directive())
    service = _agent(provider)
    with db_session_factory.begin() as session:
        result = service.suggest_strategy(
            session,
            user_id=user_id,
            command_id=f"central-brain-suggest-{uuid4()}",
            trigger_kind="daily_first_entry",
            now=NOW,
        )
    assert result.candidate is not None
    assert result.suggestion_id is not None
    candidate = result.candidate
    assert candidate.producer_type == "agent"
    assert candidate.inference_type == "strategy_directive"
    assert candidate.status == "awaiting_confirmation"
    assert candidate.requires_user_confirmation is True
    assert candidate.confirmation_status == "pending"
    assert candidate.value["decisionType"] == "phase_deprioritize"
    assert candidate.value["scopeKey"] == "言语理解"
    with db_session_factory() as session:
        rows = session.execute(
            text(
                "select count(*) from learning.inference_candidate_versions "
                "where user_id = cast(:user_id as uuid) and producer_type = 'agent'"
            ),
            {"user_id": user_id},
        ).scalar_one()
        assert rows == 1
        suggestions = session.execute(
            text(
                "select count(*) from learning.learning_brain_agent_suggestions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one()
        assert suggestions == 1
        assert provider.calls


def test_maybe_suggest_strategy_is_idempotent_within_a_day(
    db_session_factory: sessionmaker[Session],
) -> None:
    """同一天重复触发不重算、不重复写候选。"""

    user_id = _seed_user_and_learning(db_session_factory)
    provider = _DirectiveProvider(_directive())
    service = _agent(provider)
    with db_session_factory.begin() as session:
        first = service.maybe_suggest_strategy(
            session,
            user_id=user_id,
            trigger_kind="daily_first_entry",
            now=NOW,
        )
    with db_session_factory.begin() as session:
        second = service.maybe_suggest_strategy(
            session,
            user_id=user_id,
            trigger_kind="cause_confirmed",
            now=NOW + timedelta(hours=2),
        )
    assert second.skipped_reason == "already_suggested_today"
    assert second.suggestion_id == first.suggestion_id
    assert len(provider.calls) == 1
    with db_session_factory() as session:
        count = session.execute(
            text(
                "select count(*) from learning.inference_candidate_versions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one()
        assert count == 1
    # 跨天再次触发才产生新候选。
    with db_session_factory.begin() as session:
        third = service.maybe_suggest_strategy(
            session,
            user_id=user_id,
            trigger_kind="daily_first_entry",
            now=NOW + timedelta(days=1),
        )
    assert third.skipped_reason is None
    assert len(provider.calls) == 2
    with db_session_factory() as session:
        count = session.execute(
            text(
                "select count(*) from learning.inference_candidate_versions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one()
        assert count == 2


def test_agent_candidate_confirmed_then_planning_reads_active_strategy_decision(
    db_session_factory: sessionmaker[Session],
) -> None:
    """用户确认 agent 候选后，planning 能读到对应的 active strategy decision。"""

    user_id = _seed_user_and_learning(db_session_factory)
    provider = _DirectiveProvider(_directive())
    service = _agent(provider)
    with db_session_factory.begin() as session:
        result = service.suggest_strategy(
            session,
            user_id=user_id,
            command_id=f"central-brain-confirm-{uuid4()}",
            trigger_kind="daily_first_entry",
            now=NOW,
        )
    candidate = result.candidate
    assert candidate is not None
    from app.domains.learning_brain.schemas import StrategyDecisionCommand
    from app.domains.learning_brain.service import LearningBrainService

    brain = LearningBrainService()
    with db_session_factory.begin() as session:
        decision = brain.decide_strategy_candidate(
            session,
            user_id=user_id,
            candidate_id=str(candidate.candidate_id),
            command_id=f"central-brain-decision-{uuid4()}",
            command=StrategyDecisionCommand(
                based_on_candidate_version_id=candidate.candidate_version_id,
                action="confirm",
            ),
            now=NOW + timedelta(hours=1),
        )
    assert decision.decision.status == "active"
    assert decision.decision.decision_type == "phase_deprioritize"
    assert decision.decision.scope_key == "言语理解"

    from app.domains.planning.repository import PlanningRepository

    with db_session_factory() as session:
        active = PlanningRepository().active_strategy_decisions(
            session,
            user_id=user_id,
            now=NOW + timedelta(hours=2),
        )
    assert any(
        item.decision_type == "phase_deprioritize" and item.scope_key == "言语理解"
        for item in active
    )


def test_agent_provider_fails_closed_until_audit_controls_are_frozen() -> None:
    """生产环境的 provider 配置门禁：关键审计控制缺失时拒绝调用。"""

    from app.domains.learning_brain.provider import LearningBrainProvider

    with pytest.raises(LearningBrainProviderConfigurationError, match="AI_API_KEY"):
        LearningBrainProvider(Settings(environment="production")).validate_configuration()

    unsafe = _production_settings(ai_provider_base_url="http://brain.example.test/v1")
    with pytest.raises(LearningBrainProviderConfigurationError, match="HTTPS"):
        LearningBrainProvider(unsafe).validate_configuration()

    unapproved = _production_settings(
        ai_learning_brain_agent_provider_https_host_allowlist="other.example.test"
    )
    with pytest.raises(LearningBrainProviderConfigurationError, match="allowlisted"):
        LearningBrainProvider(unapproved).validate_configuration()

    ok = _production_settings()
    LearningBrainProvider(ok).validate_configuration()


def _production_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "ai_api_key": "provider-key",
        "ai_provider_base_url": "https://brain.example.test/v1",
        "ai_learning_brain_agent_model": "gemini-3.5-flash",
        "ai_learning_brain_agent_provider_release_id": "brain-provider-2026-08-04.1",
        "ai_learning_brain_agent_provider_https_host_allowlist": "brain.example.test",
        "ai_learning_brain_agent_provider_dpa_confirmed": True,
        "ai_learning_brain_agent_provider_idempotency_capability": (
            "replayable_queryable"
        ),
        "ai_learning_brain_agent_provider_idempotency_key_header": "Idempotency-Key",
        "ai_learning_brain_agent_provider_request_id_header": "X-Provider-Request-Id",
        "ai_learning_brain_agent_provider_replay_retention_seconds": 604_800,
        "ai_learning_brain_agent_max_input_bytes": 1_000_000,
        "ai_learning_brain_agent_max_output_bytes": 100_000,
        "ai_learning_brain_agent_timeout_seconds": 60.0,
    }
    values.update(changes)
    return Settings(**values)


def test_agent_rejects_directive_without_evidence_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    """策略指令 scope 落在画像外模块时，服务层拒绝落库（可复现校验）。"""

    user_id = _seed_user_and_learning(db_session_factory)
    provider = _DirectiveProvider(
        _directive(scope_key="不存在的模块"),
    )
    service = _agent(provider)
    from app.domains.learning_brain.agent import LearningBrainAgentError

    with pytest.raises(LearningBrainAgentError, match="no canonical learning evidence"):
        with db_session_factory.begin() as session:
            service.suggest_strategy(
                session,
                user_id=user_id,
                command_id=f"central-brain-invalid-{uuid4()}",
                trigger_kind="on_demand",
                now=NOW,
            )


def _seed_new_user(factory: sessionmaker[Session]) -> str:
    """造一个只有账号和岗位发布、没有做题/能力证据的新用户。"""
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '新用户')"
            ),
            {"user_id": user_id},
        )
        seed_active_position_release(
            session,
            activated_at=NOW,
            database_now=datetime.now(UTC),
            rule_set_version="central-brain-newuser-rules",
            position_data_version="central-brain-newuser-positions",
        )
    return user_id


def _guidance_directive() -> LearningBrainStrategyInstruction:
    return LearningBrainStrategyInstruction(
        focus="建立能力基线",
        cause_direction="other",
        essay_emphasis="先摸底再定申论侧重",
        mock_schedule="暂不安排模考",
        rationale="当前还没有做题记录，先完成一次能力摸底建立基线，才能针对性规划本周侧重。",
        action=LearningBrainStrategyAction(
            decision_type="baseline_guidance",
            scope_type="track",
            scope_key="xingce",
        ),
    )


def test_evidence_insufficient_produces_baseline_guidance_candidate(
    db_session_factory: sessionmaker[Session],
) -> None:
    """新用户（无做题证据）不再抛错，而是产出 baseline_guidance 候选。"""

    user_id = _seed_new_user(db_session_factory)
    provider = _DirectiveProvider(_guidance_directive())
    service = _agent(provider)
    with db_session_factory.begin() as session:
        result = service.suggest_strategy(
            session,
            user_id=user_id,
            command_id=f"central-brain-guidance-{uuid4()}",
            trigger_kind="on_demand",
            now=NOW,
        )
    assert result.candidate is not None
    candidate = result.candidate
    assert candidate.producer_type == "agent"
    assert candidate.status == "awaiting_confirmation"
    assert candidate.value["decisionType"] == "baseline_guidance"
    assert candidate.value["scopeType"] == "track"
    assert candidate.value["scopeKey"] == "xingce"
    assert "摸底" in candidate.value["rationale"]


def test_erased_evidence_summary_does_not_break_profile(
    db_session_factory: sessionmaker[Session],
) -> None:
    """脱敏（evidence_summary={erased:true}）用户建 profile 不炸，大脑可产引导。"""

    user_id = _seed_new_user(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.ability_estimate_versions (
                  estimate_version_id, user_id, exam_code, exam_year, status,
                  xingce_lower, xingce_recommended, xingce_upper,
                  shenlun_lower, shenlun_recommended, shenlun_upper,
                  total_lower, total_recommended, total_upper,
                  confidence, source_evidence_ids, evidence_summary,
                  algorithm_version, input_fingerprint, generated_at, valid_until
                ) values (
                  cast(:estimate_id as uuid), cast(:user_id as uuid),
                  'guokao', 2026, 'complete', 0, 0, 0, 0, 0, 0, 0, 0, 0,
                  'low', '[]'::jsonb, '{"erased": true}'::jsonb,
                  'erased-test-v1', :fingerprint, :now, :valid_until
                )
                """
            ),
            {
                "estimate_id": str(uuid4()),
                "user_id": user_id,
                "fingerprint": sha256(f"erased:{user_id}".encode()).hexdigest(),
                "now": NOW - timedelta(days=1),
                "valid_until": NOW + timedelta(days=30),
            },
        )

    from app.domains.learning_brain.service import LearningBrainService

    brain = LearningBrainService()
    with db_session_factory.begin() as session:
        profile = brain.get_or_create_profile(
            session, user_id=user_id, now=NOW
        )
    assert profile is not None

    # 脱敏用户走引导：suggest_strategy 产出 baseline_guidance，不抛错。
    # 用固定 now 保证 profile fingerprint 稳定（脱敏数据不变时幂等）。
    provider = _DirectiveProvider(_guidance_directive())
    service = _agent(provider)
    with db_session_factory.begin() as session:
        result = service.suggest_strategy(
            session,
            user_id=user_id,
            command_id=f"central-brain-erased-{uuid4()}",
            trigger_kind="on_demand",
            now=NOW,
        )
    assert result.candidate is not None
    assert result.candidate.value["decisionType"] == "baseline_guidance"


def test_planning_baseline_guidance_boosts_baseline_candidate() -> None:
    """baseline_guidance 决策确认后，planning 把摸底候选提为高优先级。"""

    from app.domains.planning.engine import PriorityBreakdown, TaskCandidate
    from app.domains.planning.repository import ActiveStrategyDecision
    from app.domains.planning.service import _apply_strategy_decisions
    from datetime import datetime

    candidate = TaskCandidate(
        candidate_key="xingce-baseline",
        track="xingce",
        stage="objective_baseline",
        kind="practice",
        title="先来摸个底",
        reason="先摸底",
        estimated_minutes=30,
        completion_rule="答完",
        start_url="/pages/xingce-task-intro/index?mode=baseline",
        evidence_refs=(),
        priority=PriorityBreakdown(
            goal_gain=0.45,
            ability_gain=0.95,
            verification_urgency=0.70,
            time_fit=1.0,
            load_penalty=0.15,
        ),
        strategy_module_keys=(),
    )
    decision = ActiveStrategyDecision(
        decision_version_id="dv1",
        decision_id="d1",
        decision_type="baseline_guidance",
        scope_type="track",
        scope_key="xingce",
        rationale="先完成能力摸底建立基线。",
        evidence_refs=(),
        effective_from=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
        effective_until=datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
        review_at=datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
        input_fingerprint="a" * 64,
    )
    adjusted, omissions, reason_codes = _apply_strategy_decisions(
        [candidate], [decision]
    )
    assert len(adjusted) == 1
    boosted = adjusted[0]
    assert boosted.priority.ability_gain > 0.95
    assert "先建立基线" in boosted.reason
    assert reason_codes == ["strategy_baseline_guidance_applied"]
    assert not omissions
