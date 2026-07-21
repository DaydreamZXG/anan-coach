from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.domains.learning_brain.service import LearningBrainService
from app.domains.companion.repository import CompanionRepository
from app.domains.companion.service import (
    _build_persona_prompt,
    _context_payload,
    _context_retrieval_candidates,
)
from app.domains.planning.insights_schemas import WeeklyPlanDay, WeeklyPlanVersion
from app.domains.planning.insights_service import _weekly_day_from_jsonb
from app.domains.planning.candidates import (
    EssayPlanningSnapshot,
    GoalPlanningSnapshot,
    ObjectivePlanningSnapshot,
    build_dual_track_candidates,
)
from app.domains.planning.engine import PriorityBreakdown, TaskCandidate
from app.domains.planning.repository import ActiveStrategyDecision
from app.domains.planning.schemas import PlanningEvidenceRef
from app.domains.planning.service import _apply_strategy_decisions
from app.main import create_app


NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)


def _strategy(decision_type: str) -> ActiveStrategyDecision:
    return ActiveStrategyDecision(
        decision_version_id=str(uuid4()),
        decision_id=str(uuid4()),
        decision_type=decision_type,
        scope_type="module",
        scope_key="资料分析",
        rationale="根据当前证据确认的时间取舍",
        evidence_refs=({"versionId": "evidence-v1"},),
        effective_from=NOW,
        effective_until=NOW + timedelta(days=30),
        review_at=NOW + timedelta(days=7),
        input_fingerprint="d" * 64,
    )


def _candidate(*, stage: str, key: str) -> TaskCandidate:
    return TaskCandidate(
        candidate_key=key,
        track="xingce",
        stage=stage,
        kind="practice",
        title="资料分析任务",
        reason="当前证据收益最高。",
        estimated_minutes=20,
        completion_rule="完成题组并形成证据。",
        start_url="/pages/xingce-task-intro/index",
        evidence_refs=(),
        priority=PriorityBreakdown(
            goal_gain=0.8,
            ability_gain=0.8,
            verification_urgency=0.8,
            time_fit=1.0,
            load_penalty=0.1,
        ),
        strategy_module_keys=("资料分析",),
    )


def _apply_stage4(engine: Engine) -> object:
    migration = import_module("migrations.versions.20260716_31_learning_brain_planning")
    if "learner_brain_profile_versions" not in inspect(engine).get_table_names(
        schema="learning"
    ):
        with engine.begin() as connection:
            for statement in migration._STATEMENTS:
                connection.execute(text(statement))
    return migration


def _seed_user_and_release(factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id, display_name) values (:id, '阶段四学习者')"),
            {"id": user_id},
        )
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  :release_id, 'guokao', 2026, 'stage4-rules',
                  'stage4-positions', 'active', :activated_at
                )
                """
            ),
            {"release_id": str(uuid4()), "activated_at": NOW},
        )
    return user_id


def test_stage4_schema_is_append_only_and_refuses_destructive_downgrade(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _apply_stage4(db_engine)
    assert {
        "inference_candidate_versions",
        "learner_brain_profile_versions",
        "strategy_decision_versions",
        "weekly_plan_versions",
    }.issubset(inspect(db_engine).get_table_names(schema="learning"))

    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values ('00000000-0000-0000-0000-000000000401', 'stage4-schema');
                insert into learning.inference_candidate_versions (
                  candidate_version_id, candidate_id, command_id, user_id,
                  version_no, inference_type, status, producer_type, producer_id,
                  value, evidence_refs, confidence, requires_user_confirmation,
                  confirmation_status, validation_status, policy_version,
                  valid_from, expires_at, input_fingerprint, created_at
                ) values (
                  '00000000-0000-0000-0000-000000000402',
                  '00000000-0000-0000-0000-000000000403', 'stage4-schema-command',
                  '00000000-0000-0000-0000-000000000401', 1,
                  'strategy_recommendation', 'awaiting_confirmation', 'rule',
                  'strategy-rule@v1', '{"scope":"module"}'::jsonb,
                  '[{"versionId":"evidence-v1"}]'::jsonb, 0.5, true,
                  'pending', 'valid', 'strategy-candidate@v1', now(),
                  now() + interval '7 days', repeat('a', 64), now()
                )
                """
            )
        )
        assert migration._has_stage4_records(connection) is True
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        with pytest.raises(RuntimeError, match="destructive downgrade is forbidden"):
            migration.downgrade()

        savepoint = connection.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(
                    text(
                        """
                        update learning.inference_candidate_versions
                        set confidence = 0.8
                        where candidate_version_id =
                          '00000000-0000-0000-0000-000000000402'
                        """
                    )
                )
        finally:
            savepoint.rollback()
    finally:
        transaction.rollback()
        connection.close()


def test_learning_brain_replays_same_inputs_without_inventing_evidence(
    db_engine: Engine,
    db_session_factory: sessionmaker[Session],
) -> None:
    _apply_stage4(db_engine)
    user_id = _seed_user_and_release(db_session_factory)
    service = LearningBrainService()

    with db_session_factory.begin() as session:
        first = service.get_or_create_profile(session, user_id=user_id, now=NOW)
    with db_session_factory.begin() as session:
        replay = service.get_or_create_profile(
            session,
            user_id=user_id,
            now=NOW + timedelta(hours=1),
        )

    assert first.profile_version_id == replay.profile_version_id
    assert first.input_fingerprint == replay.input_fingerprint
    assert first.readiness == "unavailable"
    assert first.ability.estimate is None
    assert first.modules == []
    assert first.error_patterns == []
    with db_session_factory() as session:
        companion_context = CompanionRepository(session).get_context(
            user_id=user_id,
            node_id=None,
        )
        assert companion_context.learning_brain_profile is not None
        assert (
            companion_context.learning_brain_profile["profileVersionId"]
            == str(first.profile_version_id)
        )
        assert _context_payload(companion_context)["learningBrain"] is not None
        assert "learning_brain" in [
            item.reference.kind
            for item in _context_retrieval_candidates(companion_context)
        ]
        persona = _build_persona_prompt(companion_context)
        assert "岸岸" in persona
        assert "沈砚" not in persona
        assert session.execute(
            text("select count(*) from learning.learner_brain_profile_versions")
        ).scalar_one() == 1
        assert session.execute(
            text(
                "select count(*) from platform.outbox_events "
                "where event_type = 'learning_brain.profile.changed'"
            )
        ).scalar_one() == 1


def test_weekly_contract_requires_exact_contiguous_five_plus_one_plus_one() -> None:
    week_start = date(2026, 7, 13)
    days = [
        WeeklyPlanDay(
            localDate=week_start + timedelta(days=index),
            weekday=index + 1,
            mode="mock" if index == 5 else "recovery" if index == 6 else "training",
            plannedMinutes=20 if index == 6 else 90,
            scheduledMinutes=0,
            completedTaskCount=0,
            totalTaskCount=0,
            status="upcoming",
            explanation="按最新证据在当天生成具体任务。",
            dailyPlanVersionId=None,
        )
        for index in range(7)
    ]
    contract = WeeklyPlanVersion(
        weeklyPlanVersionId=uuid4(),
        weekStart=week_start,
        versionNo=1,
        weeklyPreferenceVersionId=uuid4(),
        rhythm="5+1+1",
        days=days,
        evidenceRefs=[
            PlanningEvidenceRef(
                owner="planning",
                entityType="weekly_study_preference",
                entityId="preference-v1",
                versionId="preference-v1",
                role="5+1+1_rhythm",
            )
        ],
        inputVersions={"weeklyPreference": "preference-v1"},
        replanReasonCodes=["weekly_rhythm_initialized"],
        inputFingerprint="b" * 64,
        generatedAt=NOW,
        validUntil=NOW + timedelta(days=4),
    )
    assert [item.mode for item in contract.days].count("training") == 5
    assert [item.mode for item in contract.days].count("mock") == 1
    assert [item.mode for item in contract.days].count("recovery") == 1

    invalid = [item.model_copy() for item in days]
    invalid[0] = invalid[0].model_copy(update={"mode": "mock"})
    with pytest.raises(ValueError, match=r"5\+1\+1"):
        WeeklyPlanVersion(
            weeklyPlanVersionId=uuid4(),
            weekStart=week_start,
            versionNo=1,
            weeklyPreferenceVersionId=uuid4(),
            rhythm="5+1+1",
            days=invalid,
            evidenceRefs=contract.evidence_refs,
            inputVersions={"weeklyPreference": "preference-v1"},
            replanReasonCodes=[],
            inputFingerprint="c" * 64,
            generatedAt=NOW,
            validUntil=NOW + timedelta(days=4),
        )


def test_weekly_jsonb_days_rehydrate_strict_date_and_uuid_fields() -> None:
    daily_plan_version_id = str(uuid4())
    day = _weekly_day_from_jsonb(
        {
            "localDate": "2026-07-20",
            "weekday": 1,
            "mode": "training",
            "plannedMinutes": 90,
            "scheduledMinutes": 30,
            "completedTaskCount": 1,
            "totalTaskCount": 2,
            "status": "today",
            "explanation": "按最新证据在当天生成具体任务。",
            "dailyPlanVersionId": daily_plan_version_id,
        }
    )

    assert day.local_date == date(2026, 7, 20)
    assert str(day.daily_plan_version_id) == daily_plan_version_id


def test_confirmed_strategy_changes_new_learning_but_preserves_required_closure() -> None:
    practice = _candidate(stage="objective_practice", key="practice")
    closure = _candidate(stage="objective_verification", key="verification")

    deprioritized, omissions, codes = _apply_strategy_decisions(
        [practice, closure],
        [_strategy("phase_deprioritize")],
    )
    assert omissions == {}
    assert deprioritized[0].priority.goal_gain == 0.28
    assert deprioritized[0].priority.ability_gain == 0.28
    assert "阶段降权" in deprioritized[0].reason
    assert deprioritized[0].evidence_refs[0]["entityType"] == (
        "strategy_decision_version"
    )
    assert deprioritized[1] == closure
    assert "strategy_phase_deprioritize_applied" in codes
    assert "strategy_preserved_required_closure" in codes

    remaining, omissions, codes = _apply_strategy_decisions(
        [practice, closure],
        [_strategy("strategic_abandon")],
    )
    assert remaining == [closure]
    assert omissions == {}
    assert "strategy_abandon_applied" in codes
    assert "strategy_preserved_required_closure" in codes


def test_essay_strategy_uses_canonical_module_key_instead_of_display_label() -> None:
    candidate_set = build_dual_track_candidates(
        goal=GoalPlanningSnapshot(
            readiness="partial",
            positive_gap=False,
            goal_version_id=None,
            ability_estimate_version_id=None,
        ),
        objective=ObjectivePlanningSnapshot(
            baseline_required=False,
            due_reflection_count=0,
            due_verification_count=0,
            weakest_module=None,
            source_version_id=None,
            closure_readiness="available",
            closure_unavailable_reason=None,
        ),
        essay=EssayPlanningSnapshot(
            next_stage="practice_due",
            task_version_id=str(uuid4()),
            question_type="summary",
            weakest_dimension="结构层次",
            weakest_dimension_key="structure",
        ),
        available_minutes=45,
    )
    essay = next(item for item in candidate_set.candidates if item.track == "essay")
    assert essay.strategy_module_keys == ("structure",)
    assert essay.strategy_question_type == "summary"


def test_stage4_routes_are_published_on_the_wechat_backend_contract() -> None:
    paths = create_app().openapi()["paths"]
    assert {
        "/api/v2/learning-brain/profile",
        "/api/v2/learning-brain/modules/{track}/{module_key}",
        "/api/v2/learning-brain/error-patterns/{pattern_id}",
        "/api/v2/learning-brain/strategy-candidates",
        "/api/v2/learning-brain/strategy-candidates/{candidate_id}/decision",
        "/api/v2/plans/today/basis",
        "/api/v2/plans/today/settlement",
        "/api/v2/plans/week",
        "/api/v2/plans/history",
        "/api/v2/companion/ask",
    }.issubset(paths)
    assert "get" in paths["/api/v2/learning-brain/profile"]
    assert "post" in paths["/api/v2/learning-brain/strategy-candidates"]
