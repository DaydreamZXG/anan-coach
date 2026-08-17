from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.domains.xingce_learning.cause_agent.contracts import CauseAgentEvidence
from app.domains.xingce_learning.cause_agent.provider import (
    CauseAgentProvider,
    CauseAgentProviderConfigurationError,
    CauseAgentProviderError,
    build_cause_agent_providers,
)


def test_production_provider_fails_closed_until_every_audit_control_is_frozen() -> None:
    with pytest.raises(CauseAgentProviderConfigurationError, match="AI_API_KEY"):
        CauseAgentProvider(Settings(environment="production")).validate_configuration()

    unsafe = _production_settings(ai_provider_base_url="http://cause.example.test/v1")
    with pytest.raises(CauseAgentProviderConfigurationError, match="HTTPS"):
        CauseAgentProvider(unsafe).validate_configuration()

    unapproved = _production_settings(
        ai_xingce_cause_provider_https_host_allowlist="other.example.test"
    )
    with pytest.raises(CauseAgentProviderConfigurationError, match="allowlisted"):
        CauseAgentProvider(unapproved).validate_configuration()

    missing_dpa = _production_settings(ai_xingce_cause_provider_dpa_confirmed=False)
    CauseAgentProvider(missing_dpa).validate_configuration()

    missing_replay = _production_settings(
        ai_xingce_cause_provider_idempotency_capability="best_effort"
    )
    with pytest.raises(CauseAgentProviderConfigurationError, match="replayable"):
        CauseAgentProvider(missing_replay).validate_configuration()


def test_provider_sends_idempotency_key_and_parses_structured_output() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"X-Provider-Request-Id": "provider-request-1"},
            json={
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 35,
                    "prompt_tokens_details": {"cached_tokens": 20},
                },
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "output": {
                                        "kind": "candidate_ready",
                                        "causeCode": "stem_misread",
                                        "summary": "漏看了题干限定词",
                                        "improvement": "先圈出限定词再答题",
                                        "evidenceRefs": ["assessment:1"],
                                        "limitations": ["未观察到线下思考过程"],
                                    }
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
            },
        )

    provider = CauseAgentProvider(
        _production_settings(),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.generate(
        evidence=_evidence(),
        followup_answer=None,
        idempotency_key="xingce-cause:run-1:round:0",
    )

    assert result.output.kind == "candidate_ready"
    assert result.usage.input_tokens == 120
    assert result.usage.cached_input_tokens == 20
    assert result.usage.output_tokens == 35
    assert result.provider_request_id_header == "x-provider-request-id"
    assert len(result.provider_request_id_fingerprint or "") == 64
    assert captured["headers"]["idempotency-key"] == "xingce-cause:run-1:round:0"
    assert captured["body"]["model"] == "gemini-3.5-flash"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert "scratchReasoning" in captured["body"]["messages"][1]["content"]
    assert "freehand_unknown" in captured["body"]["messages"][0]["content"]
    assert (
        captured["body"]["response_format"]["json_schema"]["schema"]["properties"]
        ["output"]
    )


def test_deepseek_v4_provider_uses_supported_json_object_mode() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"X-Provider-Request-Id": "provider-request-deepseek"},
            json={
                "model": "deepseek-v4-pro",
                "usage": {"prompt_tokens": 80, "completion_tokens": 24},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "output": {
                                        "kind": "candidate_ready",
                                        "causeCode": "stem_misread",
                                        "summary": "漏核了第二个判断",
                                        "improvement": "逐项核对后再选答案",
                                        "evidenceRefs": ["assessment:1"],
                                        "limitations": [],
                                    }
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
            },
        )

    provider = CauseAgentProvider(
        _production_settings(
            ai_xingce_cause_model="deepseek-v4-pro",
            ai_xingce_cause_provider_release_id="tokenrhythm-deepseek-v4-pro",
        ),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.generate(
        evidence=_evidence(),
        followup_answer=None,
        idempotency_key="xingce-cause:deepseek:round:0",
    )

    assert result.output.kind == "candidate_ready"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["thinking"] == {"type": "disabled"}


def test_cause_provider_routes_keep_real_order_and_model_identity() -> None:
    providers = build_cause_agent_providers(
        _production_settings(
            ai_xingce_cause_deepseek_api_key="official-key",
            ai_xingce_cause_relay_api_key="relay-key",
        )
    )

    assert [provider.route_key for provider in providers] == [
        "tokenrhythm",
        "deepseek_official",
        "user_relay",
    ]
    assert [provider.model for provider in providers] == [
        "gemini-3.5-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash-free",
    ]
    assert [provider.base_url for provider in providers] == [
        "https://cause.example.test/v1",
        "https://api.deepseek.com",
        "https://ergouapi.co/v1",
    ]
    for provider in providers:
        provider.validate_configuration()


@pytest.mark.parametrize(
    ("status_code", "failover_safe"),
    [(401, False), (408, True), (429, True), (500, True), (504, True)],
)
def test_provider_marks_only_recoverable_http_failures_for_cross_route(
    status_code: int,
    failover_safe: bool,
) -> None:
    provider = CauseAgentProvider(
        _production_settings(),
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(status_code, json={"error": {}})
            )
        ),
    )

    with pytest.raises(CauseAgentProviderError) as captured:
        provider.generate(
            evidence=_evidence(),
            followup_answer=None,
            idempotency_key="xingce-cause:failure-contract",
        )

    assert captured.value.failover_safe is failover_safe


def _production_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "ai_api_key": "provider-key",
        "ai_provider_base_url": "https://cause.example.test/v1",
        "ai_xingce_cause_model": "gemini-3.5-flash",
        "ai_xingce_cause_provider_release_id": "cause-provider-2026-07-16.1",
        "ai_xingce_cause_provider_https_host_allowlist": "cause.example.test",
        "ai_xingce_cause_provider_dpa_confirmed": True,
        "ai_xingce_cause_provider_idempotency_capability": "replayable_queryable",
        "ai_xingce_cause_provider_idempotency_key_header": "Idempotency-Key",
        "ai_xingce_cause_provider_request_id_header": "X-Provider-Request-Id",
        "ai_xingce_cause_provider_replay_retention_seconds": 604_800,
        "ai_xingce_cause_max_input_bytes": 1_000_000,
        "ai_xingce_cause_max_output_bytes": 100_000,
    }
    values.update(changes)
    return Settings(**values)


def _evidence() -> CauseAgentEvidence:
    return CauseAgentEvidence(
        attempt={"evidenceRefs": ["assessment:1"]},
        reflection={"evidenceRefs": ["reflection-version:1"]},
        explanations={"evidenceRefs": ["content-version:1"]},
        taxonomy={
            "evidenceRefs": ["cause-taxonomy:v1"],
            "causes": [{"code": "stem_misread"}],
        },
        scratch_reasoning={
            "contractVersion": "xingce-scratch-reasoning@v1",
            "items": [
                {
                    "questionId": "q-1",
                    "coverage": "partial",
                    "observations": [{"kind": "freehand_unknown"}],
                }
            ],
            "evidenceRefs": ["scratch-reasoning:q-1"],
        },
    )
