from __future__ import annotations

from hashlib import sha256
import json

import pytest

from app.core.config import Settings
from app.domains.ai.hunyuan_companion import HunyuanCompanionProvider, companion_provider
from app.domains.ai.provider import AIProvider
from app.domains.ai.provider import AIProviderResponseError
from app.domains.essay_intelligence.hunyuan_cloud_function import (
    HunyuanCloudFunctionResult,
    HunyuanCloudFunctionSettings,
    HunyuanTokenUsage,
)


class _Transport:
    endpoint = (
        "https://zhus-d3gkwjeltb8e1ffd7.api.tcloudbasegateway.com/v1/functions/hunyuan-essay-grader"
    )

    def __init__(self, text: str) -> None:
        self.text = text
        self.request_id = ""
        self.messages: list[dict[str, str]] = []

    def answer_companion(
        self,
        *,
        request_id: str,
        messages: list[dict[str, str]],
    ) -> HunyuanCloudFunctionResult:
        self.request_id = request_id
        self.messages = messages
        return HunyuanCloudFunctionResult(
            request_id=request_id,
            provider_name="cloudbase-hunyuan",
            provider_release_id="hunyuan-companion-2026-07-28.1",
            model="hunyuan-test",
            text=self.text,
            usage=HunyuanTokenUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
            output_fingerprint=sha256(self.text.encode("utf-8")).hexdigest(),
            latency_ms=120,
        )


def _provider(text: str) -> tuple[HunyuanCompanionProvider, _Transport]:
    transport = _Transport(text)
    provider = HunyuanCompanionProvider(
        Settings(),
        hunyuan_settings=HunyuanCloudFunctionSettings(
            environment_id="zhus-d3gkwjeltb8e1ffd7",
            api_key="test",
            release_id="hunyuan-companion-2026-07-28.1",
            model="hunyuan-test",
        ),
        transport=transport,  # type: ignore[arg-type]
    )
    return provider, transport


def _prepared(provider: HunyuanCompanionProvider) -> dict[str, object]:
    return provider.prepare_companion_question(
        route_decision={"modelTier": "small"},
        question_text="我今晚先练什么？",
        context={"targetExam": "国考"},
        sources=[
            {
                "sourceRef": "source-1",
                "sourceType": "today_plan",
                "content": "今晚有一道资料分析复练。",
            }
        ],
    )


def test_hunyuan_companion_uses_closed_cloud_function_contract() -> None:
    content = (
        '{"answer":"先做资料分析复练。","nextActions":["完成后记录错因"],'
        '"sourceRefs":["source-1"],"confidence":"high","limitations":[]}'
    )
    provider, transport = _provider(content)
    prepared = _prepared(provider)

    result = provider.answer_prepared_companion_question(
        prepared=prepared,
        idempotency_key="companion-command-0001",
    )

    assert transport.request_id.startswith("companion-")
    assert [message["role"] for message in transport.messages] == ["system", "user"]
    assert result.answer == "先做资料分析复练。"
    assert result.source_refs == ["source-1"]
    assert result.usage.input_tokens == 100
    assert prepared["providerReleaseId"] == "hunyuan-companion-2026-07-28.1"
    assert prepared["providerReplayRetentionSeconds"] == 0


def test_hunyuan_companion_ignores_the_advisory_action_protocol() -> None:
    # The model may still emit requestedActions in its output. Provider-side
    # parsing deliberately drops the field: action output is not an execution
    # authority and the server derives actions from the classified intent.
    content = (
        '{"answer":"已识别为今晚 90 分钟。","nextActions":["预览调整"],'
        '"sourceRefs":["source-1"],"confidence":"high","limitations":[],'
        '"requestedActions":[{"name":"preview_daily_capacity",'
        '"arguments":{"availableMinutes":90}}]}'
    )
    provider, _transport = _provider(content)

    result = provider.answer_prepared_companion_question(
        prepared=_prepared(provider),
        idempotency_key="companion-command-action-1",
    )

    assert result.answer == "已识别为今晚 90 分钟。"
    assert result.source_refs == ["source-1"]


def test_hunyuan_companion_ignores_unknown_advisory_actions_without_losing_answer() -> None:
    content = (
        '{"answer":"可以开始资料分析。","nextActions":["进入训练"],'
        '"sourceRefs":["source-1"],"confidence":"high","limitations":[],'
        '"requestedActions":['
        '{"name":"change_everything","arguments":{"minutes":999}},'
        '{"name":"open_today_training","arguments":{"invented":true}}]}'
    )
    provider, _transport = _provider(content)

    result = provider.answer_prepared_companion_question(
        prepared=_prepared(provider),
        idempotency_key="companion-command-action-drift",
    )

    assert result.answer == "可以开始资料分析。"
    assert result.source_refs == ["source-1"]


def test_hunyuan_companion_keeps_grounding_strict_but_tolerates_advisory_shape_drift() -> None:
    provider, _transport = _provider(
        '{"answer":"可以开始资料分析。","sourceRefs":["source-1"],'
        '"confidence":"high","gatewayMetadata":{"format":"compatible"}}'
    )

    result = provider.answer_prepared_companion_question(
        prepared=_prepared(provider),
        idempotency_key="companion-command-shape-drift-1",
    )

    assert result.answer == "可以开始资料分析。"
    assert result.source_refs == ["source-1"]
    assert result.limitations == []


def test_hunyuan_companion_accepts_legacy_actions_without_advertising_tools() -> None:
    content = (
        '{"answer":"已准备周节奏预览。","nextActions":["确认周节奏"],'
        '"sourceRefs":["source-1"],"confidence":"high","limitations":[],'
        '"requestedActions":[{"name":"preview_weekly_schedule",'
        '"arguments":{"mockExamDay":"saturday","fixedRestDay":"sunday"}}]}'
    )
    provider, _transport = _provider(content)
    prepared = _prepared(provider)

    result = provider.answer_prepared_companion_question(
        prepared=prepared,
        idempotency_key="companion-command-action-v2",
    )

    assert result.answer == "已准备周节奏预览。"
    body = prepared["body"]
    assert isinstance(body, dict)
    messages = body["messages"]
    assert isinstance(messages, list)
    payload = json.loads(messages[1]["content"])
    assert "available_action_tools" not in payload
    assert "requestedActions" not in payload["required_schema"]
    assert "nextActions" not in payload["required_schema"]


def test_hunyuan_companion_validates_without_openai_gateway_credentials() -> None:
    provider, _ = _provider(
        '{"answer":"继续。","nextActions":["完成任务"],'
        '"sourceRefs":["source-1"],"confidence":"high","limitations":[]}'
    )

    provider.validate_configuration(idempotency_key="release-configuration-check")

    assert provider.release_id == "hunyuan-companion-2026-07-28.1"


def test_hunyuan_companion_rejects_unstructured_output() -> None:
    provider, _ = _provider("我建议你先做资料分析。")
    prepared = _prepared(provider)

    with pytest.raises(AIProviderResponseError) as caught:
        provider.answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key="companion-command-0002",
        )

    assert caught.value.code == "provider_content_invalid"


def test_openai_compatible_companion_uses_dedicated_gateway_settings() -> None:
    provider = companion_provider(
        Settings(
            ai_provider_base_url="https://shared.example/v1",
            ai_api_key="shared-key",
            ai_model="shared-model",
            ai_small_model="shared-small",
            ai_high_capability_model="shared-high",
            ai_companion_transport="openai_compatible",
            ai_companion_provider_base_url="https://companion.example/v1",
            ai_companion_api_key="companion-key",
            ai_companion_model="companion-model",
        )
    )

    assert isinstance(provider, AIProvider)
    assert provider.model_for_tier("small") == "companion-model"
    assert provider.model_for_tier("high") == "companion-model"
    assert provider._settings.ai_provider_base_url == "https://companion.example/v1"
    assert provider._settings.ai_api_key == "companion-key"
