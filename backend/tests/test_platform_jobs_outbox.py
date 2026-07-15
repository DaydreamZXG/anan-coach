from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.platform.jobs import BackgroundJobRepository, JobContractError
from app.platform.outbox import OutboxContractError, OutboxRepository


def test_outbox_append_is_transactional_and_idempotent(
    db_session_factory: sessionmaker[Session],
) -> None:
    repository = OutboxRepository()
    with db_session_factory() as session:
        first = repository.append(
            session,
            deduplication_key="attempt-1:recorded",
            aggregate_type="question_attempt",
            aggregate_id="attempt-1",
            event_type="AttemptRecorded",
            payload={"questionId": "q1", "revision": 1},
        )
        duplicate = repository.append(
            session,
            deduplication_key="attempt-1:recorded",
            aggregate_type="question_attempt",
            aggregate_id="attempt-1",
            event_type="AttemptRecorded",
            payload={"questionId": "q1", "revision": 1},
        )
        assert duplicate.event_id == first.event_id
        session.rollback()

    with db_session_factory() as session:
        count = session.execute(text("select count(*) from platform.outbox_events")).scalar_one()
        assert count == 0


def test_outbox_rejects_deduplication_key_reuse_for_different_event(
    db_session: Session,
) -> None:
    repository = OutboxRepository()
    repository.append(
        db_session,
        deduplication_key="attempt-2:recorded",
        aggregate_type="question_attempt",
        aggregate_id="attempt-2",
        event_type="AttemptRecorded",
        payload={"revision": 1},
    )

    with pytest.raises(OutboxContractError):
        repository.append(
            db_session,
            deduplication_key="attempt-2:recorded",
            aggregate_type="question_attempt",
            aggregate_id="attempt-2",
            event_type="AttemptRecorded",
            payload={"revision": 2},
        )


def test_outbox_claim_publish_and_stale_claim_protection(db_session: Session) -> None:
    repository = OutboxRepository()
    repository.append(
        db_session,
        deduplication_key="reflection-1:confirmed",
        aggregate_type="reflection",
        aggregate_id="reflection-1",
        event_type="ReflectionConfirmed",
        payload={"candidateId": "candidate-1"},
    )
    claimed = repository.claim_next(db_session)

    assert claimed is not None
    assert claimed.status == "publishing"
    assert claimed.attempt_count == 1
    assert claimed.claim_token is not None

    published = repository.published(
        db_session,
        event_id=claimed.event_id,
        claim_token=claimed.claim_token,
    )
    assert published.status == "published"
    with pytest.raises(OutboxContractError):
        repository.published(
            db_session,
            event_id=claimed.event_id,
            claim_token=claimed.claim_token,
        )


def test_background_job_key_is_idempotent_and_input_is_immutable(
    db_session: Session,
) -> None:
    repository = BackgroundJobRepository()
    first = repository.enqueue(
        db_session,
        job_key="essay-review:attempt-1:v1",
        job_type="essay_review",
        input_version="attempt-1:v1",
        payload={"attemptId": "attempt-1"},
        max_attempts=2,
    )
    duplicate = repository.enqueue(
        db_session,
        job_key="essay-review:attempt-1:v1",
        job_type="essay_review",
        input_version="attempt-1:v1",
        payload={"attemptId": "attempt-1"},
        max_attempts=2,
    )
    assert duplicate.job_id == first.job_id

    with pytest.raises(JobContractError):
        repository.enqueue(
            db_session,
            job_key="essay-review:attempt-1:v1",
            job_type="essay_review",
            input_version="attempt-1:v2",
            payload={"attemptId": "attempt-1"},
        )


def test_background_job_claim_retry_and_terminal_failure(db_session: Session) -> None:
    repository = BackgroundJobRepository()
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    repository.enqueue(
        db_session,
        job_key="ocr:report-1:v1",
        job_type="external_report_ocr",
        input_version="report-1:v1",
        payload={"reportId": "report-1"},
        max_attempts=2,
        run_after=now,
    )

    first_claim = repository.claim_next(
        db_session,
        job_types=("external_report_ocr",),
        now=now,
    )
    assert first_claim is not None and first_claim.claim_token is not None
    queued = repository.fail(
        db_session,
        job_id=first_claim.job_id,
        claim_token=first_claim.claim_token,
        error_code="provider_timeout",
        error_message="OCR provider timed out",
        retryable=True,
        retry_after=timedelta(seconds=10),
        now=now,
    )
    assert queued.status == "queued"
    assert repository.claim_next(db_session, now=now + timedelta(seconds=5)) is None

    second_claim = repository.claim_next(db_session, now=now + timedelta(seconds=10))
    assert second_claim is not None and second_claim.claim_token is not None
    failed = repository.fail(
        db_session,
        job_id=second_claim.job_id,
        claim_token=second_claim.claim_token,
        error_code="provider_timeout",
        error_message="OCR provider timed out again",
        retryable=True,
        now=now + timedelta(seconds=10),
    )
    assert failed.status == "failed"
    assert failed.attempt_count == 2


def test_background_job_success_requires_active_claim(db_session: Session) -> None:
    repository = BackgroundJobRepository()
    repository.enqueue(
        db_session,
        job_key="profile-rebuild:user-1:v4",
        job_type="learning_profile_rebuild",
        input_version="user-1:v4",
        payload={"userId": "user-1", "evidenceVersion": 4},
    )
    claimed = repository.claim_next(db_session)
    assert claimed is not None and claimed.claim_token is not None

    succeeded = repository.succeed(
        db_session,
        job_id=claimed.job_id,
        claim_token=claimed.claim_token,
        result={"profileVersion": 5},
    )
    assert succeeded.status == "succeeded"
    assert succeeded.result == {"profileVersion": 5}
    with pytest.raises(JobContractError):
        repository.succeed(
            db_session,
            job_id=claimed.job_id,
            claim_token=claimed.claim_token,
            result={"profileVersion": 6},
        )
