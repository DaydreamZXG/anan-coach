from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import bindparam

from app.domains.companion.repository import CompanionRepository
from app.domains.companion.service import (
    _build_persona_prompt,
    _context_payload,
    _context_retrieval_candidates,
)
from app.domains.learning_brain.service import LearningBrainService

NOW = datetime.now(UTC)
RATIONALE = "言语理解正确率偏低，近14天错因集中在计算，本周侧重提升。"
PENDING_RATIONALE = "资料分析近两周连续未达目标，本周应把资料分析提为主攻模块。"


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _seed_user(factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '策略学习者')"
            ),
            {"user_id": user_id},
        )
    return user_id


def _seed_profile(factory: sessionmaker[Session], *, user_id: str) -> str:
    profile_version_id = str(uuid4())
    with factory.begin() as session:
        statement = text(
                """
                insert into learning.learner_brain_profile_versions (
                  profile_version_id, user_id, version_no, readiness,
                  ability_estimate_version_id, ability_snapshot,
                  evidence_summary, candidate_summary, module_abilities,
                  error_patterns, decision_summary, conflicts, source_refs,
                  input_versions, algorithm_version, input_fingerprint,
                  supersedes_profile_version_id, generated_at, valid_until
                ) values (
                  cast(:profile_version_id as uuid), cast(:user_id as uuid),
                  1, 'unavailable', null, :ability_snapshot,
                  :evidence_summary, :candidate_summary,
                  '[]'::jsonb, '[]'::jsonb,
                  :decision_summary,
                  '[]'::jsonb, '[]'::jsonb, '{}'::jsonb,
                  'learning-brain-profile@v1', :fingerprint, null,
                  :generated_at, :valid_until
                )
                """
            ).bindparams(
                bindparam("ability_snapshot", type_=JSONB),
                bindparam("evidence_summary", type_=JSONB),
                bindparam("candidate_summary", type_=JSONB),
                bindparam("decision_summary", type_=JSONB),
            )
        session.execute(
            statement,
            {
                "profile_version_id": profile_version_id,
                "user_id": user_id,
                "fingerprint": _hash(f"profile:{user_id}"),
                "ability_snapshot": {
                    "estimate": None,
                    "readiness": "unavailable",
                    "missingComponents": ["xingce", "shenlun"],
                },
                "evidence_summary": {
                    "scoreEvidenceCount": 0,
                    "xingceAttemptCount": 0,
                    "xingceMasteryCount": 0,
                    "essayMasteryCount": 0,
                    "taskExecutionCount": 0,
                    "externalAnswerCount": 0,
                    "latestEvidenceAt": None,
                },
                "candidate_summary": {
                    "awaitingConfirmation": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "expired": 0,
                },
                "decision_summary": {
                    "activeStrategyCount": 0,
                    "reviewDueCount": 0,
                    "currentPlanVersionId": None,
                    "currentWeeklyPlanVersionId": None,
                },
                "generated_at": NOW - timedelta(days=1),
                "valid_until": NOW + timedelta(days=1),
            },
        )
    return profile_version_id


def _seed_confirmed_strategy(
    factory: sessionmaker[Session],
    *,
    user_id: str,
) -> None:
    """落一条已确认（accepted）候选 + 对应 active 策略决定。"""
    with factory.begin() as session:
        candidate_version_id = _insert_candidate(
            session,
            user_id=user_id,
            status="accepted",
            confirmation_status="confirmed",
            value={
                "decisionType": "phase_deprioritize",
                "scopeType": "module",
                "scopeKey": "言语理解",
                "rationale": RATIONALE,
            },
        )
        session.execute(
            text(
                """
                insert into learning.strategy_decision_versions (
                  decision_version_id, decision_id, command_id, user_id,
                  version_no, candidate_version_id, decision_type, status,
                  scope_type, scope_key, rationale, benefit_evidence,
                  evidence_refs, effective_from, effective_until, review_at,
                  policy_version, input_fingerprint,
                  supersedes_decision_version_id, created_at
                ) values (
                  cast(:decision_version_id as uuid),
                  cast(:decision_id as uuid), :command_id,
                  cast(:user_id as uuid), 1,
                  cast(:candidate_version_id as uuid),
                  'phase_deprioritize', 'active', 'module', '言语理解',
                  :rationale, '{"claim":"test"}'::jsonb,
                  '[{"versionId":"evidence-v1"}]'::jsonb,
                  :effective_from, :effective_until, :review_at,
                  'strategy-decision@v1', :fingerprint, null, :created_at
                )
                """
            ),
            {
                "decision_version_id": str(uuid4()),
                "decision_id": str(uuid4()),
                "command_id": f"confirmed-strategy:{uuid4()}",
                "user_id": user_id,
                "candidate_version_id": candidate_version_id,
                "rationale": RATIONALE,
                "effective_from": NOW - timedelta(days=1),
                "effective_until": NOW + timedelta(days=6),
                "review_at": NOW + timedelta(days=6),
                "fingerprint": _hash(f"decision:{user_id}"),
                "created_at": NOW - timedelta(days=1),
            },
        )


def _seed_pending_candidate(factory: sessionmaker[Session], *, user_id: str) -> None:
    with factory.begin() as session:
        _insert_candidate(
            session,
            user_id=user_id,
            status="awaiting_confirmation",
            confirmation_status="pending",
            value={
                "decisionType": "phase_deprioritize",
                "scopeType": "module",
                "scopeKey": "资料分析",
                "rationale": PENDING_RATIONALE,
            },
        )


def _insert_candidate(
    session: Session,
    *,
    user_id: str,
    status: str,
    confirmation_status: str,
    value: dict[str, object],
) -> str:
    candidate_id = str(uuid4())
    candidate_version_id = str(uuid4())
    session.execute(
        text(
            """
            insert into learning.inference_candidate_versions (
              candidate_version_id, candidate_id, command_id, user_id,
              version_no, inference_type, status, producer_type, producer_id,
              value, evidence_refs, confidence, requires_user_confirmation,
              confirmation_status, validation_status, model_id, prompt_version,
              policy_version, valid_from, expires_at, input_fingerprint,
              supersedes_candidate_version_id, created_at
            ) values (
              cast(:candidate_version_id as uuid),
              cast(:candidate_id as uuid), :command_id,
              cast(:user_id as uuid), 1, 'strategy_directive', :status,
              'rule', 'strategy-directive-test@v1', cast(:value as jsonb),
              '[{"versionId":"evidence-v1"}]'::jsonb, 0.5, true,
              :confirmation_status, 'valid', null, null,
              'strategy-candidate@v1', :valid_from, :expires_at,
              :fingerprint, null, :created_at
            )
            """
        ),
        {
            "candidate_version_id": candidate_version_id,
            "candidate_id": candidate_id,
            "command_id": f"strategy-candidate:{candidate_id}",
            "user_id": user_id,
            "status": status,
            "confirmation_status": confirmation_status,
            "value": json.dumps(value, ensure_ascii=False),
            "valid_from": NOW - timedelta(days=1),
            "expires_at": NOW + timedelta(days=6),
            "fingerprint": _hash(f"candidate:{candidate_id}:{user_id}"),
            "created_at": NOW - timedelta(days=1),
        },
    )
    return candidate_version_id


def test_current_strategy_directive_reads_active_and_pending(
    db_session_factory: sessionmaker[Session],
) -> None:
    """读方法同时返回已生效策略与待确认候选的 rationale。"""
    user_id = _seed_user(db_session_factory)
    _seed_profile(db_session_factory, user_id=user_id)
    _seed_confirmed_strategy(db_session_factory, user_id=user_id)
    _seed_pending_candidate(db_session_factory, user_id=user_id)

    with db_session_factory() as session:
        directive = LearningBrainService().current_strategy_directive(
            session,
            user_id=user_id,
        )

    assert directive is not None
    assert directive.active is not None
    assert directive.active.status == "active"
    assert directive.active.decision_type == "phase_deprioritize"
    assert directive.active.scope_key == "言语理解"
    assert directive.active.rationale == RATIONALE
    assert directive.pending is not None
    assert directive.pending.status == "awaiting_confirmation"
    assert directive.pending.scope_key == "资料分析"
    assert directive.pending.rationale == PENDING_RATIONALE


def test_current_strategy_directive_returns_none_without_strategy(
    db_session_factory: sessionmaker[Session],
) -> None:
    """没有策略时不返回空壳，返回 None 供岸岸直说数据不足。"""
    user_id = _seed_user(db_session_factory)

    with db_session_factory() as session:
        directive = LearningBrainService().current_strategy_directive(
            session,
            user_id=user_id,
        )

    assert directive is None


def test_companion_context_exposes_active_strategy_rationale(
    db_session_factory: sessionmaker[Session],
) -> None:
    """岸岸上下文注入 active 策略 rationale，用户问排期原因时可引用。"""
    user_id = _seed_user(db_session_factory)
    _seed_profile(db_session_factory, user_id=user_id)
    _seed_confirmed_strategy(db_session_factory, user_id=user_id)

    with db_session_factory() as session:
        context = CompanionRepository(session).get_context(
            user_id=user_id,
            node_id=None,
        )
        payload = _context_payload(context)

    assert context.learning_brain_profile is not None
    directive = context.learning_brain_profile["strategyDirective"]
    assert directive is not None
    assert directive["active"]["rationale"] == RATIONALE
    assert directive["pending"] is None
    learning_brain = payload["learningBrain"]
    assert isinstance(learning_brain, dict)
    assert learning_brain["strategyDirective"]["active"]["rationale"] == RATIONALE

    retrieval = _context_retrieval_candidates(context)
    brain_source = next(
        item for item in retrieval if item.reference.kind == "learning_brain"
    )
    assert RATIONALE in brain_source.text


def test_companion_context_exposes_pending_candidate_for_proactive_offer(
    db_session_factory: sessionmaker[Session],
) -> None:
    """有未确认候选时，岸岸上下文能看到 pending，可主动请用户确认。"""
    user_id = _seed_user(db_session_factory)
    _seed_profile(db_session_factory, user_id=user_id)
    _seed_pending_candidate(db_session_factory, user_id=user_id)

    with db_session_factory() as session:
        context = CompanionRepository(session).get_context(
            user_id=user_id,
            node_id=None,
        )

    assert context.learning_brain_profile is not None
    directive = context.learning_brain_profile["strategyDirective"]
    assert directive is not None
    assert directive["active"] is None
    assert directive["pending"]["status"] == "awaiting_confirmation"
    assert directive["pending"]["scopeKey"] == "资料分析"
    assert directive["pending"]["rationale"] == PENDING_RATIONALE


def test_persona_guides_citing_rationale_and_confirming_pending() -> None:
    persona = _build_persona_prompt()
    assert "strategyDirective" in persona
    assert "为什么这样排" in persona
    assert "确认" in persona
    assert "不得编造" in persona
