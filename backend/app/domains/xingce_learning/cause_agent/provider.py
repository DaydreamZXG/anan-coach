from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import time
from typing import Callable
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from app.core.config import DEFAULT_AI_PROVIDER_BASE_URL, Settings
from app.domains.ai.external_processing import validate_minimized_external_payload
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.ai.provider_contract import (
    parse_provider_json_object,
    provider_failure_is_retryable,
    provider_request_audit,
    validate_processing_approval,
    validate_provider_audit_contract,
)
from app.domains.xingce_learning.cause_agent.contracts import (
    CauseAgentEvidence,
    CauseAgentOutput,
    CauseAgentProviderEnvelope,
)


CAUSE_AGENT_PROMPT_VERSION = "xingce-cause-prompt@v1"
CAUSE_AGENT_SCHEMA_VERSION = "cause-candidate@v2"
CAUSE_AGENT_PROVIDER_INPUT_VERSION = "xingce-cause-provider-input@v1"


class CauseAgentProviderConfigurationError(RuntimeError):
    pass


class CauseAgentProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        usage: ModelTokenUsage | None = None,
        output_fingerprint: str | None = None,
        latency_ms: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.usage = usage
        self.output_fingerprint = output_fingerprint
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class CauseAgentProviderResult:
    output: CauseAgentOutput
    usage: ModelTokenUsage
    output_fingerprint: str
    latency_ms: int
    provider_request_id_header: str | None = None
    provider_request_id_fingerprint: str | None = None


class CauseAgentProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        )

    @property
    def provider_name(self) -> str:
        release = self.provider_release_id
        return f"xingce_cause_provider@{release}"

    @property
    def provider_release_id(self) -> str:
        return self._settings.ai_xingce_cause_provider_release_id.strip() or "local-unreleased"

    @property
    def model(self) -> str:
        return self._settings.ai_xingce_cause_model.strip()

    @property
    def prompt_version(self) -> str:
        return CAUSE_AGENT_PROMPT_VERSION

    @property
    def schema_version(self) -> str:
        return CAUSE_AGENT_SCHEMA_VERSION

    def validate_configuration(self) -> None:
        if self._settings.environment.strip().casefold() != "production":
            if not self.model:
                raise CauseAgentProviderConfigurationError(
                    "AI_XINGCE_CAUSE_MODEL is required"
                )
            return
        if not self._settings.ai_api_key:
            raise CauseAgentProviderConfigurationError("AI_API_KEY is required")
        if not self.model or not self._settings.ai_xingce_cause_provider_release_id.strip():
            raise CauseAgentProviderConfigurationError(
                "cause-agent model and provider release are required in production"
            )
        parsed = urlparse(self._settings.ai_provider_base_url.strip())
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme.casefold() != "https"
            or not host
            or self._settings.ai_provider_base_url.rstrip("/")
            == DEFAULT_AI_PROVIDER_BASE_URL.rstrip("/")
        ):
            raise CauseAgentProviderConfigurationError(
                "production cause-agent provider must use an approved HTTPS endpoint"
            )
        allowlist = {
            item.strip().casefold()
            for item in self._settings.ai_xingce_cause_provider_https_host_allowlist.split(",")
            if item.strip()
        }
        if host not in allowlist:
            raise CauseAgentProviderConfigurationError(
                "cause-agent provider host is not allowlisted"
            )
        try:
            validate_processing_approval(
                self._settings,
                dpa_confirmed=(
                    self._settings.ai_xingce_cause_provider_dpa_confirmed
                ),
            )
            validate_provider_audit_contract(
                idempotency_capability=(
                    self._settings.ai_xingce_cause_provider_idempotency_capability
                ),
                idempotency_key_header=(
                    self._settings.ai_xingce_cause_provider_idempotency_key_header
                ),
                request_id_source=(
                    self._settings.ai_xingce_cause_provider_request_id_header
                ),
                replay_retention_seconds=(
                    self._settings.ai_xingce_cause_provider_replay_retention_seconds
                ),
            )
        except ValueError as exc:
            raise CauseAgentProviderConfigurationError(str(exc)) from exc
        if (
            self._settings.ai_xingce_cause_max_input_bytes <= 0
            or self._settings.ai_xingce_cause_max_output_bytes <= 0
        ):
            raise CauseAgentProviderConfigurationError(
                "cause-agent provider size and replay limits are not production safe"
            )

    def generate(
        self,
        *,
        evidence: CauseAgentEvidence,
        followup_answer: str | None,
        idempotency_key: str,
    ) -> CauseAgentProviderResult:
        self.validate_configuration()
        prompt = {
            "contractVersion": CAUSE_AGENT_PROVIDER_INPUT_VERSION,
            "instruction": (
                "你是行测解题 Agent。只根据 evidence 判断；不得把推测写成事实。"
                "证据足够时返回 candidate_ready；需要一个高信息量问题时返回 "
                "followup_required；否则返回 evidence_insufficient。只返回 JSON。"
            ),
            "requiredContract": CAUSE_AGENT_SCHEMA_VERSION,
            "requiredOutputSchema": CauseAgentProviderEnvelope.model_json_schema(
                by_alias=True
            ),
            "evidence": evidence.model_dump(by_alias=True, mode="json"),
            "followupAnswer": followup_answer,
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt["instruction"]},
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": 0.1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "xingce_cause_candidate",
                    "strict": True,
                    "schema": CauseAgentProviderEnvelope.model_json_schema(
                        by_alias=True
                    ),
                },
            },
        }
        try:
            validate_minimized_external_payload(prompt)
        except ValueError as exc:
            raise CauseAgentProviderConfigurationError(str(exc)) from exc
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if (
            self._settings.ai_xingce_cause_max_input_bytes > 0
            and len(encoded) > self._settings.ai_xingce_cause_max_input_bytes
        ):
            raise CauseAgentProviderConfigurationError(
                "cause-agent provider input exceeds configured capacity"
            )
        headers = {
            "Authorization": f"Bearer {self._settings.ai_api_key}",
            "Content-Type": "application/json",
            (
                self._settings.ai_xingce_cause_provider_idempotency_key_header.strip()
                or "Idempotency-Key"
            ): idempotency_key,
        }
        started = time.monotonic()
        try:
            with self._client_factory() as client:
                response = client.post(
                    f"{self._settings.ai_provider_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    content=encoded,
                )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise CauseAgentProviderError(
                "provider_transport_uncertain",
                "cause-agent provider outcome is uncertain",
                retryable=provider_failure_is_retryable(
                    idempotency_capability=(
                        self._settings.ai_xingce_cause_provider_idempotency_capability
                    )
                ),
            ) from exc
        except httpx.HTTPStatusError as exc:
            retryable = provider_failure_is_retryable(
                idempotency_capability=(
                    self._settings.ai_xingce_cause_provider_idempotency_capability
                ),
                status_code=exc.response.status_code,
            )
            raise CauseAgentProviderError(
                f"provider_http_{exc.response.status_code}",
                "cause-agent provider rejected the request",
                retryable=retryable,
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            envelope = response.json()
            if envelope.get("model") is not None and envelope.get("model") != self.model:
                raise ValueError("provider model drifted")
            usage = _usage(envelope["usage"])
            content = envelope["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content must be text")
            if (
                self._settings.ai_xingce_cause_max_output_bytes > 0
                and len(content.encode("utf-8"))
                > self._settings.ai_xingce_cause_max_output_bytes
            ):
                raise ValueError("output exceeds configured capacity")
            parsed = CauseAgentProviderEnvelope.model_validate(
                parse_provider_json_object(content)
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            output_fingerprint = sha256(
                json.dumps(envelope if "envelope" in locals() else {}, sort_keys=True).encode()
            ).hexdigest()
            raise CauseAgentProviderError(
                "provider_response_invalid",
                "cause-agent provider returned an invalid structured response",
                retryable=False,
                usage=usage if "usage" in locals() else None,
                output_fingerprint=output_fingerprint,
                latency_ms=latency_ms,
            ) from exc
        output_fingerprint = sha256(content.encode("utf-8")).hexdigest()
        request_id_source, request_id_fingerprint = provider_request_audit(
            response=response,
            envelope=envelope,
            configured_source=(
                self._settings.ai_xingce_cause_provider_request_id_header
            ),
        )
        return CauseAgentProviderResult(
            output=parsed.output,
            usage=usage,
            output_fingerprint=output_fingerprint,
            latency_ms=latency_ms,
            provider_request_id_header=request_id_source,
            provider_request_id_fingerprint=request_id_fingerprint,
        )


def _usage(value: object) -> ModelTokenUsage:
    if not isinstance(value, dict):
        raise TypeError("usage must be an object")
    input_tokens = int(value.get("prompt_tokens", value.get("input_tokens")))
    output_tokens = int(value.get("completion_tokens", value.get("output_tokens")))
    cached = int(
        value.get("prompt_tokens_details", {}).get("cached_tokens", 0)
        if isinstance(value.get("prompt_tokens_details", {}), dict)
        else 0
    )
    return ModelTokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
    )
