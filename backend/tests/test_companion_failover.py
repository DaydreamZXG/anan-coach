from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from httpx import HTTPStatusError, Request, Response

from app.core.config import Settings
from app.domains.ai.companion_failover import (
    ArkPlanAutoCompanionProvider,
    CompanionFailoverProvider,
)
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.ai.provider import AICompanionAnswerResult, AIProvider


@dataclass
class _RouteOutcome:
    answer: str | None = None
    status_code: int | None = None


class _StubProvider(AIProvider):
    def __init__(self, name: str, outcome: _RouteOutcome, calls: list[str]) -> None:
        super().__init__(Settings())
        self._name = name
        self._outcome = outcome
        self._calls = calls

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def release_id(self) -> str:
        return f"{self._name}-release"

    @property
    def model(self) -> str:
        return "deepseek-v4-flash"

    def model_for_tier(self, tier: str) -> str:
        assert tier in {"small", "high"}
        return self.model

    def prepare_companion_question(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "promptVersion": "companion-grounded@v7",
            "schemaVersion": "grounded-answer@v3",
            "providerEndpointFingerprint": f"endpoint-{self._name}",
            "providerReplayRetentionSeconds": 0,
            "model": self.model,
            "modelTier": kwargs["route_decision"]["modelTier"],
        }

    def validate_prepared_companion_question(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str | None,
    ) -> None:
        assert prepared["model"] == self.model
        assert idempotency_key

    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str | None,
    ) -> AICompanionAnswerResult:
        self.validate_prepared_companion_question(
            prepared=prepared,
            idempotency_key=idempotency_key,
        )
        self._calls.append(self._name)
        if self._outcome.status_code is not None:
            request = Request("POST", f"https://{self._name}.example/chat/completions")
            response = Response(self._outcome.status_code, request=request)
            raise HTTPStatusError("route failed", request=request, response=response)
        assert self._outcome.answer is not None
        return AICompanionAnswerResult(
            answer=self._outcome.answer,
            source_refs=["source-1"],
            confidence="high",
            limitations=[],
            latency_ms=10,
            response_summary=self._outcome.answer,
            usage=ModelTokenUsage(
                input_tokens=10,
                cached_input_tokens=0,
                output_tokens=5,
            ),
            output_fingerprint=f"output-{self._name}",
        )


def _prepared(provider: CompanionFailoverProvider) -> dict[str, Any]:
    return provider.prepare_companion_question(
        route_decision={"modelTier": "small"},
        question_text="你吃饭了吗",
        context={},
        sources=[{"sourceRef": "source-1"}],
    )


def test_companion_failover_uses_next_real_provider_after_upstream_503() -> None:
    calls: list[str] = []
    primary = _StubProvider("primary", _RouteOutcome(status_code=503), calls)
    ark = _StubProvider("ark", _RouteOutcome(answer="吃过啦，你呢？"), calls)
    official = _StubProvider("official", _RouteOutcome(answer="不该走到这里"), calls)
    provider = CompanionFailoverProvider(Settings(), routes=(primary, ark, official))

    result = provider.answer_prepared_companion_question(
        prepared=_prepared(provider),
        idempotency_key="companion-command-1",
    )

    assert result.answer == "吃过啦，你呢？"
    assert calls == ["primary", "ark"]


def test_companion_failover_does_not_call_fallback_when_primary_succeeds() -> None:
    calls: list[str] = []
    primary = _StubProvider("primary", _RouteOutcome(answer="在呀。"), calls)
    official = _StubProvider("official", _RouteOutcome(answer="不该走到这里"), calls)
    provider = CompanionFailoverProvider(Settings(), routes=(primary, official))

    result = provider.answer_prepared_companion_question(
        prepared=_prepared(provider),
        idempotency_key="companion-command-2",
    )

    assert result.answer == "在呀。"
    assert calls == ["primary"]


def test_ark_plan_auto_alias_accepts_a_concrete_model_release() -> None:
    provider = ArkPlanAutoCompanionProvider(Settings())

    assert provider.accepts_returned_model(
        requested_model="ark-code-latest",
        returned_model="doubao-seed-code-preview-250828",
    )
    assert not provider.accepts_returned_model(
        requested_model="another-model",
        returned_model="doubao-seed-code-preview-250828",
    )
    assert not AIProvider(Settings()).accepts_returned_model(
        requested_model="fixed-model",
        returned_model="another-model",
    )
