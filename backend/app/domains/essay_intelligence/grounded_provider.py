from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app.core.config import DEFAULT_AI_PROVIDER_BASE_URL, Settings
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.essay_intelligence.grounding import GroundedReviewContext
from app.domains.essay_intelligence.provider_document import (
    PreparedGroundedReviewProviderInput,
    prepare_grounded_review_provider_input,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROVIDER_NAME,
    GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
)
from app.domains.essay_intelligence.schemas import (
    EssayProviderReviewPayload,
    EssayReviewRequest,
)


class GroundedReviewProviderConfigurationError(RuntimeError):
    pass


_RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _is_valid_hostname(value: str) -> bool:
    if not value or len(value) > 253:
        return False
    labels = value.removesuffix(".").split(".")
    return all(
        label
        and len(label) <= 63
        and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        is not None
        for label in labels
    )


def grounded_review_provider_identity(settings: Settings) -> str:
    """Versioned execution identity persisted in jobs, reviews, and model audit."""
    release_id = settings.ai_essay_review_provider_release_id.strip()
    if not release_id:
        if settings.environment.strip().casefold() == "production":
            raise GroundedReviewProviderConfigurationError(
                "AI_ESSAY_REVIEW_PROVIDER_RELEASE_ID is required in production"
            )
        return GROUNDED_REVIEW_PROVIDER_NAME
    if _RELEASE_ID_PATTERN.fullmatch(release_id) is None:
        raise GroundedReviewProviderConfigurationError(
            "AI_ESSAY_REVIEW_PROVIDER_RELEASE_ID has an invalid format"
        )
    return f"{GROUNDED_REVIEW_PROVIDER_NAME}@{release_id}"


def validate_grounded_review_provider_security(settings: Settings) -> None:
    """Fail closed on the external data-processing boundary in production."""
    grounded_review_provider_identity(settings)
    if settings.environment.strip().casefold() != "production":
        return

    base_url = settings.ai_provider_base_url.strip()
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname.casefold() if parsed.hostname else ""
        _ = parsed.port
    except ValueError as exc:
        raise GroundedReviewProviderConfigurationError(
            "AI_PROVIDER_BASE_URL is invalid"
        ) from exc
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GroundedReviewProviderConfigurationError(
            "Production grounded review requires a credential-free HTTPS provider URL"
        )

    default_host = urlsplit(DEFAULT_AI_PROVIDER_BASE_URL).hostname
    if default_host is not None and hostname == default_host.casefold():
        raise GroundedReviewProviderConfigurationError(
            "The default external AI gateway is forbidden in production"
        )

    raw_hosts = settings.ai_essay_review_provider_https_host_allowlist.split(",")
    allowed_hosts = {
        item.strip().casefold()
        for item in raw_hosts
        if item.strip()
    }
    if not allowed_hosts or any(not _is_valid_hostname(item) for item in allowed_hosts):
        raise GroundedReviewProviderConfigurationError(
            "AI_ESSAY_REVIEW_PROVIDER_HTTPS_HOST_ALLOWLIST must contain hostnames"
        )
    if hostname not in allowed_hosts:
        raise GroundedReviewProviderConfigurationError(
            "Grounded review provider host is not allowlisted"
        )
    if not settings.ai_essay_review_provider_dpa_confirmed:
        raise GroundedReviewProviderConfigurationError(
            "Grounded review provider DPA confirmation is required in production"
        )
    if (
        settings.ai_essay_review_provider_idempotency_capability.strip()
        != "replayable_queryable"
    ):
        raise GroundedReviewProviderConfigurationError(
            "Production grounded review requires verified replayable/queryable "
            "provider idempotency"
        )
    idempotency_key_header = (
        settings.ai_essay_review_provider_idempotency_key_header.strip().casefold()
    )
    if re.fullmatch(r"[a-z0-9-]+", idempotency_key_header) is None:
        raise GroundedReviewProviderConfigurationError(
            "AI_ESSAY_REVIEW_PROVIDER_IDEMPOTENCY_KEY_HEADER is required in production"
        )
    request_id_header = (
        settings.ai_essay_review_provider_request_id_header.strip().casefold()
    )
    if re.fullmatch(r"[a-z0-9-]+", request_id_header) is None:
        raise GroundedReviewProviderConfigurationError(
            "AI_ESSAY_REVIEW_PROVIDER_REQUEST_ID_HEADER is required in production"
        )


class GroundedReviewProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class GroundedReviewMeteredProviderError(GroundedReviewProviderError):
    """A billable provider response that is unsafe to admit into the domain."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        usage: ModelTokenUsage,
        latency_ms: int,
        output_fingerprint: str,
        provider_request_id_header: str | None,
        provider_request_id_fingerprint: str | None,
    ) -> None:
        super().__init__(code, message, retryable=False)
        self.usage = usage
        self.latency_ms = latency_ms
        self.output_fingerprint = output_fingerprint
        self.provider_request_id_header = provider_request_id_header
        self.provider_request_id_fingerprint = provider_request_id_fingerprint


@dataclass(frozen=True)
class GroundedReviewProviderResult:
    payload: dict[str, Any] | None
    raw_content: str
    usage: ModelTokenUsage
    latency_ms: int
    output_fingerprint: str
    provider_request_id_header: str | None = None
    provider_request_id_fingerprint: str | None = None


class GroundedEssayReviewProvider:
    """Strict structured-output provider. It never truncates task or material input."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(90.0, connect=15.0))
        )

    @property
    def provider_name(self) -> str:
        return grounded_review_provider_identity(self._settings)

    @property
    def model(self) -> str:
        return self._settings.ai_essay_review_model

    def generate(
        self,
        *,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        idempotency_key: str | None = None,
    ) -> GroundedReviewProviderResult:
        del request
        return self._call(
            prepared_input=prepare_grounded_review_provider_input(context),
            correction=None,
            idempotency_key=idempotency_key,
        )

    def repair(
        self,
        *,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        rejected_content: str,
        violation_code: str,
        violation_detail: str,
        idempotency_key: str | None = None,
    ) -> GroundedReviewProviderResult:
        del request, violation_detail
        prepared = prepare_grounded_review_provider_input(context)
        return self._call(
            prepared_input=prepared,
            correction={
                "violationCode": violation_code,
                "rejectedOutput": prepared.references.anonymize_rejected_content(
                    rejected_content
                ),
                "instruction": (
                    "重新生成完整 JSON，不得只返回补丁；所有引用必须重新按固定版本验真。"
                ),
            },
            idempotency_key=idempotency_key,
        )

    def _call(
        self,
        *,
        prepared_input: PreparedGroundedReviewProviderInput,
        correction: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> GroundedReviewProviderResult:
        self._validate_configuration()
        system_prompt = (
            "你是国家公务员考试申论训练评阅引擎。只依据传入的固定题目、完整材料、"
            "评分规程和考生答案工作。必须返回符合 JSON Schema 的单个 JSON 对象。"
            "每个 criterion 和 rubric point 必须恰好覆盖一次；总分、维度分和要点分"
            "必须分别守恒。所有材料与答案引用都必须使用传入的 normalizedText 原文，"
            "给出 nfkc_whitespace_v1、半开区间字符偏移和该规范化切片的 SHA-256。"
            "不得编造材料依据，不得把 system_compiled 规程描述为官方或已验证，"
            "该规程下置信度只能为 low，分数只能解释为 AI 训练估分区间。"
        )
        user_document: dict[str, Any] = {
            "reviewInput": prepared_input.document.model_dump(
                by_alias=True,
                mode="json",
                exclude_none=True,
            )
        }
        if correction is not None:
            user_document["correction"] = correction
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_document,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "grounded_essay_review",
                    "strict": True,
                    "schema": EssayProviderReviewPayload.model_json_schema(
                        by_alias=True
                    ),
                },
            },
        }
        encoded_body = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded_body) > self._settings.ai_essay_review_max_input_bytes:
            raise GroundedReviewProviderError(
                "model_context_capacity_exceeded",
                "Full grounded review input exceeds the configured provider capacity",
                retryable=False,
            )

        started = time.monotonic()
        try:
            with self._client_factory() as client:
                headers = {
                    "Authorization": f"Bearer {self._settings.ai_api_key}",
                    "Content-Type": "application/json",
                }
                if idempotency_key:
                    configured_header = (
                        self._settings.ai_essay_review_provider_idempotency_key_header.strip()
                    )
                    headers[configured_header or "Idempotency-Key"] = idempotency_key
                response = client.post(
                    f"{self._settings.ai_provider_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    content=encoded_body,
                )
            latency_ms = int((time.monotonic() - started) * 1000)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise GroundedReviewProviderError(
                "provider_transport_failed",
                "Grounded review provider transport failed",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise GroundedReviewProviderError(
                "provider_http_failed",
                f"Grounded review provider returned HTTP {status}",
                retryable=status == 429 or status >= 500,
            ) from exc

        try:
            data = response.json()
            usage = _parse_usage(data["usage"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GroundedReviewProviderError(
                "provider_envelope_invalid",
                "Grounded review provider response envelope or usage is invalid",
                retryable=False,
            ) from exc

        request_id_header = (
            self._settings.ai_essay_review_provider_request_id_header.strip().casefold()
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
        envelope_fingerprint = sha256(
            json.dumps(
                data,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GroundedReviewMeteredProviderError(
                "provider_envelope_invalid",
                "Grounded review provider response has usage but no content",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=envelope_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            ) from exc
        if not isinstance(content, str) or not content:
            raise GroundedReviewMeteredProviderError(
                "provider_content_missing",
                "Grounded review provider returned no content",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=envelope_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            )
        try:
            encoded_content = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise GroundedReviewMeteredProviderError(
                "provider_content_encoding_invalid",
                "Grounded review provider content is not valid UTF-8 text",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=envelope_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            ) from exc
        output_fingerprint = sha256(encoded_content).hexdigest()
        if len(encoded_content) > self._settings.ai_essay_review_max_output_bytes:
            raise GroundedReviewMeteredProviderError(
                "provider_output_capacity_exceeded",
                "Grounded review provider output exceeds the configured capacity",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_header=audit_header,
                provider_request_id_fingerprint=request_id_fingerprint,
            )
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, RecursionError):
            parsed = None
        payload = (
            prepared_input.references.restore_payload(parsed)
            if isinstance(parsed, dict)
            else None
        )
        if (
            self._settings.environment.strip().casefold() == "production"
            and request_id is None
        ):
            raise GroundedReviewMeteredProviderError(
                "provider_request_id_missing",
                "Grounded review provider omitted its configured request ID",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_header=None,
                provider_request_id_fingerprint=None,
            )
        return GroundedReviewProviderResult(
            payload=payload,
            raw_content=content,
            usage=usage,
            latency_ms=latency_ms,
            output_fingerprint=output_fingerprint,
            provider_request_id_header=audit_header,
            provider_request_id_fingerprint=request_id_fingerprint,
        )

    def _validate_configuration(self) -> None:
        validate_grounded_review_provider_security(self._settings)
        if not self._settings.ai_api_key:
            raise GroundedReviewProviderConfigurationError(
                "AI_API_KEY is required for grounded review"
            )
        if not self.model.strip():
            raise GroundedReviewProviderConfigurationError(
                "AI_ESSAY_REVIEW_MODEL is required"
            )
        if self._settings.ai_essay_review_max_input_bytes <= 0:
            raise GroundedReviewProviderConfigurationError(
                "AI_ESSAY_REVIEW_MAX_INPUT_BYTES must be configured"
            )
        if self._settings.ai_essay_review_max_output_bytes <= 0:
            raise GroundedReviewProviderConfigurationError(
                "AI_ESSAY_REVIEW_MAX_OUTPUT_BYTES must be configured"
            )


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
