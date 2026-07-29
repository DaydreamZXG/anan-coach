"""Targeted read-only audit probes.

These tests only write to the ephemeral PostgreSQL cluster supplied by
``backend/tests/conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.data_rights.worker import DataRightsWorker
from app.domains.external_ingestion.upload.ports import PrivateObjectStoragePort
from app.platform.jobs import BackgroundJobRepository
from tests.test_review_session_verdict import (
    _make_client,
    _seed_docket_card,
    _seed_user,
)


def test_review_verdict_replay_awards_xp_twice(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="audit-verdict-replay-token")
    docket_id, card_id, _ = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    client = _make_client(db_session_factory)
    url = f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict"
    headers = {"Authorization": "Bearer audit-verdict-replay-token"}
    body = {"selectedAnswer": "B", "elapsedSeconds": 42}

    first = client.post(url, headers=headers, json=body)
    replay = client.post(url, headers=headers, json=body)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["verdictId"] == replay.json()["verdictId"]
    with db_session_factory() as session:
        state = session.execute(
            text(
                """
                select
                  (select count(*) from learner.verdicts
                    where user_id = cast(:user_id as uuid)
                      and card_id = cast(:card_id as uuid)) as verdict_count,
                  (select xp from learner.official_ranks
                    where user_id = cast(:user_id as uuid)) as xp,
                  (select count(*) from learner.rank_events
                    where user_id = cast(:user_id as uuid)
                      and event_type = 'xp_gain') as xp_event_count
                """
            ),
            {"user_id": user_id, "card_id": card_id},
        ).mappings().one()

    assert dict(state) == {
        "verdict_count": 1,
        "xp": 20,
        "xp_event_count": 2,
    }


def test_data_rights_worker_does_not_recover_a_stale_claim(
    db_session_factory: sessionmaker[Session],
) -> None:
    claimed_at = datetime(2026, 7, 1, tzinfo=UTC)
    jobs = BackgroundJobRepository()
    with db_session_factory.begin() as session:
        enqueued = jobs.enqueue(
            session,
            job_key="audit:privacy-export:stale-claim",
            job_type=DataRightsWorker.JOB_TYPES[0],
            input_version="audit-v1",
            payload={
                "exportRequestId": "00000000-0000-0000-0000-000000000901",
                "userId": "00000000-0000-0000-0000-000000000902",
            },
            run_after=claimed_at,
        )
    with db_session_factory() as session:
        claimed = jobs.claim_next(
            session,
            job_types=DataRightsWorker.JOB_TYPES,
            now=claimed_at,
        )
        assert claimed is not None
        assert claimed.job_id == enqueued.job_id
        session.commit()

    worker = DataRightsWorker(
        storage=cast(PrivateObjectStoragePort, object()),
        settings=Settings(),
        jobs=jobs,
        now=lambda: claimed_at + timedelta(days=7),
    )
    with db_session_factory() as session:
        assert worker.process_next(session) is None

    with db_session_factory() as session:
        stale = jobs.by_id(session, job_id=enqueued.job_id)
        assert stale is not None
        assert stale.status == "running"
        assert stale.attempt_count == 1
        assert stale.claim_token is not None
