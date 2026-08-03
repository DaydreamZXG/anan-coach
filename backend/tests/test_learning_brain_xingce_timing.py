from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from app.domains.learning_brain.repository import LearningBrainRepository
from tests.position_release_helpers import seed_active_position_release
from tests.task_editing_helpers import claim_task_edit_headers
from tests.test_planning_routes import (
    _client,
    _seed_active_route_day_preference,
)
from tests.test_xingce_learning_routes import _xingce_task
from tests.test_xingce_result_semantics import _answer, _started_session


def test_real_nested_timing_evidence_populates_average_and_is_user_isolated(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, first = _started_session(
        db_session_factory,
        token="brain-timing-owner-one",
    )
    first_user_id = _session_owner(db_session_factory, first["sessionId"])
    _submit_timed_attempt(
        client,
        auth,
        first,
        elapsed_seconds=(12, 24, 36),
        command_suffix="owner-one",
    )
    _seed_active_position_release(db_session_factory)
    profile_response = client.get("/api/v2/learning-brain/profile", headers=auth)
    assert profile_response.status_code == 200, profile_response.text
    profile_modules = profile_response.json()["data"]["modules"]
    first_module = next(item for item in profile_modules if item["track"] == "xingce")
    assert first_module["averageSeconds"] == 24

    second_user_id = _seed_user_for_existing_catalog(
        db_session_factory,
        token="brain-timing-owner-two",
    )
    second_client = _client(db_session_factory)
    second_auth = {"Authorization": "Bearer brain-timing-owner-two"}
    second = _start_session(second_client, second_auth)
    _submit_timed_attempt(
        second_client,
        second_auth,
        second,
        elapsed_seconds=(80, 100, 120),
        command_suffix="owner-two",
    )

    with db_session_factory() as session:
        snapshots = list(
            session.execute(
                text(
                    """
                    select result_snapshot
                    from learning.xingce_assessments
                    where user_id = cast(:user_id as uuid)
                    order by question_id
                    """
                ),
                {"user_id": first_user_id},
            ).scalars()
        )
        assert [item["timingEvidence"]["elapsedSeconds"] for item in snapshots] == [
            12,
            24,
            36,
            12,
            24,
            36,
        ]
        assert all("questionElapsedSeconds" not in item for item in snapshots)

        first_modules = LearningBrainRepository(session).xingce_modules(user_id=first_user_id)
        second_modules = LearningBrainRepository(session).xingce_modules(user_id=second_user_id)

    assert len(first_modules) == 1
    assert first_modules[0]["average_seconds"] == 24
    assert len(second_modules) == 1
    assert second_modules[0]["average_seconds"] == 100


def test_missing_and_invalid_nested_timing_does_not_fabricate_an_average(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, started = _started_session(
        db_session_factory,
        token="brain-timing-invalid",
    )
    user_id = _session_owner(db_session_factory, started["sessionId"])
    checkpoint = _checkpoint_answers(
        client,
        auth,
        started,
        elapsed_seconds=(10, 20, 30),
        command_suffix="invalid",
    )
    _insert_assessments(
        db_session_factory,
        started=started,
        user_id=user_id,
        session_revision=checkpoint["revision"],
        result_snapshots=(
            {
                "resultCode": "correct",
                "questionElapsedSeconds": 10,
            },
            {
                "resultCode": "correct",
                "timingEvidence": {"elapsedSeconds": "20"},
            },
            {
                "resultCode": "correct",
                "timingEvidence": {"elapsedSeconds": 86_401},
            },
        )
        * 2,
    )

    with db_session_factory() as session:
        modules = LearningBrainRepository(session).xingce_modules(user_id=user_id)

    assert len(modules) == 1
    assert modules[0]["attempt_count"] == 6
    assert modules[0]["average_seconds"] is None


def _start_session(client: TestClient, auth: dict[str, str]) -> dict:
    task = _xingce_task(client, auth)
    response = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": f"brain-timing-start-{uuid4()}"},
        json={"planTaskId": task["id"]},
    )
    assert response.status_code == 200, response.text
    started = response.json()["data"]
    auth.update(
        claim_task_edit_headers(
            client,
            auth,
            resource_type="xingce_task_session",
            resource_id=started["sessionId"],
            revision=started["revision"],
        )
    )
    return started


def _checkpoint_answers(
    client: TestClient,
    auth: dict[str, str],
    started: dict,
    *,
    elapsed_seconds: tuple[int, int, int],
    command_suffix: str,
) -> dict:
    questions = started["bundle"]["questions"]
    expanded_elapsed_seconds = tuple(
        elapsed_seconds[index % len(elapsed_seconds)] for index in range(len(questions))
    )
    response = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={
            **auth,
            "Idempotency-Key": f"brain-timing-checkpoint-{command_suffix}",
        },
        json={
            "expectedRevision": started["revision"],
            "currentQuestionId": questions[-1]["questionId"],
            "answers": [
                _answer(question["questionId"], "A", "high", elapsed)
                for question, elapsed in zip(
                    questions,
                    expanded_elapsed_seconds,
                    strict=True,
                )
            ],
            "totalElapsedSeconds": sum(expanded_elapsed_seconds),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _submit_timed_attempt(
    client: TestClient,
    auth: dict[str, str],
    started: dict,
    *,
    elapsed_seconds: tuple[int, int, int],
    command_suffix: str,
) -> None:
    questions = started["bundle"]["questions"]
    expanded_elapsed_seconds = tuple(
        elapsed_seconds[index % len(elapsed_seconds)] for index in range(len(questions))
    )
    checkpoint = _checkpoint_answers(
        client,
        auth,
        started,
        elapsed_seconds=elapsed_seconds,
        command_suffix=command_suffix,
    )
    response = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": f"brain-timing-submit-{command_suffix}"},
        json={
            "expectedRevision": checkpoint["revision"],
            "totalElapsedSeconds": sum(expanded_elapsed_seconds),
        },
    )
    assert response.status_code == 200, response.text


def _seed_user_for_existing_catalog(
    factory: sessionmaker[Session],
    *,
    token: str,
) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '计时隔离学习者')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid),
                  :token_hash, 'brain-timing-device', :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id, daily_minutes, study_mode, onboarding_complete,
                  profile_version
                ) values (
                  cast(:user_id as uuid), 45, 'parttime', true, 1
                )
                """
            ),
            {"user_id": user_id},
        )
        _seed_active_route_day_preference(session, user_id=user_id)
    return user_id


def _seed_active_position_release(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with factory.begin() as session:
        seed_active_position_release(
            session,
            activated_at=now,
            database_now=now,
            rule_set_version="brain-timing-rules",
            position_data_version="brain-timing-positions",
        )


def _session_owner(
    factory: sessionmaker[Session],
    session_id: str,
) -> str:
    with factory() as session:
        return str(
            session.execute(
                text(
                    """
                    select user_id
                    from learning.xingce_task_sessions
                    where session_id = cast(:session_id as uuid)
                    """
                ),
                {"session_id": session_id},
            ).scalar_one()
        )


def _insert_assessments(
    factory: sessionmaker[Session],
    *,
    started: dict,
    user_id: str,
    session_revision: int,
    result_snapshots: tuple[dict, dict, dict],
) -> None:
    session_id = started["sessionId"]
    command_id = f"brain-timing-fixture-{uuid4()}"
    attempt_group_id = str(uuid4())
    content_versions = {
        question["questionId"]: question["contentVersionId"]
        for question in started["bundle"]["questions"]
    }
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.xingce_commands (
                  user_id, command_id, command_type, target_id,
                  request_fingerprint, response_payload, created_at
                ) values (
                  cast(:user_id as uuid), :command_id, 'attempt_submit',
                  cast(:session_id as uuid), :request_fingerprint,
                  '{}'::jsonb, :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "session_id": session_id,
                "request_fingerprint": sha256(command_id.encode()).hexdigest(),
                "created_at": datetime.now(UTC),
            },
        )
        answers = list(
            session.execute(
                text(
                    """
                    select answer_version_id, question_id
                    from learning.xingce_answer_versions
                    where user_id = cast(:user_id as uuid)
                      and session_id = cast(:session_id as uuid)
                    order by question_id
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).mappings()
        )
        assert len(answers) == len(result_snapshots)
        for index, (answer, result_snapshot) in enumerate(
            zip(answers, result_snapshots, strict=True),
            start=1,
        ):
            question_id = str(answer["question_id"])
            attempt_record_id = str(uuid4())
            session.execute(
                text(
                    """
                    insert into learning.xingce_attempt_records (
                      attempt_record_id, attempt_group_id, item_attempt_id,
                      submit_command_id, user_id, session_id, question_id,
                      session_revision, answer_version_id, content_version_id,
                      scoring_policy_version, timing_evidence_ref, timing_evidence,
                      submission_hash, source_lineage_refs, created_at
                    ) values (
                      cast(:attempt_record_id as uuid),
                      cast(:attempt_group_id as uuid), :item_attempt_id,
                      :command_id, cast(:user_id as uuid),
                      cast(:session_id as uuid), :question_id, :session_revision,
                      cast(:answer_version_id as uuid), :content_version_id,
                      'xingce-objective-exact-match-v1', :timing_evidence_ref,
                      '{}'::jsonb, :submission_hash,
                      '["plan:test","bundle:test","content:test"]'::jsonb,
                      :created_at
                    )
                    """
                ),
                {
                    "attempt_record_id": attempt_record_id,
                    "attempt_group_id": attempt_group_id,
                    "item_attempt_id": sha256(f"{command_id}:item:{index}".encode()).hexdigest(),
                    "command_id": command_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "session_revision": session_revision,
                    "answer_version_id": str(answer["answer_version_id"]),
                    "content_version_id": content_versions[question_id],
                    "timing_evidence_ref": sha256(
                        f"{command_id}:timing:{index}".encode()
                    ).hexdigest(),
                    "submission_hash": sha256(
                        f"{command_id}:submission:{index}".encode()
                    ).hexdigest(),
                    "created_at": datetime.now(UTC),
                },
            )
            session.execute(
                text(
                    """
                    insert into learning.xingce_assessments (
                      assessment_id, attempt_record_id, submit_command_id,
                      user_id, session_id, question_id, answer_version_id,
                      content_version_id, scoring_policy_version,
                      assessment_key_hash, result_code, correct,
                      result_snapshot, created_at
                    ) values (
                      cast(:assessment_id as uuid),
                      cast(:attempt_record_id as uuid), :command_id,
                      cast(:user_id as uuid), cast(:session_id as uuid),
                      :question_id, cast(:answer_version_id as uuid),
                      :content_version_id, 'xingce-objective-exact-match-v1',
                      :assessment_key_hash, 'correct', true,
                      cast(:result_snapshot as jsonb), :created_at
                    )
                    """
                ),
                {
                    "assessment_id": str(uuid4()),
                    "attempt_record_id": attempt_record_id,
                    "command_id": command_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "answer_version_id": str(answer["answer_version_id"]),
                    "content_version_id": content_versions[question_id],
                    "assessment_key_hash": sha256(
                        f"{command_id}:assessment:{index}".encode()
                    ).hexdigest(),
                    "result_snapshot": json.dumps(result_snapshot),
                    "created_at": datetime.now(UTC),
                },
            )
