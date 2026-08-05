"""中控大脑决策 agent 的 LLM provider。

镜像 cause_agent/provider.py 的调用模式：严格 JSON schema（response_format
json_schema strict）+ 配置门禁 + 可重试错误分类 + 用量解析。provider 只负责
把冻结的 evidence 送到模型并解析出策略指令，不做任何业务落库。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import time
from typing import Callable
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from app.core.config import DEFAULT_AI_PROVIDER_BASE_URL, Settings
from app.domains.ai.external_processing import validate_minimized_external_payload
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.ai.provider_contract import (
    parse_provider_json_object,
    provider_failure_is_retryable,
    provider_request_audit,
    validate_provider_audit_contract,
)
from app.domains.learning_brain.agent_contracts import (
    LEARNING_BRAIN_AGENT_EVIDENCE_VERSION,
    LEARNING_BRAIN_DIRECTIVE_CONTRACT_VERSION,
    LearningBrainAgentEvidence,
    LearningBrainStrategyEnvelope,
    LearningBrainStrategyInstruction,
)


LEARNING_BRAIN_AGENT_PROMPT_VERSION = "learning-brain-strategy@v1"


class LearningBrainProviderConfigurationError(RuntimeError):
    pass


class LearningBrainProviderError(RuntimeError):
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
class LearningBrainProviderResult:
    output: LearningBrainStrategyInstruction
    usage: ModelTokenUsage
    output_fingerprint: str
    latency_ms: int
    provider_request_id_header: str | None = None
    provider_request_id_fingerprint: str | None = None


class LearningBrainProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(
                    self._settings.ai_learning_brain_agent_timeout_seconds,
                    connect=10.0,
                )
            )
        )

    @property
    def provider_name(self) -> str:
        release = self.provider_release_id
        return f"learning_brain_agent@{release}"

    @property
    def provider_release_id(self) -> str:
        return self._settings.ai_learning_brain_agent_provider_release_id.strip() or (
            "local-unreleased"
        )

    @property
    def model(self) -> str:
        return self._settings.ai_learning_brain_agent_model.strip()

    @property
    def prompt_version(self) -> str:
        return LEARNING_BRAIN_AGENT_PROMPT_VERSION

    @property
    def schema_version(self) -> str:
        return LEARNING_BRAIN_DIRECTIVE_CONTRACT_VERSION

    def validate_configuration(self) -> None:
        if self._settings.environment.strip().casefold() != "production":
            if not self.model:
                raise LearningBrainProviderConfigurationError(
                    "AI_LEARNING_BRAIN_AGENT_MODEL is required"
                )
            return
        if not self._settings.ai_api_key:
            raise LearningBrainProviderConfigurationError("AI_API_KEY is required")
        if (
            not self.model
            or not self._settings.ai_learning_brain_agent_provider_release_id.strip()
        ):
            raise LearningBrainProviderConfigurationError(
                "learning-brain agent model and provider release are required in production"
            )
        parsed = urlparse(self._settings.ai_provider_base_url.strip())
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme.casefold() != "https"
            or not host
            or self._settings.ai_provider_base_url.rstrip("/")
            == DEFAULT_AI_PROVIDER_BASE_URL.rstrip("/")
        ):
            raise LearningBrainProviderConfigurationError(
                "production learning-brain agent provider must use an approved HTTPS endpoint"
            )
        allowlist = {
            item.strip().casefold()
            for item in self._settings.ai_learning_brain_agent_provider_https_host_allowlist.split(
                ","
            )
            if item.strip()
        }
        if host not in allowlist:
            raise LearningBrainProviderConfigurationError(
                "learning-brain agent provider host is not allowlisted"
            )
        try:
            validate_provider_audit_contract(
                idempotency_capability=(
                    self._settings.ai_learning_brain_agent_provider_idempotency_capability
                ),
                idempotency_key_header=(
                    self._settings.ai_learning_brain_agent_provider_idempotency_key_header
                ),
                request_id_source=(
                    self._settings.ai_learning_brain_agent_provider_request_id_header
                ),
                replay_retention_seconds=(
                    self._settings.ai_learning_brain_agent_provider_replay_retention_seconds
                ),
            )
        except ValueError as exc:
            raise LearningBrainProviderConfigurationError(str(exc)) from exc
        if (
            self._settings.ai_learning_brain_agent_max_input_bytes <= 0
            or self._settings.ai_learning_brain_agent_max_output_bytes <= 0
            or self._settings.ai_learning_brain_agent_timeout_seconds <= 0
        ):
            raise LearningBrainProviderConfigurationError(
                "learning-brain agent provider size, timeout, and replay limits "
                "must be positive"
            )

    def generate(
        self,
        *,
        evidence: LearningBrainAgentEvidence,
        idempotency_key: str,
    ) -> LearningBrainProviderResult:
        self.validate_configuration()
        prompt = {
            "contractVersion": LEARNING_BRAIN_AGENT_EVIDENCE_VERSION,
            "instruction": (
                "你是「中控大脑」学习策略 Agent。只根据 evidence 判断，不得把"
                "推测写成事实，不得编造不存在的成绩、错因或知识点。你的任务是"
                "产出本周的策略指令：决定「本周该侧重什么」以及为侧重腾出空间的"
                "取舍（action），而不是排具体任务（具体任务由确定性计划引擎负责）。"
                "fields 使用说明：focus 是本周侧重的行测模块（必须是 evidence."
                "modules[].moduleKey 里存在的模块，选正确率最低或改善收益最高的"
                "那个）；cause_direction 用 8 类错因码之一（stem_misread / "
                "knowledge_gap / method_selection / calculation_error / "
                "time_pressure / guessing / active_abandon / other），只选 evidence."
                "error_patterns 里有依据的错因，没有就 other；essay_emphasis 是"
                "申论本周侧重（如作答-评阅、材料阅读、结构训练，依据 evidence."
                "modules 里 essay 轨道）；mock_schedule 建议每周模考安排，依据"
                "evidence.weeklyRhythm 的 mock_weekday 与剩余时间；rationale 是"
                "面向用户的人话解释（为什么这样排，依据 focus 正确率、错因、离目标"
                "分差）。action 只在确实需要取舍时给一个决策：decisionType 可以是"
                "exam_skip / phase_deprioritize / strategic_abandon / baseline_guidance "
                "之一。当 evidence.sufficientEvidence 为 false（没有模块做题证据，"
                "可能是新用户或数据已清除）时：必须输出 baseline_guidance——focus 写"
                "「建立能力基线」，scopeType=track、scopeKey=xingce（若 essay 有证据则"
                "essay），rationale 用 goal_gap 的分差说明为什么先摸底（如「当前距目标"
                "还差 X 分，先完成一次能力摸底建立基线，才能针对性规划」），不要编造"
                "模块侧重。当 sufficientEvidence 为 true 时：scopeType 是 track 或 module，"
                "scopeKey 必须是 evidence.modules[].moduleKey 里存在的键，没有依据就"
                "不要给 action（null）。只返回 JSON。"
            ),
            "requiredContract": LEARNING_BRAIN_DIRECTIVE_CONTRACT_VERSION,
            "requiredOutputSchema": LearningBrainStrategyEnvelope.model_json_schema(
                by_alias=True
            ),
            "evidence": evidence.model_dump(by_alias=True, mode="json"),
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
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "learning_brain_strategy_directive",
                    "strict": True,
                    "schema": LearningBrainStrategyEnvelope.model_json_schema(
                        by_alias=True
                    ),
                },
            },
        }
        try:
            validate_minimized_external_payload(prompt)
        except ValueError as exc:
            raise LearningBrainProviderConfigurationError(str(exc)) from exc
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if (
            self._settings.ai_learning_brain_agent_max_input_bytes > 0
            and len(encoded) > self._settings.ai_learning_brain_agent_max_input_bytes
        ):
            raise LearningBrainProviderConfigurationError(
                "learning-brain agent provider input exceeds configured capacity"
            )
        headers = {
            "Authorization": f"Bearer {self._settings.ai_api_key}",
            "Content-Type": "application/json",
            (
                self._settings.ai_learning_brain_agent_provider_idempotency_key_header.strip()
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
            raise LearningBrainProviderError(
                "provider_transport_uncertain",
                "learning-brain agent provider outcome is uncertain",
                retryable=provider_failure_is_retryable(
                    idempotency_capability=(
                        self._settings.ai_learning_brain_agent_provider_idempotency_capability
                    )
                ),
            ) from exc
        except httpx.HTTPStatusError as exc:
            retryable = provider_failure_is_retryable(
                idempotency_capability=(
                    self._settings.ai_learning_brain_agent_provider_idempotency_capability
                ),
                status_code=exc.response.status_code,
            )
            raise LearningBrainProviderError(
                f"provider_http_{exc.response.status_code}",
                "learning-brain agent provider rejected the request",
                retryable=retryable,
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            envelope = response.json()
            returned_model = envelope.get("model")
            if returned_model is not None and returned_model != self.model:
                if not (self.model == "ark-code-latest" and returned_model == "auto"):
                    raise ValueError("provider model drifted")
            usage = _usage(envelope["usage"])
            content = envelope["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content must be text")
            if (
                self._settings.ai_learning_brain_agent_max_output_bytes > 0
                and len(content.encode("utf-8"))
                > self._settings.ai_learning_brain_agent_max_output_bytes
            ):
                raise ValueError("output exceeds configured capacity")
            parsed = LearningBrainStrategyEnvelope.model_validate(
                parse_provider_json_object(content)
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            output_fingerprint = sha256(
                json.dumps(
                    envelope if "envelope" in locals() else {}, sort_keys=True
                ).encode()
            ).hexdigest()
            raise LearningBrainProviderError(
                "provider_response_invalid",
                "learning-brain agent provider returned an invalid structured response",
                retryable=True,
                usage=usage if "usage" in locals() else None,
                output_fingerprint=output_fingerprint,
                latency_ms=latency_ms,
            ) from exc
        output_fingerprint = sha256(content.encode("utf-8")).hexdigest()
        request_id_source, request_id_fingerprint = provider_request_audit(
            response=response,
            envelope=envelope,
            configured_source=(
                self._settings.ai_learning_brain_agent_provider_request_id_header
            ),
        )
        return LearningBrainProviderResult(
            output=parsed.output,
            usage=usage,
            output_fingerprint=output_fingerprint,
            latency_ms=latency_ms,
            provider_request_id_header=request_id_source,
            provider_request_id_fingerprint=request_id_fingerprint,
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
