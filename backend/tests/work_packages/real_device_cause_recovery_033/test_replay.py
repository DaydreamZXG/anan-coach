from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker
from tests.work_packages.real_device_cause_recovery_033.helpers import (
    prepare_pending_run,
    primary_provider,
    worker,
)


class _CrashAfterProviderWorker(CauseAgentWorker):
    crash_once = True

    def _publish_output(self, *args, **kwargs):
        if self.crash_once:
            self.crash_once = False
            raise KeyboardInterrupt("crash after durable provider checkpoint")
        return super()._publish_output(*args, **kwargs)


def test_replay_uses_completed_checkpoint_without_a_second_model_call(
    db_session_factory: sessionmaker[Session],
) -> None:
    provider = primary_provider()
    job_id = prepare_pending_run(
        db_session_factory,
        token="cause-route-replay-token",
    )
    crash_worker = worker(
        (provider,),
        worker_type=_CrashAfterProviderWorker,
    )

    try:
        with db_session_factory() as session:
            crash_worker.process_next(session)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("simulated process death was not raised")

    with db_session_factory.begin() as session:
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
        assert crash_worker.recover_stale_claims(session) == 1
    with db_session_factory() as session:
        completed = crash_worker.process_next(session)

    assert completed is not None and completed.status == "succeeded"
    assert len(provider.calls) == 1
    with db_session_factory() as session:
        ledger_count, distinct_keys = session.execute(
            text(
                "select count(*), count(distinct call_key) "
                "from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(job_id)},
        ).one()
    assert (ledger_count, distinct_keys) == (1, 1)
    assert provider.calls[0].endswith(":route:tokenrhythm")
