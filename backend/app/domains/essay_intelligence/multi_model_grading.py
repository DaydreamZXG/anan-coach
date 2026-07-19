from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domains.essay_intelligence.schemas import (
    AnswerEvidenceRef,
    EssayContractModel,
    EssayProviderReviewPayload,
    MaterialEvidenceRef,
    Sha256,
)


GRADING_PLAN_VERSION = "essay-multi-model-grading-v1"
COMPARISON_POLICY_VERSION = "essay-candidate-comparison-v1"
ADJUDICATION_CONTRACT_VERSION = "essay-blind-adjudication-v1"

CandidateStatus = Literal["valid", "invalid", "provider_failed"]
DisagreementState = Literal[
    "consistent",
    "minor_disagreement",
    "major_disagreement",
    "unable_to_score",
]


class EssayGradingCandidate(EssayContractModel):
    candidate_id: UUID = Field(alias="candidateId")
    slot_id: str = Field(alias="slotId", min_length=1, max_length=40)
    provider_release_id: str = Field(
        alias="providerReleaseId", min_length=1, max_length=160
    )
    model: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(alias="promptVersion", min_length=1, max_length=160)
    schema_version: str = Field(alias="schemaVersion", min_length=1, max_length=160)
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    output_fingerprint: Sha256 | None = Field(alias="outputFingerprint")
    status: CandidateStatus
    payload: EssayProviderReviewPayload | None
    model_call_id: UUID | None = Field(alias="modelCallId")
    error_code: str | None = Field(default=None, alias="errorCode", max_length=160)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "EssayGradingCandidate":
        if self.status == "valid":
            if (
                self.payload is None
                or self.output_fingerprint is None
                or self.model_call_id is None
                or self.error_code is not None
            ):
                raise ValueError("valid candidate requires payload and complete audit identity")
        elif self.payload is not None or self.error_code is None:
            raise ValueError("rejected candidate requires an error and cannot expose payload")
        return self


class EssayCandidateComparison(EssayContractModel):
    policy_version: Literal[COMPARISON_POLICY_VERSION] = Field(alias="policyVersion")
    state: DisagreementState
    valid_candidate_ids: list[UUID] = Field(alias="validCandidateIds")
    rejected_candidate_ids: list[UUID] = Field(alias="rejectedCandidateIds")
    common_score_lower: float | None = Field(default=None, alias="commonScoreLower")
    common_score_upper: float | None = Field(default=None, alias="commonScoreUpper")
    reason_codes: list[str] = Field(alias="reasonCodes", min_length=1)
    comparison_fingerprint: Sha256 = Field(alias="comparisonFingerprint")

    @model_validator(mode="after")
    def validate_state(self) -> "EssayCandidateComparison":
        if len(self.valid_candidate_ids) != len(set(self.valid_candidate_ids)):
            raise ValueError("valid candidate IDs must be unique")
        if len(self.rejected_candidate_ids) != len(set(self.rejected_candidate_ids)):
            raise ValueError("rejected candidate IDs must be unique")
        if set(self.valid_candidate_ids) & set(self.rejected_candidate_ids):
            raise ValueError("candidate cannot be both valid and rejected")
        if self.state == "unable_to_score":
            if len(self.valid_candidate_ids) >= 2:
                raise ValueError("unable state requires fewer than two valid candidates")
            if self.common_score_lower is not None or self.common_score_upper is not None:
                raise ValueError("unable state cannot publish a score interval")
        else:
            if len(self.valid_candidate_ids) < 2:
                raise ValueError("scored comparison requires two independent candidates")
            if self.common_score_lower is None or self.common_score_upper is None:
                raise ValueError("scored comparison requires an interval envelope")
            if self.common_score_lower > self.common_score_upper:
                raise ValueError("comparison interval must be ordered")
        return self


class BlindCandidateDocument(EssayContractModel):
    candidate_ref: str = Field(alias="candidateRef", pattern=r"^candidate-[A-C]$")
    payload: EssayProviderReviewPayload


class EssayBlindAdjudicationRequest(EssayContractModel):
    contract_version: Literal[ADJUDICATION_CONTRACT_VERSION] = Field(
        alias="contractVersion"
    )
    comparison_fingerprint: Sha256 = Field(alias="comparisonFingerprint")
    disagreement_state: Literal["minor_disagreement", "major_disagreement"] = Field(
        alias="disagreementState"
    )
    candidates: list[BlindCandidateDocument] = Field(min_length=2, max_length=3)


class EssayBlindAdjudicationDecision(EssayContractModel):
    contract_version: Literal[ADJUDICATION_CONTRACT_VERSION] = Field(
        alias="contractVersion"
    )
    selected_candidate_ref: str = Field(
        alias="selectedCandidateRef", pattern=r"^candidate-[A-C]$"
    )
    rationale: str = Field(min_length=1, max_length=2000)
    rubric_point_ids: list[str] = Field(alias="rubricPointIds", min_length=1)
    answer_evidence_refs: list[AnswerEvidenceRef] = Field(
        alias="answerEvidenceRefs", min_length=1
    )
    material_evidence_refs: list[MaterialEvidenceRef] = Field(
        alias="materialEvidenceRefs", min_length=1
    )


@dataclass(frozen=True)
class ResolvedEssayGrading:
    comparison: EssayCandidateComparison
    payload: EssayProviderReviewPayload | None
    selected_candidate_id: UUID | None
    adjudication: EssayBlindAdjudicationDecision | None


def compare_essay_grading_candidates(
    candidates: list[EssayGradingCandidate],
) -> EssayCandidateComparison:
    if not 2 <= len(candidates) <= 3:
        raise ValueError("formal grading requires exactly two or three candidate slots")
    if len({item.slot_id for item in candidates}) != len(candidates):
        raise ValueError("formal grading candidate slots must be unique")
    if len({item.candidate_id for item in candidates}) != len(candidates):
        raise ValueError("formal grading candidate identities must be unique")

    valid = sorted(
        (item for item in candidates if item.status == "valid"),
        key=lambda item: str(item.candidate_id),
    )
    rejected = sorted(
        (item for item in candidates if item.status != "valid"),
        key=lambda item: str(item.candidate_id),
    )
    reason_codes: list[str] = []
    common_lower: float | None = None
    common_upper: float | None = None

    if len(valid) < 2:
        state: DisagreementState = "unable_to_score"
        reason_codes.append("fewer_than_two_valid_candidates")
    elif not _independent_releases(valid) or not _independent_model_calls(valid):
        state = "unable_to_score"
        reason_codes.append(
            "candidate_releases_not_independent"
            if not _independent_releases(valid)
            else "candidate_model_calls_not_independent"
        )
        rejected = sorted(candidates, key=lambda item: str(item.candidate_id))
        valid = []
    else:
        payloads = [item.payload for item in valid]
        assert all(payload is not None for payload in payloads)
        resolved_payloads = [payload for payload in payloads if payload is not None]
        maxima = {payload.score.maximum for payload in resolved_payloads}
        if len(maxima) != 1:
            state = "major_disagreement"
            reason_codes.append("score_maximum_conflict")
            common_lower = min(payload.score.score_range.lower for payload in resolved_payloads)
            common_upper = max(payload.score.score_range.upper for payload in resolved_payloads)
        else:
            intersection_lower = max(
                payload.score.score_range.lower for payload in resolved_payloads
            )
            intersection_upper = min(
                payload.score.score_range.upper for payload in resolved_payloads
            )
            common_lower = min(
                payload.score.score_range.lower for payload in resolved_payloads
            )
            common_upper = max(
                payload.score.score_range.upper for payload in resolved_payloads
            )
            if intersection_lower > intersection_upper:
                state = "major_disagreement"
                reason_codes.append("score_intervals_disjoint")
            elif _substantive_signature_conflict(resolved_payloads):
                state = "major_disagreement"
                reason_codes.append("rubric_or_material_conclusion_conflict")
            elif len({_canonical_payload(payload) for payload in resolved_payloads}) == 1:
                state = "consistent"
                reason_codes.append("candidate_outputs_identical")
                common_lower = intersection_lower
                common_upper = intersection_upper
            else:
                state = "minor_disagreement"
                reason_codes.append("overlapping_scores_with_nonidentical_output")

    comparison_document = {
        "policyVersion": COMPARISON_POLICY_VERSION,
        "state": state,
        "validCandidateIds": [item.candidate_id for item in valid],
        "rejectedCandidateIds": [item.candidate_id for item in rejected],
        "commonScoreLower": common_lower,
        "commonScoreUpper": common_upper,
        "reasonCodes": reason_codes,
    }
    fingerprint_document = {
        **comparison_document,
        "validCandidateIds": [str(item.candidate_id) for item in valid],
        "rejectedCandidateIds": [str(item.candidate_id) for item in rejected],
        "candidateFingerprints": [
            {
                "candidateId": str(item.candidate_id),
                "status": item.status,
                "outputFingerprint": item.output_fingerprint,
            }
            for item in sorted(candidates, key=lambda item: str(item.candidate_id))
        ],
    }
    return EssayCandidateComparison(
        **comparison_document,
        comparisonFingerprint=_sha256(fingerprint_document),
    )


def build_blind_adjudication_request(
    *,
    candidates: list[EssayGradingCandidate],
    comparison: EssayCandidateComparison,
) -> tuple[EssayBlindAdjudicationRequest, dict[str, UUID]]:
    if comparison.state not in {"minor_disagreement", "major_disagreement"}:
        raise ValueError("only disputed candidates can enter adjudication")
    by_id = {item.candidate_id: item for item in candidates}
    ordered = sorted(comparison.valid_candidate_ids, key=str)
    mapping: dict[str, UUID] = {}
    documents: list[BlindCandidateDocument] = []
    for index, candidate_id in enumerate(ordered):
        candidate = by_id[candidate_id]
        if candidate.payload is None:
            raise ValueError("blind adjudication cannot include an invalid candidate")
        ref = f"candidate-{chr(ord('A') + index)}"
        mapping[ref] = candidate_id
        documents.append(BlindCandidateDocument(candidateRef=ref, payload=candidate.payload))
    request = EssayBlindAdjudicationRequest(
        contractVersion=ADJUDICATION_CONTRACT_VERSION,
        comparisonFingerprint=comparison.comparison_fingerprint,
        disagreementState=comparison.state,
        candidates=documents,
    )
    serialized = request.model_dump(by_alias=True, mode="json")
    if any(
        forbidden in json.dumps(serialized, ensure_ascii=False)
        for forbidden in ("providerReleaseId", '"model"', '"slotId"')
    ):
        raise ValueError("blind adjudication request leaked provider identity")
    return request, mapping


def resolve_essay_grading(
    *,
    candidates: list[EssayGradingCandidate],
    comparison: EssayCandidateComparison,
    adjudication: EssayBlindAdjudicationDecision | None = None,
) -> ResolvedEssayGrading:
    by_id = {item.candidate_id: item for item in candidates}
    if comparison.state == "unable_to_score":
        if adjudication is not None:
            raise ValueError("unable comparison cannot be adjudicated")
        return ResolvedEssayGrading(comparison, None, None, None)

    if comparison.state == "consistent":
        if adjudication is not None:
            raise ValueError("identical candidates do not require adjudication")
        selected_id = min(comparison.valid_candidate_ids, key=str)
        payload = by_id[selected_id].payload
        if payload is None:
            raise ValueError("consistent candidate payload is missing")
        return ResolvedEssayGrading(comparison, payload, selected_id, None)

    if adjudication is None:
        raise ValueError("disputed candidates require blind adjudication")
    request, mapping = build_blind_adjudication_request(
        candidates=candidates,
        comparison=comparison,
    )
    del request
    selected_id = mapping.get(adjudication.selected_candidate_ref)
    if selected_id is None:
        raise ValueError("adjudicator selected an unknown blind candidate")
    selected = by_id[selected_id]
    assert selected.payload is not None
    _validate_adjudication_evidence(adjudication, selected.payload)
    return ResolvedEssayGrading(comparison, selected.payload, selected_id, adjudication)


def _independent_releases(candidates: list[EssayGradingCandidate]) -> bool:
    identities = {
        (item.provider_release_id.casefold(), item.model.casefold())
        for item in candidates
    }
    return len(identities) == len(candidates)


def _independent_model_calls(candidates: list[EssayGradingCandidate]) -> bool:
    call_ids = {item.model_call_id for item in candidates}
    return None not in call_ids and len(call_ids) == len(candidates)


def _substantive_signature_conflict(
    payloads: list[EssayProviderReviewPayload],
) -> bool:
    point_signatures = {
        tuple(sorted((item.rubric_point_id, item.state) for item in payload.point_coverage))
        for payload in payloads
    }
    claim_signatures = {
        tuple(sorted((item.claim_id, item.verdict) for item in payload.claim_evidence))
        for payload in payloads
    }
    dimension_codes = {
        tuple(sorted(item.criterion_code for item in payload.dimensions))
        for payload in payloads
    }
    return len(point_signatures) != 1 or len(claim_signatures) != 1 or len(dimension_codes) != 1


def _validate_adjudication_evidence(
    decision: EssayBlindAdjudicationDecision,
    payload: EssayProviderReviewPayload,
) -> None:
    selected_points = {item.rubric_point_id for item in payload.point_coverage}
    if not set(decision.rubric_point_ids).issubset(selected_points):
        raise ValueError("adjudication cited a rubric point absent from selected candidate")
    answer_keys = {
        (str(ref.answer_version_id), ref.segment_id, ref.quote_sha256)
        for item in (
            *payload.dimensions,
            *payload.point_coverage,
            *payload.claim_evidence,
            *payload.findings,
        )
        for ref in item.answer_evidence_refs
    }
    material_keys = {
        (
            str(ref.material_bundle_version_id),
            ref.material_id,
            ref.segment_id,
            ref.quote_sha256,
        )
        for item in (
            *payload.dimensions,
            *payload.point_coverage,
            *payload.claim_evidence,
            *payload.findings,
        )
        for ref in item.material_evidence_refs
    }
    if any(
        (str(ref.answer_version_id), ref.segment_id, ref.quote_sha256) not in answer_keys
        for ref in decision.answer_evidence_refs
    ):
        raise ValueError("adjudication answer evidence is not sealed in selected candidate")
    if any(
        (
            str(ref.material_bundle_version_id),
            ref.material_id,
            ref.segment_id,
            ref.quote_sha256,
        )
        not in material_keys
        for ref in decision.material_evidence_refs
    ):
        raise ValueError("adjudication material evidence is not sealed in selected candidate")


def _canonical_payload(payload: EssayProviderReviewPayload) -> str:
    return json.dumps(
        payload.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
