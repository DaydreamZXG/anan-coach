from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4

import pytest

from app.core.config import DEFAULT_AI_PROVIDER_BASE_URL, Settings
from app.domains.essay_intelligence.grounded_provider import (
    GroundedEssayReviewProvider,
    GroundedReviewMeteredProviderError,
    GroundedReviewProviderConfigurationError,
    GroundedReviewProviderError,
    grounded_review_provider_identity,
    validate_grounded_review_provider_security,
)
from app.domains.essay_intelligence.grounding import (
    GroundedReviewContext,
    normalize_grounding_text,
    review_input_fingerprint,
    review_output_fingerprint,
)
from app.domains.essay_intelligence.provider_document import (
    prepare_grounded_review_provider_input,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROMPT_VERSION,
    GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
    REVIEW_CONTRACT_VERSION,
    RUBRIC_CONTRACT_VERSION,
)
from app.domains.essay_intelligence.review_service import (
    GroundedReviewApplicationService,
)
from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayAttempt,
    EssayMaterialBundleVersion,
    EssayProviderReviewPayload,
    EssayReviewAudit,
    EssayReviewRequest,
    EssayRubricVersion,
    EssayTaskVersion,
)


NOW = datetime(2026, 7, 13, 8, tzinfo=UTC)
TASK_ID = UUID("00000000-0000-4000-8000-000000005001")
MATERIAL_ID = UUID("00000000-0000-4000-8000-000000005002")
RUBRIC_ID = UUID("00000000-0000-4000-8000-000000005003")
USER_ID = UUID("00000000-0000-4000-8000-000000005004")
ATTEMPT_ID = UUID("00000000-0000-4000-8000-000000005005")
ANSWER_ID = UUID("00000000-0000-4000-8000-000000005006")


class _ProviderResponse:
    def __init__(
        self,
        *,
        request_id: str | None = "provider-request-123",
        envelope: dict[str, object] | None = None,
    ) -> None:
        self.headers = (
            {"x-provider-request-id": request_id} if request_id is not None else {}
        )
        self.envelope = envelope

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.envelope or {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }


class _ProviderClient:
    def __init__(self, response: _ProviderResponse | None = None) -> None:
        self.response = response or _ProviderResponse()
        self.url: str | None = None
        self.headers: dict[str, str] = {}
        self.content = b""

    def __enter__(self) -> "_ProviderClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
    ) -> _ProviderResponse:
        self.url = url
        self.headers = headers
        self.content = content
        return self.response


def test_provider_request_contains_only_minimized_anonymous_review_document() -> None:
    context = _context()
    client = _ProviderClient()
    settings = _provider_settings(environment="local")
    result = GroundedEssayReviewProvider(
        settings,
        client_factory=lambda: client,  # type: ignore[arg-type]
    ).generate(
        context=context,
        request=_request(),
        idempotency_key="durable-call-key",
    )

    body = json.loads(client.content)
    user_message = json.loads(body["messages"][1]["content"])
    review_input = user_message["reviewInput"]
    forbidden_keys = {
        "userId",
        "attemptId",
        "taskVersionId",
        "materialBundleVersionId",
        "rubricVersionId",
        "answerVersionId",
        "inputFingerprint",
        "promptSha256",
        "contentSha256",
        "textSha256",
        "quoteSha256",
        "answerSha256",
        "thinkingSha256",
        "submittedAt",
        "createdAt",
    }
    assert forbidden_keys.isdisjoint(_all_document_keys(review_input))
    for internal_value in (
        context.attempt.user_id,
        context.attempt.attempt_id,
        context.task.task_version_id,
        context.material.material_bundle_version_id,
        context.rubric.rubric_version_id,
        context.answer.answer_version_id,
        context.material.segments[0].segment_id,
        context.material.segments[1].segment_id,
    ):
        assert str(internal_value) not in _all_string_values(review_input)
    assert review_input["material"]["segments"][0]["text"] == (
        context.material.segments[0].text
    )
    assert review_input["material"]["segments"][1]["text"] == (
        context.material.segments[1].text
    )
    assert review_input["answer"]["text"] == context.answer.answer_text
    assert client.headers["X-Provider-Idempotency-Key"] == "durable-call-key"
    assert result.provider_request_id_header == "x-provider-request-id"
    assert result.provider_request_id_fingerprint == _hash("provider-request-123")
    assert "provider-request-123" not in result.provider_request_id_fingerprint


def test_provider_reference_map_round_trips_output_without_exposing_domain_ids() -> None:
    context = _context()
    prepared = prepare_grounded_review_provider_input(context)
    internal = _output().model_dump(by_alias=True, mode="json")

    anonymous = prepared.references.anonymize_payload(internal)
    anonymous_text = json.dumps(anonymous, ensure_ascii=False, sort_keys=True)
    assert str(context.material.material_bundle_version_id) not in anonymous_text
    assert str(context.answer.answer_version_id) not in anonymous_text
    assert prepared.references.restore_payload(anonymous) == internal


def test_repair_never_forwards_internal_violation_detail_or_domain_references() -> None:
    context = _context()
    client = _ProviderClient()
    internal_output = _output().model_dump(by_alias=True, mode="json")
    GroundedEssayReviewProvider(
        _provider_settings(environment="local"),
        client_factory=lambda: client,  # type: ignore[arg-type]
    ).repair(
        context=context,
        request=_request(),
        rejected_content=json.dumps(internal_output, ensure_ascii=False),
        violation_code="quote_text_anchor_invalid",
        violation_detail=f"private {context.attempt.user_id} {context.attempt.attempt_id}",
        idempotency_key="repair-call-key",
    )

    body = json.loads(client.content)
    user_message = json.loads(body["messages"][1]["content"])
    correction = user_message["correction"]
    correction_text = json.dumps(correction, ensure_ascii=False, sort_keys=True)
    assert "violationDetail" not in correction
    assert str(context.attempt.user_id) not in correction_text
    assert str(context.attempt.attempt_id) not in correction_text
    assert str(context.material.material_bundle_version_id) not in correction_text
    assert str(context.answer.answer_version_id) not in correction_text


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"ai_essay_review_provider_release_id": ""}, "RELEASE_ID"),
        (
            {
                "ai_provider_base_url": DEFAULT_AI_PROVIDER_BASE_URL,
                "ai_essay_review_provider_https_host_allowlist": (
                    "gemini-web2api.ergouapi.co"
                ),
            },
            "default external AI gateway",
        ),
        ({"ai_provider_base_url": "http://approved.example/v1"}, "HTTPS"),
        (
            {"ai_essay_review_provider_https_host_allowlist": "other.example"},
            "not allowlisted",
        ),
        ({"ai_essay_review_provider_dpa_confirmed": False}, "DPA"),
        ({"ai_essay_review_provider_idempotency_capability": ""}, "replayable"),
        ({"ai_essay_review_provider_idempotency_key_header": ""}, "KEY_HEADER"),
        ({"ai_essay_review_provider_request_id_header": ""}, "REQUEST_ID_HEADER"),
    ],
)
def test_production_provider_security_contract_rejects_unapproved_configuration(
    mutation: dict[str, object],
    message: str,
) -> None:
    settings = replace(_provider_settings(environment="production"), **mutation)

    with pytest.raises(GroundedReviewProviderConfigurationError, match=message):
        validate_grounded_review_provider_security(settings)


def test_production_provider_release_identity_and_security_contract_are_explicit() -> None:
    settings = _provider_settings(environment="production")

    validate_grounded_review_provider_security(settings)
    assert (
        grounded_review_provider_identity(settings)
        == "openai_compatible@essay-review-release-2026-07-13"
    )


def test_production_provider_requires_response_request_id() -> None:
    client = _ProviderClient(_ProviderResponse(request_id=None))
    provider = GroundedEssayReviewProvider(
        _provider_settings(environment="production"),
        client_factory=lambda: client,  # type: ignore[arg-type]
    )

    with pytest.raises(
        GroundedReviewMeteredProviderError,
        match="omitted its configured request ID",
    ) as error:
        provider.generate(context=_context(), request=_request())
    assert error.value.code == "provider_request_id_missing"
    assert error.value.usage.input_tokens == 10
    assert error.value.usage.output_tokens == 2
    assert len(error.value.output_fingerprint) == 64
    assert error.value.provider_request_id_header is None
    assert error.value.provider_request_id_fingerprint is None


def test_provider_usage_is_metered_when_structured_content_is_missing() -> None:
    response = _ProviderResponse(
        envelope={
            "choices": [],
            "usage": {
                "prompt_tokens": 13,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
        }
    )
    provider = GroundedEssayReviewProvider(
        _provider_settings(environment="local"),
        client_factory=lambda: _ProviderClient(response),  # type: ignore[arg-type]
    )

    with pytest.raises(GroundedReviewMeteredProviderError) as error:
        provider.generate(context=_context(), request=_request())

    assert error.value.code == "provider_envelope_invalid"
    assert error.value.usage.input_tokens == 13
    assert error.value.usage.cached_input_tokens == 2
    assert error.value.usage.output_tokens == 3
    assert len(error.value.output_fingerprint) == 64
    assert error.value.provider_request_id_header == "x-provider-request-id"
    assert error.value.provider_request_id_fingerprint == _hash(
        "provider-request-123"
    )
    assert "provider-request-123" not in error.value.provider_request_id_fingerprint


def test_provider_usage_is_metered_when_output_exceeds_capacity() -> None:
    provider = GroundedEssayReviewProvider(
        replace(
            _provider_settings(environment="local"),
            ai_essay_review_max_output_bytes=1,
        ),
        client_factory=lambda: _ProviderClient(),  # type: ignore[arg-type]
    )

    with pytest.raises(GroundedReviewMeteredProviderError) as error:
        provider.generate(context=_context(), request=_request())

    assert error.value.code == "provider_output_capacity_exceeded"
    assert error.value.usage.input_tokens == 10
    assert error.value.usage.output_tokens == 2
    assert error.value.output_fingerprint == _hash("{}")
    assert error.value.provider_request_id_fingerprint == _hash(
        "provider-request-123"
    )


def test_provider_usage_is_metered_when_content_has_invalid_unicode() -> None:
    response = _ProviderResponse(
        envelope={
            "choices": [{"message": {"content": "\ud800"}}],
            "usage": {
                "prompt_tokens": 17,
                "completion_tokens": 1,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }
    )
    provider = GroundedEssayReviewProvider(
        _provider_settings(environment="local"),
        client_factory=lambda: _ProviderClient(response),  # type: ignore[arg-type]
    )

    with pytest.raises(GroundedReviewMeteredProviderError) as error:
        provider.generate(context=_context(), request=_request())

    assert error.value.code == "provider_content_encoding_invalid"
    assert error.value.usage.input_tokens == 17
    assert error.value.usage.output_tokens == 1
    assert len(error.value.output_fingerprint) == 64


def test_grounded_review_application_completes_only_fully_verified_output() -> None:
    context = _context()
    request = _request()
    output = _output()
    audit = _audit(context, request, output)

    result = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=request,
        provider_output=output,
        audit=audit,
    )

    assert result.response.status == "completed"
    assert result.response.score is not None
    assert result.response.score.score_range.recommended == 10
    assert result.response.readiness.blocking_codes == []
    assert result.grounding is not None
    assert result.grounding.input_fingerprint == audit.input_fingerprint
    assert result.grounding.output_fingerprint == audit.output_fingerprint
    assert result.grounding.answer_segment_ids == ("answer-1", "answer-2")
    assert result.blocking_detail is None


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("offset", "quote_text_anchor_invalid"),
        ("offset_out_of_range", "normalized_offset_out_of_range"),
        ("segment", "source_segment_unknown"),
        ("answer_version", "answer_version_anchor_invalid"),
        ("material_version", "material_version_anchor_invalid"),
        ("material_pair", "material_segment_anchor_invalid"),
        ("hash", "provider_schema_invalid"),
        ("normalization", "normalization_contract_missing"),
    ],
)
def test_any_quote_anchor_failure_blocks_the_entire_review(
    mutation: str,
    expected_code: str,
) -> None:
    context = _context()
    request = _request()
    payload = _output().model_dump(by_alias=True, mode="json")
    anchor = payload["dimensions"][0]["answerEvidenceRefs"][0]
    if mutation == "offset":
        anchor["normalizedStart"] = 1
        anchor["normalizedEnd"] = 5
    elif mutation == "offset_out_of_range":
        anchor["normalizedEnd"] = 500
    elif mutation == "segment":
        anchor["segmentId"] = "answer-unknown"
    elif mutation == "answer_version":
        anchor["answerVersionId"] = str(uuid4())
    elif mutation == "material_version":
        anchor = payload["dimensions"][0]["materialEvidenceRefs"][0]
        anchor["materialBundleVersionId"] = str(uuid4())
    elif mutation == "material_pair":
        anchor = payload["dimensions"][0]["materialEvidenceRefs"][0]
        anchor["materialId"] = "material-unknown"
    elif mutation == "hash":
        anchor["quoteSha256"] = "f" * 64
    else:
        anchor["normalization"] = None
        anchor["normalizedStart"] = None
        anchor["normalizedEnd"] = None
    audit = _audit_for_raw(context, request, payload)

    result = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=request,
        provider_output=payload,
        audit=audit,
    )

    _assert_whole_review_blocked(result, expected_code)
    if mutation == "hash":
        assert "基层治理" not in (result.blocking_detail or "")


def test_grounding_validator_rechecks_hash_after_schema_boundary() -> None:
    context = _context()
    request = _request()
    output = _output()
    dimension = output.dimensions[0]
    corrupt_ref = dimension.answer_evidence_refs[0].model_copy(
        update={"quote_sha256": "f" * 64}
    )
    corrupt_dimension = dimension.model_copy(
        update={
            "answer_evidence_refs": [
                corrupt_ref,
                *dimension.answer_evidence_refs[1:],
            ]
        }
    )
    corrupt_output = output.model_copy(
        update={"dimensions": [corrupt_dimension, *output.dimensions[1:]]}
    )

    result = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=request,
        provider_output=corrupt_output,
        audit=_audit(context, request, corrupt_output),
    )

    _assert_whole_review_blocked(result, "quote_hash_anchor_invalid")


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("criteria", "criteria_coverage_incomplete"),
        ("points", "point_coverage_incomplete"),
        ("score", "point_score_conservation_invalid"),
        ("point_range", "point_score_out_of_range"),
        ("point_scope", "point_material_scope_invalid"),
    ],
)
def test_rubric_coverage_and_score_conservation_are_code_enforced(
    mutation: str,
    expected_code: str,
) -> None:
    context = _context()
    request = _request()
    payload = _output().model_dump(by_alias=True, mode="json")
    if mutation == "criteria":
        payload["dimensions"][1]["criterionCode"] = "invented"
    elif mutation == "points":
        payload["pointCoverage"][1]["rubricPointId"] = "invented-point"
        payload["rewritePlan"]["targetPointIds"][1] = "invented-point"
    elif mutation == "score":
        payload["pointCoverage"][0]["awardedRange"] = {
            "lower": 3.0,
            "recommended": 4.0,
            "upper": 5.0,
        }
    elif mutation == "point_range":
        payload["score"]["scoreRange"] = {
            "lower": 8.0,
            "recommended": 11.0,
            "upper": 17.0,
        }
        payload["dimensions"][0]["scoreRange"] = {
            "lower": 4.0,
            "recommended": 6.0,
            "upper": 7.0,
        }
        payload["dimensions"][1]["scoreRange"] = {
            "lower": 4.0,
            "recommended": 5.0,
            "upper": 10.0,
        }
        payload["pointCoverage"][0]["awardedRange"] = {
            "lower": 4.0,
            "recommended": 6.0,
            "upper": 11.0,
        }
    else:
        payload["pointCoverage"][0]["materialEvidenceRefs"] = [
            payload["pointCoverage"][1]["materialEvidenceRefs"][0]
        ]
    audit = _audit_for_raw(context, request, payload)

    result = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=request,
        provider_output=payload,
        audit=audit,
    )

    _assert_whole_review_blocked(result, expected_code)


def test_unverified_rubric_caps_confidence_at_low() -> None:
    context = _context(unverified=True)
    request = _request()
    medium_output = _output(confidence="medium")
    blocked = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=request,
        provider_output=medium_output,
        audit=_audit(context, request, medium_output),
    )
    _assert_whole_review_blocked(blocked, "unverified_rubric_overconfidence")

    low_output = _output(confidence="low")
    completed = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=request,
        provider_output=low_output,
        audit=_audit(context, request, low_output),
    )
    assert completed.response.status == "completed"
    assert completed.response.score is not None
    assert completed.response.score.confidence == "low"


def test_non_json_structured_payload_is_blocked_at_application_boundary() -> None:
    context = _context()
    request = _request()
    payload = _output().model_dump(by_alias=True, mode="json")
    payload["score"] = object()

    result = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=request,
        provider_output=payload,
        audit=_audit(context, request, _output()),
    )

    _assert_whole_review_blocked(result, "provider_schema_invalid")


def test_version_or_audit_mismatch_is_blocked_with_canonical_context_identity() -> None:
    context = _context()
    request_payload = _request().model_dump(by_alias=True, mode="json")
    request_payload["answerVersionId"] = str(uuid4())
    wrong_request = EssayReviewRequest.model_validate_json(json.dumps(request_payload))
    output = _output()
    version_blocked = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=wrong_request,
        provider_output=output,
        audit=_audit(context, wrong_request, output),
    )
    _assert_whole_review_blocked(version_blocked, "version_binding_invalid")
    assert (
        version_blocked.response.answer_version_id
        == context.answer.answer_version_id
    )

    audit_payload = _audit(context, _request(), output).model_dump(
        by_alias=True,
        mode="json",
    )
    audit_payload["outputFingerprint"] = "a" * 64
    audit_blocked = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=_request(),
        provider_output=output,
        audit=audit_payload,
    )
    _assert_whole_review_blocked(audit_blocked, "review_audit_binding_invalid")


@pytest.mark.parametrize(
    "field",
    [
        "contractVersion",
        "providerSchemaVersion",
        "rubricVersion",
        "promptVersion",
    ],
)
def test_review_execution_contract_versions_are_fixed(field: str) -> None:
    context = _context()
    request = _request()
    output = _output()
    audit = _audit(context, request, output).model_dump(
        by_alias=True,
        mode="json",
    )
    audit[field] = "stale-v1"

    result = GroundedReviewApplicationService().apply(
        review_version_id=uuid4(),
        context=context,
        request=request,
        provider_output=output,
        audit=audit,
    )

    _assert_whole_review_blocked(result, "review_audit_binding_invalid")


def _context(*, unverified: bool = False) -> GroundedReviewContext:
    task = EssayTaskVersion(
        task_version_id=TASK_ID,
        task_key="guokao-grounding-test",
        version_no=1,
        question_id=None,
        question_type="summary",
        prompt="根据材料概括问题。",
        prompt_sha256=_hash("根据材料概括问题。"),
        word_limit=200,
        maximum_score=20.0,
        state="published",
        supersedes_task_version_id=None,
        created_at=NOW,
    )
    material = EssayMaterialBundleVersion.model_validate(
        {
            "materialBundleVersionId": MATERIAL_ID,
            "taskVersionId": TASK_ID,
            "versionNo": 1,
            "completeness": "complete",
            "sourceKind": "official",
            "contentSha256": "b" * 64,
            "segments": [
                {
                    "materialId": "material-1",
                    "segmentId": "segment-1",
                    "materialOrder": 1,
                    "segmentOrder": 1,
                    "text": "基层  治理存在问题。",
                    "textSha256": _hash("基层  治理存在问题。"),
                },
                {
                    "materialId": "material-2",
                    "segmentId": "segment-2",
                    "materialOrder": 2,
                    "segmentOrder": 1,
                    "text": "执行 效率仍需提升。",
                    "textSha256": _hash("执行 效率仍需提升。"),
                },
            ],
            "supersedesMaterialBundleVersionId": None,
            "createdAt": NOW,
        }
    )
    rubric = EssayRubricVersion.model_validate(
        {
            "rubricVersionId": RUBRIC_ID,
            "taskVersionId": TASK_ID,
            "materialBundleVersionId": MATERIAL_ID,
            "versionNo": 1,
            "sourceKind": "system_compiled" if unverified else "official",
            "verificationStatus": "unverified" if unverified else "verified",
            "state": "published",
            "totalMaximum": 20.0,
            "criteria": [
                {
                    "criterionCode": "content",
                    "label": "内容",
                    "maximum": 10.0,
                    "scoringRule": "按要点覆盖评分",
                    "materialEvidenceRequired": True,
                },
                {
                    "criterionCode": "structure",
                    "label": "结构",
                    "maximum": 10.0,
                    "scoringRule": "按结构层次评分",
                    "materialEvidenceRequired": True,
                },
            ],
            "points": [
                {
                    "rubricPointId": "point-1",
                    "criterionCode": "content",
                    "category": "problem",
                    "label": "基层治理问题",
                    "maximumCredit": 10.0,
                    "materialSegmentIds": ["segment-1"],
                    "materialEvidenceRefs": [
                        {
                            "materialBundleVersionId": MATERIAL_ID,
                            "materialId": "material-1",
                            "segmentId": "segment-1",
                            "startOffset": 0,
                            "endOffset": 2,
                            "quote": "基层",
                            "quoteSha256": _hash("基层"),
                        }
                    ],
                },
                {
                    "rubricPointId": "point-2",
                    "criterionCode": "structure",
                    "category": "problem",
                    "label": "执行效率问题",
                    "maximumCredit": 10.0,
                    "materialSegmentIds": ["segment-2"],
                    "materialEvidenceRefs": [
                        {
                            "materialBundleVersionId": MATERIAL_ID,
                            "materialId": "material-2",
                            "segmentId": "segment-2",
                            "startOffset": 0,
                            "endOffset": 2,
                            "quote": "执行",
                            "quoteSha256": _hash("执行"),
                        }
                    ],
                },
            ],
            "contentSha256": "c" * 64,
            "supersedesRubricVersionId": None,
            "createdAt": NOW,
        }
    )
    attempt = EssayAttempt(
        attempt_id=ATTEMPT_ID,
        user_id=USER_ID,
        task_version_id=TASK_ID,
        material_bundle_version_id=MATERIAL_ID,
        rubric_version_id=RUBRIC_ID,
        source_context="in_program",
        timed_status="untimed",
        elapsed_seconds=None,
        input_fingerprint="d" * 64,
        submitted_at=NOW,
    )
    answer_text = "基层治理存在问题。\n执行效率仍需提升。"
    answer = EssayAnswerVersion(
        answer_version_id=ANSWER_ID,
        attempt_id=ATTEMPT_ID,
        user_id=USER_ID,
        task_version_id=TASK_ID,
        version_no=1,
        source_type="typed",
        answer_text=answer_text,
        answer_sha256=_hash(answer_text),
        thinking=None,
        thinking_sha256=None,
        input_fingerprint="e" * 64,
        supersedes_answer_version_id=None,
        created_at=NOW,
    )
    return GroundedReviewContext(
        task=task,
        material=material,
        rubric=rubric,
        attempt=attempt,
        answer=answer,
    )


def _request() -> EssayReviewRequest:
    return EssayReviewRequest(
        attempt_id=ATTEMPT_ID,
        task_version_id=TASK_ID,
        answer_version_id=ANSWER_ID,
        material_bundle_version_id=MATERIAL_ID,
        rubric_version_id=RUBRIC_ID,
        review_scope="grounded_full",
    )


def _output(*, confidence: str = "medium") -> EssayProviderReviewPayload:
    answer_1 = _answer_anchor("answer-1", "基层治理", 0, 4)
    answer_2 = _answer_anchor("answer-2", "执行效率", 0, 4)
    material_1 = _material_anchor(
        "material-1",
        "segment-1",
        "基层 治理",
        0,
        5,
    )
    material_2 = _material_anchor(
        "material-2",
        "segment-2",
        "执行 效率",
        0,
        5,
    )
    payload = {
        "score": {
            "maximum": 20.0,
            "scoreRange": {"lower": 8.0, "recommended": 10.0, "upper": 12.0},
            "confidence": confidence,
            "confidenceReasons": ["版本与引用均已验证"],
        },
        "dimensions": [
            {
                "criterionCode": "content",
                "label": "内容",
                "maximum": 10.0,
                "scoreRange": {"lower": 4.0, "recommended": 5.0, "upper": 6.0},
                "rationale": "覆盖部分材料要点",
                "findingIds": ["finding-1"],
                "answerEvidenceRefs": [answer_1],
                "materialEvidenceRefs": [material_1],
            },
            {
                "criterionCode": "structure",
                "label": "结构",
                "maximum": 10.0,
                "scoreRange": {"lower": 4.0, "recommended": 5.0, "upper": 6.0},
                "rationale": "结构基本完整",
                "findingIds": ["finding-2"],
                "answerEvidenceRefs": [answer_2],
                "materialEvidenceRefs": [material_2],
            },
        ],
        "pointCoverage": [
            {
                "rubricPointId": "point-1",
                "state": "partial",
                "awardedRange": {"lower": 4.0, "recommended": 5.0, "upper": 6.0},
                "explanation": "概括仍可提高",
                "answerEvidenceRefs": [answer_1],
                "materialEvidenceRefs": [material_1],
            },
            {
                "rubricPointId": "point-2",
                "state": "partial",
                "awardedRange": {"lower": 4.0, "recommended": 5.0, "upper": 6.0},
                "explanation": "执行问题表达不够具体",
                "answerEvidenceRefs": [answer_2],
                "materialEvidenceRefs": [material_2],
            },
        ],
        "claimEvidence": [],
        "findings": [
            {
                "findingId": "finding-1",
                "type": "abstraction",
                "severity": "medium",
                "diagnosis": "概括层级不足",
                "action": "合并同类事实",
                "attribution": "unknown",
                "answerEvidenceRefs": [answer_1],
                "materialEvidenceRefs": [material_1],
            },
            {
                "findingId": "finding-2",
                "type": "structure",
                "severity": "low",
                "diagnosis": "层次标记不足",
                "action": "按问题类别分层",
                "attribution": "unknown",
                "answerEvidenceRefs": [answer_2],
                "materialEvidenceRefs": [material_2],
            },
        ],
        "rewritePlan": {
            "priorityFindingIds": ["finding-1", "finding-2"],
            "targetPointIds": ["point-1", "point-2"],
            "constraints": [{"code": "word_limit", "value": "200"}],
            "steps": [
                {
                    "stepId": "step-1",
                    "instruction": "先分类再概括",
                    "successCriterion": "两个问题层次清晰",
                }
            ],
        },
    }
    return EssayProviderReviewPayload.model_validate_json(
        json.dumps(payload, ensure_ascii=False)
    )


def _answer_anchor(
    segment_id: str,
    quote: str,
    start: int,
    end: int,
) -> dict[str, object]:
    normalized = normalize_grounding_text(quote)
    return {
        "answerVersionId": str(ANSWER_ID),
        "segmentId": segment_id,
        "quote": quote,
        "quoteSha256": _hash(normalized),
        "normalization": "nfkc_whitespace_v1",
        "normalizedStart": start,
        "normalizedEnd": end,
    }


def _material_anchor(
    material_id: str,
    segment_id: str,
    quote: str,
    start: int,
    end: int,
) -> dict[str, object]:
    normalized = normalize_grounding_text(quote)
    return {
        "materialBundleVersionId": str(MATERIAL_ID),
        "materialId": material_id,
        "segmentId": segment_id,
        "quote": quote,
        "quoteSha256": _hash(normalized),
        "normalization": "nfkc_whitespace_v1",
        "normalizedStart": start,
        "normalizedEnd": end,
    }


def _audit(
    context: GroundedReviewContext,
    request: EssayReviewRequest,
    output: EssayProviderReviewPayload,
) -> EssayReviewAudit:
    return EssayReviewAudit(
        contract_version=REVIEW_CONTRACT_VERSION,
        provider_schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
        rubric_version=RUBRIC_CONTRACT_VERSION,
        rubric_source=context.rubric.source_kind,
        rubric_verification=context.rubric.verification_status,
        prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
        provider="provider",
        model="model",
        input_fingerprint=review_input_fingerprint(context, request),
        output_fingerprint=review_output_fingerprint(output),
        generated_at=NOW,
        valid_until=NOW + timedelta(days=30),
    )


def _audit_for_raw(
    context: GroundedReviewContext,
    request: EssayReviewRequest,
    payload: dict[str, object],
) -> dict[str, object]:
    parsed = None
    try:
        parsed = EssayProviderReviewPayload.model_validate_json(
            json.dumps(payload, ensure_ascii=False)
        )
    except Exception:
        pass
    return {
        "contractVersion": REVIEW_CONTRACT_VERSION,
        "providerSchemaVersion": GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
        "rubricVersion": RUBRIC_CONTRACT_VERSION,
        "rubricSource": context.rubric.source_kind,
        "rubricVerification": context.rubric.verification_status,
        "promptVersion": GROUNDED_REVIEW_PROMPT_VERSION,
        "provider": "provider",
        "model": "model",
        "inputFingerprint": review_input_fingerprint(context, request),
        "outputFingerprint": (
            review_output_fingerprint(parsed) if parsed is not None else "f" * 64
        ),
        "generatedAt": NOW.isoformat(),
        "validUntil": (NOW + timedelta(days=30)).isoformat(),
    }


def _provider_settings(*, environment: str) -> Settings:
    return Settings(
        environment=environment,
        ai_provider_base_url="https://approved.example/v1",
        ai_api_key="provider-secret",
        ai_essay_review_model="approved-model",
        ai_essay_review_provider_release_id="essay-review-release-2026-07-13",
        ai_essay_review_provider_https_host_allowlist="approved.example",
        ai_essay_review_provider_dpa_confirmed=True,
        ai_essay_review_provider_idempotency_capability="replayable_queryable",
        ai_essay_review_provider_idempotency_key_header=(
            "X-Provider-Idempotency-Key"
        ),
        ai_essay_review_provider_request_id_header="X-Provider-Request-Id",
        ai_essay_review_max_input_bytes=10_000_000,
        ai_essay_review_max_output_bytes=1_000_000,
    )


def _all_document_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        result = set(value)
        for item in value.values():
            result.update(_all_document_keys(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_all_document_keys(item))
        return result
    return set()


def _all_string_values(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(_all_string_values(item))
        return result
    if isinstance(value, list):
        result = set()
        for item in value:
            result.update(_all_string_values(item))
        return result
    return set()


def _assert_whole_review_blocked(result: object, expected_code: str) -> None:
    response = result.response  # type: ignore[attr-defined]
    assert response.status == "blocked"
    assert response.readiness.blocking_codes == [expected_code]
    assert response.score is None
    assert response.dimensions == []
    assert response.point_coverage == []
    assert response.findings == []
    assert response.rewrite_plan is None
    assert result.grounding is None  # type: ignore[attr-defined]


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
