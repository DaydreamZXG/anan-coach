from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.learning_brain.service import LearningBrainService
from app.domains.learning_brain.schemas import (
    ErrorPatternSummary,
    LearningBrainProfileVersion,
)
from tests.position_release_helpers import seed_active_position_release
from tests.test_xingce_cause_agent_workflow import SequenceProvider, _reflect, _run_one
from tests.test_xingce_planning_closure import _submitted_attempt
from tests.test_xingce_question_cause_candidates import _session_owner


def test_stable_verification_withdraws_exact_cause_without_cross_user_leak(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-verification-feedback-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="cause-verification-feedback-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate = current["review"]["aiCandidate"]
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "cause-verification-feedback-decision"},
        json={
            "expectedRevision": current["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text
    user_id = _session_owner(
        db_session_factory,
        session_id=submitted["sessionId"],
    )
    with db_session_factory.begin() as session:
        now = datetime.now(UTC)
        seed_active_position_release(
            session,
            activated_at=now,
            database_now=now,
            rule_set_version="cause-verification-feedback-rules",
            position_data_version="cause-verification-feedback-positions",
        )

    service = LearningBrainService()
    with db_session_factory.begin() as session:
        confirmed = service.get_or_create_profile(session, user_id=user_id)
    pattern = _xingce_pattern(confirmed)
    assert pattern.status == "confirmed"
    assert pattern.counterevidence_count == 0
    assert pattern.evidence_refs[0].entity_type == "xingce_confirmed_cause_version"

    first_review_at = datetime.now(UTC) + timedelta(minutes=1)
    _append_mastery_head(
        db_session_factory,
        user_id=user_id,
        state="provisionally_mastered",
        stage="first_review",
        due_at=first_review_at + timedelta(days=2),
        created_at=first_review_at,
    )
    with db_session_factory.begin() as session:
        still_testing = service.get_or_create_profile(session, user_id=user_id)
    assert _xingce_pattern(still_testing).status == "confirmed"
    assert _xingce_pattern(still_testing).counterevidence_count == 0

    delayed_at = first_review_at + timedelta(minutes=1)
    _append_mastery_head(
        db_session_factory,
        user_id=user_id,
        state="provisionally_mastered",
        stage="delayed_review",
        due_at=delayed_at + timedelta(days=10),
        created_at=delayed_at,
    )
    stable_at = delayed_at + timedelta(minutes=1)
    stable_version_id = _append_mastery_head(
        db_session_factory,
        user_id=user_id,
        state="stable_mastered",
        stage="delayed_review",
        due_at=None,
        created_at=stable_at,
    )

    with db_session_factory.begin() as session:
        stable = service.get_or_create_profile(session, user_id=user_id)
        replay = service.get_or_create_profile(session, user_id=user_id)
    withdrawn = _xingce_pattern(stable)
    assert withdrawn.status == "counterevidence"
    assert withdrawn.counterevidence_count == withdrawn.occurrence_count == 1
    assert withdrawn.evidence_refs[0].owner == "xingce_mastery"
    assert withdrawn.evidence_refs[0].entity_type == "xingce_mastery_state_version"
    assert withdrawn.evidence_refs[0].version_id == stable_version_id
    assert withdrawn.evidence_refs[0].role == "counterevidence"
    assert replay.profile_version_id == stable.profile_version_id

    second_mastery_id = _insert_unresolved_matching_cause(
        db_session_factory,
        user_id=user_id,
        created_at=stable_at + timedelta(minutes=1),
    )
    with db_session_factory.begin() as session:
        partially_repaired = service.get_or_create_profile(session, user_id=user_id)
    partial = _xingce_pattern(partially_repaired)
    assert partial.status == "confirmed"
    assert partial.occurrence_count == 2
    assert partial.counterevidence_count == 1
    assert partial.evidence_refs[0].entity_type == "xingce_confirmed_cause_version"

    second_first_at = stable_at + timedelta(minutes=2)
    _append_mastery_head(
        db_session_factory,
        user_id=user_id,
        mastery_id=second_mastery_id,
        state="provisionally_mastered",
        stage="first_review",
        due_at=second_first_at + timedelta(days=2),
        created_at=second_first_at,
    )
    second_delayed_at = second_first_at + timedelta(minutes=1)
    _append_mastery_head(
        db_session_factory,
        user_id=user_id,
        mastery_id=second_mastery_id,
        state="provisionally_mastered",
        stage="delayed_review",
        due_at=second_delayed_at + timedelta(days=10),
        created_at=second_delayed_at,
    )
    second_stable_at = second_delayed_at + timedelta(minutes=1)
    second_stable_version_id = _append_mastery_head(
        db_session_factory,
        user_id=user_id,
        mastery_id=second_mastery_id,
        state="stable_mastered",
        stage="delayed_review",
        due_at=None,
        created_at=second_stable_at,
    )
    with db_session_factory.begin() as session:
        fully_repaired = service.get_or_create_profile(session, user_id=user_id)
    fully_withdrawn = _xingce_pattern(fully_repaired)
    assert fully_withdrawn.status == "counterevidence"
    assert fully_withdrawn.counterevidence_count == fully_withdrawn.occurrence_count == 2
    assert fully_withdrawn.evidence_refs[0].version_id == second_stable_version_id

    other_user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '隔离学习者')"
            ),
            {"user_id": other_user_id},
        )
        isolated = service.get_or_create_profile(session, user_id=other_user_id)
    assert isolated.error_patterns == []

    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from learning.xingce_confirmed_cause_versions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 2
        assert session.execute(
            text(
                "select count(*) from learning.xingce_mastery_state_versions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 8


def _xingce_pattern(profile: LearningBrainProfileVersion) -> ErrorPatternSummary:
    patterns = [pattern for pattern in profile.error_patterns if pattern.track == "xingce"]
    assert len(patterns) == 1
    return patterns[0]


def _append_mastery_head(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    mastery_id: str | None = None,
    state: str,
    stage: str,
    due_at: datetime | None,
    created_at: datetime,
) -> str:
    mastery_version_id = str(uuid4())
    command_id = f"cause-verification-{uuid4()}"
    with factory.begin() as session:
        current = session.execute(
            text(
                """
                select mastery.*
                from learning.xingce_mastery_state_versions mastery
                where mastery.user_id = cast(:user_id as uuid)
                  and (
                    cast(:selected_mastery_id as uuid) is null
                    or mastery.mastery_id = cast(:selected_mastery_id as uuid)
                  )
                  and not exists (
                    select 1
                    from learning.xingce_mastery_state_versions successor
                    where successor.supersedes_mastery_version_id =
                          mastery.mastery_version_id
                  )
                """
            ),
            {"user_id": user_id, "selected_mastery_id": mastery_id},
        ).mappings().one()
        session.execute(
            text(
                """
                insert into learning.xingce_commands (
                  user_id, command_id, command_type, target_id,
                  request_fingerprint, response_payload, created_at
                ) values (
                  cast(:user_id as uuid), :command_id, 'cause_decision',
                  cast(:target_id as uuid), repeat('a', 64), '{}'::jsonb, :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "target_id": str(current["mastery_id"]),
                "created_at": created_at,
            },
        )
        session.execute(
            text(
                """
                insert into learning.xingce_mastery_state_versions (
                  mastery_version_id, mastery_id, command_id, user_id,
                  version_no, state, verification_stage, due_at, rating,
                  module_name, scheduler_card, scheduler_review_log,
                  scheduler_parameters, scheduler_algorithm_version,
                  scheduler_policy_version, used_question_ids,
                  source_review_id, source_review_version_id, source_session_id,
                  evidence_refs, supersedes_mastery_version_id, created_at
                ) values (
                  cast(:version_id as uuid), cast(:mastery_id as uuid), :command_id,
                  cast(:user_id as uuid), :version_no, :state, :stage, :due_at, 3,
                  :module_name, cast(:scheduler_card as jsonb),
                  '{"outcome":"test-verification"}'::jsonb,
                  cast(:scheduler_parameters as jsonb), :algorithm_version,
                  :policy_version, cast(:used_question_ids as jsonb),
                  cast(:source_review_id as uuid),
                  cast(:source_review_version_id as uuid),
                  cast(:source_session_id as uuid), cast(:evidence_refs as jsonb),
                  cast(:supersedes as uuid), :created_at
                )
                """
            ),
            {
                "version_id": mastery_version_id,
                "mastery_id": str(current["mastery_id"]),
                "command_id": command_id,
                "user_id": user_id,
                "version_no": int(current["version_no"]) + 1,
                "state": state,
                "stage": stage,
                "due_at": due_at,
                "module_name": current["module_name"],
                "scheduler_card": json.dumps(current["scheduler_card"]),
                "scheduler_parameters": json.dumps(current["scheduler_parameters"]),
                "algorithm_version": current["scheduler_algorithm_version"],
                "policy_version": current["scheduler_policy_version"],
                "used_question_ids": json.dumps(current["used_question_ids"]),
                "source_review_id": str(current["source_review_id"]),
                "source_review_version_id": str(current["source_review_version_id"]),
                "source_session_id": str(current["source_session_id"]),
                "evidence_refs": json.dumps(current["evidence_refs"]),
                "supersedes": str(current["mastery_version_id"]),
                "created_at": created_at,
            },
        )
    return mastery_version_id


def _insert_unresolved_matching_cause(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    created_at: datetime,
) -> str:
    review_id = str(uuid4())
    review_version_id = str(uuid4())
    reflection_version_id = str(uuid4())
    candidate_version_id = str(uuid4())
    decision_version_id = str(uuid4())
    confirmed_cause_version_id = str(uuid4())
    confirmed_cause_id = str(uuid4())
    mastery_id = str(uuid4())
    mastery_version_id = str(uuid4())
    command_id = f"cause-verification-second-{uuid4()}"
    reflection_payload = {
        "rawText": "第二次独立作答仍出现相同审题问题。",
        "tags": ["审题"],
    }
    with factory.begin() as session:
        source = session.execute(
            text(
                """
                select cause.*, mastery.module_name, mastery.scheduler_card,
                       mastery.scheduler_parameters,
                       mastery.scheduler_algorithm_version,
                       mastery.scheduler_policy_version,
                       mastery.used_question_ids, mastery.evidence_refs as mastery_evidence_refs
                from learning.xingce_confirmed_cause_versions cause
                join learning.xingce_mastery_state_versions mastery
                  on mastery.user_id = cause.user_id
                 and mastery.source_review_id = cause.review_id
                 and mastery.version_no = 1
                where cause.user_id = cast(:user_id as uuid)
                order by cause.created_at
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        session.execute(
            text(
                """
                insert into learning.xingce_commands (
                  user_id, command_id, command_type, target_id,
                  request_fingerprint, response_payload, created_at
                ) values (
                  cast(:user_id as uuid), :command_id, 'cause_decision',
                  cast(:session_id as uuid), repeat('b', 64), '{}'::jsonb, :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "session_id": str(source["session_id"]),
                "created_at": created_at,
            },
        )
        session.execute(
            text(
                """
                insert into learning.xingce_reflection_versions (
                  reflection_version_id, command_id, user_id, review_id, session_id,
                  version_no, payload, payload_sha256,
                  supersedes_reflection_version_id, created_at
                ) values (
                  cast(:reflection_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), 1, cast(:payload as jsonb), repeat('c', 64),
                  null, :created_at
                )
                """
            ),
            {
                "reflection_version_id": reflection_version_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": review_id,
                "session_id": str(source["session_id"]),
                "payload": json.dumps(reflection_payload, ensure_ascii=False),
                "created_at": created_at,
            },
        )
        session.execute(
            text(
                """
                insert into learning.xingce_cause_candidate_versions (
                  candidate_version_id, command_id, user_id, review_id, session_id,
                  version_no, state, reason_code, charged, input_version_refs,
                  created_at, agent_run_id, payload, output_fingerprint,
                  entitlement_policy_version, supersedes_candidate_version_id
                ) values (
                  cast(:candidate_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), 1, 'unavailable',
                  'scenario_not_activated', false, '["test:independent-instance"]'::jsonb,
                  :created_at, null, '{}'::jsonb, null, 'legacy-unavailable@v1', null
                )
                """
            ),
            {
                "candidate_version_id": candidate_version_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": review_id,
                "session_id": str(source["session_id"]),
                "created_at": created_at,
            },
        )
        session.execute(
            text(
                """
                insert into learning.xingce_cause_decision_versions (
                  decision_version_id, command_id, user_id, review_id, session_id,
                  version_no, decision, replacement_cause,
                  supersedes_decision_version_id, created_at, candidate_version_id,
                  confirmed_cause_version_id
                ) values (
                  cast(:decision_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), 1, 'confirm', null, null, :created_at,
                  cast(:candidate_version_id as uuid),
                  cast(:confirmed_cause_version_id as uuid)
                )
                """
            ),
            {
                "decision_version_id": decision_version_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": review_id,
                "session_id": str(source["session_id"]),
                "candidate_version_id": candidate_version_id,
                "confirmed_cause_version_id": confirmed_cause_version_id,
                "created_at": created_at,
            },
        )
        session.execute(
            text(
                """
                insert into learning.xingce_confirmed_cause_versions (
                  confirmed_cause_version_id, confirmed_cause_id, command_id, user_id,
                  review_id, session_id, decision_version_id, candidate_version_id,
                  version_no, source_kind, cause_code, cause_text, evidence_refs,
                  supersedes_confirmed_cause_version_id, created_at, replacement_code
                ) values (
                  cast(:confirmed_cause_version_id as uuid),
                  cast(:confirmed_cause_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), cast(:decision_version_id as uuid),
                  cast(:candidate_version_id as uuid), 1, 'ai_confirmed',
                  :cause_code, :cause_text, cast(:evidence_refs as jsonb),
                  null, :created_at, null
                )
                """
            ),
            {
                "confirmed_cause_version_id": confirmed_cause_version_id,
                "confirmed_cause_id": confirmed_cause_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": review_id,
                "session_id": str(source["session_id"]),
                "decision_version_id": decision_version_id,
                "candidate_version_id": candidate_version_id,
                "cause_code": source["cause_code"],
                "cause_text": source["cause_text"],
                "evidence_refs": json.dumps(source["evidence_refs"]),
                "created_at": created_at,
            },
        )
        session.execute(
            text(
                """
                insert into learning.xingce_attempt_review_versions (
                  review_version_id, command_id, review_id, user_id, session_id,
                  revision, state, requires_cause_decision, reflection_version_id,
                  cause_candidate_version_id, cause_decision_version_id,
                  supersedes_review_version_id, created_at
                ) values (
                  cast(:review_version_id as uuid), :command_id,
                  cast(:review_id as uuid), cast(:user_id as uuid),
                  cast(:session_id as uuid), 1, 'immediate_verification_due', true,
                  cast(:reflection_version_id as uuid),
                  cast(:candidate_version_id as uuid),
                  cast(:decision_version_id as uuid), null, :created_at
                )
                """
            ),
            {
                "review_version_id": review_version_id,
                "command_id": command_id,
                "review_id": review_id,
                "user_id": user_id,
                "session_id": str(source["session_id"]),
                "reflection_version_id": reflection_version_id,
                "candidate_version_id": candidate_version_id,
                "decision_version_id": decision_version_id,
                "created_at": created_at,
            },
        )
        session.execute(
            text(
                """
                insert into learning.xingce_mastery_state_versions (
                  mastery_version_id, mastery_id, command_id, user_id,
                  version_no, state, verification_stage, due_at, rating,
                  module_name, scheduler_card, scheduler_review_log,
                  scheduler_parameters, scheduler_algorithm_version,
                  scheduler_policy_version, used_question_ids, source_review_id,
                  source_review_version_id, source_session_id, evidence_refs,
                  supersedes_mastery_version_id, created_at
                ) values (
                  cast(:mastery_version_id as uuid), cast(:mastery_id as uuid),
                  :command_id, cast(:user_id as uuid), 1, 'reflected', 'immediate',
                  :due_at, null, :module_name, cast(:scheduler_card as jsonb), null,
                  cast(:scheduler_parameters as jsonb), :algorithm_version,
                  :policy_version, cast(:used_question_ids as jsonb),
                  cast(:review_id as uuid), cast(:review_version_id as uuid),
                  cast(:session_id as uuid), cast(:evidence_refs as jsonb),
                  null, :created_at
                )
                """
            ),
            {
                "mastery_version_id": mastery_version_id,
                "mastery_id": mastery_id,
                "command_id": command_id,
                "user_id": user_id,
                "due_at": created_at + timedelta(minutes=5),
                "module_name": source["module_name"],
                "scheduler_card": json.dumps(source["scheduler_card"]),
                "scheduler_parameters": json.dumps(source["scheduler_parameters"]),
                "algorithm_version": source["scheduler_algorithm_version"],
                "policy_version": source["scheduler_policy_version"],
                "used_question_ids": json.dumps(source["used_question_ids"]),
                "review_id": review_id,
                "review_version_id": review_version_id,
                "session_id": str(source["session_id"]),
                "evidence_refs": json.dumps(source["mastery_evidence_refs"]),
                "created_at": created_at,
            },
        )
    return mastery_id
