from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx
import pytest

from app.domains.teach_back_provider_shadow import (
    TeachBackProviderConfig,
    TeachBackProviderConfigurationError,
    TeachBackProviderError,
    TeachBackShadowProvider,
    run_provider_shadow,
    shadow_idempotency_key,
)
from app.domains.teach_back_shadow import (
    ShadowPreparationInput,
    StudentReproductionOutput,
    prepare_shadow_evaluation,
)
from app.domains.teach_back_shadow.contracts import GroundingVerifierRequest
from app.domains.teaching_packages import SafeStructuredVisualProjection


def shadow_input(**overrides: object) -> ShadowPreparationInput:
    values: dict[str, object] = {
        "packageFingerprint": "a" * 64,
        "methodCardRef": "method-card:growth-rate-base:v7",
        "methodSteps": ("先算出基期量", "用增长量除以基期量"),
        "pitfalls": ("不要把现期量当分母",),
        "studentQuestionRef": "question:student:v1",
        "studentStem": "某指标现期为 120，增长量为 20，增长率约为多少？",
        "studentOptions": ("A. 16.7%", "B. 20%", "C. 25%", "D. 30%"),
        "studentAnswerKey": ("B",),
        "studentExplanation": "基期量为 100，增长率为 20/100=20%。",
        "userExplanation": "先用现期量减增长量得到基期量，再用增长量除以基期量。",
    }
    values.update(overrides)
    return ShadowPreparationInput.model_validate(values)


def safe_visual() -> SafeStructuredVisualProjection:
    return SafeStructuredVisualProjection(
        evidenceRef="visual:question:student:v1",
        questionRef="question:student:v1",
        chartType="table",
        title="2024 年某项指标",
        unit="万人",
        facts=(
            {"factRef": "visual:question:student:v1:fact:2023", "kind": "table_cell", "dimensions": ("2023", "指标"), "value": "100", "unit": "万人"},
            {"factRef": "visual:question:student:v1:fact:2024", "kind": "table_cell", "dimensions": ("2024", "指标"), "value": "120", "unit": "万人"},
        ),
    )


def verifier_visual() -> SafeStructuredVisualProjection:
    payload = safe_visual().model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "evidenceRef": "visual:question:student:v1:analysis",
            "audiences": ["verifier"],
            "facts": [
                fact.model_copy(update={"fact_ref": fact.fact_ref + ":analysis"})
                for fact in safe_visual().facts
            ],
        }
    )
    return SafeStructuredVisualProjection.model_validate(payload)


def provider_config(**overrides: object) -> TeachBackProviderConfig:
    values: dict[str, object] = {
        "endpoint": "https://shadow.example.test/v1",
        "api_key": "shadow-key",
        "provider_release_id": "synthetic-shadow-2026-08-12.1",
        "model": "shadow-model-v1",
        "https_host_allowlist": ("shadow.example.test",),
        "idempotency_capability": "nonreplayable_audited",
        "idempotency_key_header": "Idempotency-Key",
        "request_id_source": "X-Provider-Request-Id",
        "replay_retention_seconds": 0,
        "max_input_bytes": 1_000_000,
        "max_output_bytes": 100_000,
        "timeout_seconds": 30.0,
    }
    values.update(overrides)
    return TeachBackProviderConfig(**values)


class SyntheticPriceCard:
    def cost(self, usage: object) -> object:
        input_tokens = Decimal(getattr(usage, "input_tokens"))
        cached_tokens = Decimal(getattr(usage, "cached_input_tokens"))
        output_tokens = Decimal(getattr(usage, "output_tokens"))
        total = (
            (input_tokens - cached_tokens)
            + cached_tokens * Decimal("0.5")
            + output_tokens * Decimal("2")
        ) / Decimal("1000000")
        return SimpleNamespace(total_cost_cny=total)


def price_card() -> SyntheticPriceCard:
    return SyntheticPriceCard()


def response_for(
    request: httpx.Request,
    *,
    content: dict[str, object],
    request_id: str,
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
    include_request_id: bool = True,
) -> httpx.Response:
    headers = {"X-Provider-Request-Id": request_id} if include_request_id else {}
    return httpx.Response(
        200,
        request=request,
        headers=headers,
        json={
            "id": request_id,
            "model": "shadow-model-v1",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
            "choices": [
                {"message": {"content": json.dumps(content, ensure_ascii=False)}}
            ],
        },
    )


def test_two_isolated_calls_produce_ledger_aligned_shadow_candidates() -> None:
    captured: list[tuple[dict[str, object], str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        key = request.headers["idempotency-key"]
        captured.append((body, key))
        schema_name = body["response_format"]["json_schema"]["name"]
        if schema_name == "teach_back_student_reproduction":
            return response_for(
                request,
                content={
                    "state": "reproduced",
                    "reproducedSteps": [
                        "先用现期量减增长量得到基期量",
                        "再用增长量除以基期量",
                    ],
                    "attemptedOptionId": "B",
                },
                request_id="student-request-1",
                prompt_tokens=100,
                cached_tokens=20,
                completion_tokens=30,
            )
        return response_for(
            request,
            content={
                "claims": [
                    {
                        "classification": "supported",
                        "summary": "复现了基期量计算步骤。",
                        "evidenceRefs": [
                            "method:method-card:growth-rate-base:v7:step:1"
                        ],
                    },
                    {
                        "classification": "supported",
                        "summary": "按用户讲法得到了正确选项。",
                        "evidenceRefs": ["question:question:student:v1:answer"],
                    },
                ],
                "nextAction": "ready_for_transfer",
            },
            request_id="verifier-request-1",
            prompt_tokens=120,
            cached_tokens=0,
            completion_tokens=40,
        )

    provider = TeachBackShadowProvider(
        provider_config(),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = run_provider_shadow(
        shadow_input(), provider=provider, price_card=price_card()
    )

    assert result.state == "displayable"
    assert len(result.student_outputs) == 1
    assert len(result.grounding_candidates) == 1
    assert len(captured) == 2

    student_body, student_key = captured[0]
    verifier_body, verifier_key = captured[1]
    student_wire = json.dumps(student_body, ensure_ascii=False)
    verifier_wire = json.dumps(verifier_body, ensure_ascii=False)
    for forbidden in (
        "methodSteps",
        "answerKey",
        "answerAuthority",
        "studentExplanation",
        "pitfalls",
        "mastery",
        "ability",
    ):
        assert forbidden not in student_wire
    assert "userExplanation" in student_wire
    assert "questionRef" in student_wire
    assert "methodSteps" in verifier_wire
    assert "answerAuthority" in verifier_wire
    assert "studentCandidate" in verifier_wire
    assert student_key != verifier_key
    assert student_key.endswith(":student:v1")
    assert verifier_key.endswith(":verifier:v1")

    student_audit, verifier_audit = result.call_audits
    assert student_audit.operation == "teach_back_student_reproduction"
    assert verifier_audit.operation == "teach_back_grounding_verification"
    assert student_audit.total_cost_cny == "0.00015000"
    assert verifier_audit.total_cost_cny == "0.00020000"
    assert student_audit.input_tokens == 100
    assert student_audit.cached_input_tokens == 20
    assert student_audit.output_tokens == 30
    assert student_audit.request_id_source == "x-provider-request-id"
    assert student_audit.request_id_fingerprint == sha256(
        b"student-request-1"
    ).hexdigest()


def test_verified_visual_is_safe_in_student_wire_and_exact_in_verifier_authority() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        schema_name = body["response_format"]["json_schema"]["name"]
        if schema_name == "teach_back_student_reproduction":
            return response_for(
                request,
                content={
                    "state": "reproduced",
                    "reproducedSteps": ["读取 2023 和 2024 的数值", "按用户步骤计算"],
                    "attemptedOptionId": "B",
                },
                request_id="visual-student-request",
                prompt_tokens=100,
                cached_tokens=0,
                completion_tokens=20,
            )
        return response_for(
            request,
            content={
                "claims": [
                    {
                        "classification": "supported",
                        "summary": "使用了当前题已绑定的 2024 数值。",
                        "evidenceRefs": ["visual:question:student:v1:fact:2024"],
                    }
                ],
                "nextAction": "ready_for_transfer",
            },
            request_id="visual-verifier-request",
            prompt_tokens=120,
            cached_tokens=0,
            completion_tokens=20,
        )

    provider = TeachBackShadowProvider(
        provider_config(),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = run_provider_shadow(
        shadow_input(studentVisualEvidence=(safe_visual(),)),
        provider=provider,
        price_card=price_card(),
    )

    assert result.state == "displayable"
    student_prompt = json.loads(captured[0]["messages"][1]["content"])
    verifier_prompt = json.loads(captured[1]["messages"][1]["content"])
    visual_payload = student_prompt["visualEvidence"]
    assert visual_payload[0]["facts"][1]["value"] == "120"
    serialized_student = json.dumps(student_prompt, ensure_ascii=False)
    for forbidden in ("methodSteps", "answerKey", "studentExplanation", "mastery", "ability"):
        assert forbidden not in serialized_student
    assert verifier_prompt["visualAuthority"] == visual_payload
    assert "visual:question:student:v1:fact:2024" in verifier_prompt[
        "allowedEvidenceRefs"
    ]


def test_provider_keeps_explanation_media_out_of_student_wire_but_in_verifier_authority() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        schema_name = body["response_format"]["json_schema"]["name"]
        content = (
            {
                "state": "reproduced",
                "reproducedSteps": ["按用户讲法尝试"],
                "attemptedOptionId": "B",
            }
            if schema_name == "teach_back_student_reproduction"
            else {
                "claims": [
                    {
                        "classification": "supported",
                        "summary": "核对了解析侧图片事实。",
                        "evidenceRefs": [
                            "visual:question:student:v1:fact:2024:analysis"
                        ],
                    }
                ],
                "nextAction": "ready_for_transfer",
            }
        )
        return response_for(
            request,
            content=content,
            request_id=f"audience-{len(captured)}",
            prompt_tokens=20,
            cached_tokens=0,
            completion_tokens=10,
        )

    result = run_provider_shadow(
        shadow_input(
            studentVisualEvidence=(safe_visual(),),
            verifierVisualEvidence=(verifier_visual(),),
        ),
        provider=TeachBackShadowProvider(
            provider_config(),
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(handler)
            ),
        ),
        price_card=price_card(),
    )

    assert result.state == "displayable"
    student_prompt = json.loads(captured[0]["messages"][1]["content"])
    verifier_prompt = json.loads(captured[1]["messages"][1]["content"])
    assert [item["evidenceRef"] for item in student_prompt["visualEvidence"]] == [
        safe_visual().evidence_ref
    ]
    assert {item["evidenceRef"] for item in verifier_prompt["visualAuthority"]} == {
        safe_visual().evidence_ref,
        verifier_visual().evidence_ref,
    }


def test_out_of_scope_verifier_reference_rejects_whole_result_without_partial_output() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            content = {
                "state": "reproduced",
                "reproducedSteps": ("先算基期量",),
                "attemptedOptionId": "B",
            }
        else:
            content = {
                "claims": [
                    {
                        "classification": "supported",
                        "summary": "伪造了模型内部依据。",
                        "evidenceRefs": ["model-memory:secret"],
                    }
                ],
                "nextAction": "ready_for_transfer",
            }
        return response_for(
            request,
            content=content,
            request_id=f"request-{call_count}",
            prompt_tokens=10,
            cached_tokens=0,
            completion_tokens=5,
        )

    result = run_provider_shadow(
        shadow_input(),
        provider=TeachBackShadowProvider(
            provider_config(),
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(handler)
            ),
        ),
        price_card=price_card(),
    )

    assert call_count == 2
    assert result.state == "undisplayable"
    assert result.reason_codes == ("GROUNDING_REFERENCE_OUT_OF_SCOPE",)
    assert result.student_outputs == ()
    assert result.grounding_candidates == ()
    assert len(result.call_audits) == 2


def test_invalid_verifier_response_raises_without_returning_a_partial_shadow_result() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        content = (
            {
                "state": "reproduced",
                "reproducedSteps": ["先算基期量"],
                "attemptedOptionId": "B",
            }
            if call_count == 1
            else {"claims": [], "nextAction": "invented_action"}
        )
        return response_for(
            request,
            content=content,
            request_id=f"request-{call_count}",
            prompt_tokens=10,
            cached_tokens=0,
            completion_tokens=5,
        )

    with pytest.raises(TeachBackProviderError) as error:
        run_provider_shadow(
            shadow_input(),
            provider=TeachBackShadowProvider(
                provider_config(),
                client_factory=lambda: httpx.Client(
                    transport=httpx.MockTransport(handler)
                ),
            ),
            price_card=price_card(),
        )

    assert call_count == 2
    assert error.value.code == "provider_response_invalid"
    assert error.value.retryable is False
    assert error.value.usage is not None


def test_student_cannot_select_an_option_outside_the_safe_question() -> None:
    prepared = prepare_shadow_evaluation(shadow_input())

    def handler(request: httpx.Request) -> httpx.Response:
        return response_for(
            request,
            content={
                "state": "reproduced",
                "reproducedSteps": ["先算基期量", "再算增长率"],
                "attemptedOptionId": "Z",
            },
            request_id="invented-option-request",
            prompt_tokens=12,
            cached_tokens=0,
            completion_tokens=6,
        )

    provider = TeachBackShadowProvider(
        provider_config(),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(TeachBackProviderError) as error:
        provider.generate_student(
            prepared.student_requests[0],
            idempotency_key=shadow_idempotency_key("a" * 64, "student"),
        )

    assert error.value.code == "provider_response_invalid"
    assert error.value.retryable is False
    assert error.value.usage is not None


def test_verifier_rejects_authority_not_derived_from_the_current_package() -> None:
    command = shadow_input()
    mismatched = GroundingVerifierRequest(
        packageFingerprint=command.package_fingerprint,
        allowedEvidenceRefs=(
            "method:another-card:step:1",
            f"question:{command.student_question_ref}:answer",
        ),
    )
    provider = TeachBackShadowProvider(provider_config())

    with pytest.raises(TeachBackProviderConfigurationError, match="authority"):
        provider.generate_verifier(
            command,
            mismatched,
            student_output=prepare_student_output(),
            idempotency_key=shadow_idempotency_key("a" * 64, "verifier"),
        )


def prepare_student_output() -> StudentReproductionOutput:
    return StudentReproductionOutput(
        state="reproduced",
        reproducedSteps=("先算基期量", "再算增长率"),
        attemptedOptionId="B",
    )


def test_missing_provider_request_id_fails_closed_after_usage_is_known() -> None:
    prepared = prepare_shadow_evaluation(shadow_input())

    def handler(request: httpx.Request) -> httpx.Response:
        return response_for(
            request,
            content={
                "state": "blocked",
                "reproducedSteps": [],
                "blockedAt": "缺少分母步骤",
                "clarificationQuestion": "得到基期量后要用什么作分母？",
            },
            request_id="not-exposed",
            prompt_tokens=12,
            cached_tokens=0,
            completion_tokens=6,
            include_request_id=False,
        )

    provider = TeachBackShadowProvider(
        provider_config(request_id_source="X-Provider-Request-Id"),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(TeachBackProviderError) as error:
        provider.generate_student(
            prepared.student_requests[0],
            idempotency_key=shadow_idempotency_key("a" * 64, "student"),
        )

    assert error.value.code == "provider_request_id_invalid"
    assert error.value.retryable is False
    assert error.value.usage is not None


def test_nonreplayable_timeout_and_http_failures_make_one_attempt_and_never_retry() -> None:
    prepared = prepare_shadow_evaluation(shadow_input())
    timeout_attempts = 0

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_attempts
        timeout_attempts += 1
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    timeout_provider = TeachBackShadowProvider(
        provider_config(),
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(timeout_handler)
        ),
    )
    with pytest.raises(TeachBackProviderError) as timeout_error:
        timeout_provider.generate_student(
            prepared.student_requests[0], idempotency_key="shadow:timeout:student"
        )
    assert timeout_attempts == 1
    assert timeout_error.value.code == "provider_transport_uncertain"
    assert timeout_error.value.retryable is False

    for status_code in (429, 503):
        attempts = 0

        def http_handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(status_code, request=request)

        provider = TeachBackShadowProvider(
            provider_config(),
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(http_handler)
            ),
        )
        with pytest.raises(TeachBackProviderError) as http_error:
            provider.generate_student(
                prepared.student_requests[0],
                idempotency_key=f"shadow:http-{status_code}:student",
            )
        assert attempts == 1
        assert http_error.value.code == f"provider_http_{status_code}"
        assert http_error.value.retryable is False


def test_replay_preserves_phase_keys_and_input_fingerprints() -> None:
    captured: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        schema_name = body["response_format"]["json_schema"]["name"]
        key = request.headers["idempotency-key"]
        captured.append((schema_name, key, sha256(request.content).hexdigest()))
        content = (
            {
                "state": "reproduced",
                "reproducedSteps": ["先算基期量", "再算增长率"],
                "attemptedOptionId": "B",
            }
            if schema_name == "teach_back_student_reproduction"
            else {
                "claims": [
                    {
                        "classification": "supported",
                        "summary": "完成复现。",
                        "evidenceRefs": [
                            "method:method-card:growth-rate-base:v7:step:1"
                        ],
                    }
                ],
                "nextAction": "ready_for_transfer",
            }
        )
        return response_for(
            request,
            content=content,
            request_id=f"request-{len(captured)}",
            prompt_tokens=20,
            cached_tokens=0,
            completion_tokens=10,
        )

    provider = TeachBackShadowProvider(
        provider_config(),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    first = run_provider_shadow(
        shadow_input(), provider=provider, price_card=price_card()
    )
    second = run_provider_shadow(
        shadow_input(), provider=provider, price_card=price_card()
    )

    assert [item.idempotency_key for item in first.call_audits] == [
        item.idempotency_key for item in second.call_audits
    ]
    assert [item.input_fingerprint for item in first.call_audits] == [
        item.input_fingerprint for item in second.call_audits
    ]
    assert captured[0][1:] == captured[2][1:]
    assert captured[1][1:] == captured[3][1:]
    assert captured[0][1] != captured[1][1]


def test_configuration_rejects_unapproved_transport_and_audit_controls() -> None:
    with pytest.raises(TeachBackProviderConfigurationError, match="HTTPS"):
        TeachBackShadowProvider(
            provider_config(endpoint="http://shadow.example.test/v1")
        ).validate_configuration()
    with pytest.raises(TeachBackProviderConfigurationError, match="allowlisted"):
        TeachBackShadowProvider(
            provider_config(https_host_allowlist=("other.example.test",))
        ).validate_configuration()
    with pytest.raises(TeachBackProviderConfigurationError, match="replayable"):
        TeachBackShadowProvider(
            provider_config(idempotency_capability="best_effort")
        ).validate_configuration()


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://user:secret@shadow.example.test/v1",
        "https://shadow.example.test/v1?tenant=synthetic",
        "https://shadow.example.test/v1#credential-fragment",
    ),
)
def test_configuration_rejects_endpoint_metadata_that_could_leak_into_audit(
    endpoint: str,
) -> None:
    with pytest.raises(TeachBackProviderConfigurationError, match="credentials"):
        TeachBackShadowProvider(
            provider_config(endpoint=endpoint)
        ).validate_configuration()
