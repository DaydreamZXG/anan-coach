from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
import time
from typing import Any, Callable, Literal, TypedDict

import httpx


CONTRACT_VERSION = "hunyuan-text-candidate@v2"
PROVIDER_NAME = "cloudbase-hunyuan"
CONNECTIVITY_PURPOSE = "connectivity_probe"
ESSAY_SCORE_PURPOSE = "essay_score_candidate"
COMPANION_PURPOSE = "companion_answer"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_FUNCTION_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,59}$")
_RELEASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_ROLES = frozenset({"system", "user", "assistant"})


class HunyuanMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class HunyuanCloudFunctionConfigurationError(RuntimeError):
    pass


class HunyuanCloudFunctionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class HunyuanTokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class HunyuanCloudFunctionSettings:
    environment_id: str
    api_key: str
    function_name: str = "hunyuan-essay-grader"
    release_id: str = ""
    model: str = "hunyuan-2.0-instruct-20251111"
    maximum_input_bytes: int = 196608
    maximum_output_bytes: int = 262144

    @classmethod
    def from_environment(cls) -> "HunyuanCloudFunctionSettings":
        return cls(
            environment_id=os.getenv("CLOUDBASE_ENV_ID", ""),
            api_key=os.getenv("CLOUDBASE_API_KEY", ""),
            function_name=os.getenv(
                "CLOUDBASE_HUNYUAN_FUNCTION_NAME",
                "hunyuan-essay-grader",
            ),
            release_id=os.getenv("CLOUDBASE_HUNYUAN_RELEASE_ID", ""),
            model=os.getenv(
                "CLOUDBASE_HUNYUAN_MODEL",
                "hunyuan-2.0-instruct-20251111",
            ),
            maximum_input_bytes=int(
                os.getenv("CLOUDBASE_HUNYUAN_MAX_INPUT_BYTES", "196608")
            ),
            maximum_output_bytes=int(
                os.getenv("CLOUDBASE_HUNYUAN_MAX_OUTPUT_BYTES", "262144")
            ),
        )

    @classmethod
    def from_companion_environment(cls) -> "HunyuanCloudFunctionSettings":
        shared = cls.from_environment()
        return cls(
            environment_id=os.getenv(
                "CLOUDBASE_COMPANION_ENV_ID",
                shared.environment_id,
            ),
            api_key=os.getenv(
                "CLOUDBASE_COMPANION_API_KEY",
                shared.api_key,
            ),
            function_name=os.getenv(
                "CLOUDBASE_HUNYUAN_COMPANION_FUNCTION_NAME",
                shared.function_name,
            ),
            release_id=os.getenv(
                "CLOUDBASE_HUNYUAN_COMPANION_RELEASE_ID",
                "",
            ),
            model=os.getenv(
                "CLOUDBASE_HUNYUAN_COMPANION_MODEL",
                shared.model,
            ),
            maximum_input_bytes=int(
                os.getenv(
                    "CLOUDBASE_HUNYUAN_COMPANION_MAX_INPUT_BYTES",
                    str(shared.maximum_input_bytes),
                )
            ),
            maximum_output_bytes=int(
                os.getenv(
                    "CLOUDBASE_HUNYUAN_COMPANION_MAX_OUTPUT_BYTES",
                    str(shared.maximum_output_bytes),
                )
            ),
        )


@dataclass(frozen=True)
class HunyuanCloudFunctionResult:
    request_id: str
    provider_name: str
    provider_release_id: str
    model: str
    text: str
    usage: HunyuanTokenUsage
    output_fingerprint: str
    latency_ms: int


def validate_hunyuan_cloud_function_configuration(
    settings: HunyuanCloudFunctionSettings,
) -> None:
    env_id = settings.environment_id.strip().casefold()
    if _ENVIRONMENT_PATTERN.fullmatch(env_id) is None:
        raise HunyuanCloudFunctionConfigurationError(
            "CLOUDBASE_ENV_ID must be a valid CloudBase environment ID"
        )
    if not settings.api_key.strip():
        raise HunyuanCloudFunctionConfigurationError("CLOUDBASE_API_KEY is required")
    if _FUNCTION_PATTERN.fullmatch(settings.function_name.strip()) is None:
        raise HunyuanCloudFunctionConfigurationError(
            "CLOUDBASE_HUNYUAN_FUNCTION_NAME has an invalid format"
        )
    release_id = settings.release_id.strip()
    if _RELEASE_PATTERN.fullmatch(release_id) is None:
        raise HunyuanCloudFunctionConfigurationError(
            "CLOUDBASE_HUNYUAN_RELEASE_ID is required and must be versioned"
        )
    if not settings.model.strip():
        raise HunyuanCloudFunctionConfigurationError(
            "CLOUDBASE_HUNYUAN_MODEL is required"
        )
    if settings.maximum_input_bytes <= 0:
        raise HunyuanCloudFunctionConfigurationError(
            "CLOUDBASE_HUNYUAN_MAX_INPUT_BYTES must be positive"
        )
    if settings.maximum_output_bytes <= 0:
        raise HunyuanCloudFunctionConfigurationError(
            "CLOUDBASE_HUNYUAN_MAX_OUTPUT_BYTES must be positive"
        )


class HunyuanCloudFunctionClient:
    """Closed transport to the server-owned CloudBase Hunyuan model release."""

    def __init__(
        self,
        settings: HunyuanCloudFunctionSettings,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        validate_hunyuan_cloud_function_configuration(settings)
        self._settings = settings
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(135.0, connect=15.0))
        )

    @property
    def endpoint(self) -> str:
        env_id = self._settings.environment_id.strip().casefold()
        function_name = self._settings.function_name.strip()
        return (
            f"https://{env_id}.api.tcloudbasegateway.com/v1/functions/{function_name}"
        )

    def connectivity_probe(
        self,
        *,
        request_id: str,
        messages: list[HunyuanMessage],
    ) -> HunyuanCloudFunctionResult:
        return self._invoke(
            request_id=request_id,
            purpose=CONNECTIVITY_PURPOSE,
            messages=messages,
        )

    def score_candidate(
        self,
        *,
        request_id: str,
        messages: list[HunyuanMessage],
    ) -> HunyuanCloudFunctionResult:
        return self._invoke(
            request_id=request_id,
            purpose=ESSAY_SCORE_PURPOSE,
            messages=messages,
        )

    def answer_companion(
        self,
        *,
        request_id: str,
        messages: list[HunyuanMessage],
    ) -> HunyuanCloudFunctionResult:
        return self._invoke(
            request_id=request_id,
            purpose=COMPANION_PURPOSE,
            messages=messages,
        )

    def _invoke(
        self,
        *,
        request_id: str,
        purpose: Literal[
            "connectivity_probe",
            "essay_score_candidate",
            "companion_answer",
        ],
        messages: list[HunyuanMessage],
    ) -> HunyuanCloudFunctionResult:
        document = _request_document(
            request_id=request_id,
            purpose=purpose,
            messages=messages,
        )
        content = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(content) > self._settings.maximum_input_bytes:
            raise HunyuanCloudFunctionError(
                "request_invalid",
                "CloudBase Hunyuan request exceeds the configured input limit",
                retryable=False,
            )
        started = time.monotonic()
        try:
            with self._client_factory() as client:
                response = client.post(
                    self.endpoint,
                    headers={
                        "Authorization": (f"Bearer {self._settings.api_key.strip()}"),
                        "Content-Type": "application/json",
                        "X-Request-Id": request_id,
                    },
                    content=content,
                )
                response.raise_for_status()
                envelope = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise HunyuanCloudFunctionError(
                "provider_failed",
                f"CloudBase function invocation failed with HTTP {status}",
                retryable=status in {408, 425, 429} or status >= 500,
            ) from exc
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as exc:
            raise HunyuanCloudFunctionError(
                "provider_failed",
                "CloudBase function invocation failed",
                retryable=isinstance(exc, httpx.RequestError),
            ) from exc

        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        payload = _unwrap_cloudbase_response(envelope)
        return _validate_result(
            payload,
            expected_request_id=request_id,
            expected_model=self._settings.model.strip(),
            release_id=self._settings.release_id.strip(),
            maximum_output_bytes=self._settings.maximum_output_bytes,
            latency_ms=latency_ms,
        )


def _request_document(
    *,
    request_id: str,
    purpose: Literal[
        "connectivity_probe",
        "essay_score_candidate",
        "companion_answer",
    ],
    messages: list[HunyuanMessage],
) -> dict[str, Any]:
    if _IDENTIFIER_PATTERN.fullmatch(request_id) is None:
        raise HunyuanCloudFunctionError(
            "request_invalid",
            "request_id has an invalid format",
            retryable=False,
        )
    if not 1 <= len(messages) <= 8:
        raise HunyuanCloudFunctionError(
            "request_invalid",
            "messages must contain between one and eight items",
            retryable=False,
        )
    normalized: list[dict[str, str]] = []
    for message in messages:
        if set(message) != {"role", "content"}:
            raise HunyuanCloudFunctionError(
                "request_invalid",
                "message fields do not match the v1 contract",
                retryable=False,
            )
        role = message["role"]
        content = message["content"]
        if role not in _ALLOWED_ROLES:
            raise HunyuanCloudFunctionError(
                "request_invalid",
                "message role is not permitted",
                retryable=False,
            )
        if not isinstance(content, str) or not content:
            raise HunyuanCloudFunctionError(
                "request_invalid",
                "message content must be non-empty text",
                retryable=False,
            )
        normalized.append({"role": role, "content": content})
    return {
        "contractVersion": CONTRACT_VERSION,
        "requestId": request_id,
        "purpose": purpose,
        "messages": normalized,
    }


def _unwrap_cloudbase_response(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise HunyuanCloudFunctionError(
            "response_invalid",
            "CloudBase response must be an object",
            retryable=False,
        )
    code = envelope.get("code")
    if code not in (None, 0, "0", "SUCCESS", "success"):
        raise HunyuanCloudFunctionError(
            "provider_failed",
            "CloudBase returned a failed function envelope",
            retryable=False,
        )
    candidate: Any = envelope
    data = envelope.get("data")
    if isinstance(data, dict):
        candidate = data.get("response_data", data.get("responseData", data))
    elif "result" in envelope:
        candidate = envelope["result"]
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise HunyuanCloudFunctionError(
                "response_invalid",
                "CloudBase function result is not valid JSON",
                retryable=False,
            ) from exc
    if not isinstance(candidate, dict):
        raise HunyuanCloudFunctionError(
            "response_invalid",
            "CloudBase function result must be an object",
            retryable=False,
        )
    return candidate


def _validate_result(
    payload: dict[str, Any],
    *,
    expected_request_id: str,
    expected_model: str,
    release_id: str,
    maximum_output_bytes: int,
    latency_ms: int,
) -> HunyuanCloudFunctionResult:
    error_payload = payload.get("error")
    if isinstance(error_payload, dict):
        code = error_payload.get("code")
        safe_code = code if isinstance(code, str) and code else "provider_failed"
        raise HunyuanCloudFunctionError(
            safe_code,
            "CloudBase Hunyuan function rejected the request",
            retryable=safe_code == "provider_failed",
        )
    required = {
        "contractVersion",
        "requestId",
        "provider",
        "model",
        "text",
        "usage",
        "outputSha256",
    }
    if set(payload) != required:
        raise HunyuanCloudFunctionError(
            "response_invalid",
            "CloudBase function result fields do not match the v1 contract",
            retryable=False,
        )
    if payload["contractVersion"] != CONTRACT_VERSION:
        raise HunyuanCloudFunctionError(
            "response_mismatch", "contract version mismatch", retryable=False
        )
    if payload["requestId"] != expected_request_id:
        raise HunyuanCloudFunctionError(
            "response_mismatch", "request identity mismatch", retryable=False
        )
    if payload["provider"] != PROVIDER_NAME:
        raise HunyuanCloudFunctionError(
            "response_mismatch", "provider identity mismatch", retryable=False
        )
    if payload["model"] != expected_model:
        raise HunyuanCloudFunctionError(
            "response_mismatch", "model identity mismatch", retryable=False
        )
    text = payload["text"]
    if not isinstance(text, str) or not text:
        raise HunyuanCloudFunctionError(
            "response_invalid", "provider text is empty", retryable=False
        )
    if len(text.encode("utf-8")) > maximum_output_bytes:
        raise HunyuanCloudFunctionError(
            "response_invalid",
            "provider text exceeds the configured output limit",
            retryable=False,
        )
    fingerprint = sha256(text.encode("utf-8")).hexdigest()
    if payload["outputSha256"] != fingerprint:
        raise HunyuanCloudFunctionError(
            "response_mismatch", "output fingerprint mismatch", retryable=False
        )
    usage_payload = payload["usage"]
    if not isinstance(usage_payload, dict) or set(usage_payload) != {
        "promptTokens",
        "completionTokens",
        "totalTokens",
    }:
        raise HunyuanCloudFunctionError(
            "response_invalid", "token usage is invalid", retryable=False
        )
    values = [
        usage_payload["promptTokens"],
        usage_payload["completionTokens"],
        usage_payload["totalTokens"],
    ]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ):
        raise HunyuanCloudFunctionError(
            "response_invalid", "token usage is invalid", retryable=False
        )
    usage = HunyuanTokenUsage(
        prompt_tokens=values[0],
        completion_tokens=values[1],
        total_tokens=values[2],
    )
    return HunyuanCloudFunctionResult(
        request_id=expected_request_id,
        provider_name=PROVIDER_NAME,
        provider_release_id=release_id,
        model=expected_model,
        text=text,
        usage=usage,
        output_fingerprint=fingerprint,
        latency_ms=latency_ms,
    )
