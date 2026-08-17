from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.work_packages.real_device_cause_recovery_033.helpers import (
    job_result,
    official_provider,
    prepare_pending_run,
    primary_provider,
    worker,
)


def test_healthy_primary_does_not_dispatch_fallback(
    db_session_factory: sessionmaker[Session],
) -> None:
    primary = primary_provider()
    fallback = official_provider()
    job_id = prepare_pending_run(
        db_session_factory,
        token="cause-route-happy-token",
    )

    with db_session_factory() as session:
        completed = worker((primary, fallback)).process_next(session)

    assert completed is not None and completed.status == "succeeded"
    assert len(primary.calls) == 1
    assert fallback.calls == []
    assert job_result(db_session_factory, job_id)["providerRoute"] == "tokenrhythm"
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(job_id)},
        ).scalar_one() == 1
