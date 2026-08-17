from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.model_cost import ModelPriceCard
from app.domains.xingce_learning.cause_agent.contracts import CauseAgentEvidence
from app.domains.xingce_learning.cause_agent.provider import (
    CauseAgentProviderError,
    CauseAgentProviderResult,
)
from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker
from tests.test_xingce_cause_agent_workflow import SequenceProvider, _reflect
from tests.test_xingce_planning_closure import _submitted_attempt


class RouteProvider(SequenceProvider):
    prompt_version = "xingce-cause-prompt@v4"
    schema_version = "cause-candidate@v2"
    idempotency_capability = "nonreplayable_audited"

    def __init__(
        self,
        *,
        route_key: str,
        release_id: str,
        model: str,
        output: str = "candidate_ready",
        error: CauseAgentProviderError | None = None,
    ) -> None:
        super().__init__((output,))
        self.route_key = route_key
        self.provider_release_id = release_id
        self.model = model
        self.base_url = f"https://{route_key}.example.test/v1"
        self.error = error
        self.calls: list[str] = []

    @property
    def provider_name(self) -> str:
        return f"xingce_cause_provider@{self.provider_release_id}"

    def generate(
        self,
        *,
        evidence: CauseAgentEvidence,
        followup_answer: str | None,
        idempotency_key: str,
    ) -> CauseAgentProviderResult:
        self.calls.append(idempotency_key)
        if self.error is not None:
            raise self.error
        return super().generate(
            evidence=evidence,
            followup_answer=followup_answer,
            idempotency_key=idempotency_key,
        )


def primary_provider(*, error: CauseAgentProviderError | None = None) -> RouteProvider:
    return RouteProvider(
        route_key="tokenrhythm",
        release_id="local-unreleased",
        model="gemini-3.5-flash",
        error=error,
    )


def official_provider(*, error: CauseAgentProviderError | None = None) -> RouteProvider:
    return RouteProvider(
        route_key="deepseek_official",
        release_id="deepseek-official-v4-pro",
        model="deepseek-v4-pro",
        error=error,
    )


def relay_provider(*, error: CauseAgentProviderError | None = None) -> RouteProvider:
    return RouteProvider(
        route_key="user_relay",
        release_id="user-relay-v4-flash-free",
        model="deepseek-v4-flash-free",
        error=error,
    )


def worker(
    providers: tuple[RouteProvider, ...],
    *,
    worker_type: type[CauseAgentWorker] = CauseAgentWorker,
) -> CauseAgentWorker:
    return worker_type(
        settings=Settings(),
        providers=providers,
        price_card=ModelPriceCard(
            version="cause-route-recovery-test-v1",
            input_cny_per_million=Decimal("1.00"),
            cached_input_cny_per_million=Decimal("0.10"),
            output_cny_per_million=Decimal("2.00"),
        ),
    )


def prepare_pending_run(
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
                "where job_type = 'xingce_cause_agent_v1' "
                "order by created_at desc limit 1"
            )
        ).scalar_one()


def job_result(factory: sessionmaker[Session], job_id: UUID) -> dict[str, Any]:
    with factory() as session:
        result = session.execute(
            text(
                "select result from platform.background_jobs "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(job_id)},
        ).scalar_one()
    return dict(result or {})
