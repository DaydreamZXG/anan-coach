from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domains.qualification.schemas import (
    EligibilityEvaluation,
    PositionRequirement,
    QualificationFact,
    QualificationFactKey,
    RequirementConfirmation,
    RequirementCheck,
)


def evaluate_eligibility(
    *,
    facts: list[QualificationFact],
    requirements: list[PositionRequirement],
    rule_set_version: str,
    position_data_version: str,
    evaluated_at: datetime | None = None,
    requirements_authoritative: bool = False,
    confirmations: list[RequirementConfirmation] | None = None,
) -> EligibilityEvaluation:
    """Evaluate hard conditions without inventing facts or interpreting prose."""

    facts_by_key = _latest_facts(facts)
    checks: list[RequirementCheck] = []
    missing: list[QualificationFactKey] = []
    confirmations_by_requirement = {
        item.requirement_id: item for item in (confirmations or [])
    }

    if not requirements and not requirements_authoritative:
        return EligibilityEvaluation(
            status="needs_confirmation",
            checks=[
                RequirementCheck(
                    requirement_id="position-requirements",
                    status="needs_confirmation",
                    reason_code="requirements_missing",
                )
            ],
            missing_fact_keys=[],
            rule_set_version=rule_set_version,
            position_data_version=position_data_version,
            evaluated_at=evaluated_at or datetime.now(UTC),
        )

    for requirement in requirements:
        if not requirement.hard_condition:
            continue
        if requirement.operator == "manual_review":
            confirmation = confirmations_by_requirement.get(requirement.requirement_id)
            if confirmation is None or confirmation.response == "uncertain":
                checks.append(
                    _check(
                        requirement,
                        "needs_confirmation",
                        "manual_review_required",
                        confirmation=confirmation,
                    )
                )
            elif confirmation.response == "satisfied":
                checks.append(
                    _check(
                        requirement,
                        "met",
                        "manual_condition_confirmed",
                        confirmation=confirmation,
                    )
                )
            else:
                checks.append(
                    _check(
                        requirement,
                        "not_met",
                        "manual_condition_not_satisfied",
                        confirmation=confirmation,
                    )
                )
            continue
        fact = facts_by_key.get(requirement.field)
        if fact is None:
            missing.append(requirement.field)
            checks.append(_check(requirement, "needs_confirmation", "fact_missing"))
            continue
        if fact.verification_status == "conflicting":
            checks.append(
                _check(requirement, "needs_confirmation", "fact_conflicting", fact)
            )
            continue
        matched = _matches(
            actual=fact.value,
            operator=requirement.operator,
            expected=requirement.expected,
        )
        checks.append(
            _check(
                requirement,
                "met" if matched else "not_met",
                "condition_met" if matched else "condition_not_met",
                fact,
            )
        )

    if any(check.status == "not_met" for check in checks):
        status = "ineligible"
    elif any(check.status == "needs_confirmation" for check in checks):
        status = "needs_confirmation"
    else:
        status = "eligible"

    return EligibilityEvaluation(
        status=status,
        checks=checks,
        missing_fact_keys=list(dict.fromkeys(missing)),
        rule_set_version=rule_set_version,
        position_data_version=position_data_version,
        evaluated_at=evaluated_at or datetime.now(UTC),
    )


def _latest_facts(facts: list[QualificationFact]) -> dict[QualificationFactKey, QualificationFact]:
    latest: dict[QualificationFactKey, QualificationFact] = {}
    for fact in sorted(facts, key=lambda item: (item.effective_at, item.fact_id)):
        latest[fact.key] = fact
    return latest


def _matches(*, actual: Any, operator: str, expected: Any) -> bool:
    if operator == "equals":
        return actual == expected
    if operator == "one_of":
        return isinstance(expected, list) and actual in expected
    if operator == "at_least":
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and actual >= expected
        )
    if operator == "contains_any":
        expected_values = expected if isinstance(expected, list) else [expected]
        actual_values = actual if isinstance(actual, list) else [actual]
        normalized_actual = {str(value).strip().casefold() for value in actual_values}
        return any(
            str(value).strip().casefold() in normalized_actual for value in expected_values
        )
    raise ValueError(f"unsupported deterministic operator: {operator}")


def _check(
    requirement: PositionRequirement,
    status: str,
    reason_code: str,
    fact: QualificationFact | None = None,
    confirmation: RequirementConfirmation | None = None,
) -> RequirementCheck:
    return RequirementCheck(
        requirement_id=requirement.requirement_id,
        status=status,
        reason_code=reason_code,
        fact_id=fact.fact_id if fact else None,
        evidence_ids=[item.evidence_id for item in fact.evidence] if fact else [],
        field=requirement.field,
        operator=requirement.operator,
        expected=requirement.expected,
        source_text=requirement.source_text,
        source_reference=requirement.source_reference,
        confirmation_id=confirmation.confirmation_id if confirmation else None,
    )
