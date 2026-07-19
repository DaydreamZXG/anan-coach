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
from app.domains.ai.external_processing import validate_minimized_external_payload
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.ai.provider_contract import (
    parse_provider_json_object,
    provider_failure_is_retryable,
    provider_request_audit,
    validate_processing_approval,
    validate_provider_audit_contract,
)
from app.domains.essay_intelligence.grounding import GroundedReviewContext
from app.domains.essay_intelligence.normalization import (
    NORMALIZATION_CONTRACT,
    normalize_grounding_text,
)
from app.domains.essay_intelligence.provider_document import (
    PreparedGroundedReviewProviderInput,
    prepare_grounded_review_provider_input,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROVIDER_NAME,
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
    try:
        validate_processing_approval(
            settings,
            dpa_confirmed=settings.ai_essay_review_provider_dpa_confirmed,
        )
        validate_provider_audit_contract(
            idempotency_capability=(
                settings.ai_essay_review_provider_idempotency_capability
            ),
            idempotency_key_header=(
                settings.ai_essay_review_provider_idempotency_key_header
            ),
            request_id_source=(
                settings.ai_essay_review_provider_request_id_header
            ),
            replay_retention_seconds=(
                settings.ai_essay_review_provider_replay_retention_seconds
            ),
        )
    except ValueError as exc:
        raise GroundedReviewProviderConfigurationError(str(exc)) from exc


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


GROUNDED_REVIEW_SYSTEM_PROMPT = (
    "你是国家公务员考试申论训练评阅引擎。只依据传入的固定题目、完整材料、"
    "评分规程和考生答案工作。必须返回符合 JSON Schema 的单个 JSON 对象。"
    "返回对象的顶层必须直接是 requiredOutputSchema 定义的 score、dimensions、"
    "pointCoverage、claimEvidence、findings、rewritePlan，不得增加 reviewOutput、"
    "result、data 或 providerSchemaVersion 等包装字段。"
    "findings 至少返回一项，rewritePlan.priorityFindingIds 至少引用其中一项，"
    "不得用空数组表示没有问题。"
    "证据引用只返回对应匿名段落引用和逐字原文 quote；不要计算或返回"
    "normalization、normalizedStart、normalizedEnd 或 quoteSha256。规范化、"
    "唯一定位、字符区间和密码学摘要全部由服务端生成。"
    "每个 criterion 和 rubric point 必须恰好覆盖一次；总分、维度分和要点分"
    "必须分别守恒。不得编造材料依据，不得把 system_compiled 规程描述为官方"
    "或已验证，该规程下置信度只能为 low，分数只能解释为 AI 训练估分区间。"
)


def build_grounded_review_messages(
    prepared_input: PreparedGroundedReviewProviderInput,
    *,
    correction: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any], dict[str, Any]]:
    """Build one provider-neutral grading document for every candidate adapter."""
    output_schema = _provider_output_schema()
    user_document: dict[str, Any] = {
        "requiredOutputSchema": output_schema,
        "reviewInput": prepared_input.document.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
        ),
    }
    if correction is not None:
        user_document["correction"] = correction
    messages = [
        {"role": "system", "content": GROUNDED_REVIEW_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                user_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]
    return messages, output_schema, user_document


def parse_grounded_review_content(
    content: str,
    prepared_input: PreparedGroundedReviewProviderInput,
) -> dict[str, Any] | None:
    """Validate JSON framing, seal quotes locally, and restore immutable IDs."""
    try:
        parsed = parse_provider_json_object(content)
    except (ValueError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    return prepared_input.references.restore_payload(
        _seal_evidence_anchors(parsed, prepared_input)
    )


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
    def provider_release_id(self) -> str:
        return self._settings.ai_essay_review_provider_release_id.strip()

    @property
    def endpoint(self) -> str:
        return f"{self._settings.ai_provider_base_url.rstrip('/')}/chat/completions"

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
        messages, output_schema, user_document = build_grounded_review_messages(
            prepared_input,
            correction=correction,
        )
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "grounded_essay_review",
                    "strict": True,
                    "schema": output_schema,
                },
            },
        }
        try:
            validate_minimized_external_payload(user_document)
        except ValueError as exc:
            raise GroundedReviewProviderError(
                "provider_input_not_minimized",
                str(exc),
                retryable=False,
            ) from exc
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
                retryable=provider_failure_is_retryable(
                    idempotency_capability=(
                        self._settings.ai_essay_review_provider_idempotency_capability
                    )
                ),
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise GroundedReviewProviderError(
                "provider_http_failed",
                f"Grounded review provider returned HTTP {status}",
                retryable=provider_failure_is_retryable(
                    idempotency_capability=(
                        self._settings.ai_essay_review_provider_idempotency_capability
                    ),
                    status_code=status,
                ),
            ) from exc

        try:
            data = response.json()
            usage = _parse_usage(data["usage"])
            if data.get("model") is not None and data.get("model") != self.model:
                raise ValueError("provider model drifted")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GroundedReviewProviderError(
                "provider_envelope_invalid",
                "Grounded review provider response envelope or usage is invalid",
                retryable=False,
            ) from exc

        audit_header, request_id_fingerprint = provider_request_audit(
            response=response,
            envelope=data,
            configured_source=(
                self._settings.ai_essay_review_provider_request_id_header
            ),
        )
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
        payload = parse_grounded_review_content(content, prepared_input)
        if (
            self._settings.environment.strip().casefold() == "production"
            and request_id_fingerprint is None
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


def _provider_output_schema() -> dict[str, Any]:
    """Expose only model-owned fields; evidence digests belong to the server."""
    schema = EssayProviderReviewPayload.model_json_schema(by_alias=True)
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise GroundedReviewProviderConfigurationError(
            "Grounded review output schema has no definitions"
        )
    changed = 0
    for definition in definitions.values():
        if not isinstance(definition, dict):
            continue
        properties = definition.get("properties")
        if not isinstance(properties, dict):
            continue
        server_owned_fields = {
            "quoteSha256",
            "normalization",
            "normalizedStart",
            "normalizedEnd",
        }
        if "quote" not in properties or not server_owned_fields.issubset(properties):
            continue
        for field in server_owned_fields:
            properties.pop(field)
        required = definition.get("required")
        if isinstance(required, list):
            definition["required"] = [
                item for item in required if item not in server_owned_fields
            ]
        changed += 1
    if changed != 2:
        raise GroundedReviewProviderConfigurationError(
            "Grounded review output schema evidence boundary drifted"
        )
    return schema


def _seal_evidence_anchors(
    payload: dict[str, Any],
    prepared_input: PreparedGroundedReviewProviderInput,
) -> dict[str, Any]:
    """Resolve model quotes against immutable anonymous source segments."""
    material_segments = {
        item.segment_ref: item.normalized_text
        for item in prepared_input.document.material.segments
    }
    answer_segments = {
        item.segment_ref: item.normalized_text
        for item in prepared_input.document.answer.segments
    }

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        quote = value.get("quote")
        segment_id = value.get("segmentId")
        segments = (
            answer_segments
            if "answerVersionId" in value
            else material_segments if "materialBundleVersionId" in value else None
        )
        if isinstance(quote, str) and isinstance(segment_id, str) and segments:
            normalized_quote = normalize_grounding_text(quote)
            source = segments.get(segment_id)
            if source is not None:
                start = source.find(normalized_quote)
                is_unique = start >= 0 and source.find(normalized_quote, start + 1) < 0
                if is_unique:
                    value["normalization"] = NORMALIZATION_CONTRACT
                    value["normalizedStart"] = start
                    value["normalizedEnd"] = start + len(normalized_quote)
                    value["quoteSha256"] = sha256(
                        normalized_quote.encode("utf-8")
                    ).hexdigest()
        for item in value.values():
            visit(item)

    visit(payload)
    return payload


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
