from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.model_cost import ModelPriceCard
from app.domains.data_rights.repository import EXPORT_JOB_TYPE
from app.domains.data_rights.worker import DataRightsWorker
from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker
from app.platform.jobs import (
    JOB_LEASE_CONTRACT_VERSION,
    BackgroundJob,
    BackgroundJobRepository,
    JobContractError,
)


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


class _NoopStorage:
    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        del object_ref, operation_key


def test_claim_kill_restart_uses_lease_and_fences_the_old_worker(
    db_session: Session,
) -> None:
    jobs = BackgroundJobRepository()
    queued = jobs.enqueue(
        db_session,
        job_key="lease:data-export:restart",
        job_type=EXPORT_JOB_TYPE,
        input_version="personal-data-export@v1",
        payload={"exportRequestId": str(uuid4()), "userId": str(uuid4())},
        run_after=NOW,
    )
    first = jobs.claim_next(db_session, job_types=(EXPORT_JOB_TYPE,), now=NOW)
    assert first is not None
    assert first.job_id == queued.job_id
    assert first.claim_token is not None
    assert first.claimed_at == NOW
    assert first.lease_expires_at == NOW + timedelta(minutes=15)
    db_session.commit()

    with pytest.raises(JobContractError, match="active claim"):
        jobs.succeed(
            db_session,
            job_id=first.job_id,
            claim_token=first.claim_token,
            result={"state": "too-late"},
            completed_at=NOW + timedelta(minutes=16),
        )

    second = jobs.claim_next(
        db_session,
        job_types=(EXPORT_JOB_TYPE,),
        now=NOW + timedelta(minutes=16),
    )
    assert second is not None and second.claim_token is not None
    assert second.claim_token != first.claim_token
    assert second.attempt_count == 1

    with pytest.raises(JobContractError, match="active claim"):
        jobs.succeed(
            db_session,
            job_id=first.job_id,
            claim_token=first.claim_token,
            result={"state": "stale-worker"},
            completed_at=NOW + timedelta(minutes=16, seconds=1),
        )

    completed = jobs.succeed(
        db_session,
        job_id=second.job_id,
        claim_token=second.claim_token,
        result={"contract": JOB_LEASE_CONTRACT_VERSION},
        completed_at=NOW + timedelta(minutes=16, seconds=1),
    )
    assert completed.status == "succeeded"
    assert completed.claim_token is None
    assert completed.lease_expires_at is None


def test_retry_and_checkpoint_reject_an_expired_token(db_session: Session) -> None:
    jobs = BackgroundJobRepository()
    jobs.enqueue(
        db_session,
        job_key="lease:data-export:expired-transitions",
        job_type=EXPORT_JOB_TYPE,
        input_version="personal-data-export@v1",
        payload={"exportRequestId": str(uuid4()), "userId": str(uuid4())},
        run_after=NOW,
    )
    claimed = jobs.claim_next(db_session, job_types=(EXPORT_JOB_TYPE,), now=NOW)
    assert claimed is not None and claimed.claim_token is not None
    expired_at = NOW + timedelta(minutes=16)

    with pytest.raises(JobContractError, match="checkpoint"):
        jobs.checkpoint(
            db_session,
            job_id=claimed.job_id,
            claim_token=claimed.claim_token,
            result={"phase": "late"},
            updated_at=expired_at,
        )
    with pytest.raises(JobContractError, match="active claim"):
        jobs.fail(
            db_session,
            job_id=claimed.job_id,
            claim_token=claimed.claim_token,
            error_code="late_failure",
            error_message="late",
            retryable=True,
            now=expired_at,
        )


def test_seven_day_data_rights_claim_is_reclaimed_by_worker_poll(
    db_session: Session,
) -> None:
    jobs = BackgroundJobRepository()
    jobs.enqueue(
        db_session,
        job_key="lease:data-export:seven-days",
        job_type=EXPORT_JOB_TYPE,
        input_version="personal-data-export@v1",
        payload={"exportRequestId": str(uuid4()), "userId": str(uuid4())},
        run_after=NOW,
    )
    stale = jobs.claim_next(db_session, job_types=(EXPORT_JOB_TYPE,), now=NOW)
    assert stale is not None and stale.claim_token is not None
    db_session.commit()

    result = DataRightsWorker(
        storage=_NoopStorage(),
        settings=Settings(privacy_export_expiry_seconds=86400),
        jobs=jobs,
        now=lambda: NOW + timedelta(days=7),
    ).process_next(db_session)

    assert result is not None
    assert result.job_id == stale.job_id
    assert result.status == "queued"
    assert result.attempt_count == 1
    with pytest.raises(JobContractError, match="active claim"):
        jobs.succeed(
            db_session,
            job_id=stale.job_id,
            claim_token=stale.claim_token,
            result={"state": "stale-worker"},
            completed_at=NOW + timedelta(days=7, seconds=1),
        )


def test_legacy_running_row_without_lease_is_recovered(db_session: Session) -> None:
    jobs = BackgroundJobRepository()
    queued = jobs.enqueue(
        db_session,
        job_key="lease:data-export:legacy",
        job_type=EXPORT_JOB_TYPE,
        input_version="personal-data-export@v1",
        payload={"exportRequestId": str(uuid4()), "userId": str(uuid4())},
        run_after=NOW,
    )
    legacy_token = uuid4()
    db_session.execute(
        text(
            """
            update platform.background_jobs set
              status = 'running', attempt_count = 1,
              claimed_at = :claimed_at, claim_token = :claim_token,
              lease_expires_at = null
            where job_id = :job_id
            """
        ),
        {
            "job_id": str(queued.job_id),
            "claimed_at": NOW - timedelta(days=7),
            "claim_token": str(legacy_token),
        },
    )
    db_session.commit()

    reclaimed = jobs.claim_next(
        db_session,
        job_types=(EXPORT_JOB_TYPE,),
        now=NOW,
    )

    assert reclaimed is not None and reclaimed.claim_token is not None
    assert reclaimed.claim_token != legacy_token
    assert reclaimed.attempt_count == 1


def test_concurrent_claims_never_share_a_job(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory.begin() as setup:
        BackgroundJobRepository().enqueue(
            setup,
            job_key="lease:data-export:concurrent",
            job_type=EXPORT_JOB_TYPE,
            input_version="personal-data-export@v1",
            payload={"exportRequestId": str(uuid4()), "userId": str(uuid4())},
            run_after=NOW,
        )

    with db_session_factory() as first_session, db_session_factory() as second_session:
        first = BackgroundJobRepository().claim_next(
            first_session,
            job_types=(EXPORT_JOB_TYPE,),
            now=NOW,
        )
        second = BackgroundJobRepository().claim_next(
            second_session,
            job_types=(EXPORT_JOB_TYPE,),
            now=NOW,
        )

        assert first is not None
        assert second is None


class _FencedCauseJobs:
    def __init__(self, *, stale: BackgroundJob, current: BackgroundJob) -> None:
        self.stale = stale
        self.current = current
        self.fail_calls = 0

    def claim_next(self, *_args: object, **_kwargs: object) -> BackgroundJob:
        return self.stale

    def by_id(self, *_args: object, **_kwargs: object) -> BackgroundJob:
        return self.current

    def fail(self, *_args: object, **_kwargs: object) -> BackgroundJob:
        self.fail_calls += 1
        raise AssertionError("a stale cause worker must not fail the replacement claim")


class _ExplodingCauseWorker(CauseAgentWorker):
    def _process_claim(
        self,
        session: Session,
        *,
        job: BackgroundJob,
    ) -> BackgroundJob:
        del session, job
        raise RuntimeError("old worker failed after its lease was replaced")


def test_stale_cause_worker_never_borrows_the_replacement_claim_token(
    db_session: Session,
) -> None:
    job_id = uuid4()
    stale = _background_job(job_id=job_id, claim_token=uuid4())
    current = _background_job(job_id=job_id, claim_token=uuid4())
    jobs = _FencedCauseJobs(stale=stale, current=current)
    worker = _ExplodingCauseWorker(
        settings=Settings(),
        provider=object(),
        price_card=ModelPriceCard(
            version="job-lease-test",
            input_cny_per_million=Decimal("1"),
            cached_input_cny_per_million=Decimal("0"),
            output_cny_per_million=Decimal("1"),
        ),
        jobs=jobs,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="old worker failed"):
        worker.process_next(db_session)

    assert jobs.fail_calls == 0


def _background_job(*, job_id, claim_token) -> BackgroundJob:
    return BackgroundJob(
        job_id=job_id,
        job_key=f"cause:{job_id}",
        job_type="xingce_cause_agent_v1",
        input_version="cause-agent-run@v1",
        payload={},
        status="running",
        attempt_count=1,
        max_attempts=3,
        run_after=NOW,
        claimed_at=NOW,
        claim_token=claim_token,
        lease_expires_at=NOW + timedelta(minutes=15),
        result=None,
        last_error_code=None,
        last_error_message=None,
        created_at=NOW,
        completed_at=None,
    )
