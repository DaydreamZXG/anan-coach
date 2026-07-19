from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.config import Settings
from app.domains.essay_intelligence.blind_adjudicator import (
    BlindAdjudicatorSettings,
    OpenAICompatibleBlindAdjudicatorProvider,
)
from app.domains.essay_intelligence.multi_model_grading import (
    ADJUDICATION_CONTRACT_VERSION,
    EssayBlindAdjudicationDecision,
    EssayGradingCandidate,
    build_blind_adjudication_request,
    compare_essay_grading_candidates,
    resolve_essay_grading,
)
from app.domains.essay_intelligence.multi_model_runtime import (
    EssayMultiModelRuntimeConfigurationError,
    EssayMultiModelRuntimePlan,
    EssayReviewExecutionIdentity,
)
from app.domains.essay_intelligence.review_worker import GroundedEssayReviewWorker
from app.domains.essay_intelligence.schemas import EssayProviderReviewPayload
from app.domains.essay_intelligence.workflow_repository import (
    EssayWorkflowContractError,
)
from app.domains.essay_intelligence.workflow_service import (
    EssayReviewWorkflowService,
)


ANSWER_ID = UUID("00000000-0000-0000-0000-000000001005")
MATERIAL_ID = UUID("00000000-0000-0000-0000-000000001002")


def test_formal_runtime_fails_closed_without_a_versioned_complete_plan() -> None:
    with pytest.raises(
        EssayMultiModelRuntimeConfigurationError,
        match="PLAN_RELEASE_ID",
    ):
        EssayMultiModelRuntimePlan.from_settings(Settings())


def test_production_forbids_single_model_worker_and_queue_overrides() -> None:
    settings = Settings(environment="production")
    with pytest.raises(ValueError, match="forbids single-model"):
        GroundedEssayReviewWorker(settings=settings, provider=object())  # type: ignore[arg-type]
    with pytest.raises(EssayWorkflowContractError, match="cannot override"):
        EssayReviewWorkflowService(
            settings=settings,
            execution_identity_override=EssayReviewExecutionIdentity(
                provider="single-model",
                model="single-model",
            ),
        )


def test_identical_independent_candidates_resolve_without_averaging() -> None:
    payload = _payload()
    candidates = [_candidate("gemini", payload), _candidate("grok", payload)]

    comparison = compare_essay_grading_candidates(candidates)
    resolved = resolve_essay_grading(candidates=candidates, comparison=comparison)

    assert comparison.state == "consistent"
    assert comparison.reason_codes == ["candidate_outputs_identical"]
    assert resolved.payload == payload
    assert resolved.payload.score.score_range.recommended == 12
    assert resolved.adjudication is None


def test_overlapping_nonidentical_candidates_require_brand_blind_adjudication() -> None:
    first = _payload()
    second_document = first.model_dump(by_alias=True, mode="json")
    second_document["score"]["scoreRange"] = {
        "lower": 11.0,
        "recommended": 13.0,
        "upper": 15.0,
    }
    second_document["dimensions"][0]["scoreRange"] = {
        "lower": 11.0,
        "recommended": 13.0,
        "upper": 15.0,
    }
    second_document["pointCoverage"][0]["awardedRange"] = {
        "lower": 11.0,
        "recommended": 13.0,
        "upper": 15.0,
    }
    second = _parse_payload(second_document)
    candidates = [_candidate("gemini", first), _candidate("grok", second)]

    comparison = compare_essay_grading_candidates(candidates)
    request, mapping = build_blind_adjudication_request(
        candidates=candidates,
        comparison=comparison,
    )

    assert comparison.state == "minor_disagreement"
    dumped = json.dumps(request.model_dump(by_alias=True, mode="json"))
    assert "gemini" not in dumped and "grok" not in dumped
    selected_ref = next(ref for ref, candidate_id in mapping.items() if candidate_id == candidates[1].candidate_id)
    decision = _decision(selected_ref, second)
    resolved = resolve_essay_grading(
        candidates=candidates,
        comparison=comparison,
        adjudication=decision,
    )
    assert resolved.selected_candidate_id == candidates[1].candidate_id
    assert resolved.payload == second
    assert resolved.payload.score.score_range.recommended == 13


def test_disjoint_ranges_or_rubric_conclusions_are_major_disagreement() -> None:
    first = _payload()
    second_document = first.model_dump(by_alias=True, mode="json")
    second_document["score"]["scoreRange"] = {
        "lower": 16.0,
        "recommended": 17.0,
        "upper": 18.0,
    }
    second_document["dimensions"][0]["scoreRange"] = {
        "lower": 16.0,
        "recommended": 17.0,
        "upper": 18.0,
    }
    second_document["pointCoverage"][0].update(
        {
            "state": "covered",
            "awardedRange": {"lower": 16.0, "recommended": 17.0, "upper": 18.0},
        }
    )
    second = _parse_payload(second_document)

    comparison = compare_essay_grading_candidates(
        [_candidate("gemini", first), _candidate("grok", second)]
    )

    assert comparison.state == "major_disagreement"
    assert "score_intervals_disjoint" in comparison.reason_codes


def test_fewer_than_two_valid_independent_candidates_is_unable_to_score() -> None:
    valid = _candidate("gemini", _payload())
    failed = EssayGradingCandidate(
        candidateId=uuid4(),
        slotId="grok",
        providerReleaseId="grok-release-v1",
        model="grok-model-v1",
        promptVersion="prompt-v1",
        schemaVersion="schema-v1",
        inputFingerprint="a" * 64,
        outputFingerprint=None,
        status="provider_failed",
        payload=None,
        modelCallId=None,
        errorCode="provider_failed",
    )

    comparison = compare_essay_grading_candidates([valid, failed])
    resolved = resolve_essay_grading(candidates=[valid, failed], comparison=comparison)

    assert comparison.state == "unable_to_score"
    assert resolved.payload is None
    assert resolved.selected_candidate_id is None


def test_adjudicator_cannot_invent_evidence_or_select_unknown_candidate() -> None:
    payload = _payload()
    changed = deepcopy(payload.model_dump(by_alias=True, mode="json"))
    changed["findings"][0]["diagnosis"] = "表达还可更准确"
    other = _parse_payload(changed)
    candidates = [_candidate("gemini", payload), _candidate("grok", other)]
    comparison = compare_essay_grading_candidates(candidates)

    with pytest.raises(ValueError, match="unknown blind candidate"):
        resolve_essay_grading(
            candidates=candidates,
            comparison=comparison,
            adjudication=_decision("candidate-C", payload),
        )


def test_openai_adjudicator_receives_no_candidate_brand_and_never_averages() -> None:
    payload = _payload()
    changed = payload.model_dump(by_alias=True, mode="json")
    changed["findings"][0]["diagnosis"] = "另一种诊断表达"
    other = _parse_payload(changed)
    candidates = [_candidate("gemini", payload), _candidate("grok", other)]
    comparison = compare_essay_grading_candidates(candidates)
    request, _ = build_blind_adjudication_request(
        candidates=candidates,
        comparison=comparison,
    )
    captured: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        document = json.loads(http_request.content)
        captured.update(document)
        content = _decision("candidate-A", request.candidates[0].payload).model_dump_json(
            by_alias=True
        )
        return httpx.Response(
            200,
            request=http_request,
            json={
                "model": "adjudicator-model-v1",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    provider = OpenAICompatibleBlindAdjudicatorProvider(
        BlindAdjudicatorSettings(
            base_url="https://adjudicator.example/v1",
            api_key="test-secret",
            provider_release_id="adjudicator-release-v1",
            model="adjudicator-model-v1",
            idempotency_key_header="Idempotency-Key",
            request_id_header="",
            idempotency_capability="replayable_queryable",
            maximum_input_bytes=2_000_000,
            maximum_output_bytes=200_000,
        ),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.adjudicate(
        request=request,
        idempotency_key="essay-grading:run:adjudication",
    )

    serialized = json.dumps(captured, ensure_ascii=False).casefold()
    assert "gemini" not in serialized and "grok" not in serialized
    assert "不得平均分数" in serialized
    assert result.decision.selected_candidate_ref == "candidate-A"
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 20

    decision = _decision("candidate-A", payload).model_dump(by_alias=True, mode="json")
    decision["materialEvidenceRefs"][0]["segmentId"] = "invented"
    with pytest.raises(ValueError, match="material evidence"):
        resolve_essay_grading(
            candidates=candidates,
            comparison=comparison,
            adjudication=EssayBlindAdjudicationDecision.model_validate_json(
                json.dumps(decision, ensure_ascii=False)
            ),
        )


def _candidate(slot: str, payload: EssayProviderReviewPayload) -> EssayGradingCandidate:
    serialized = json.dumps(
        payload.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return EssayGradingCandidate(
        candidateId=uuid4(),
        slotId=slot,
        providerReleaseId=f"{slot}-release-v1",
        model=f"{slot}-model-v1",
        promptVersion="prompt-v1",
        schemaVersion="schema-v1",
        inputFingerprint="a" * 64,
        outputFingerprint=sha256(serialized.encode()).hexdigest(),
        status="valid",
        payload=payload,
        modelCallId=uuid4(),
        errorCode=None,
    )


def _decision(
    selected_ref: str,
    payload: EssayProviderReviewPayload,
) -> EssayBlindAdjudicationDecision:
    point = payload.point_coverage[0]
    return EssayBlindAdjudicationDecision(
        contractVersion=ADJUDICATION_CONTRACT_VERSION,
        selectedCandidateRef=selected_ref,
        rationale="该候选对评分点与材料证据的对应更完整。",
        rubricPointIds=[point.rubric_point_id],
        answerEvidenceRefs=[point.answer_evidence_refs[0]],
        materialEvidenceRefs=[point.material_evidence_refs[0]],
    )


def _payload() -> EssayProviderReviewPayload:
    answer_ref = {
        "answerVersionId": str(ANSWER_ID),
        "segmentId": "answer-1",
        "quote": "概括问题",
        "quoteSha256": sha256("概括问题".encode()).hexdigest(),
    }
    material_ref = {
        "materialBundleVersionId": str(MATERIAL_ID),
        "materialId": "material-1",
        "segmentId": "segment-1",
        "quote": "基层存在问题",
        "quoteSha256": sha256("基层存在问题".encode()).hexdigest(),
    }
    return _parse_payload(
        {
            "score": {
                "maximum": 20.0,
                "scoreRange": {"lower": 10.0, "recommended": 12.0, "upper": 14.0},
                "confidence": "medium",
                "confidenceReasons": ["评分规程与材料证据完整"],
            },
            "dimensions": [
                {
                    "criterionCode": "content",
                    "label": "内容",
                    "maximum": 20.0,
                    "scoreRange": {"lower": 10.0, "recommended": 12.0, "upper": 14.0},
                    "rationale": "要点部分覆盖",
                    "findingIds": ["finding-1"],
                    "answerEvidenceRefs": [answer_ref],
                    "materialEvidenceRefs": [material_ref],
                }
            ],
            "pointCoverage": [
                {
                    "rubricPointId": "point-1",
                    "state": "partial",
                    "awardedRange": {"lower": 10.0, "recommended": 12.0, "upper": 14.0},
                    "explanation": "提到问题但概括不完整",
                    "answerEvidenceRefs": [answer_ref],
                    "materialEvidenceRefs": [material_ref],
                }
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
                    "answerEvidenceRefs": [answer_ref],
                    "materialEvidenceRefs": [material_ref],
                }
            ],
            "rewritePlan": {
                "priorityFindingIds": ["finding-1"],
                "targetPointIds": ["point-1"],
                "constraints": [{"code": "word_limit", "value": "200"}],
                "steps": [
                    {
                        "stepId": "step-1",
                        "instruction": "合并同类问题",
                        "successCriterion": "形成上位概念",
                    }
                ],
            },
        }
    )


def _parse_payload(document: dict[str, object]) -> EssayProviderReviewPayload:
    return EssayProviderReviewPayload.model_validate_json(
        json.dumps(document, ensure_ascii=False)
    )
