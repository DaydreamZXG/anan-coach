from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re
import time
from typing import Any, Callable, Literal

import httpx

from app.core.config import Settings
from app.domains.ai.external_processing import validate_minimized_external_payload
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.ai.provider_contract import (
    parse_provider_json_object,
    provider_failure_is_retryable,
    provider_request_audit,
)
from app.domains.essay_intelligence.grounded_provider import (
    validate_grounded_review_provider_security,
)
from app.domains.essay_intelligence.rubric_ai_contracts import (
    RUBRIC_AI_CRITIC_PROMPT_VERSION,
    RUBRIC_AI_GENERATOR_PROMPT_VERSION,
    RubricAICriticOutput,
    RubricAIGeneratorOutput,
    RubricAIProviderDocument,
)


class RubricAIProviderConfigurationError(RuntimeError):
    pass


class RubricAIProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class RubricAIMeteredProviderError(RubricAIProviderError):
    """Provider returned billable usage, but its response cannot enter the domain."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        usage: ModelTokenUsage,
        latency_ms: int,
        output_fingerprint: str,
        provider_request_id_sha256: str | None,
    ) -> None:
        super().__init__(code, message, retryable=False)
        self.usage = usage
        self.latency_ms = latency_ms
        self.output_fingerprint = output_fingerprint
        self.provider_request_id_sha256 = provider_request_id_sha256


@dataclass(frozen=True)
class RubricAIProviderResult:
    payload: RubricAIGeneratorOutput | RubricAICriticOutput
    usage: ModelTokenUsage
    latency_ms: int
    output_fingerprint: str
    provider_request_id_sha256: str | None


class GuokaoRubricAIProvider:
    """Structured provider boundary with no learner or answer data."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or (
            # Complete essay material plus a rubric can legitimately take longer
            # than an ordinary chat completion on the low-cost Flash route.
            lambda: httpx.Client(timeout=httpx.Timeout(300.0, connect=15.0))
        )

    @property
    def provider_name(self) -> str:
        release_id = self._settings.ai_essay_rubric_provider_release_id.strip()
        if not release_id:
            if self._settings.environment.strip().casefold() == "production":
                raise RubricAIProviderConfigurationError(
                    "AI_ESSAY_RUBRIC_PROVIDER_RELEASE_ID is required in production"
                )
            return "openai-compatible-guokao-rubric-v1"
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release_id) is None:
            raise RubricAIProviderConfigurationError(
                "AI_ESSAY_RUBRIC_PROVIDER_RELEASE_ID has an invalid format"
            )
        return f"openai-compatible-guokao-rubric-v1@{release_id}"

    @property
    def generator_model(self) -> str:
        return self._settings.ai_essay_rubric_generator_model.strip()

    @property
    def critic_model(self) -> str:
        return self._settings.ai_essay_rubric_critic_model.strip()

    @property
    def base_url(self) -> str:
        return self._provider_settings().ai_provider_base_url.strip()

    def generate(
        self,
        *,
        document: RubricAIProviderDocument,
        idempotency_key: str,
    ) -> RubricAIProviderResult:
        return self._call(
            phase="generator",
            document=document,
            candidate=None,
            idempotency_key=idempotency_key,
        )

    def criticize_and_revise(
        self,
        *,
        document: RubricAIProviderDocument,
        candidate: RubricAIGeneratorOutput,
        idempotency_key: str,
    ) -> RubricAIProviderResult:
        return self._call(
            phase="critic",
            document=document,
            candidate=candidate,
            idempotency_key=idempotency_key,
        )

    def _call(
        self,
        *,
        phase: Literal["generator", "critic"],
        document: RubricAIProviderDocument,
        candidate: RubricAIGeneratorOutput | None,
        idempotency_key: str,
    ) -> RubricAIProviderResult:
        self._validate_configuration()
        provider_settings = self._provider_settings()
        if not idempotency_key.strip():
            raise RubricAIProviderConfigurationError(
                "Rubric AI provider idempotency key is required"
            )
        is_generator = phase == "generator"
        model = self.generator_model if is_generator else self.critic_model
        schema_type = RubricAIGeneratorOutput if is_generator else RubricAICriticOutput
        prompt_version = (
            RUBRIC_AI_GENERATOR_PROMPT_VERSION if is_generator else RUBRIC_AI_CRITIC_PROMPT_VERSION
        )
        system_prompt = _GENERATOR_PROMPT if is_generator else _CRITIC_PROMPT
        output_schema = schema_type.model_json_schema(by_alias=True)
        provider_input: dict[str, Any] = {
            "promptVersion": prompt_version,
            "requiredOutputSchema": output_schema,
            "taskAndCompleteMaterial": document.model_dump(
                by_alias=True,
                mode="json",
            ),
        }
        if candidate is not None:
            provider_input["generatorCandidate"] = candidate.model_dump(
                by_alias=True,
                mode="json",
            )
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        provider_input,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": 0,
            # DeepSeek V4 exposes structured output through JSON mode.  The
            # complete JSON Schema remains in the prompt and is validated by
            # Pydantic and the deterministic compiler before publication.
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": 32_768,
        }
        try:
            validate_minimized_external_payload(provider_input)
        except ValueError as exc:
            raise RubricAIProviderError(
                "rubric_provider_input_not_minimized",
                str(exc),
                retryable=False,
            ) from exc
        encoded_body = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded_body) > self._settings.ai_essay_rubric_max_input_bytes:
            raise RubricAIProviderError(
                "rubric_model_context_capacity_exceeded",
                "Complete rubric source exceeds configured provider capacity",
                retryable=False,
            )

        started = time.monotonic()
        try:
            with self._client_factory() as client:
                idempotency_header = (
                    provider_settings.ai_essay_review_provider_idempotency_key_header.strip()
                    or "Idempotency-Key"
                )
                response = client.post(
                    f"{provider_settings.ai_provider_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {provider_settings.ai_api_key}",
                        "Content-Type": "application/json",
                        idempotency_header: idempotency_key,
                    },
                    content=encoded_body,
                )
            latency_ms = int((time.monotonic() - started) * 1000)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RubricAIProviderError(
                "rubric_provider_transport_failed",
                "Rubric AI provider transport failed",
                retryable=provider_failure_is_retryable(
                    idempotency_capability=(
                        provider_settings.ai_essay_review_provider_idempotency_capability
                    )
                ),
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RubricAIProviderError(
                "rubric_provider_http_failed",
                f"Rubric AI provider returned HTTP {exc.response.status_code}: "
                + _http_error_summary(exc.response),
                retryable=provider_failure_is_retryable(
                    idempotency_capability=(
                        provider_settings.ai_essay_review_provider_idempotency_capability
                    ),
                    status_code=exc.response.status_code,
                ),
            ) from exc

        try:
            envelope = response.json()
            usage = _parse_usage(envelope["usage"])
            if envelope.get("model") is not None and envelope.get("model") != model:
                raise ValueError("provider model drifted")
        except (KeyError, TypeError, ValueError) as exc:
            raise RubricAIProviderError(
                "rubric_provider_response_invalid",
                "Rubric AI provider response envelope or usage is invalid",
                # A successful HTTP response can already be billable. Preserve the
                # logical call and replay the exact provider idempotency key until
                # usage can be audited instead of writing a false zero-cost failure.
                retryable=provider_failure_is_retryable(
                    idempotency_capability=(
                        provider_settings.ai_essay_review_provider_idempotency_capability
                    )
                ),
            ) from exc

        _, provider_request_id_sha256 = provider_request_audit(
            response=response,
            envelope=envelope,
            configured_source=(provider_settings.ai_essay_review_provider_request_id_header),
        )
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RubricAIMeteredProviderError(
                "rubric_provider_response_invalid",
                "Rubric AI provider response has usage but no structured content",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=sha256(response.content).hexdigest(),
                provider_request_id_sha256=provider_request_id_sha256,
            ) from exc
        if not isinstance(content, str) or not content:
            raise RubricAIMeteredProviderError(
                "rubric_provider_response_invalid",
                "Rubric AI provider content must be a non-empty string",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=sha256(response.content).hexdigest(),
                provider_request_id_sha256=provider_request_id_sha256,
            )
        encoded_content = content.encode("utf-8")
        output_fingerprint = sha256(encoded_content).hexdigest()
        if len(encoded_content) > self._settings.ai_essay_rubric_max_output_bytes:
            raise RubricAIMeteredProviderError(
                "rubric_provider_output_capacity_exceeded",
                "Rubric AI provider output exceeds configured capacity",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_sha256=provider_request_id_sha256,
            )
        try:
            raw_payload = parse_provider_json_object(content)
            _bind_server_owned_fields(
                raw_payload,
                document=document,
                phase=phase,
            )
            payload = schema_type.model_validate_json(
                json.dumps(raw_payload, ensure_ascii=False, separators=(",", ":"))
            )
        except ValueError as exc:
            raise RubricAIMeteredProviderError(
                "rubric_provider_response_invalid",
                "Rubric AI provider response does not satisfy its strict schema: "
                + _validation_error_summary(exc),
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_sha256=provider_request_id_sha256,
            ) from exc

        if (
            self._settings.environment.strip().casefold() == "production"
            and not provider_request_id_sha256
        ):
            raise RubricAIMeteredProviderError(
                "rubric_provider_request_id_missing",
                "Rubric AI provider omitted its configured request ID",
                usage=usage,
                latency_ms=latency_ms,
                output_fingerprint=output_fingerprint,
                provider_request_id_sha256=None,
            )
        return RubricAIProviderResult(
            payload=payload,
            usage=usage,
            latency_ms=latency_ms,
            output_fingerprint=output_fingerprint,
            provider_request_id_sha256=provider_request_id_sha256,
        )

    def _validate_configuration(self) -> None:
        _ = self.provider_name
        provider_settings = self._provider_settings()
        try:
            rubric_release_settings = replace(
                provider_settings,
                ai_essay_review_provider_release_id=(
                    self._settings.ai_essay_rubric_provider_release_id
                ),
            )
            validate_grounded_review_provider_security(rubric_release_settings)
        except RuntimeError as exc:
            raise RubricAIProviderConfigurationError(str(exc)) from exc
        if not provider_settings.ai_api_key.strip():
            raise RubricAIProviderConfigurationError("An API key is required for rubric generation")
        if not self.generator_model or not self.critic_model:
            raise RubricAIProviderConfigurationError(
                "Generator and critic models must both be explicitly configured"
            )
        if self._settings.ai_essay_rubric_max_input_bytes <= 0:
            raise RubricAIProviderConfigurationError(
                "AI_ESSAY_RUBRIC_MAX_INPUT_BYTES must be configured"
            )
        if self._settings.ai_essay_rubric_max_output_bytes <= 0:
            raise RubricAIProviderConfigurationError(
                "AI_ESSAY_RUBRIC_MAX_OUTPUT_BYTES must be configured"
            )

    def _provider_settings(self) -> Settings:
        """Use the dedicated essay provider when it is configured.

        The rubric pipeline historically reused the global AI credential even
        though its models and release were configured for TokenRhythm.  Keep the
        global provider as a backwards-compatible fallback for deployments that
        have not configured the dedicated essay route.
        """

        if not (
            self._settings.ai_essay_tokenrhythm_base_url.strip()
            or self._settings.ai_essay_tokenrhythm_api_key.strip()
        ):
            return self._settings
        return replace(
            self._settings,
            ai_provider_base_url=self._settings.ai_essay_tokenrhythm_base_url,
            ai_api_key=self._settings.ai_essay_tokenrhythm_api_key,
            ai_essay_review_provider_https_host_allowlist=(
                self._settings.ai_essay_tokenrhythm_provider_https_host_allowlist
            ),
            ai_essay_review_provider_dpa_confirmed=(
                self._settings.ai_essay_tokenrhythm_provider_dpa_confirmed
            ),
            ai_essay_review_provider_idempotency_capability=(
                self._settings.ai_essay_tokenrhythm_provider_idempotency_capability
            ),
            ai_essay_review_provider_idempotency_key_header=(
                self._settings.ai_essay_tokenrhythm_provider_idempotency_key_header
            ),
            ai_essay_review_provider_request_id_header=(
                self._settings.ai_essay_tokenrhythm_provider_request_id_header
            ),
            ai_essay_review_provider_replay_retention_seconds=(
                self._settings.ai_essay_tokenrhythm_provider_replay_retention_seconds
            ),
        )


def _validation_error_summary(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        items = errors()
        summaries: list[str] = []
        for item in items[:8]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "root"
            summaries.append(f"{location}:{item.get('type', 'invalid')}")
        if summaries:
            return ", ".join(summaries)
    detail = " ".join(str(exc).split())
    return detail[:500] or "invalid structured output"


def _bind_server_owned_fields(
    payload: dict[str, Any],
    *,
    document: RubricAIProviderDocument,
    phase: Literal["generator", "critic"],
) -> None:
    """Keep identity and version fields authoritative on the server.

    The model decides rubric content. Repeating UUIDs, hashes, score totals and
    our schema version is serialization work and must not be a publication gate.
    """

    payload.update(
        {
            "schemaVersion": (
                "guokao-essay-rubric-generator-schema-v3"
                if phase == "generator"
                else "guokao-essay-rubric-critic-schema-v3"
            ),
            "taskVersionId": str(document.task_version_id),
            "materialBundleVersionId": str(document.material_bundle_version_id),
            "promptSha256": document.prompt_sha256,
            "materialContentSha256": document.material_content_sha256,
            "totalMaximum": document.maximum_score,
        }
    )


def _http_error_summary(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = " ".join(str(error.get("code", "")).split())
            message = " ".join(str(error.get("message", "")).split())
            return f"{code} {message}".strip()[:500] or "provider rejected request"
        message = " ".join(str(payload.get("message", "")).split())
        if message:
            return message[:500]
    return "provider rejected request"


_GENERATOR_PROMPT = (
    "你是国考申论评分规程生成器。输入只包含一个已发布题目和它的完整材料。"
    "必须基于全部材料返回完整 json 评分规程；不得裁剪、概括替代材料，不得引用"
    "外部知识。每个材料得分点只需给出 materialId、segmentId 和逐字 quote；quote 必须是"
    "对应 segment.text 中连续存在、逐字符完全相同的 4 至 80 字子串，直接复制原文，不能改写、"
    "纠错、补字、删字或替换标点；"
    "字符偏移与 SHA-256 由服务端计算，禁止模型生成。criterion 总分必须等于题目总分，材料型 criterion 的"
    "point 分值必须完全守恒；结构、表达等非材料维度可不生成 point。"
    "同一原文切片不得重复给分。不要输出 official、verified"
    "或任何用户、考生、答案字段。"
)

_CRITIC_PROMPT = (
    "你是独立的国考申论评分规程 critic/reviser。只返回完整 json。逐项核验候选规程与完整题目材料，"
    "修正引用、重复要点、分类和分值守恒，并始终返回一份完整终稿，禁止只返回"
    "补丁。引用只返回 materialId、segmentId 和逐字 quote，不生成偏移或哈希。逐条忽略候选中"
    "无法定位的 quote，回到对应 segment.text 重新复制一个连续存在、逐字符完全相同的 4 至 80 字"
    "短句；不得改写、纠错、补字、删字或替换标点。输出前必须对每个 quote 在对应 segment.text"
    "中做逐字符查找。只有终稿中每个引用都能逐字定位、所有分值守恒且无重复计分时才可"
    "approved=true；否则 approved=false 并列出 rejectionCodes。不得声称规程是"
    "官方或已验证，不得输出用户、考生或答案字段。"
)


def _parse_usage(value: Any) -> ModelTokenUsage:
    if not isinstance(value, dict):
        raise ValueError("usage must be an object")
    input_tokens = value.get("prompt_tokens")
    output_tokens = value.get("completion_tokens")
    details = value.get("prompt_tokens_details") or {}
    cached_tokens = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    if any(
        isinstance(item, bool) or not isinstance(item, int)
        for item in (input_tokens, output_tokens, cached_tokens)
    ):
        raise ValueError("provider usage token fields must be integers")
    return ModelTokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
    )
