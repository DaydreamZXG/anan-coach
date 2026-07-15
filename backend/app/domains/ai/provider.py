from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from app.core.config import DEFAULT_AI_PROVIDER_BASE_URL, Settings
from app.domains.ai.model_cost import ModelTokenUsage


COMPANION_PROMPT_VERSION = "companion-grounded-v2"
COMPANION_RESPONSE_SCHEMA_VERSION = "companion-answer-v1"
COMPANION_PROVIDER_INPUT_CONTRACT_VERSION = "companion-provider-input-v1"


class AIProviderConfigurationError(RuntimeError):
    pass


class AIProviderResponseError(RuntimeError):
    """A provider accepted the request but returned an unusable response."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_response_invalid",
        usage: ModelTokenUsage | None = None,
        latency_ms: int = 0,
        output_fingerprint: str | None = None,
        provider_request_id_header: str | None = None,
        provider_request_id_fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.usage = usage
        self.latency_ms = latency_ms
        self.output_fingerprint = output_fingerprint
        self.provider_request_id_header = provider_request_id_header
        self.provider_request_id_fingerprint = provider_request_id_fingerprint


@dataclass(frozen=True)
class AICompanionAnswerResult:
    answer: str
    next_actions: list[str]
    latency_ms: int
    response_summary: str
    usage: ModelTokenUsage
    output_fingerprint: str
    provider_request_id_header: str | None = None
    provider_request_id_fingerprint: str | None = None


class AIProvider:
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
        release = self._settings.ai_companion_provider_release_id.strip()
        return f"gemini_reverse_proxy@{release}" if release else "gemini_reverse_proxy"

    @property
    def model(self) -> str:
        return self._settings.ai_model

    @property
    def prompt_version(self) -> str:
        return COMPANION_PROMPT_VERSION

    @property
    def schema_version(self) -> str:
        return COMPANION_RESPONSE_SCHEMA_VERSION

    def prepare_companion_question(
        self,
        *,
        scenario: str,
        question_text: str,
        context: dict[str, Any],
        persona: str | None = None,
    ) -> dict[str, Any]:
        system_prompt = (
            "你是公务员考试备考搭子，只能基于传入的 learner_context 回答。"
            "不得编造课程、分数、岗位、错题或方法卡。"
            "如果 learner_context 数据不足，必须明确说数据不足，并给出下一步采集动作。"
            "只返回合法 JSON 对象，不要 Markdown。"
        )
        if persona:
            system_prompt = system_prompt + "\n\n" + persona
        user_prompt = {
            "scenario": scenario,
            "question": question_text,
            "learner_context": context,
            "required_schema": {
                "answer": "short Chinese answer grounded in learner_context",
                "nextActions": ["1-3 concrete actions"],
            },
        }
        return {
            "contractVersion": COMPANION_PROVIDER_INPUT_CONTRACT_VERSION,
            "promptVersion": self.prompt_version,
            "schemaVersion": self.schema_version,
            "providerReleaseId": (
                self._settings.ai_companion_provider_release_id.strip()
                or "unversioned-local"
            ),
            "providerEndpointFingerprint": _endpoint_fingerprint(
                self._settings.ai_provider_base_url
            ),
            "providerReplayRetentionSeconds": (
                self._settings.ai_companion_provider_replay_retention_seconds
            ),
            "body": {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            user_prompt,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                "temperature": 0.2,
            },
        }

    def answer_companion_question(
        self,
        *,
        scenario: str,
        question_text: str,
        context: dict[str, Any],
        persona: str | None = None,
        idempotency_key: str | None = None,
    ) -> AICompanionAnswerResult:
        prepared = self.prepare_companion_question(
            scenario=scenario,
            question_text=question_text,
            context=context,
            persona=persona,
        )
        return self.answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key=idempotency_key,
        )

    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str | None,
    ) -> AICompanionAnswerResult:
        encoded_body = self._encoded_prepared_body(
            prepared=prepared,
            idempotency_key=idempotency_key,
        )

        headers = {
            "Authorization": f"Bearer {self._settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        idempotency_header = (
            self._settings.ai_companion_provider_idempotency_key_header.strip()
            or "Idempotency-Key"
        )
        assert idempotency_key is not None
        headers[idempotency_header] = idempotency_key

        started = time.monotonic()
        with self._client_factory() as client:
            response = client.post(
                f"{self._settings.ai_provider_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                content=encoded_body,
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        response.raise_for_status()

        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise AIProviderResponseError(
                "AI provider returned a non-JSON response envelope",
                code="provider_envelope_invalid",
                latency_ms=latency_ms,
            ) from exc
        envelope_fingerprint = sha256(
            json.dumps(
                data,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            usage = _parse_usage(data["usage"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AIProviderResponseError(
                "AI provider response omitted valid token usage",
                code="provider_usage_invalid",
                latency_ms=latency_ms,
                output_fingerprint=envelope_fingerprint,
            ) from exc

        request_id_header = (
            self._settings.ai_companion_provider_request_id_header.strip().casefold()
        )
        raw_request_id = (
            response.headers.get(request_id_header) if request_id_header else None
        )
        request_id = raw_request_id.strip() if raw_request_id else None
        request_id_fingerprint = (
            sha256(request_id.encode("utf-8")).hexdigest()
            if request_id is not None
            else None
        )
        audit_header = request_id_header if request_id is not None else None

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderResponseError(
                "AI provider response has usage but no Companion content",
                code="provider_content_missing",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=envelope_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            ) from exc
        if not isinstance(content, str) or not content:
            raise AIProviderResponseError(
                "AI provider returned empty Companion content",
                code="provider_content_missing",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=envelope_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            )
        encoded_content = content.encode("utf-8")
        output_fingerprint = sha256(encoded_content).hexdigest()
        if (
            self._settings.ai_companion_max_output_bytes > 0
            and len(encoded_content) > self._settings.ai_companion_max_output_bytes
        ):
            raise AIProviderResponseError(
                "AI provider Companion output exceeds the configured capacity",
                code="provider_output_capacity_exceeded",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            )
        if (
            self._settings.environment.strip().casefold() == "production"
            and request_id is None
        ):
            raise AIProviderResponseError(
                "AI provider omitted its configured request ID",
                code="provider_request_id_missing",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
            )
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise AIProviderResponseError(
                "AI provider returned non-JSON Companion content",
                code="provider_content_invalid",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            ) from exc
        if not isinstance(payload, dict):
            raise AIProviderResponseError(
                "AI provider returned invalid Companion content",
                code="provider_content_invalid",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            )
        answer = payload.get("answer")
        next_actions = payload.get("nextActions")
        if not isinstance(answer, str) or not answer.strip():
            raise AIProviderResponseError(
                "AI Companion answer is missing answer",
                code="provider_schema_invalid",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            )
        if not isinstance(next_actions, list) or not all(
            isinstance(item, str) and item.strip() for item in next_actions
        ):
            raise AIProviderResponseError(
                "AI Companion answer is missing nextActions",
                code="provider_schema_invalid",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            )
        return AICompanionAnswerResult(
            answer=answer,
            next_actions=next_actions[:3],
            latency_ms=latency_ms,
            response_summary=content[:500],
            usage=usage,
            output_fingerprint=output_fingerprint,
            provider_request_id_header=audit_header,
            provider_request_id_fingerprint=request_id_fingerprint,
        )

    def validate_prepared_companion_question(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str | None,
    ) -> None:
        self._encoded_prepared_body(
            prepared=prepared,
            idempotency_key=idempotency_key,
        )

    def _encoded_prepared_body(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str | None,
    ) -> bytes:
        self._validate_configuration(idempotency_key=idempotency_key)
        body = _validated_prepared_body(
            prepared,
            model=self.model,
            provider_release_id=(
                self._settings.ai_companion_provider_release_id.strip()
                or "unversioned-local"
            ),
            provider_endpoint_fingerprint=_endpoint_fingerprint(
                self._settings.ai_provider_base_url
            ),
            provider_replay_retention_seconds=(
                self._settings.ai_companion_provider_replay_retention_seconds
            ),
        )
        encoded_body = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if (
            self._settings.ai_companion_max_input_bytes > 0
            and len(encoded_body) > self._settings.ai_companion_max_input_bytes
        ):
            raise AIProviderConfigurationError(
                "Companion provider input exceeds the configured capacity"
            )
        return encoded_body

    def _validate_configuration(self, *, idempotency_key: str | None) -> None:
        if not self._settings.ai_api_key:
            raise AIProviderConfigurationError("AI_API_KEY is required for AI routes")
        if not self.model.strip():
            raise AIProviderConfigurationError("AI_MODEL is required for Companion")
        if not idempotency_key or not idempotency_key.strip():
            raise AIProviderConfigurationError(
                "A stable provider idempotency key is required for Companion"
            )
        if self._settings.environment.strip().casefold() != "production":
            return
        _validate_production_security(self._settings)


def _validate_production_security(settings: Settings) -> None:
    release_id = settings.ai_companion_provider_release_id.strip()
    if not release_id:
        raise AIProviderConfigurationError(
            "AI_COMPANION_PROVIDER_RELEASE_ID is required in production"
        )
    parsed = urlparse(settings.ai_provider_base_url.strip())
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not hostname:
        raise AIProviderConfigurationError(
            "Production Companion provider must use an absolute HTTPS URL"
        )
    if settings.ai_provider_base_url.rstrip("/") == DEFAULT_AI_PROVIDER_BASE_URL.rstrip(
        "/"
    ):
        raise AIProviderConfigurationError(
            "The default external AI gateway is forbidden in production"
        )
    allowed_hosts = {
        item.strip().casefold()
        for item in settings.ai_companion_provider_https_host_allowlist.split(",")
        if item.strip()
    }
    if not allowed_hosts or any(not _is_valid_hostname(item) for item in allowed_hosts):
        raise AIProviderConfigurationError(
            "AI_COMPANION_PROVIDER_HTTPS_HOST_ALLOWLIST must contain hostnames"
        )
    if hostname not in allowed_hosts:
        raise AIProviderConfigurationError(
            "Companion provider host is not allowlisted"
        )
    if not settings.ai_companion_provider_dpa_confirmed:
        raise AIProviderConfigurationError(
            "Companion provider DPA confirmation is required in production"
        )
    if (
        settings.ai_companion_provider_idempotency_capability.strip()
        != "replayable_queryable"
    ):
        raise AIProviderConfigurationError(
            "Production Companion requires verified replayable/queryable provider idempotency"
        )
    for value, name in (
        (
            settings.ai_companion_provider_idempotency_key_header,
            "AI_COMPANION_PROVIDER_IDEMPOTENCY_KEY_HEADER",
        ),
        (
            settings.ai_companion_provider_request_id_header,
            "AI_COMPANION_PROVIDER_REQUEST_ID_HEADER",
        ),
    ):
        if re.fullmatch(r"[A-Za-z0-9-]+", value.strip()) is None:
            raise AIProviderConfigurationError(f"{name} is required in production")
    if settings.ai_companion_max_input_bytes <= 0:
        raise AIProviderConfigurationError(
            "AI_COMPANION_MAX_INPUT_BYTES must be positive in production"
        )
    if settings.ai_companion_max_output_bytes <= 0:
        raise AIProviderConfigurationError(
            "AI_COMPANION_MAX_OUTPUT_BYTES must be positive in production"
        )
    if settings.ai_companion_provider_replay_retention_seconds < 604_800:
        raise AIProviderConfigurationError(
            "AI_COMPANION_PROVIDER_REPLAY_RETENTION_SECONDS must be at least 604800"
        )


def _validated_prepared_body(
    prepared: dict[str, Any],
    *,
    model: str,
    provider_release_id: str,
    provider_endpoint_fingerprint: str,
    provider_replay_retention_seconds: int,
) -> dict[str, Any]:
    if (
        prepared.get("contractVersion")
        != COMPANION_PROVIDER_INPUT_CONTRACT_VERSION
        or prepared.get("promptVersion") != COMPANION_PROMPT_VERSION
        or prepared.get("schemaVersion") != COMPANION_RESPONSE_SCHEMA_VERSION
        or prepared.get("providerReleaseId") != provider_release_id
        or prepared.get("providerEndpointFingerprint")
        != provider_endpoint_fingerprint
        or prepared.get("providerReplayRetentionSeconds")
        != provider_replay_retention_seconds
        or not isinstance(prepared.get("body"), dict)
    ):
        raise AIProviderConfigurationError(
            "Stored Companion provider input has an unsupported contract"
        )
    body = dict(prepared["body"])
    if body.get("model") != model:
        raise AIProviderConfigurationError(
            "Stored Companion provider input targets a different model release"
        )
    return body


def _endpoint_fingerprint(value: str) -> str:
    return sha256(value.strip().rstrip("/").encode("utf-8")).hexdigest()


def _parse_usage(value: Any) -> ModelTokenUsage:
    if not isinstance(value, dict):
        raise ValueError("usage must be an object")
    input_tokens = value.get("prompt_tokens")
    output_tokens = value.get("completion_tokens")
    details = value.get("prompt_tokens_details") or {}
    cached_tokens = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    if (
        isinstance(input_tokens, bool)
        or not isinstance(input_tokens, int)
        or isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or isinstance(cached_tokens, bool)
        or not isinstance(cached_tokens, int)
    ):
        raise ValueError("provider usage token fields must be integers")
    return ModelTokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
    )


def _is_valid_hostname(value: str) -> bool:
    return bool(
        len(value) <= 253
        and re.fullmatch(
            r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
            value,
        )
    )
