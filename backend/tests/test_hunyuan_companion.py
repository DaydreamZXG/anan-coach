from __future__ import annotations

from hashlib import sha256

import pytest

from app.core.config import Settings
from app.domains.ai.hunyuan_companion import HunyuanCompanionProvider
from app.domains.ai.provider import AIProviderResponseError
from app.domains.essay_intelligence.hunyuan_cloud_function import (
    HunyuanCloudFunctionResult,
    HunyuanCloudFunctionSettings,
    HunyuanTokenUsage,
)


class _Transport:
    endpoint = (
        "https://zhus-d3gkwjeltb8e1ffd7.api.tcloudbasegateway.com/"
        "v1/functions/hunyuan-essay-grader"
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


def test_hunyuan_companion_rejects_unstructured_output() -> None:
    provider, _ = _provider("我建议你先做资料分析。")
    prepared = _prepared(provider)

    with pytest.raises(AIProviderResponseError) as caught:
        provider.answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key="companion-command-0002",
        )

    assert caught.value.code == "provider_content_invalid"
