from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domains.ability_estimation.schemas import AbilityEstimateCurrent
from app.domains.positions.fit import (
    evaluate_position_fit,
    has_position_recommendation_evidence,
    position_recommendation_sort_key,
)
from app.domains.positions.schemas import PositionDiscoveryItem, TargetScoreRange
from app.main import create_app


NOW = datetime(2026, 7, 13, 8, tzinfo=UTC)


def test_fit_band_boundaries_and_gap_semantics_are_deterministic() -> None:
    position = _position(target=_target())

    conservative = evaluate_position_fit(
        position=position,
        ability=_complete_ability(lower=130, recommended=135, upper=140),
        exam_year=2026,
    )
    assert conservative.status == "conservative"
    assert conservative.band == "conservative"
    assert conservative.evidence_status == "ready"
    assert conservative.score_gap is not None
    assert conservative.score_gap.model_dump(by_alias=True) == {
        "lower": -20.0,
        "recommended": -10.0,
        "upper": 0.0,
        "unit": "points",
        "direction": "positive_means_target_above_current",
    }

    below = evaluate_position_fit(
        position=position,
        ability=_complete_ability(lower=100, recommended=110, upper=119),
        exam_year=2026,
    )
    assert below.status == "below_target"
    assert below.band == "below_target"
    assert below.score_gap is not None
    assert below.score_gap.lower == 1.0
    assert below.score_gap.recommended == 15.0
    assert below.score_gap.upper == 30.0

    overlap = evaluate_position_fit(
        position=position,
        ability=_complete_ability(lower=118, recommended=125, upper=132),
        exam_year=2026,
    )
    assert overlap.status == "overlap"
    assert overlap.band == "overlap"
    assert overlap.score_gap is not None
    assert overlap.score_gap.lower == -12.0
    assert overlap.score_gap.recommended == 0.0
    assert overlap.score_gap.upper == 12.0


def test_recommendation_sort_requires_eligibility_then_uses_fit_and_burden() -> None:
    ability = _complete_ability(lower=118, recommended=125, upper=132)
    well_matched = _position(
        target=_target(),
        eligibility_status="eligible",
        headcount=1,
        has_extra_exam=False,
        pending_count=0,
        not_met_count=0,
    )
    same_fit_with_extra_exam = _position(
        target=_target(),
        eligibility_status="eligible",
        headcount=20,
        has_extra_exam=True,
        pending_count=0,
        not_met_count=0,
    )
    high_target = _position(
        target=TargetScoreRange(
            lower_bound=140,
            recommended=145,
            upper_bound=150,
            sample_count=8,
            basis_level="direct",
            evidence_level="direct",
            generated_at=NOW,
            valid_until=NOW + timedelta(days=365),
        ),
        eligibility_status="eligible",
        headcount=30,
        has_extra_exam=False,
        pending_count=0,
        not_met_count=0,
    )
    pending = _position(
        target=TargetScoreRange(
            lower_bound=100,
            recommended=105,
            upper_bound=110,
            sample_count=8,
            basis_level="direct",
            evidence_level="direct",
            generated_at=NOW,
            valid_until=NOW + timedelta(days=365),
        ),
        eligibility_status="needs_confirmation",
        headcount=100,
        has_extra_exam=False,
        pending_count=1,
        not_met_count=0,
    )
    positions = [pending, high_target, same_fit_with_extra_exam, well_matched]
    fits = {
        position.position_id: evaluate_position_fit(
            position=position,
            ability=ability,
            exam_year=2026,
        )
        for position in positions
    }

    ranked = sorted(
        positions,
        key=lambda position: position_recommendation_sort_key(
            position=position,
            fit=fits[position.position_id],
        ),
    )

    assert ranked == [well_matched, same_fit_with_extra_exam, high_target, pending]
    assert has_position_recommendation_evidence(
        position=well_matched,
        fit=fits[well_matched.position_id],
    )
    assert not has_position_recommendation_evidence(
        position=pending,
        fit=fits[pending.position_id],
    )


def test_eligible_position_with_target_score_is_recommendable_before_baseline() -> None:
    position = _position(
        target=_target(),
        eligibility_status="eligible",
        has_extra_exam=False,
        pending_count=0,
        not_met_count=0,
    )
    fit = evaluate_position_fit(
        position=position,
        ability=AbilityEstimateCurrent(
            estimate=None,
            readiness="unavailable",
            missing_components=["xingce", "shenlun"],
        ),
        exam_year=2026,
    )

    assert fit.status == "evidence_unavailable"
    assert has_position_recommendation_evidence(position=position, fit=fit)


def test_openapi_publishes_required_workspace_fit_contract_without_probability_or_hours() -> None:
    schema = create_app().openapi()
    fit_schema = schema["components"]["schemas"]["PositionFitAssessment"]
    burden_schema = schema["components"]["schemas"]["PositionFitBurden"]
    gap_schema = schema["components"]["schemas"]["PositionScoreGapRange"]

    assert set(fit_schema["required"]) == {
        "status",
        "evidenceStatus",
        "band",
        "scoreGap",
        "requirementsBurden",
        "evidenceReasonCodes",
    }
    assert set(burden_schema["required"]) == {
        "additionalExamRequired",
        "pendingRequirementCount",
        "hardConditionNotMetCount",
        "pendingRequirementIds",
        "hardConditionNotMetRequirementIds",
    }
    assert set(gap_schema["required"]) == {
        "lower",
        "recommended",
        "upper",
        "unit",
        "direction",
    }
    assert "/api/v2/positions/workspace" in schema["paths"]
    assert "/api/v2/positions/{position_id}/fit" in schema["paths"]
    serialized = str({"fit": fit_schema, "burden": burden_schema}).lower()
    assert "probability" not in serialized
    assert "hour" not in serialized


@pytest.mark.parametrize(
    ("case", "evidence_status", "reason"),
    [
        (
            "unavailable",
            "unavailable",
            "ability_estimate_unavailable",
        ),
        (
            "partial",
            "partial",
            "ability_estimate_partial",
        ),
        (
            "stale",
            "stale",
            "ability_estimate_stale",
        ),
        (
            "year_mismatch",
            "unavailable",
            "ability_estimate_exam_year_mismatch",
        ),
        (
            "target_unavailable",
            "unavailable",
            "target_score_unavailable",
        ),
    ],
)
def test_fit_with_incomplete_or_mismatched_evidence_never_claims_a_band_or_gap(
    case: str,
    evidence_status: str,
    reason: str,
) -> None:
    if case == "unavailable":
        ability = AbilityEstimateCurrent(
            estimate=None,
            readiness="unavailable",
            missing_components=["xingce", "shenlun"],
        )
    elif case == "partial":
        ability = _partial_ability()
    elif case == "stale":
        ability = _complete_ability(
            lower=118,
            recommended=125,
            upper=132,
            readiness="stale",
        )
    elif case == "year_mismatch":
        ability = _complete_ability(
            lower=118,
            recommended=125,
            upper=132,
            exam_year=2025,
        )
    else:
        ability = _complete_ability(lower=118, recommended=125, upper=132)
    target = None if case == "target_unavailable" else _target()
    fit = evaluate_position_fit(
        position=_position(target=target),
        ability=ability,
        exam_year=2026,
    )

    assert fit.status == "evidence_unavailable"
    assert fit.evidence_status == evidence_status
    assert fit.band is None
    assert fit.score_gap is None
    assert reason in fit.evidence_reason_codes


def _target() -> TargetScoreRange:
    return TargetScoreRange(
        lower_bound=120,
        recommended=125,
        upper_bound=130,
        sample_count=8,
        basis_level="direct",
        evidence_level="direct",
        generated_at=NOW,
        valid_until=NOW + timedelta(days=365),
    )


def _position(
    *,
    target: TargetScoreRange | None,
    eligibility_status: str = "needs_confirmation",
    headcount: int = 1,
    has_extra_exam: bool = True,
    pending_count: int = 1,
    not_met_count: int = 1,
) -> PositionDiscoveryItem:
    pending_ids = [str(uuid4()) for _ in range(pending_count)]
    not_met_ids = [str(uuid4()) for _ in range(not_met_count)]
    return PositionDiscoveryItem(
        position_id=str(uuid4()),
        position_code="300110000001",
        name="测试岗位",
        department_full="测试部门",
        system_code="test",
        region="北京",
        city="北京市",
        headcount=headcount,
        has_extra_exam=has_extra_exam,
        eligibility_status=eligibility_status,
        met_count=0,
        pending_count=pending_count,
        not_met_count=not_met_count,
        pending_requirement_ids=pending_ids,
        not_met_requirement_ids=not_met_ids,
        reason_codes=[],
        target_score=target,
        is_primary_goal=False,
    )


def _complete_ability(
    *,
    lower: float,
    recommended: float,
    upper: float,
    readiness: str = "complete",
    exam_year: int = 2026,
) -> AbilityEstimateCurrent:
    estimate_id = str(uuid4())
    evidence_id = str(uuid4())
    component = {"lower": 50, "recommended": 60, "upper": 70}
    return AbilityEstimateCurrent.model_validate(
        {
            "estimate": {
                "estimateVersionId": estimate_id,
                "examCode": "guokao",
                "examYear": exam_year,
                "status": "complete",
                "xingce": component,
                "shenlun": component,
                "total": {
                    "lower": lower,
                    "recommended": recommended,
                    "upper": upper,
                },
                "confidence": "medium",
                "sourceEvidenceIds": [evidence_id],
                "evidenceSummary": {
                    "evidenceCount": 1,
                    "xingceEvidenceCount": 1,
                    "shenlunEvidenceCount": 1,
                    "sourceCounts": {"controlled_mock": 1},
                    "latestEvidenceAt": NOW.isoformat(),
                    "missingComponents": [],
                    "conflictComponents": [],
                },
                "algorithmVersion": "test-v1",
                "inputFingerprint": "a" * 64,
                "supersedesEstimateVersionId": None,
                "generatedAt": NOW.isoformat(),
                "validUntil": (NOW + timedelta(days=30)).isoformat(),
            },
            "readiness": readiness,
            "missingComponents": [],
        }
    )


def _partial_ability() -> AbilityEstimateCurrent:
    evidence_id = str(uuid4())
    return AbilityEstimateCurrent.model_validate(
        {
            "estimate": {
                "estimateVersionId": str(uuid4()),
                "examCode": "guokao",
                "examYear": 2026,
                "status": "partial",
                "xingce": {"lower": 50, "recommended": 60, "upper": 70},
                "shenlun": None,
                "total": None,
                "confidence": "low",
                "sourceEvidenceIds": [evidence_id],
                "evidenceSummary": {
                    "evidenceCount": 1,
                    "xingceEvidenceCount": 1,
                    "shenlunEvidenceCount": 0,
                    "sourceCounts": {"self_report": 1},
                    "latestEvidenceAt": NOW.isoformat(),
                    "missingComponents": ["shenlun"],
                    "conflictComponents": [],
                },
                "algorithmVersion": "test-v1",
                "inputFingerprint": "b" * 64,
                "supersedesEstimateVersionId": None,
                "generatedAt": NOW.isoformat(),
                "validUntil": (NOW + timedelta(days=30)).isoformat(),
            },
            "readiness": "partial",
            "missingComponents": ["shenlun"],
        }
    )
