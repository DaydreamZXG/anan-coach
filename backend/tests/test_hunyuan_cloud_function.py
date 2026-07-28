from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json

import httpx
import pytest

from app.domains.essay_intelligence.hunyuan_cloud_function import (
    CONTRACT_VERSION,
    HunyuanCloudFunctionClient,
    HunyuanCloudFunctionConfigurationError,
    HunyuanCloudFunctionError,
    HunyuanCloudFunctionSettings,
)


class _Response:
    def __init__(self, envelope: object, *, status_code: int = 200) -> None:
        self._envelope = envelope
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://cloudbase.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def json(self) -> object:
        return self._envelope


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.url = ""
        self.headers: dict[str, str] = {}
        self.content = b""

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
    ) -> _Response:
        self.url = url
        self.headers = headers
        self.content = content
        return self.response


def _settings(**overrides: object) -> HunyuanCloudFunctionSettings:
    settings = HunyuanCloudFunctionSettings(
        environment_id="zhus-d3gkwjeltb8e1ffd7",
        api_key="test-secret",
        function_name="hunyuan-essay-grader",
        release_id="hunyuan-cloud-function-2026-07-18.1",
        model="hunyuan-test-model",
        maximum_input_bytes=196608,
        maximum_output_bytes=262144,
    )
    return replace(settings, **overrides)


def _payload(**overrides: object) -> dict[str, object]:
    text = str(overrides.pop("text", "PONG"))
    value: dict[str, object] = {
        "contractVersion": CONTRACT_VERSION,
        "requestId": "probe-20260718-0001",
        "provider": "cloudbase-hunyuan",
        "model": "hunyuan-test-model",
        "text": text,
        "usage": {"promptTokens": 7, "completionTokens": 1, "totalTokens": 8},
        "outputSha256": sha256(text.encode()).hexdigest(),
    }
    value.update(overrides)
    return value


def test_invokes_exact_cloudbase_function_and_validates_wrapped_result() -> None:
    client = _Client(
        _Response(
            {
                "code": "SUCCESS",
                "data": {"response_data": json.dumps(_payload())},
            }
        )
    )
    result = HunyuanCloudFunctionClient(
        _settings(),
        client_factory=lambda: client,  # type: ignore[arg-type]
    ).connectivity_probe(
        request_id="probe-20260718-0001",
        messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
    )

    assert client.url == (
        "https://zhus-d3gkwjeltb8e1ffd7.api.tcloudbasegateway.com/"
        "v1/functions/hunyuan-essay-grader"
    )
    assert client.headers["Authorization"] == "Bearer test-secret"
    assert json.loads(client.content) == {
        "contractVersion": CONTRACT_VERSION,
        "requestId": "probe-20260718-0001",
        "purpose": "connectivity_probe",
        "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
    }
    assert result.text == "PONG"
    assert result.output_fingerprint == sha256(b"PONG").hexdigest()
    assert result.usage.total_tokens == 8


@pytest.mark.parametrize(
    "settings",
    [
        _settings(environment_id="https://evil.example"),
        _settings(api_key=""),
        _settings(function_name="../escape"),
        _settings(release_id=""),
        _settings(maximum_input_bytes=0),
    ],
)
def test_configuration_fails_closed(
    settings: HunyuanCloudFunctionSettings,
) -> None:
    with pytest.raises(HunyuanCloudFunctionConfigurationError):
        HunyuanCloudFunctionClient(settings)


@pytest.mark.parametrize(
    "payload",
    [
        _payload(requestId="other-request"),
        _payload(provider="other-provider"),
        _payload(model="caller-model"),
        _payload(outputSha256="0" * 64),
        {**_payload(), "unexpected": True},
        _payload(usage={"promptTokens": 1, "completionTokens": 1}),
    ],
)
def test_rejects_mismatched_or_malformed_result(payload: dict[str, object]) -> None:
    client = _Client(
        _Response({"code": "SUCCESS", "data": {"response_data": json.dumps(payload)}})
    )
    transport = HunyuanCloudFunctionClient(
        _settings(),
        client_factory=lambda: client,  # type: ignore[arg-type]
    )
    with pytest.raises(HunyuanCloudFunctionError) as caught:
        transport.connectivity_probe(
            request_id="probe-20260718-0001",
            messages=[{"role": "user", "content": "PONG"}],
        )
    assert caught.value.retryable is False


def test_rejects_oversized_or_invalid_request_identity() -> None:
    transport = HunyuanCloudFunctionClient(
        _settings(maximum_input_bytes=256),
        client_factory=lambda: _Client(_Response(_payload())),  # type: ignore[arg-type]
    )
    with pytest.raises(HunyuanCloudFunctionError):
        transport.connectivity_probe(
            request_id="probe-20260718-0001",
            messages=[{"role": "user", "content": "x" * 1024}],
        )
    with pytest.raises(HunyuanCloudFunctionError):
        transport.connectivity_probe(
            request_id="short",
            messages=[{"role": "user", "content": "PONG"}],
        )


def test_invokes_formal_score_candidate_without_exposing_model_selection() -> None:
    payload = _payload(requestId="score-20260719-0001", text='{"score":{}}')
    client = _Client(_Response(payload))
    result = HunyuanCloudFunctionClient(
        _settings(),
        client_factory=lambda: client,  # type: ignore[arg-type]
    ).score_candidate(
        request_id="score-20260719-0001",
        messages=[
            {"role": "system", "content": "server-owned scoring contract"},
            {"role": "user", "content": "anonymous immutable review input"},
        ],
    )

    request = json.loads(client.content)
    assert request["purpose"] == "essay_score_candidate"
    assert "model" not in request
    assert result.text == '{"score":{}}'


def test_invokes_companion_answer_with_stable_request_identity() -> None:
    content = (
        '{"answer":"先复盘","nextActions":["完成一道同类题"],'
        '"sourceRefs":["source-1"],"confidence":"high","limitations":[]}'
    )
    payload = _payload(requestId="companion-20260728-0001", text=content)
    client = _Client(_Response(payload))
    result = HunyuanCloudFunctionClient(
        _settings(),
        client_factory=lambda: client,  # type: ignore[arg-type]
    ).answer_companion(
        request_id="companion-20260728-0001",
        messages=[
            {"role": "system", "content": "server-owned companion contract"},
            {"role": "user", "content": "minimized learner context"},
        ],
    )

    request = json.loads(client.content)
    assert request["purpose"] == "companion_answer"
    assert request["requestId"] == "companion-20260728-0001"
    assert result.text == content


def test_http_429_is_typed_retryable_without_exposing_response_body() -> None:
    client = _Client(_Response({"secret": "must-not-surface"}, status_code=429))
    transport = HunyuanCloudFunctionClient(
        _settings(),
        client_factory=lambda: client,  # type: ignore[arg-type]
    )
    with pytest.raises(HunyuanCloudFunctionError) as caught:
        transport.connectivity_probe(
            request_id="probe-20260718-0001",
            messages=[{"role": "user", "content": "PONG"}],
        )
    assert caught.value.retryable is True
    assert "secret" not in str(caught.value)


def test_cloud_function_typed_error_is_preserved_without_returning_diagnostics() -> (
    None
):
    client = _Client(
        _Response(
            {
                "error": {
                    "code": "request_invalid",
                    "message": "diagnostic that must not cross the boundary",
                }
            }
        )
    )
    transport = HunyuanCloudFunctionClient(
        _settings(),
        client_factory=lambda: client,  # type: ignore[arg-type]
    )
    with pytest.raises(HunyuanCloudFunctionError) as caught:
        transport.connectivity_probe(
            request_id="probe-20260718-0001",
            messages=[{"role": "user", "content": "PONG"}],
        )
    assert caught.value.code == "request_invalid"
    assert caught.value.retryable is False
    assert "diagnostic" not in str(caught.value)
