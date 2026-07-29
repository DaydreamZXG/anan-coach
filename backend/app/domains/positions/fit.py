from __future__ import annotations

from app.domains.ability_estimation.schemas import AbilityEstimateCurrent
from app.domains.positions.schemas import (
    PositionDiscoveryItem,
    PositionFitAssessment,
    PositionFitBurden,
    PositionFitEvidenceReason,
    PositionFitEvidenceStatus,
    PositionScoreGapRange,
    TargetScoreRange,
)


def has_position_recommendation_evidence(
    *,
    position: PositionDiscoveryItem,
    fit: PositionFitAssessment,
) -> bool:
    """Only fully eligible positions with replayable fit evidence are recommendable."""

    burden = fit.requirements_burden
    return (
        position.eligibility_status == "eligible"
        and fit.status != "evidence_unavailable"
        and fit.evidence_status == "ready"
        and fit.band is not None
        and fit.score_gap is not None
        and not fit.evidence_reason_codes
        and burden.pending_requirement_count == 0
        and burden.hard_condition_not_met_count == 0
    )


def position_recommendation_sort_key(
    *,
    position: PositionDiscoveryItem,
    fit: PositionFitAssessment,
) -> tuple[int, int, int, float, int, int, int, int, str]:
    """Return the stable workspace ordering key.

    Qualification is the first gate.  Within the same qualification state,
    complete ability/target evidence, fit band, score gap and requirement
    burden all outrank headcount.  Position id is the final deterministic
    tiebreaker.
    """

    eligibility_rank = {
        "eligible": 0,
        "needs_confirmation": 1,
        "ineligible": 2,
    }[position.eligibility_status]
    evidence_rank = 0 if fit.evidence_status == "ready" else 1
    band_rank = {
        "conservative": 0,
        "overlap": 1,
        "below_target": 2,
        None: 3,
    }[fit.band]
    score_gap = fit.score_gap.recommended if fit.score_gap is not None else float("inf")
    burden = fit.requirements_burden
    return (
        eligibility_rank,
        evidence_rank,
        band_rank,
        score_gap,
        int(burden.additional_exam_required),
        burden.pending_requirement_count,
        burden.hard_condition_not_met_count,
        -position.headcount,
        position.position_id,
    )


def evaluate_position_fit(
    *,
    position: PositionDiscoveryItem,
    ability: AbilityEstimateCurrent,
    exam_year: int,
) -> PositionFitAssessment:
    """Classify score fit without probabilities or study-time projections."""

    burden = PositionFitBurden(
        additional_exam_required=position.has_extra_exam,
        pending_requirement_count=position.pending_count,
        hard_condition_not_met_count=position.not_met_count,
        pending_requirement_ids=position.pending_requirement_ids,
        hard_condition_not_met_requirement_ids=position.not_met_requirement_ids,
    )
    evidence_reasons = _evidence_reasons(
        ability=ability,
        exam_year=exam_year,
        target_score=position.target_score,
    )
    if evidence_reasons:
        return PositionFitAssessment(
            status="evidence_unavailable",
            evidence_status=_evidence_status(
                ability=ability,
                exam_year=exam_year,
                target_score=position.target_score,
            ),
            band=None,
            score_gap=None,
            requirements_burden=burden,
            evidence_reason_codes=evidence_reasons,
        )

    estimate = ability.estimate
    target = position.target_score
    assert estimate is not None and estimate.total is not None and target is not None
    current = estimate.total
    if current.lower >= target.upper_bound:
        fit_status = "conservative"
    elif current.upper < target.lower_bound:
        fit_status = "below_target"
    else:
        fit_status = "overlap"

    return PositionFitAssessment(
        status=fit_status,
        evidence_status="ready",
        band=fit_status,
        score_gap=PositionScoreGapRange(
            lower=round(target.lower_bound - current.upper, 2),
            recommended=round(target.recommended - current.recommended, 2),
            upper=round(target.upper_bound - current.lower, 2),
            unit="points",
            direction="positive_means_target_above_current",
        ),
        requirements_burden=burden,
        evidence_reason_codes=[],
    )


def _evidence_reasons(
    *,
    ability: AbilityEstimateCurrent,
    exam_year: int,
    target_score: TargetScoreRange | None,
) -> list[PositionFitEvidenceReason]:
    reasons: list[PositionFitEvidenceReason] = []
    if ability.readiness == "unavailable":
        reasons.append("ability_estimate_unavailable")
    elif ability.readiness == "partial":
        reasons.append("ability_estimate_partial")
    elif ability.readiness == "stale":
        reasons.append("ability_estimate_stale")
    elif ability.estimate is None or ability.estimate.exam_year != exam_year:
        reasons.append("ability_estimate_exam_year_mismatch")
    if target_score is None:
        reasons.append("target_score_unavailable")
    return reasons


def _evidence_status(
    *,
    ability: AbilityEstimateCurrent,
    exam_year: int,
    target_score: TargetScoreRange | None,
) -> PositionFitEvidenceStatus:
    if target_score is None or ability.readiness == "unavailable":
        return "unavailable"
    if ability.readiness == "partial":
        return "partial"
    if ability.readiness == "stale":
        return "stale"
    if ability.estimate is None or ability.estimate.exam_year != exam_year:
        return "unavailable"
    return "ready"
