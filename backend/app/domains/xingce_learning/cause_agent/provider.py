from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import time
from typing import Callable
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from app.core.config import DEFAULT_AI_PROVIDER_BASE_URL, Settings
from app.domains.ai.model_cost import ModelTokenUsage
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
        if not self._settings.ai_xingce_cause_provider_dpa_confirmed:
            raise CauseAgentProviderConfigurationError(
                "cause-agent provider DPA confirmation is required"
            )
        if (
            self._settings.ai_xingce_cause_provider_idempotency_capability.strip()
            != "replayable_queryable"
        ):
            raise CauseAgentProviderConfigurationError(
                "cause-agent provider requires replayable/queryable idempotency"
            )
        for value in (
            self._settings.ai_xingce_cause_provider_idempotency_key_header,
            self._settings.ai_xingce_cause_provider_request_id_header,
        ):
            if re.fullmatch(r"[A-Za-z0-9-]+", value.strip()) is None:
                raise CauseAgentProviderConfigurationError(
                    "cause-agent provider audit headers are required"
                )
        if (
            self._settings.ai_xingce_cause_max_input_bytes <= 0
            or self._settings.ai_xingce_cause_max_output_bytes <= 0
            or self._settings.ai_xingce_cause_provider_replay_retention_seconds < 604_800
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
        }
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
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code in {408, 429} or exc.response.status_code >= 500
            raise CauseAgentProviderError(
                f"provider_http_{exc.response.status_code}",
                "cause-agent provider rejected the request",
                retryable=retryable,
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            envelope = response.json()
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
            parsed = CauseAgentProviderEnvelope.model_validate_json(content)
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
        header = self._settings.ai_xingce_cause_provider_request_id_header.strip().casefold()
        request_id = response.headers.get(header) if header else None
        return CauseAgentProviderResult(
            output=parsed.output,
            usage=usage,
            output_fingerprint=output_fingerprint,
            latency_ms=latency_ms,
            provider_request_id_header=(header or None) if request_id else None,
            provider_request_id_fingerprint=(
                sha256(request_id.encode()).hexdigest() if request_id else None
            ),
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
