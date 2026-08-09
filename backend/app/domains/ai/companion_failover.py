from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any, Iterable

from httpx import HTTPStatusError, NetworkError, TimeoutException

from app.core.config import Settings
from app.domains.ai.provider import (
    AICompanionAnswerResult,
    AIProvider,
    AIProviderConfigurationError,
    AIProviderResponseError,
)
from app.domains.ai.model_cost import ModelTokenUsage


COMPANION_FAILOVER_CONTRACT = "companion-provider-failover@v1"


class CompanionFailoverProvider(AIProvider):
    """One logical Companion provider with ordered, real-model failover.

    Every route receives the same authorized learner context and prepares its
    own immutable provider request.  A route is attempted only after the
    previous real provider failed; no canned/local answer is synthesized.
    """

    def __init__(self, settings: Settings, *, routes: Iterable[AIProvider]) -> None:
        super().__init__(settings)
        self._routes = tuple(routes)
        if len(self._routes) < 2:
            raise AIProviderConfigurationError(
                "Companion failover requires at least two provider routes"
            )

    @property
    def provider_name(self) -> str:
        return f"companion_failover@{self.release_id}"

    @property
    def release_id(self) -> str:
        identity = "\x1f".join(
            f"{route.provider_name}:{route.release_id}:{route.model}" for route in self._routes
        )
        return "v1-" + sha256(identity.encode("utf-8")).hexdigest()[:20]

    @property
    def model(self) -> str:
        return self._routes[0].model

    def model_for_tier(self, tier: str) -> str:
        return self._routes[0].model_for_tier(tier)

    def prepare_companion_question(self, **kwargs: Any) -> dict[str, Any]:
        prepared_routes = [route.prepare_companion_question(**kwargs) for route in self._routes]
        primary = prepared_routes[0]
        route_identity = [
            {
                "provider": route.provider_name,
                "releaseId": route.release_id,
                "model": request["model"],
                "prepared": request,
            }
            for route, request in zip(self._routes, prepared_routes, strict=True)
        ]
        endpoint_identity = "\x1f".join(
            str(request["providerEndpointFingerprint"]) for request in prepared_routes
        )
        return {
            "contractVersion": COMPANION_FAILOVER_CONTRACT,
            "promptVersion": primary["promptVersion"],
            "schemaVersion": primary["schemaVersion"],
            "providerReleaseId": self.release_id,
            "providerEndpointFingerprint": sha256(
                endpoint_identity.encode("utf-8")
            ).hexdigest(),
            # Provider failover makes one fresh query per route.  It never
            # claims that a gateway can safely replay an uncertain request.
            "providerReplayRetentionSeconds": 0,
            "model": primary["model"],
            "modelTier": primary["modelTier"],
            "routes": route_identity,
        }

    def validate_prepared_companion_question(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str | None,
    ) -> None:
        routes = self._validated_routes(prepared)
        if not idempotency_key or not idempotency_key.strip():
            raise AIProviderConfigurationError(
                "A stable provider idempotency key is required for Companion"
            )
        errors: list[AIProviderConfigurationError] = []
        for index, (provider, request) in enumerate(routes, start=1):
            try:
                provider.validate_prepared_companion_question(
                    prepared=request,
                    idempotency_key=_route_idempotency_key(idempotency_key, index),
                )
            except AIProviderConfigurationError as exc:
                errors.append(exc)
        if len(errors) == len(routes):
            raise AIProviderConfigurationError(
                "No configured Companion provider route is available"
            ) from errors[-1]

    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str | None,
    ) -> AICompanionAnswerResult:
        routes = self._validated_routes(prepared)
        if not idempotency_key or not idempotency_key.strip():
            raise AIProviderConfigurationError(
                "A stable provider idempotency key is required for Companion"
            )
        last_error: Exception | None = None
        metered_usage = ModelTokenUsage(
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )
        total_latency_ms = 0
        for index, (provider, request) in enumerate(routes, start=1):
            route_key = _route_idempotency_key(idempotency_key, index)
            try:
                result = provider.answer_prepared_companion_question(
                    prepared=request,
                    idempotency_key=route_key,
                )
                return replace(
                    result,
                    usage=_add_usage(metered_usage, result.usage),
                    latency_ms=total_latency_ms + result.latency_ms,
                )
            except HTTPStatusError as exc:
                # Authentication, balance, throttling and upstream 5xx are all
                # route-local failures.  The next configured real model gets a
                # chance before the user sees an error.
                last_error = exc
            except (
                AIProviderConfigurationError,
                TimeoutException,
                NetworkError,
            ) as exc:
                last_error = exc
            except AIProviderResponseError as exc:
                last_error = exc
                total_latency_ms += exc.latency_ms
                if exc.usage is not None:
                    metered_usage = _add_usage(metered_usage, exc.usage)
        if last_error is None:
            raise AIProviderConfigurationError("No Companion provider route is configured")
        raise last_error

    def _validated_routes(
        self,
        prepared: dict[str, Any],
    ) -> tuple[tuple[AIProvider, dict[str, Any]], ...]:
        raw_routes = prepared.get("routes")
        if (
            prepared.get("contractVersion") != COMPANION_FAILOVER_CONTRACT
            or prepared.get("providerReleaseId") != self.release_id
            or prepared.get("providerReplayRetentionSeconds") != 0
            or not isinstance(raw_routes, list)
            or len(raw_routes) != len(self._routes)
        ):
            raise AIProviderConfigurationError(
                "Stored Companion failover request has an unsupported contract"
            )
        validated: list[tuple[AIProvider, dict[str, Any]]] = []
        for provider, route_payload in zip(self._routes, raw_routes, strict=True):
            if (
                not isinstance(route_payload, dict)
                or route_payload.get("provider") != provider.provider_name
                or route_payload.get("releaseId") != provider.release_id
                or not isinstance(route_payload.get("prepared"), dict)
            ):
                raise AIProviderConfigurationError(
                    "Stored Companion failover route targets another provider"
                )
            validated.append((provider, dict(route_payload["prepared"])))
        return tuple(validated)


def openai_compatible_companion_route(
    settings: Settings,
    *,
    base_url: str,
    api_key: str,
    model: str,
    release_id: str,
    host_allowlist: str,
    idempotency_capability: str,
    idempotency_key_header: str,
    request_id_header: str,
    replay_retention_seconds: int,
) -> AIProvider:
    return AIProvider(
        replace(
            settings,
            ai_provider_base_url=base_url,
            ai_api_key=api_key,
            ai_model=model,
            ai_small_model=model,
            ai_high_capability_model=model,
            ai_companion_provider_release_id=release_id,
            ai_companion_provider_https_host_allowlist=host_allowlist,
            ai_companion_provider_idempotency_capability=idempotency_capability,
            ai_companion_provider_idempotency_key_header=idempotency_key_header,
            ai_companion_provider_request_id_header=request_id_header,
            ai_companion_provider_replay_retention_seconds=replay_retention_seconds,
        )
    )


def _route_idempotency_key(base: str, index: int) -> str:
    return f"{base}:provider-route-{index}"


def _add_usage(left: ModelTokenUsage, right: ModelTokenUsage) -> ModelTokenUsage:
    return ModelTokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
    )
