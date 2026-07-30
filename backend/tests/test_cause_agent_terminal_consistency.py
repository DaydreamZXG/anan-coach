from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.model_cost import ModelPriceCard
from app.domains.xingce_learning.cause_agent.provider import CauseAgentProviderError
from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker
from app.platform.jobs import BackgroundJobRepository
from tests.test_xingce_cause_agent_workflow import (
    SequenceProvider,
    _reflect,
)
from tests.test_xingce_planning_closure import _submitted_attempt


class _AlwaysUncertainProvider(SequenceProvider):
    def __init__(self) -> None:
        super().__init__(())

    def generate(self, **kwargs):
        del kwargs
        raise CauseAgentProviderError(
            "provider_transport_uncertain",
            "provider outcome remains uncertain",
            retryable=True,
        )


class _CrashOnceProvider(SequenceProvider):
    def __init__(self) -> None:
        super().__init__(("candidate_ready",))
        self._crash = True

    def generate(self, **kwargs):
        if self._crash:
            self._crash = False
            raise KeyboardInterrupt("simulated worker process death")
        return super().generate(**kwargs)


def test_retry_exhaustion_atomically_closes_ledger_refunds_quota_and_terminates_job(
    db_session_factory: sessionmaker[Session],
) -> None:
    worker = _worker(_AlwaysUncertainProvider())
    job_id = _prepare_pending_run(
        db_session_factory,
        token="cause-terminal-exhaustion-token",
    )

    states = _exhaust_retries(db_session_factory, worker)

    assert states == ["queued", "queued", "failed"]
    assert _terminal_state(db_session_factory, job_id) == {
        "job_status": "failed",
        "job_error": "provider_transport_uncertain",
        "job_state": "terminated",
        "run_state": "terminated",
        "run_reason": "provider_transport_uncertain",
        "review_state": "cause_agent_terminated",
        "candidate_state": "terminated",
        "reservation_status": "refunded",
        "daily_used_count": 0,
        "ledger_status": "failed",
        "ledger_error": "provider_transport_uncertain",
    }


def test_terminal_replay_is_idempotent_without_double_refund(
    db_session_factory: sessionmaker[Session],
) -> None:
    worker = _worker(_AlwaysUncertainProvider())
    job_id = _prepare_pending_run(
        db_session_factory,
        token="cause-terminal-replay-token",
    )
    _exhaust_retries(db_session_factory, worker)

    with db_session_factory() as session:
        first = worker.reconcile_terminal_job(session, job_id=job_id)
    with db_session_factory() as session:
        replay = worker.reconcile_terminal_job(session, job_id=job_id)

    assert first.status == replay.status == "failed"
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from commerce.entitlement_audit_logs "
                    "where action = 'quota_refunded'"
                )
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("select count(*) from learning.xingce_cause_candidate_versions")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("select count(*) from ai_audit.agent_run_versions where state = 'terminated'")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select used_count from commerce.entitlement_usage_daily "
                    "where feature = 'xingce_cause_analysis'"
                )
            ).scalar_one()
            == 0
        )


def test_stale_running_call_is_reconciled_after_worker_restart(
    db_session_factory: sessionmaker[Session],
) -> None:
    provider = _CrashOnceProvider()
    worker = _worker(provider)
    job_id = _prepare_pending_run(
        db_session_factory,
        token="cause-terminal-restart-token",
    )

    with pytest.raises(KeyboardInterrupt, match="simulated worker process death"):
        with db_session_factory() as session:
            worker.process_next(session)

    with db_session_factory.begin() as session:
        before = (
            session.execute(
                text(
                    """
                select job.status as job_status, ledger.status as ledger_status,
                       reservation.status as reservation_status
                from platform.background_jobs job
                join ai_audit.model_call_ledger ledger on ledger.job_id = job.job_id
                join commerce.feature_usage_reservations reservation
                  on reservation.job_id = job.job_id
                where job.job_id = cast(:job_id as uuid)
                """
                ),
                {"job_id": str(job_id)},
            )
            .mappings()
            .one()
        )
        assert dict(before) == {
            "job_status": "running",
            "ledger_status": "running",
            "reservation_status": "reserved",
        }
        session.execute(
            text(
                "update platform.background_jobs "
                "set claimed_at = now() - interval '2 seconds', "
                "lease_expires_at = now() - interval '1 second' "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(job_id)},
        )

    with db_session_factory() as session:
        assert worker.recover_stale_claims(session) == 1
    with db_session_factory() as session:
        completed = worker.process_next(session)

    assert completed is not None
    assert completed.status == "succeeded"
    with db_session_factory() as session:
        after = (
            session.execute(
                text(
                    """
                select job.status as job_status, ledger.status as ledger_status,
                       reservation.status as reservation_status,
                       run.state as run_state
                from platform.background_jobs job
                join ai_audit.model_call_ledger ledger on ledger.job_id = job.job_id
                join commerce.feature_usage_reservations reservation
                  on reservation.job_id = job.job_id
                join ai_audit.agent_run_versions run on run.job_id = job.job_id
                 and not exists (
                   select 1 from ai_audit.agent_run_versions successor
                   where successor.supersedes_run_version_id = run.run_version_id
                 )
                where job.job_id = cast(:job_id as uuid)
                """
                ),
                {"job_id": str(job_id)},
            )
            .mappings()
            .one()
        )
    assert dict(after) == {
        "job_status": "succeeded",
        "ledger_status": "succeeded",
        "reservation_status": "consumed",
        "run_state": "candidate_ready",
    }


def test_concurrent_terminal_callbacks_converge_on_one_closed_outcome(
    db_session_factory: sessionmaker[Session],
) -> None:
    worker = _worker(_AlwaysUncertainProvider())
    job_id = _prepare_pending_run(
        db_session_factory,
        token="cause-terminal-race-token",
    )
    for _ in range(2):
        with db_session_factory() as session:
            queued = worker.process_next(session)
        assert queued is not None and queued.status == "queued"
        _make_due(db_session_factory, job_id)

    jobs = BackgroundJobRepository()
    with db_session_factory() as session:
        claimed = jobs.claim_next(
            session,
            job_types=("xingce_cause_agent_v1",),
        )
        assert claimed is not None and claimed.claim_token is not None
        session.commit()

    start = Barrier(2)

    def terminate() -> str:
        callback_worker = _worker(_AlwaysUncertainProvider())
        with db_session_factory() as session:
            start.wait()
            return callback_worker._process_claim(session, job=claimed).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        callback_states = list(executor.map(lambda _: terminate(), range(2)))

    assert callback_states == ["failed", "failed"]
    with db_session_factory() as session:
        counts = (
            session.execute(
                text(
                    """
                select
                  (select count(*) from learning.xingce_cause_candidate_versions)
                    as candidate_count,
                  (select count(*) from ai_audit.agent_run_versions
                    where state = 'terminated') as terminal_run_count,
                  (select count(*) from commerce.entitlement_audit_logs
                    where action = 'quota_refunded') as refund_count,
                  (select count(*) from ai_audit.model_call_ledger
                    where status = 'failed') as failed_ledger_count
                """
                )
            )
            .mappings()
            .one()
        )
    assert dict(counts) == {
        "candidate_count": 1,
        "terminal_run_count": 1,
        "refund_count": 1,
        "failed_ledger_count": 1,
    }


def _worker(provider: SequenceProvider) -> CauseAgentWorker:
    return CauseAgentWorker(
        settings=Settings(),
        provider=provider,
        price_card=ModelPriceCard(
            version="cause-terminal-test-v1",
            input_cny_per_million=Decimal("1.00"),
            cached_input_cny_per_million=Decimal("0.10"),
            output_cny_per_million=Decimal("2.00"),
        ),
    )


def _prepare_pending_run(
    factory: sessionmaker[Session],
    *,
    token: str,
) -> UUID:
    client, auth, _task, submitted = _submitted_attempt(
        factory,
        token=token,
        choices=["D", "B", "C"],
    )
    _reflect(
        client,
        auth,
        submitted["review"],
        key=f"{token}-reflection",
    )
    with factory() as session:
        return session.execute(
            text(
                "select job_id from platform.background_jobs "
                "where job_type = 'xingce_cause_agent_v1'"
            )
        ).scalar_one()


def _exhaust_retries(
    factory: sessionmaker[Session],
    worker: CauseAgentWorker,
) -> list[str]:
    states: list[str] = []
    for attempt in range(3):
        with factory() as session:
            result = worker.process_next(session)
        assert result is not None
        states.append(result.status)
        if attempt < 2:
            _make_due(factory, result.job_id)
    return states


def _make_due(factory: sessionmaker[Session], job_id: UUID) -> None:
    with factory.begin() as session:
        session.execute(
            text(
                "update platform.background_jobs "
                "set run_after = now() - interval '1 second' "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(job_id)},
        )


def _terminal_state(
    factory: sessionmaker[Session],
    job_id: UUID,
) -> dict[str, object]:
    with factory() as session:
        row = (
            session.execute(
                text(
                    """
                select
                  job.status as job_status,
                  job.last_error_code as job_error,
                  job.result ->> 'state' as job_state,
                  run.state as run_state,
                  run.termination_reason as run_reason,
                  review.state as review_state,
                  candidate.state as candidate_state,
                  reservation.status as reservation_status,
                  usage.used_count as daily_used_count,
                  ledger.status as ledger_status,
                  ledger.error_code as ledger_error
                from platform.background_jobs job
                join ai_audit.agent_run_versions run on run.job_id = job.job_id
                 and not exists (
                   select 1 from ai_audit.agent_run_versions successor
                   where successor.supersedes_run_version_id = run.run_version_id
                 )
                join learning.xingce_attempt_review_versions review
                  on review.review_id = run.review_id
                 and not exists (
                   select 1 from learning.xingce_attempt_review_versions successor
                   where successor.supersedes_review_version_id =
                     review.review_version_id
                 )
                join learning.xingce_cause_candidate_versions candidate
                  on candidate.candidate_version_id = run.candidate_version_id
                join commerce.feature_usage_reservations reservation
                  on reservation.job_id = job.job_id
                join commerce.entitlement_usage_daily usage
                  on usage.user_id = reservation.user_id
                 and usage.feature = reservation.feature
                 and usage.usage_date = reservation.usage_date
                join ai_audit.model_call_ledger ledger on ledger.job_id = job.job_id
                where job.job_id = cast(:job_id as uuid)
                """
                ),
                {"job_id": str(job_id)},
            )
            .mappings()
            .one()
        )
    return dict(row)
