from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.xingce_learning.cause_agent.provider import CauseAgentProviderError
from tests.test_xingce_cause_agent_workflow import (
    SequenceProvider,
    _reflect,
    _run_one,
)
from tests.test_xingce_planning_closure import _submitted_attempt


def test_cause_agent_reserves_and_consumes_each_logical_round_once(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-agent-billing-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    first = _reflect(client, auth, review, key="cause-billing-reflection")
    replay = _reflect(client, auth, review, key="cause-billing-reflection")
    assert replay == first

    provider = SequenceProvider(["candidate_ready"])
    _run_one(db_session_factory, provider)
    _run_none(db_session_factory, provider)

    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate = current["review"]["aiCandidate"]
    assert candidate["quotaState"] == "consumed"
    assert candidate["entitlementPolicyVersion"] == ("metered-model-round@v1")
    assert "charged" not in candidate

    with db_session_factory() as session:
        reservation = (
            session.execute(
                text(
                    "select status, feature from commerce.feature_usage_reservations "
                    "where feature = 'xingce_cause_analysis'"
                )
            )
            .mappings()
            .one()
        )
        assert dict(reservation) == {
            "status": "consumed",
            "feature": "xingce_cause_analysis",
        }
        ledger = (
            session.execute(
                text(
                    "select status, feature, operation, input_tokens, output_tokens, "
                    "total_cost_cny from ai_audit.model_call_ledger"
                )
            )
            .mappings()
            .one()
        )
        assert ledger["status"] == "succeeded"
        assert ledger["feature"] == "xingce_cause_analysis"
        assert ledger["operation"] == "cause_candidate_generate"
        assert ledger["input_tokens"] == 180
        assert ledger["output_tokens"] == 45
        assert ledger["total_cost_cny"] > 0
        assert (
            session.execute(
                text("select count(*) from learning.xingce_cause_candidate_versions")
            ).scalar_one()
            == 1
        )


def test_all_correct_high_confidence_skips_provider_and_still_publishes_audit_state(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-agent-no-model-token",
        choices=["A", "B", "C"],
    )
    view = _reflect(
        client,
        auth,
        submitted["review"],
        key="cause-no-model-reflection",
    )

    assert view["state"] == "cause_evidence_insufficient"
    assert view["review"]["aiCandidate"]["state"] == "evidence_insufficient"
    assert view["review"]["aiCandidate"]["reason"] == "all_correct_high_confidence"
    assert view["review"]["aiCandidate"]["quotaState"] == "not_charged"
    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from platform.background_jobs")).scalar_one() == 0
        )
        assert (
            session.execute(text("select count(*) from ai_audit.model_call_ledger")).scalar_one()
            == 0
        )
        run = (
            session.execute(
                text(
                    "select state, model_call_count, tool_call_count "
                    "from ai_audit.agent_run_versions"
                )
            )
            .mappings()
            .one()
        )
        assert dict(run) == {
            "state": "evidence_insufficient",
            "model_call_count": 0,
            "tool_call_count": 0,
        }


def test_definitive_unbilled_provider_failure_refunds_reserved_quota(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-agent-refund-token",
        choices=["D", "B", "C"],
    )
    _reflect(
        client,
        auth,
        submitted["review"],
        key="cause-refund-reflection",
    )
    provider = DefinitiveFailureProvider([])
    _run_one(db_session_factory, provider, expected_status="failed")

    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    assert current["review"]["aiCandidate"]["quotaState"] == "not_charged"
    with db_session_factory() as session:
        reservation = session.execute(
            text(
                "select status from commerce.feature_usage_reservations "
                "where feature = 'xingce_cause_analysis'"
            )
        ).scalar_one()
        assert reservation == "refunded"
        assert (
            session.execute(
                text(
                    "select used_count from commerce.entitlement_usage_daily "
                    "where feature = 'xingce_cause_analysis'"
                )
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(text("select status from ai_audit.model_call_ledger")).scalar_one()
            == "failed"
        )


def test_usage_known_provider_failure_keeps_consumed_quota_when_job_terminates(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-agent-usage-known-failure-token",
        choices=["D", "B", "C"],
    )
    _reflect(
        client,
        auth,
        submitted["review"],
        key="cause-usage-known-failure-reflection",
    )
    provider = UsageKnownFailureProvider([])

    _run_one(
        db_session_factory,
        provider,
        expected_status="failed",
    )

    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    assert current["review"]["aiCandidate"]["quotaState"] == "consumed"
    with db_session_factory() as session:
        terminal = (
            session.execute(
                text(
                    """
                select job.status as job_status,
                       ledger.status as ledger_status,
                       reservation.status as reservation_status,
                       usage.used_count
                from platform.background_jobs job
                join ai_audit.model_call_ledger ledger on ledger.job_id = job.job_id
                join commerce.feature_usage_reservations reservation
                  on reservation.job_id = job.job_id
                join commerce.entitlement_usage_daily usage
                  on usage.user_id = reservation.user_id
                 and usage.feature = reservation.feature
                 and usage.usage_date = reservation.usage_date
                """
                )
            )
            .mappings()
            .one()
        )
    assert dict(terminal) == {
        "job_status": "failed",
        "ledger_status": "succeeded",
        "reservation_status": "consumed",
        "used_count": 1,
    }


class DefinitiveFailureProvider(SequenceProvider):
    def generate(self, **kwargs):
        del kwargs
        raise CauseAgentProviderError(
            "provider_request_rejected",
            "provider rejected before processing",
            retryable=False,
        )


class UsageKnownFailureProvider(SequenceProvider):
    def generate(self, **kwargs):
        del kwargs
        raise CauseAgentProviderError(
            "provider_response_invalid",
            "provider usage is known but the response cannot be accepted",
            retryable=True,
            usage=ModelTokenUsage(
                input_tokens=180,
                cached_input_tokens=20,
                output_tokens=45,
            ),
            output_fingerprint="a" * 64,
            latency_ms=25,
        )


def _run_none(
    factory: sessionmaker[Session],
    provider: SequenceProvider,
) -> None:
    from decimal import Decimal

    from app.core.config import Settings
    from app.domains.ai.model_cost import ModelPriceCard
    from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker

    worker = CauseAgentWorker(
        settings=Settings(),
        provider=provider,
        price_card=ModelPriceCard(
            version="cause-agent-test-v1",
            input_cny_per_million=Decimal("1.00"),
            cached_input_cny_per_million=Decimal("0.10"),
            output_cny_per_million=Decimal("2.00"),
        ),
    )
    with factory() as session:
        assert worker.process_next(session) is None
