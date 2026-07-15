from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domains.qualification.schemas import PositionRequirement, QualificationFact
from app.domains.qualification.service import evaluate_eligibility


NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _fact(
    key: str,
    value: str | int | bool | list[str],
    *,
    fact_id: str | None = None,
    status: str = "confirmed",
    effective_at: datetime = NOW,
) -> QualificationFact:
    return QualificationFact.model_validate(
        {
            "factId": fact_id or f"fact-{key}",
            "key": key,
            "value": value,
            "verificationStatus": status,
            "evidence": [
                {
                    "evidenceId": f"evidence-{key}",
                    "sourceType": "learner_report",
                    "recordedAt": NOW,
                }
            ],
            "effectiveAt": effective_at,
        }
    )


def _requirement(
    field: str,
    operator: str,
    expected: str | int | bool | list[str] | None,
) -> PositionRequirement:
    body = {
        "requirementId": f"requirement-{field}",
        "field": field,
        "operator": operator,
        "hardCondition": True,
        "sourceText": f"{field} 原始招录条件",
        "sourceReference": "2026-guokao:300110001001",
    }
    if expected is not None:
        body["expected"] = expected
    return PositionRequirement.model_validate(body)


def _evaluate(
    facts: list[QualificationFact],
    requirements: list[PositionRequirement],
):
    return evaluate_eligibility(
        facts=facts,
        requirements=requirements,
        rule_set_version="guokao-2026-rules-v1",
        position_data_version="guokao-2026-20260712",
        evaluated_at=NOW,
    )


def test_all_hard_conditions_met_is_eligible_and_replayable() -> None:
    result = _evaluate(
        [_fact("education", "本科"), _fact("grassroots_years", 2)],
        [
            _requirement("education", "one_of", ["本科", "硕士"]),
            _requirement("grassroots_years", "at_least", 2),
        ],
    )

    assert result.status == "eligible"
    assert [check.status for check in result.checks] == ["met", "met"]
    assert result.checks[0].evidence_ids == ["evidence-education"]
    assert result.checks[0].source_text == "education 原始招录条件"
    assert result.checks[0].field == "education"
    assert result.rule_set_version == "guokao-2026-rules-v1"


def test_unmet_hard_condition_is_ineligible() -> None:
    result = _evaluate(
        [_fact("political_status", "群众")],
        [_requirement("political_status", "equals", "中共党员")],
    )

    assert result.status == "ineligible"
    assert result.checks[0].reason_code == "condition_not_met"


def test_position_without_structured_requirements_never_claims_eligible() -> None:
    result = _evaluate([_fact("education", "本科")], [])

    assert result.status == "needs_confirmation"
    assert result.checks[0].reason_code == "requirements_missing"


@pytest.mark.parametrize("case", ["missing", "conflicting", "manual_review"])
def test_unknown_or_non_deterministic_condition_never_claims_eligible(case: str) -> None:
    facts = []
    requirement = _requirement("hukou", "equals", "江西")
    if case == "conflicting":
        facts = [_fact("hukou", "江西", status="conflicting")]
    elif case == "manual_review":
        facts = [_fact("hukou", "江西")]
        requirement = _requirement("hukou", "manual_review", None)

    result = _evaluate(facts, [requirement])

    assert result.status == "needs_confirmation"
    assert result.checks[0].status == "needs_confirmation"


def test_latest_fact_version_is_used_deterministically() -> None:
    result = _evaluate(
        [
            _fact("fresh_graduate", False, fact_id="older", effective_at=NOW),
            _fact(
                "fresh_graduate",
                True,
                fact_id="newer",
                effective_at=NOW + timedelta(seconds=1),
            ),
        ],
        [_requirement("fresh_graduate", "equals", True)],
    )

    assert result.status == "eligible"
    assert result.checks[0].fact_id == "newer"


def test_manual_requirement_cannot_smuggle_an_expected_value() -> None:
    with pytest.raises(ValidationError):
        _requirement("major", "manual_review", "法学")
