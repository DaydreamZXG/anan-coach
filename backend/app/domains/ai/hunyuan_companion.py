from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any

from httpx import NetworkError, Request
from pydantic import ValidationError

from app.core.config import Settings
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.ai.provider import (
    AICompanionAnswerResult,
    AIProvider,
    AIProviderConfigurationError,
    AIProviderResponseError,
    CompanionProviderAnswer,
)
from app.domains.ai.provider_contract import parse_provider_json_object
from app.domains.essay_intelligence.hunyuan_cloud_function import (
    HunyuanCloudFunctionClient,
    HunyuanCloudFunctionConfigurationError,
    HunyuanCloudFunctionError,
    HunyuanCloudFunctionSettings,
    validate_hunyuan_cloud_function_configuration,
)


class HunyuanCompanionProvider(AIProvider):
    """Companion provider backed by the server-owned CloudBase Hunyuan function."""

    def __init__(
        self,
        settings: Settings,
        *,
        hunyuan_settings: HunyuanCloudFunctionSettings | None = None,
        transport: HunyuanCloudFunctionClient | None = None,
    ) -> None:
        super().__init__(settings)
        self._hunyuan_settings = (
            hunyuan_settings or HunyuanCloudFunctionSettings.from_companion_environment()
        )
        try:
            self._transport = transport or HunyuanCloudFunctionClient(self._hunyuan_settings)
        except HunyuanCloudFunctionConfigurationError as exc:
            raise AIProviderConfigurationError(str(exc)) from exc

    @property
    def provider_name(self) -> str:
        return f"cloudbase-hunyuan@{self._hunyuan_settings.release_id.strip()}"

    @property
    def release_id(self) -> str:
        return self._hunyuan_settings.release_id.strip()

    @property
    def model(self) -> str:
        return self._hunyuan_settings.model.strip()

    def model_for_tier(self, tier: str) -> str:
        if tier not in {"small", "high"}:
            raise AIProviderConfigurationError("Unsupported Companion model tier")
        return self.model

    def validate_configuration(self, *, idempotency_key: str) -> None:
        if not idempotency_key.strip():
            raise AIProviderConfigurationError(
                "A stable provider idempotency key is required for Companion"
            )
        try:
            validate_hunyuan_cloud_function_configuration(self._hunyuan_settings)
        except HunyuanCloudFunctionConfigurationError as exc:
            raise AIProviderConfigurationError(str(exc)) from exc

    def prepare_companion_question(
        self,
        *,
        route_decision: dict[str, Any],
        question_text: str,
        context: dict[str, Any],
        sources: list[dict[str, Any]],
        persona: str | None = None,
    ) -> dict[str, Any]:
        prepared = super().prepare_companion_question(
            route_decision=route_decision,
            question_text=question_text,
            context=context,
            sources=sources,
            persona=persona,
        )
        prepared["providerReleaseId"] = self._hunyuan_settings.release_id.strip()
        prepared["providerEndpointFingerprint"] = endpoint_fingerprint(self._transport.endpoint)
        prepared["providerReplayRetentionSeconds"] = 0
        prepared["model"] = self.model
        body = prepared["body"]
        assert isinstance(body, dict)
        body["model"] = self.model
        return prepared

    def validate_prepared_companion_question(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str | None,
    ) -> None:
        if not idempotency_key or not idempotency_key.strip():
            raise AIProviderConfigurationError(
                "A stable provider idempotency key is required for Companion"
            )
        expected = {
            "providerReleaseId": self._hunyuan_settings.release_id.strip(),
            "providerEndpointFingerprint": endpoint_fingerprint(self._transport.endpoint),
            "providerReplayRetentionSeconds": 0,
            "model": self.model,
        }
        if any(prepared.get(key) != value for key, value in expected.items()):
            raise AIProviderConfigurationError(
                "Stored Companion request targets another Hunyuan release"
            )
        body = prepared.get("body")
        messages = body.get("messages") if isinstance(body, dict) else None
        if (
            not isinstance(messages, list)
            or len(messages) != 2
            or any(not isinstance(message, dict) for message in messages)
            or [message.get("role") for message in messages] != ["system", "user"]
            or any(not isinstance(message.get("content"), str) for message in messages)
        ):
            raise AIProviderConfigurationError(
                "Stored Companion request has an invalid Hunyuan message contract"
            )

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
        assert idempotency_key is not None
        body = prepared["body"]
        assert isinstance(body, dict)
        messages = body["messages"]
        assert isinstance(messages, list)
        request_id = "companion-" + sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
        try:
            result = self._transport.answer_companion(
                request_id=request_id,
                messages=messages,
            )
        except HunyuanCloudFunctionError as exc:
            if exc.retryable:
                raise NetworkError(
                    "CloudBase Hunyuan Companion request is uncertain",
                    request=Request("POST", self._transport.endpoint),
                ) from exc
            raise AIProviderResponseError(
                "CloudBase Hunyuan returned an unusable Companion response",
                code=exc.code,
            ) from exc
        try:
            parsed = CompanionProviderAnswer.model_validate(parse_provider_json_object(result.text))
        except (ValidationError, ValueError, RecursionError) as exc:
            raise AIProviderResponseError(
                "CloudBase Hunyuan returned invalid Companion content",
                code="provider_content_invalid",
                latency_ms=result.latency_ms,
                output_fingerprint=result.output_fingerprint,
                provider_request_id_header="X-Request-Id",
                provider_request_id_fingerprint=sha256(
                    result.request_id.encode("utf-8")
                ).hexdigest(),
            ) from exc
        return AICompanionAnswerResult(
            answer=parsed.answer,
            next_actions=parsed.next_actions,
            source_refs=parsed.source_refs,
            confidence=parsed.confidence,
            limitations=parsed.limitations,
            latency_ms=result.latency_ms,
            response_summary=result.text[:500],
            usage=ModelTokenUsage(
                input_tokens=result.usage.prompt_tokens,
                cached_input_tokens=0,
                output_tokens=result.usage.completion_tokens,
            ),
            output_fingerprint=result.output_fingerprint,
            provider_request_id_header="X-Request-Id",
            provider_request_id_fingerprint=sha256(result.request_id.encode("utf-8")).hexdigest(),
            requested_actions=tuple(parsed.requested_actions),
        )


def companion_provider(settings: Settings) -> AIProvider:
    transport = settings.ai_companion_transport.strip().casefold()
    if transport == "openai_compatible":
        companion_model = settings.ai_companion_model.strip()
        return AIProvider(
            replace(
                settings,
                ai_provider_base_url=(
                    settings.ai_companion_provider_base_url.strip() or settings.ai_provider_base_url
                ),
                ai_api_key=(settings.ai_companion_api_key.strip() or settings.ai_api_key),
                ai_model=companion_model or settings.ai_model,
                ai_small_model=companion_model or settings.ai_small_model,
                ai_high_capability_model=(companion_model or settings.ai_high_capability_model),
            )
        )
    if transport == "cloudbase_hunyuan":
        return HunyuanCompanionProvider(settings)
    raise AIProviderConfigurationError(
        "AI_COMPANION_TRANSPORT must be openai_compatible or cloudbase_hunyuan"
    )
