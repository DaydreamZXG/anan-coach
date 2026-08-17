from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.xingce_learning.cause_agent.provider import CauseAgentProviderError
from tests.work_packages.real_device_cause_recovery_033.helpers import (
    official_provider,
    prepare_pending_run,
    primary_provider,
    relay_provider,
    worker,
)


def test_safe_failures_use_all_routes_and_nonrecoverable_4xx_stops(
    db_session_factory: sessionmaker[Session],
) -> None:
    primary = primary_provider(
        error=CauseAgentProviderError(
            "provider_http_504",
            "gateway timed out",
            retryable=False,
            failover_safe=True,
        )
    )
    official = official_provider(
        error=CauseAgentProviderError(
            "provider_transport_uncertain",
            "network unavailable",
            retryable=False,
            failover_safe=True,
        )
    )
    relay = relay_provider()
    job_id = prepare_pending_run(
        db_session_factory,
        token="cause-route-failure-token",
    )

    with db_session_factory() as session:
        completed = worker((primary, official, relay)).process_next(session)

    assert completed is not None and completed.status == "succeeded"
    assert [len(route.calls) for route in (primary, official, relay)] == [1, 1, 1]
    assert len(set(primary.calls + official.calls + relay.calls)) == 3
    with db_session_factory() as session:
        ledgers = session.execute(
            text(
                "select provider, model, status, call_key "
                "from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid) order by started_at"
            ),
            {"job_id": str(job_id)},
        ).mappings().all()
        assert [row["status"] for row in ledgers] == ["failed", "failed", "succeeded"]
        assert [row["model"] for row in ledgers] == [
            "gemini-3.5-flash",
            "deepseek-v4-pro",
            "deepseek-v4-flash-free",
        ]
        assert session.execute(
            text(
                "select used_count from commerce.entitlement_usage_daily usage "
                "join commerce.feature_usage_reservations reservation "
                "on reservation.user_id = usage.user_id "
                "and reservation.feature = usage.feature "
                "and reservation.usage_date = usage.usage_date "
                "where reservation.job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(job_id)},
        ).scalar_one() == 1


def test_nonrecoverable_4xx_does_not_dispatch_fallback(
    db_session_factory: sessionmaker[Session],
) -> None:
    blocked_primary = primary_provider(
        error=CauseAgentProviderError(
            "provider_http_401",
            "request unauthorized",
            retryable=False,
            failover_safe=False,
        )
    )
    blocked_fallback = official_provider()
    prepare_pending_run(
        db_session_factory,
        token="cause-route-nonrecoverable-token",
    )
    with db_session_factory() as session:
        blocked = worker((blocked_primary, blocked_fallback)).process_next(session)
    assert blocked is not None and blocked.status == "failed"
    assert len(blocked_primary.calls) == 1
    assert blocked_fallback.calls == []
