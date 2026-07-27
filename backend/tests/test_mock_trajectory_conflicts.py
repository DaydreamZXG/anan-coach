from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.mock_exam.repository import (
    MockExamRepository,
    MockExamTrajectoryWriteConflict,
)
from tests.test_mock_exam_calibration import (
    _make_client,
    _seed_mock_qbank,
    _seed_user,
)


def test_trajectory_conflict_constraints_have_stable_scopes(
    db_session: Session,
) -> None:
    constraint_names = set(
        db_session.execute(
            text(
                """
                select constraint_name
                from information_schema.table_constraints
                where table_schema = 'learner'
                  and table_name = 'mock_exam_trajectory_events'
                  and constraint_type = 'UNIQUE'
                """
            )
        ).scalars()
    )

    assert "mock_exam_trajectory_user_command_key" in constraint_names
    assert "mock_exam_trajectory_session_seq_key" in constraint_names
    assert "mock_exam_trajectory_events_command_id_key" not in constraint_names


def test_trajectory_sequence_conflict_is_replayable_and_reports_server_next_seq(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-trajectory-conflict-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-trajectory-conflict-token"}

    created_response = client.post("/api/v1/mock-exams/weekly", headers=auth)
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    question_id = created["questions"][0]["questionId"]
    event = {
        "eventSeq": 1,
        "eventType": "visited",
        "questionId": question_id,
        "clientOccurredAt": datetime.now(UTC).isoformat(),
        "sessionRevision": created["revision"],
        "payload": {"device": "phone"},
    }
    url = f"/api/v2/mock-exams/{created['sessionId']}/trajectory-events"

    accepted = client.post(
        url,
        headers={**auth, "Idempotency-Key": "trajectory-phone-event-0001"},
        json=event,
    )
    assert accepted.status_code == 200, accepted.text

    replayed = client.post(
        url,
        headers={**auth, "Idempotency-Key": "trajectory-phone-event-0001"},
        json=event,
    )
    assert replayed.status_code == 200, replayed.text
    assert (
        replayed.json()["data"]["trajectoryEventId"]
        == (accepted.json()["data"]["trajectoryEventId"])
    )
    assert replayed.json()["data"]["sessionId"] == accepted.json()["data"]["sessionId"]
    assert replayed.json()["data"]["eventSeq"] == accepted.json()["data"]["eventSeq"]
    assert replayed.json()["data"]["eventType"] == accepted.json()["data"]["eventType"]
    assert datetime.fromisoformat(
        replayed.json()["data"]["acceptedAt"].replace("Z", "+00:00")
    ) == datetime.fromisoformat(accepted.json()["data"]["acceptedAt"].replace("Z", "+00:00"))

    ahead = client.post(
        url,
        headers={**auth, "Idempotency-Key": "trajectory-tablet-event-0003"},
        json={
            **event,
            "eventSeq": 3,
            "payload": {"device": "tablet"},
        },
    )
    assert ahead.status_code == 409, ahead.text
    assert ahead.json()["code"] == "mock_trajectory_sequence_conflict"
    assert ahead.json()["message"] == "作答轨迹已经在其他设备更新，请同步后继续。"
    assert ahead.json()["details"] == {
        "expectedEventSeq": 2,
        "receivedEventSeq": 3,
    }
    assert ahead.json()["requestId"]

    occupied = client.post(
        url,
        headers={**auth, "Idempotency-Key": "trajectory-tablet-event-0001"},
        json={
            **event,
            "payload": {"device": "tablet"},
        },
    )
    assert occupied.status_code == 409, occupied.text
    assert occupied.json()["code"] == "mock_trajectory_sequence_conflict"
    assert occupied.json()["details"] == {
        "expectedEventSeq": 2,
        "receivedEventSeq": 1,
    }

    refreshed = client.get(
        f"/api/v1/mock-exams/{created['sessionId']}",
        headers=auth,
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["nextTrajectoryEventSeq"] == 2

    rebased = client.post(
        url,
        headers={**auth, "Idempotency-Key": "trajectory-tablet-event-0002-rebase"},
        json={
            **event,
            "eventSeq": 2,
            "payload": {"device": "tablet", "rebased": True},
        },
    )
    assert rebased.status_code == 200, rebased.text
    assert rebased.json()["data"]["eventSeq"] == 2


def test_trajectory_command_id_scope_matches_user_scoped_replay_lookup(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-command-scope-user-a")
    _seed_user(db_session_factory, token="mock-command-scope-user-b")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    shared_command_id = "trajectory-shared-across-users-0001"

    for token in ("mock-command-scope-user-a", "mock-command-scope-user-b"):
        auth = {"Authorization": f"Bearer {token}"}
        created_response = client.post("/api/v1/mock-exams/weekly", headers=auth)
        assert created_response.status_code == 200, created_response.text
        created = created_response.json()
        response = client.post(
            f"/api/v2/mock-exams/{created['sessionId']}/trajectory-events",
            headers={**auth, "Idempotency-Key": shared_command_id},
            json={
                "eventSeq": 1,
                "eventType": "visited",
                "questionId": created["questions"][0]["questionId"],
                "clientOccurredAt": datetime.now(UTC).isoformat(),
                "sessionRevision": created["revision"],
                "payload": {"token": token},
            },
        )
        assert response.status_code == 200, response.text


def test_repository_translates_only_named_trajectory_integrity_conflicts(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(
        db_session_factory,
        token="mock-trajectory-repository-conflict-token",
    )
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-trajectory-repository-conflict-token"}
    created_response = client.post("/api/v1/mock-exams/weekly", headers=auth)
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    question_id = created["questions"][0]["questionId"]
    accepted = client.post(
        f"/api/v2/mock-exams/{created['sessionId']}/trajectory-events",
        headers={**auth, "Idempotency-Key": "trajectory-first-event-0001"},
        json={
            "eventSeq": 1,
            "eventType": "visited",
            "questionId": question_id,
            "clientOccurredAt": datetime.now(UTC).isoformat(),
            "sessionRevision": created["revision"],
            "payload": {"device": "phone"},
        },
    )
    assert accepted.status_code == 200, accepted.text

    with db_session_factory() as session:
        repository = MockExamRepository(session)
        with pytest.raises(MockExamTrajectoryWriteConflict) as caught:
            repository.record_trajectory_event(
                trajectory_event_id=str(uuid4()),
                command_id="trajectory-conflicting-sequence-0001",
                user_id=user_id,
                session_id=created["sessionId"],
                event_seq=1,
                event_type="visited",
                question_id=question_id,
                client_occurred_at=datetime.now(UTC),
                session_revision=created["revision"],
                payload={"device": "tablet"},
                request_fingerprint="a" * 64,
                created_at=datetime.now(UTC),
            )

        assert caught.value.constraint_name == "mock_exam_trajectory_session_seq_key"
        assert repository.next_trajectory_event_seq(session_id=created["sessionId"]) == 2
