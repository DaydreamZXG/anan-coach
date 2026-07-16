from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_xingce_cause_agent_workflow import (
    SequenceProvider,
    _reflect,
    _run_one,
)
from tests.test_xingce_planning_closure import _submitted_attempt


def test_cause_agent_is_included_not_reserved_and_each_logical_round_costs_once(
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
    assert candidate["quotaState"] == "not_metered"
    assert candidate["entitlementPolicyVersion"] == (
        "included-with-canonical-practice@v1"
    )
    assert "charged" not in candidate

    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from commerce.feature_usage_reservations "
                "where feature in ('xingce_cause_analysis', 'xingce_cause_agent')"
            )
        ).scalar_one() == 0
        ledger = session.execute(
            text(
                "select status, feature, operation, input_tokens, output_tokens, "
                "total_cost_cny from ai_audit.model_call_ledger"
            )
        ).mappings().one()
        assert ledger["status"] == "succeeded"
        assert ledger["feature"] == "xingce_cause_analysis"
        assert ledger["operation"] == "cause_candidate_generate"
        assert ledger["input_tokens"] == 180
        assert ledger["output_tokens"] == 45
        assert ledger["total_cost_cny"] > 0
        assert session.execute(
            text("select count(*) from learning.xingce_cause_candidate_versions")
        ).scalar_one() == 1


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
    assert view["review"]["aiCandidate"]["quotaState"] == "not_metered"
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from platform.background_jobs")
        ).scalar_one() == 0
        assert session.execute(
            text("select count(*) from ai_audit.model_call_ledger")
        ).scalar_one() == 0
        run = session.execute(
            text(
                "select state, model_call_count, tool_call_count "
                "from ai_audit.agent_run_versions"
            )
        ).mappings().one()
        assert dict(run) == {
            "state": "evidence_insufficient",
            "model_call_count": 0,
            "tool_call_count": 0,
        }


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
