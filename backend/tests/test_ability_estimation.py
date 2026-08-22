from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.ability_estimation.schemas import ReportedScoreEvidenceRequest
from app.domains.ability_estimation.service import (
    AbilityEstimateContractError,
    AbilityEstimateService,
)
from app.domains.auth.repository import token_hash_for_tests
from app.main import create_app


TOKEN = "ability-estimation-token"
NOW = datetime(2026, 7, 13, 8, tzinfo=UTC)


def test_reported_score_creates_complete_version_and_is_immutable_idempotent(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    client = _client(db_session_factory)
    payload = {
        "reportedContext": "full_mock",
        "xingceScore": 65,
        "shenlunScore": 55,
        "timedStatus": "timed",
        "occurredAt": "2026-07-12T08:00:00Z",
    }
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Idempotency-Key": "reported-score-1",
    }

    first = client.post("/api/v2/score-evidence/reported", headers=headers, json=payload)
    repeated = client.post("/api/v2/score-evidence/reported", headers=headers, json=payload)

    assert first.status_code == 200
    assert repeated.status_code == 200
    result = first.json()["data"]
    assert result["evidence"]["totalScore"] == 120.0
    assert result["estimate"]["status"] == "complete"
    assert result["estimate"]["total"] == {
        "lower": 102.0,
        "recommended": 120.0,
        "upper": 138.0,
    }
    assert result["estimate"]["confidence"] == "low"
    assert result["estimate"]["evidenceSummary"]["missingComponents"] == []
    assert repeated.json()["data"]["evidence"]["evidenceId"] == result["evidence"]["evidenceId"]
    assert (
        repeated.json()["data"]["estimate"]["estimateVersionId"]
        == result["estimate"]["estimateVersionId"]
    )

    changed = client.post(
        "/api/v2/score-evidence/reported",
        headers=headers,
        json={**payload, "xingceScore": 70},
    )
    assert changed.status_code == 409

    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from learning.score_evidence")).scalar_one() == 1
        )
        assert (
            session.execute(
                text("select count(*) from learning.ability_estimate_versions")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where event_type = 'ability_estimate.changed'"
                )
            ).scalar_one()
            == 1
        )


def test_partial_components_become_one_versioned_total_estimate(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    client = _client(db_session_factory)
    common = {
        "Authorization": f"Bearer {TOKEN}",
        "Idempotency-Key": "partial-xingce-1",
    }
    first = client.post(
        "/api/v2/score-evidence/reported",
        headers=common,
        json={
            "reportedContext": "historical_estimate",
            "xingceScore": 62,
            "timedStatus": "unknown",
            "occurredAt": "2026-07-10T08:00:00Z",
        },
    )
    assert first.status_code == 200
    first_estimate = first.json()["data"]["estimate"]
    assert first_estimate["status"] == "partial"
    assert first_estimate["total"] is None
    assert first_estimate["evidenceSummary"]["missingComponents"] == ["shenlun"]

    second = client.post(
        "/api/v2/score-evidence/reported",
        headers={**common, "Idempotency-Key": "partial-shenlun-1"},
        json={
            "reportedContext": "historical_estimate",
            "shenlunScore": 56,
            "timedStatus": "unknown",
            "occurredAt": "2026-07-11T08:00:00Z",
        },
    )
    assert second.status_code == 200
    estimate = second.json()["data"]["estimate"]
    assert estimate["status"] == "complete"
    assert estimate["total"]["recommended"] == 118.0
    assert estimate["supersedesEstimateVersionId"] == first_estimate["estimateVersionId"]
    assert estimate["evidenceSummary"]["evidenceCount"] == 2

    current = client.get(
        "/api/v2/ability-estimates/current",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert current.status_code == 200
    assert current.json()["data"]["readiness"] == "complete"
    assert current.json()["data"]["missingComponents"] == []
    assert current.json()["data"]["estimate"]["estimateVersionId"] == estimate["estimateVersionId"]


def test_score_correction_excludes_superseded_evidence_without_mutating_history(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    client = _client(db_session_factory)
    initial = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "correction-original-1",
        },
        json={
            "reportedContext": "official_exam",
            "xingceScore": 60,
            "shenlunScore": 50,
            "timedStatus": "timed",
            "occurredAt": "2026-07-01T08:00:00Z",
        },
    ).json()["data"]

    corrected = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "correction-revised-1",
        },
        json={
            "reportedContext": "official_exam",
            "xingceScore": 66,
            "shenlunScore": 54,
            "timedStatus": "timed",
            "occurredAt": "2026-07-01T08:00:00Z",
            "supersedesEvidenceId": initial["evidence"]["evidenceId"],
        },
    )
    assert corrected.status_code == 200
    result = corrected.json()["data"]
    assert result["estimate"]["total"]["recommended"] == 120.0
    assert result["estimate"]["evidenceSummary"]["evidenceCount"] == 1
    assert result["estimate"]["sourceEvidenceIds"] == [result["evidence"]["evidenceId"]]

    with db_session_factory() as session:
        history = session.execute(
            text(
                "select xingce_score, shenlun_score from learning.score_evidence "
                "order by created_at, evidence_id"
            )
        ).all()
    assert [(float(row[0]), float(row[1])) for row in history] == [(60.0, 50.0), (66.0, 54.0)]


def test_current_estimate_is_explicitly_unavailable_without_evidence(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    response = _client(db_session_factory).get(
        "/api/v2/ability-estimates/current",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "estimate": None,
        "readiness": "unavailable",
        "missingComponents": ["xingce", "shenlun"],
    }


def test_unverified_single_question_ai_review_never_becomes_score_or_gap_evidence(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(db_session_factory, token=TOKEN)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.essay_attempts (
                  attempt_id, learner_key, task_id, original_answer,
                  review_payload, created_at, updated_at
                ) values (
                  'legacy-ai-training-score', :learner_key, 'single-summary-question',
                  '单题答案',
                  cast(:review_payload as jsonb),
                  :recorded_at, :recorded_at
                )
                """
            ),
            {
                "learner_key": user_id,
                "review_payload": json.dumps(
                    {
                        "dimensions": [{"name": "要点完整度", "score": 5}],
                        "totalScore": 99,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "recorded_at": NOW,
            },
        )

    current = _client(db_session_factory).get(
        "/api/v2/ability-estimates/current",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert current.status_code == 200
    assert current.json()["data"] == {
        "estimate": None,
        "readiness": "unavailable",
        "missingComponents": ["xingce", "shenlun"],
    }
    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from learning.score_evidence")).scalar_one() == 0
        )
        assert (
            session.execute(
                text("select count(*) from learning.ability_estimate_versions")
            ).scalar_one()
            == 0
        )


def test_reported_score_endpoint_rejects_ai_review_as_an_evidence_source(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    response = _client(db_session_factory).post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "reject-ai-review-source",
        },
        json={
            "sourceType": "ai_review",
            "reportedContext": "historical_estimate",
            "shenlunScore": 99,
            "timedStatus": "unknown",
            "occurredAt": "2026-07-12T08:00:00Z",
        },
    )

    assert response.status_code == 422


def test_reported_score_rejects_naive_and_future_occurrence_times(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    client = _client(db_session_factory)
    base = {
        "reportedContext": "full_mock",
        "xingceScore": 60,
        "timedStatus": "timed",
    }
    naive = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "naive-score-time",
        },
        json={**base, "occurredAt": "2026-07-12T08:00:00"},
    )
    future = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "future-score-time",
        },
        json={**base, "occurredAt": "2099-07-12T08:00:00Z"},
    )

    assert naive.status_code == 422
    assert future.status_code == 409


def test_conflicting_scores_widen_the_component_and_lower_confidence(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    client = _client(db_session_factory)
    first = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "conflict-score-1",
        },
        json={
            "reportedContext": "full_mock",
            "xingceScore": 40,
            "shenlunScore": 52,
            "timedStatus": "timed",
            "occurredAt": "2026-07-10T08:00:00Z",
        },
    )
    assert first.status_code == 200
    second = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "conflict-score-2",
        },
        json={
            "reportedContext": "full_mock",
            "xingceScore": 80,
            "timedStatus": "timed",
            "occurredAt": "2026-07-11T08:00:00Z",
        },
    )

    assert second.status_code == 200
    estimate = second.json()["data"]["estimate"]
    assert estimate["confidence"] == "low"
    assert estimate["evidenceSummary"]["conflictComponents"] == ["xingce"]
    assert estimate["xingce"]["upper"] - estimate["xingce"]["lower"] == pytest.approx(40.0)


def test_score_evidence_cannot_supersede_another_learner(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    client = _client(db_session_factory)
    original = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "owner-score-1",
        },
        json={
            "reportedContext": "historical_estimate",
            "xingceScore": 60,
            "timedStatus": "unknown",
            "occurredAt": "2026-07-10T08:00:00Z",
        },
    ).json()["data"]["evidence"]
    _seed_user(db_session_factory, token="other-ability-token")

    response = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": "Bearer other-ability-token",
            "Idempotency-Key": "cross-owner-score-1",
        },
        json={
            "reportedContext": "historical_estimate",
            "xingceScore": 65,
            "timedStatus": "unknown",
            "occurredAt": "2026-07-10T08:00:00Z",
            "supersedesEvidenceId": original["evidenceId"],
        },
    )

    assert response.status_code == 409


def test_concurrent_evidence_updates_serialize_into_one_complete_latest_input_set(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(db_session_factory, token=TOKEN)

    def record(command_id: str, component: str) -> None:
        report = ReportedScoreEvidenceRequest(
            reported_context="full_mock",
            xingce_score=64 if component == "xingce" else None,
            shenlun_score=56 if component == "shenlun" else None,
            timed_status="timed",
            occurred_at=NOW - timedelta(days=1),
        )
        with db_session_factory.begin() as session:
            AbilityEstimateService().record_reported_score(
                session,
                user_id=user_id,
                command_id=command_id,
                report=report,
                now=NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(record, "concurrent-xingce", "xingce"),
            executor.submit(record, "concurrent-shenlun", "shenlun"),
        ]
        for future in futures:
            future.result()

    with db_session_factory() as session:
        latest = (
            session.execute(
                text(
                    """
                select status, source_evidence_ids, evidence_summary
                from learning.ability_estimate_versions
                where user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1
                    from learning.ability_estimate_versions successor
                    where successor.supersedes_estimate_version_id =
                          ability_estimate_versions.estimate_version_id
                  )
                limit 1
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
    assert latest["status"] == "complete"
    assert len(latest["source_evidence_ids"]) == 2
    assert latest["evidence_summary"]["evidenceCount"] == 2


def test_database_rejects_inconsistent_reported_total(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(db_session_factory, token=TOKEN)

    with pytest.raises(IntegrityError):
        with db_session_factory.begin() as session:
            session.execute(
                text(
                    """
                    insert into learning.score_evidence (
                      evidence_id, command_id, user_id, exam_code, exam_year,
                      source_type, reported_context, xingce_score, shenlun_score,
                      total_score, timed_status, verification_status, occurred_at,
                      command_payload
                    ) values (
                      :evidence_id, 'invalid-total', :user_id, 'guokao', 2026,
                      'self_report', 'full_mock', 60, 50, 999,
                      'timed', 'user_confirmed', :occurred_at, '{}'::jsonb
                    )
                    """
                ),
                {
                    "evidence_id": str(uuid4()),
                    "user_id": user_id,
                    "occurred_at": NOW,
                },
            )


def test_database_requires_total_when_both_reported_components_are_present(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(db_session_factory, token=TOKEN)

    with pytest.raises(IntegrityError):
        with db_session_factory.begin() as session:
            session.execute(
                text(
                    """
                    insert into learning.score_evidence (
                      evidence_id, command_id, user_id, exam_code, exam_year,
                      source_type, reported_context, xingce_score, shenlun_score,
                      total_score, timed_status, verification_status, occurred_at,
                      command_payload
                    ) values (
                      :evidence_id, 'missing-total', :user_id, 'guokao', 2026,
                      'self_report', 'full_mock', 60, 50, null,
                      'timed', 'user_confirmed', :occurred_at, '{}'::jsonb
                    )
                    """
                ),
                {
                    "evidence_id": str(uuid4()),
                    "user_id": user_id,
                    "occurred_at": NOW,
                },
            )


def test_database_requires_complete_ability_range_when_recommended_is_present(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(db_session_factory, token=TOKEN)

    with pytest.raises(IntegrityError):
        with db_session_factory.begin() as session:
            session.execute(
                text(
                    """
                    insert into learning.ability_estimate_versions (
                      estimate_version_id, user_id, exam_code, exam_year, status,
                      xingce_lower, xingce_recommended, xingce_upper,
                      confidence, source_evidence_ids, evidence_summary,
                      algorithm_version, input_fingerprint, generated_at, valid_until
                    ) values (
                      :estimate_id, :user_id, 'guokao', 2026, 'partial',
                      null, 60, 70,
                      'low', '[]'::jsonb, '{}'::jsonb,
                      'ability-estimate-v1', :fingerprint, :generated_at, :valid_until
                    )
                    """
                ),
                {
                    "estimate_id": str(uuid4()),
                    "user_id": user_id,
                    "fingerprint": "a" * 64,
                    "generated_at": NOW,
                    "valid_until": NOW + timedelta(days=1),
                },
            )


def test_database_rejects_cross_owner_score_evidence_supersedes_chain(
    db_session_factory: sessionmaker[Session],
) -> None:
    first_user_id = _seed_user_and_release(db_session_factory, token=TOKEN)
    second_user_id = _seed_user(
        db_session_factory,
        token="ability-other-owner-db-token",
    )
    predecessor_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.score_evidence (
                  evidence_id, command_id, user_id, exam_code, exam_year,
                  source_type, reported_context, xingce_score, shenlun_score,
                  total_score, timed_status, verification_status, occurred_at,
                  command_payload
                ) values (
                  :evidence_id, 'db-owner-predecessor', :user_id, 'guokao', 2026,
                  'self_report', 'historical_estimate', 60, null, null,
                  'unknown', 'user_confirmed', :occurred_at, '{}'::jsonb
                )
                """
            ),
            {
                "evidence_id": predecessor_id,
                "user_id": first_user_id,
                "occurred_at": NOW,
            },
        )

    with pytest.raises(IntegrityError):
        with db_session_factory.begin() as session:
            session.execute(
                text(
                    """
                    insert into learning.score_evidence (
                      evidence_id, command_id, user_id, exam_code, exam_year,
                      source_type, reported_context, xingce_score, shenlun_score,
                      total_score, timed_status, verification_status, occurred_at,
                      supersedes_evidence_id, command_payload
                    ) values (
                      :evidence_id, 'db-owner-invalid-successor', :user_id,
                      'guokao', 2026, 'self_report', 'historical_estimate',
                      65, null, null, 'unknown', 'user_confirmed', :occurred_at,
                      :supersedes_evidence_id, '{}'::jsonb
                    )
                    """
                ),
                {
                    "evidence_id": str(uuid4()),
                    "user_id": second_user_id,
                    "occurred_at": NOW,
                    "supersedes_evidence_id": predecessor_id,
                },
            )


def test_database_rejects_cross_owner_ability_estimate_supersedes_chain(
    db_session_factory: sessionmaker[Session],
) -> None:
    first_user_id = _seed_user_and_release(db_session_factory, token=TOKEN)
    second_user_id = _seed_user(
        db_session_factory,
        token="ability-other-estimate-db-token",
    )
    predecessor_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.ability_estimate_versions (
                  estimate_version_id, user_id, exam_code, exam_year, status,
                  xingce_lower, xingce_recommended, xingce_upper,
                  confidence, source_evidence_ids, evidence_summary,
                  algorithm_version, input_fingerprint, generated_at, valid_until
                ) values (
                  :estimate_id, :user_id, 'guokao', 2026, 'partial',
                  50, 60, 70, 'low', '[]'::jsonb, '{}'::jsonb,
                  'ability-estimate-v1', :fingerprint, :generated_at, :valid_until
                )
                """
            ),
            {
                "estimate_id": predecessor_id,
                "user_id": first_user_id,
                "fingerprint": "a" * 64,
                "generated_at": NOW,
                "valid_until": NOW + timedelta(days=1),
            },
        )

    with pytest.raises(IntegrityError):
        with db_session_factory.begin() as session:
            session.execute(
                text(
                    """
                    insert into learning.ability_estimate_versions (
                      estimate_version_id, user_id, exam_code, exam_year, status,
                      xingce_lower, xingce_recommended, xingce_upper,
                      confidence, source_evidence_ids, evidence_summary,
                      algorithm_version, input_fingerprint,
                      supersedes_estimate_version_id, generated_at, valid_until
                    ) values (
                      :estimate_id, :user_id, 'guokao', 2026, 'partial',
                      51, 61, 71, 'low', '[]'::jsonb, '{}'::jsonb,
                      'ability-estimate-v1', :fingerprint, :supersedes_id,
                      :generated_at, :valid_until
                    )
                    """
                ),
                {
                    "estimate_id": str(uuid4()),
                    "user_id": second_user_id,
                    "fingerprint": "b" * 64,
                    "supersedes_id": predecessor_id,
                    "generated_at": NOW,
                    "valid_until": NOW + timedelta(days=1),
                },
            )


def test_old_reported_evidence_is_rejected_and_current_estimate_can_be_stale(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(db_session_factory, token=TOKEN)
    service = AbilityEstimateService()
    old_report = ReportedScoreEvidenceRequest(
        reported_context="full_mock",
        xingce_score=60,
        shenlun_score=50,
        timed_status="timed",
        occurred_at=NOW - timedelta(days=366),
    )
    with pytest.raises(AbilityEstimateContractError, match="newer than 365 days"):
        with db_session_factory.begin() as session:
            service.record_reported_score(
                session,
                user_id=user_id,
                command_id="expired-evidence",
                report=old_report,
                now=NOW,
            )

    recent_report = ReportedScoreEvidenceRequest(
        reported_context="full_mock",
        xingce_score=62,
        shenlun_score=54,
        timed_status="timed",
        occurred_at=NOW - timedelta(days=364),
    )
    with db_session_factory.begin() as session:
        recorded = service.record_reported_score(
            session,
            user_id=user_id,
            command_id="soon-stale-evidence",
            report=recent_report,
            now=NOW,
        )
    assert recorded.estimate.valid_until == NOW + timedelta(days=1)

    with db_session_factory() as session:
        current = service.get_current(
            session,
            user_id=user_id,
            now=NOW + timedelta(days=2),
        )
    assert current.readiness == "stale"
    assert current.estimate is not None
    assert current.estimate.estimate_version_id == recorded.estimate.estimate_version_id
    assert current.missing_components == []


def test_idempotent_replay_uses_original_exam_release_after_active_year_changes(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    client = _client(db_session_factory)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Idempotency-Key": "release-switch-replay",
    }
    occurred_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    payload = {
        "reportedContext": "full_mock",
        "xingceScore": 64,
        "shenlunScore": 55,
        "timedStatus": "timed",
        "occurredAt": occurred_at,
    }
    original = client.post(
        "/api/v2/score-evidence/reported",
        headers=headers,
        json=payload,
    )
    assert original.status_code == 200
    original_data = original.json()["data"]
    assert original_data["evidence"]["examYear"] == 2026

    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'retired'
                where exam_code = 'guokao' and exam_year = 2026
                """
            )
        )
        _insert_active_release(
            session,
            exam_year=2027,
            rule_set_version="rules-v2",
            position_data_version="positions-v2",
            activated_at=NOW + timedelta(days=1),
        )

    replay = client.post(
        "/api/v2/score-evidence/reported",
        headers=headers,
        json=payload,
    )

    assert replay.status_code == 200
    replay_data = replay.json()["data"]
    assert replay_data["evidence"]["evidenceId"] == original_data["evidence"]["evidenceId"]
    assert replay_data["evidence"]["examYear"] == 2026
    assert (
        replay_data["estimate"]["estimateVersionId"]
        == original_data["estimate"]["estimateVersionId"]
    )
    with db_session_factory() as session:
        count = session.execute(text("select count(*) from learning.score_evidence")).scalar_one()
    assert count == 1


def test_score_evidence_history_exposes_immutable_correction_chain(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user_and_release(db_session_factory, token=TOKEN)
    client = _client(db_session_factory)
    authorization = {"Authorization": f"Bearer {TOKEN}"}
    original = client.post(
        "/api/v2/score-evidence/reported",
        headers={**authorization, "Idempotency-Key": "history-original"},
        json={
            "reportedContext": "full_mock",
            "xingceScore": 61,
            "shenlunScore": 52,
            "timedStatus": "timed",
            "occurredAt": "2026-07-12T08:00:00Z",
        },
    ).json()["data"]["evidence"]
    corrected = client.post(
        "/api/v2/score-evidence/reported",
        headers={**authorization, "Idempotency-Key": "history-corrected"},
        json={
            "reportedContext": "full_mock",
            "xingceScore": 63,
            "shenlunScore": 53,
            "timedStatus": "timed",
            "occurredAt": "2026-07-12T08:00:00Z",
            "supersedesEvidenceId": original["evidenceId"],
        },
    ).json()["data"]["evidence"]

    response = client.get("/api/v2/score-evidence", headers=authorization)

    assert response.status_code == 200
    items = {item["evidenceId"]: item for item in response.json()["data"]["items"]}
    assert set(items) == {original["evidenceId"], corrected["evidenceId"]}
    assert items[original["evidenceId"]]["active"] is False
    assert items[original["evidenceId"]]["supersededByEvidenceId"] == corrected["evidenceId"]
    assert items[corrected["evidenceId"]]["active"] is True
    assert items[corrected["evidenceId"]]["supersedesEvidenceId"] == original["evidenceId"]
    assert items[corrected["evidenceId"]]["reportedContext"] == "full_mock"


def test_new_component_does_not_reuse_expired_other_component(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(db_session_factory, token=TOKEN)
    service = AbilityEstimateService()
    with db_session_factory.begin() as session:
        first = service.record_reported_score(
            session,
            user_id=user_id,
            command_id="expiring-shenlun",
            report=ReportedScoreEvidenceRequest(
                reported_context="full_mock",
                shenlun_score=53,
                timed_status="timed",
                occurred_at=NOW,
            ),
            now=NOW,
        )
    assert first.estimate.status == "partial"
    assert first.estimate.shenlun is not None

    later = NOW + timedelta(days=366)
    with db_session_factory.begin() as session:
        second = service.record_reported_score(
            session,
            user_id=user_id,
            command_id="fresh-xingce-after-expiry",
            report=ReportedScoreEvidenceRequest(
                reported_context="full_mock",
                xingce_score=65,
                timed_status="timed",
                occurred_at=later - timedelta(days=1),
            ),
            now=later,
        )

    assert second.estimate.status == "partial"
    assert second.estimate.xingce is not None
    assert second.estimate.shenlun is None
    assert second.estimate.total is None
    assert second.estimate.source_evidence_ids == [second.evidence.evidence_id]
    with db_session_factory() as session:
        current = service.get_current(session, user_id=user_id, now=later)
    assert current.readiness == "partial"
    assert current.missing_components == ["shenlun"]


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    return TestClient(app)


def _seed_user_and_release(factory: sessionmaker[Session], *, token: str) -> str:
    user_id = _seed_user(factory, token=token)
    with factory.begin() as session:
        _insert_active_release(
            session,
            exam_year=2026,
            rule_set_version="rules-v1",
            position_data_version="positions-v1",
            activated_at=NOW,
        )
    return user_id


def _insert_active_release(
    session: Session,
    *,
    exam_year: int,
    rule_set_version: str,
    position_data_version: str,
    activated_at: datetime,
) -> None:
    release_id = str(uuid4())
    session.execute(
        text(
            """
            insert into product.position_rule_releases (
              release_id, exam_code, exam_year, rule_set_version,
              position_data_version, status, activated_at
            ) values (
              cast(:release_id as uuid), 'guokao', :exam_year,
              :rule_set_version, :position_data_version, 'draft', null
            )
            """
        ),
        {
            "release_id": release_id,
            "exam_year": exam_year,
            "rule_set_version": rule_set_version,
            "position_data_version": position_data_version,
        },
    )
    session.execute(
        text(
            """
            insert into product.position_release_windows (
              window_id, release_id, window_kind, sequence_no,
              opens_at, closes_at, official_source_url,
              official_source_title, official_source_published_at,
              official_source_received_at, official_source_sha256,
              official_source_receipt
            ) values (
              cast(:window_id as uuid), cast(:release_id as uuid),
              'primary', 1, :opens_at, :closes_at,
              'https://example.gov.cn/guokao/ability-fixture',
              '能力估计测试岗位发布窗口', :published_at, :received_at,
              :source_sha256, 'ability-estimation-fixture'
            )
            """
        ),
        {
            "window_id": str(uuid4()),
            "release_id": release_id,
            "opens_at": activated_at - timedelta(days=30),
            "closes_at": activated_at + timedelta(days=730),
            "published_at": activated_at - timedelta(days=40),
            "received_at": activated_at - timedelta(days=35),
            "source_sha256": "a" * 64,
        },
    )
    session.execute(
        text(
            """
            update product.position_rule_releases
            set status = 'active', activated_at = :activated_at
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": release_id, "activated_at": activated_at},
    )


def _seed_user(factory: sessionmaker[Session], *, token: str) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
            {"id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  :session_id, :user_id, :token_hash, 'wechat-ability-test', :expires_at
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
    return user_id
