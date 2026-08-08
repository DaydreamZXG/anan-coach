from __future__ import annotations

from hashlib import sha256
from inspect import signature
from uuid import uuid4

import pytest
from httpx import TimeoutException

from app.domains.ai.governance.contracts import EvidenceReference, IntentDecision
from app.domains.ai.governance.retrieval import RetrievedSource, RetrievalBundle
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.ai.provider import (
    AICompanionAnswerResult,
    AIProviderResponseError,
)
from app.domains.companion.agent import (
    default_companion_actions,
    is_capacity_change_request,
    requested_plan_focus,
    requested_plan_minutes,
    requested_weekly_schedule,
)
from app.domains.companion.model_reply import CompanionModelReply
from app.domains.companion.provider_context import CompanionProviderContextAssembler
from app.domains.companion.service import _allowed_actions
from app.domains.companion.short_term import ShortTermConversationMessage


def planner_decision() -> IntentDecision:
    return IntentDecision.model_validate(
        {
            "scenarioKey": "planning_explanation",
            "respondingAgent": "planner",
            "answerType": "plan_explanation",
            "modelTier": "small",
            "policyDecision": "answer",
            "companionIntent": "thinking",
            "reasonCodes": ["test"],
        }
    )


def _bundle(source_id: str = "source-1") -> RetrievalBundle:
    return RetrievalBundle(
        bundleId=uuid4(),
        queryFingerprint="a" * 64,
        sources=(
            RetrievedSource(
                reference=EvidenceReference(
                    kind="daily_plan",
                    sourceId=source_id,
                    version="1",
                    anchor="today",
                    title="今日计划",
                ),
                trustScope="daily_plan",
                excerpt="今日先做资料分析。",
                lexicalScore=0.5,
                semanticScore=0.5,
                sourceWeight=1,
                finalScore=0.8,
                untrustedInput=False,
            ),
        ),
        blockedSourceRefs=(),
        securityDecision="clear",
    )


def _answer(source_id: str = "source-1") -> AICompanionAnswerResult:
    text = "先做资料分析复练。"
    return AICompanionAnswerResult(
        answer=text,
        source_refs=[source_id],
        confidence="high",
        limitations=[],
        latency_ms=12,
        response_summary=text,
        usage=ModelTokenUsage(
            input_tokens=40,
            cached_input_tokens=0,
            output_tokens=10,
        ),
        output_fingerprint=sha256(text.encode("utf-8")).hexdigest(),
    )


class _RecordingProvider:
    provider_name = "recording-provider"

    def __init__(self) -> None:
        self.prepared_kwargs: dict[str, object] | None = None
        self.answer_calls: list[str] = []
        self.cached: dict[str, AICompanionAnswerResult] = {}

    def prepare_companion_question(self, **kwargs: object) -> dict[str, object]:
        self.prepared_kwargs = kwargs
        return {"model": "test-model", "payload": kwargs}

    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str,
    ) -> AICompanionAnswerResult:
        assert prepared["model"] == "test-model"
        self.answer_calls.append(idempotency_key)
        return self.cached.setdefault(idempotency_key, _answer())


class _TimeoutProvider(_RecordingProvider):
    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str,
    ) -> AICompanionAnswerResult:
        del prepared, idempotency_key
        raise TimeoutException("provider timed out")


class _FailureProvider(_RecordingProvider):
    def answer_prepared_companion_question(
        self,
        *,
        prepared: dict[str, object],
        idempotency_key: str,
    ) -> AICompanionAnswerResult:
        del prepared, idempotency_key
        raise AIProviderResponseError(
            "provider output invalid",
            code="provider_content_invalid",
            usage=ModelTokenUsage(
                input_tokens=40,
                cached_input_tokens=0,
                output_tokens=10,
            ),
            latency_ms=12,
            output_fingerprint="b" * 64,
        )


def test_provider_context_normalizes_immutable_short_term_history() -> None:
    provider = _RecordingProvider()
    history = (
        ShortTermConversationMessage(role="user", content="  我只有半小时  "),
        ShortTermConversationMessage(role="assistant", content="先完成短任务。"),
    )

    prepared = CompanionProviderContextAssembler(provider).prepare(  # type: ignore[arg-type]
        decision=planner_decision(),
        question_text="那先做哪一个？",
        context_payload={"todayPlan": {"tasks": ["资料分析"]}},
        retrieval=_bundle(),
        persona="岸岸 persona",
        short_term_history=history,
    )

    assert prepared["model"] == "test-model"
    assert provider.prepared_kwargs is not None
    assert provider.prepared_kwargs["conversation_history"] == [
        {"role": "user", "content": "我只有半小时"},
        {"role": "assistant", "content": "先完成短任务。"},
    ]
    assert history[0].content == "  我只有半小时  "


def test_model_reply_normal_dialog_and_replay_keep_the_stable_provider_key() -> None:
    provider = _RecordingProvider()
    reply = CompanionModelReply(provider)  # type: ignore[arg-type]
    prepared = {"model": "test-model"}
    retrieval_payload = _bundle().model_dump(by_alias=True, mode="json")

    first = reply.answer(
        prepared=prepared,
        idempotency_key="stable-provider-key",
        retrieval_payload=retrieval_payload,
    )
    replay = reply.answer(
        prepared=prepared,
        idempotency_key="stable-provider-key",
        retrieval_payload=retrieval_payload,
    )

    assert replay == first
    assert provider.answer_calls == ["stable-provider-key", "stable-provider-key"]
    assert len(provider.cached) == 1


def test_model_reply_preserves_timeout_and_model_failure_categories() -> None:
    retrieval_payload = _bundle().model_dump(by_alias=True, mode="json")
    with pytest.raises(TimeoutException, match="provider timed out"):
        CompanionModelReply(_TimeoutProvider()).answer(  # type: ignore[arg-type]
            prepared={"model": "test-model"},
            idempotency_key="timeout-key",
            retrieval_payload=retrieval_payload,
        )
    with pytest.raises(AIProviderResponseError) as caught:
        CompanionModelReply(_FailureProvider()).answer(  # type: ignore[arg-type]
            prepared={"model": "test-model"},
            idempotency_key="failure-key",
            retrieval_payload=retrieval_payload,
        )

    assert caught.value.code == "provider_content_invalid"


def test_model_reply_rejects_citations_outside_the_frozen_retrieval_bundle() -> None:
    provider = _RecordingProvider()
    provider.cached["invalid-source-key"] = _answer("invented-source")

    with pytest.raises(AIProviderResponseError) as caught:
        CompanionModelReply(provider).answer(  # type: ignore[arg-type]
            prepared={"model": "test-model"},
            idempotency_key="invalid-source-key",
            retrieval_payload=_bundle().model_dump(by_alias=True, mode="json"),
        )

    assert caught.value.code == "provider_grounding_invalid"


def test_actions_are_derived_only_from_intent_and_current_user_text() -> None:
    parameters = signature(default_companion_actions).parameters
    action = default_companion_actions(
        answer_type="plan_explanation",
        question_text="今晚只能学 90 分钟，帮我重新安排",
    )[0]

    assert set(parameters) == {"answer_type", "question_text"}
    assert action.model_dump(by_alias=True) == {
        "kind": "adjust_plan_preview",
        "label": "预览调整为 90 分钟",
        "targetRef": "/plans/day-adjustment/index?source=anan&availableMinutes=90",
        "requiresConfirmation": True,
    }


def test_extracts_explicit_same_day_capacity_without_guessing() -> None:
    assert requested_plan_minutes("今晚只能学1小时，帮我重新安排") == 60
    assert requested_plan_minutes("今天只有90分钟") == 90
    assert requested_plan_minutes("今天只能学1小时30分钟") == 90
    assert requested_plan_minutes("今天只能学1小时半") == 90
    assert requested_plan_minutes("今天休息，不要安排任务") == 0
    assert requested_plan_minutes("帮我调整一下今天的计划") is None
    assert requested_plan_minutes("我有8项任务") is None
    assert requested_plan_minutes("今天学25小时") is None


def test_plan_action_keeps_manual_preview_when_no_capacity_is_stated() -> None:
    action = _allowed_actions(
        decision=planner_decision(),
        question_text="帮我调整一下今天的计划",
    )[0]

    assert action.target_ref == "/plans/day-adjustment/index"


def test_explicit_exclusive_module_request_opens_confirmable_preview() -> None:
    focus = requested_plan_focus("今天我可以只学资料分析吗")
    action = _allowed_actions(
        decision=planner_decision(),
        question_text="今天我可以只学资料分析吗",
    )[0]

    assert focus is not None
    assert focus.module == "资料分析"
    assert focus.xingce_only is True
    assert action.label == "预览今天只练资料分析"
    assert action.target_ref is not None and "xingceOnly=1" in action.target_ref
    assert action.requires_confirmation is True


def test_capacity_mutation_intent_does_not_treat_explanation_as_a_write() -> None:
    assert is_capacity_change_request("今晚只能学一小时，帮我重新安排")
    assert is_capacity_change_request("今天休息，不要排任务")
    assert is_capacity_change_request("今天我可以只学资料分析吗")
    assert not is_capacity_change_request("为什么今天只有一小时？")


def test_weekly_schedule_is_deterministic_and_never_invents_missing_days() -> None:
    request = requested_weekly_schedule("把周六安排成模考日，周日休息")
    action = _allowed_actions(
        decision=planner_decision(),
        question_text="把周六安排成模考日，周日休息",
    )[0]

    assert request is not None
    assert request.mock_exam_day == "saturday"
    assert request.fixed_rest_day == "sunday"
    assert action.target_ref == (
        "/plans/study-preference/index?source=anan&mockExamDay=saturday&fixedRestDay=sunday"
    )
    assert requested_weekly_schedule("帮我重新安排这周") is None
    assert requested_weekly_schedule("周六既模考又休息") is None
