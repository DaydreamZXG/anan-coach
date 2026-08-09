from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from sqlalchemy.engine import RowMapping

from app.domains.knowledge.module_vocabulary import ReleasedModuleVocabulary
from app.domains.planning.candidates import EssayPlanningSnapshot


@dataclass(frozen=True)
class ProfileSnapshot:
    profile_version: int
    daily_minutes: int
    study_mode: str


@dataclass(frozen=True)
class PreferenceSnapshot:
    preference_version_id: str
    version_no: int
    timezone: str
    weekday_minutes: int
    weekend_minutes: int
    long_block_minutes: int
    fragment_minutes: int
    regular_rest_weekday: int
    regular_mock_weekday: int
    confirmed: bool
    input_fingerprint: str


@dataclass(frozen=True)
class CapacitySnapshot:
    capacity_version_id: str
    version_no: int
    available_minutes: int
    energy: int
    stress: int
    confidence_level: int
    preferred_xingce_module: str | None
    xingce_only: bool
    preferred_track: str
    confirmed: bool
    source_type: str
    input_fingerprint: str


@dataclass(frozen=True)
class GoalAbilitySnapshot:
    goal_version_id: str | None
    position_name: str | None
    department_name: str | None
    target_lower: float | None
    target_recommended: float | None
    target_upper: float | None
    target_evidence_level: str | None
    target_valid_until: datetime | None
    estimate_version_id: str | None
    estimate_status: str | None
    xingce_lower: float | None
    xingce_recommended: float | None
    xingce_upper: float | None
    shenlun_lower: float | None
    shenlun_recommended: float | None
    shenlun_upper: float | None
    total_lower: float | None
    total_recommended: float | None
    total_upper: float | None
    estimate_confidence: str | None
    estimate_valid_until: datetime | None
    evidence_summary: dict[str, Any] | None
    target_owner: str = "position_goal"
    target_entity_type: str = "position_goal_version"
    target_evidence_summary: str | None = None


@dataclass(frozen=True)
class ActiveStrategyDecision:
    decision_version_id: str
    decision_id: str
    decision_type: str
    scope_type: str
    scope_key: str
    rationale: str
    evidence_refs: tuple[dict[str, Any], ...]
    effective_from: datetime
    effective_until: datetime
    review_at: datetime
    input_fingerprint: str


def _preference_snapshot(row: RowMapping) -> PreferenceSnapshot:
    return PreferenceSnapshot(
        preference_version_id=str(row["preference_version_id"]),
        version_no=int(row["version_no"]),
        timezone=str(row["timezone"]),
        weekday_minutes=int(row["weekday_minutes"]),
        weekend_minutes=int(row["weekend_minutes"]),
        long_block_minutes=int(row["long_block_minutes"]),
        fragment_minutes=int(row["fragment_minutes"]),
        regular_rest_weekday=int(row["regular_rest_weekday"]),
        regular_mock_weekday=int(row["regular_mock_weekday"]),
        confirmed=bool(row["confirmed"]),
        input_fingerprint=str(row["input_fingerprint"]),
    )


def _capacity_snapshot(row: RowMapping) -> CapacitySnapshot:
    return CapacitySnapshot(
        capacity_version_id=str(row["capacity_version_id"]),
        version_no=int(row["version_no"]),
        available_minutes=int(row["available_minutes"]),
        energy=int(row["energy_level"]),
        stress=int(row["stress_level"]),
        confidence_level=int(row["confidence_level"]),
        preferred_xingce_module=(
            str(row["preferred_xingce_module"])
            if row["preferred_xingce_module"] is not None
            else None
        ),
        xingce_only=bool(row["xingce_only"]),
        preferred_track=str(row["preferred_track"]),
        confirmed=bool(row["confirmed"]),
        source_type=str(row["source_type"]),
        input_fingerprint=str(row["input_fingerprint"]),
    )


def _review_is_learning_eligible(review: Any) -> bool:
    if review is None or review["review_status"] != "completed":
        return False
    report = review["grounding_report"]
    return (
        review["grounding_contract_version"] == "essay-grounding-v2"
        and isinstance(report, dict)
        and report.get("verified") is True
        and report.get("eligibleForLearning") is True
    )


def _weakest_dimension(findings: Any, rewrite_plan: Any = None) -> str | None:
    labels = {
        "point_finding": "找点与要点完整度",
        "abstraction": "概括抽象度",
        "structure": "结构层次",
        "expression": "语言表达",
        "format": "格式规范",
        "unsupported_claim": "材料依据",
    }
    key = _weakest_dimension_key(findings, rewrite_plan)
    return labels.get(key) if key is not None else None


def _weakest_dimension_key(findings: Any, rewrite_plan: Any = None) -> str | None:
    if not isinstance(findings, list):
        return None
    recognized_types = {
        "point_finding",
        "abstraction",
        "structure",
        "expression",
        "format",
        "unsupported_claim",
    }
    recognized = [
        finding
        for finding in findings
        if isinstance(finding, dict) and finding.get("type") in recognized_types
    ]
    by_id = {
        str(finding["findingId"]): finding
        for finding in recognized
        if isinstance(finding.get("findingId"), str)
    }
    priority_ids = (
        rewrite_plan.get("priorityFindingIds") if isinstance(rewrite_plan, dict) else None
    )
    if isinstance(priority_ids, list):
        for finding_id in priority_ids:
            finding = by_id.get(str(finding_id))
            if finding is not None:
                return str(finding["type"])
    severity_order = {"high": 0, "medium": 1, "low": 2}
    for finding in sorted(
        recognized,
        key=lambda item: severity_order.get(str(item.get("severity")), 3),
    ):
        if isinstance(finding, dict) and finding.get("type") in recognized_types:
            return str(finding["type"])
    return None


def _fingerprint(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _weakest_canonical_module(
    rows: list[RowMapping],
    *,
    vocabulary: ReleasedModuleVocabulary | None,
) -> str | None:
    if vocabulary is None:
        return None
    totals: dict[str, tuple[int, int]] = {}
    for row in rows:
        canonical = vocabulary.canonicalize(str(row["module_name"]))
        if canonical is None:
            continue
        correct, assessed = totals.get(canonical, (0, 0))
        totals[canonical] = (
            correct + int(row["correct_count"]),
            assessed + int(row["assessment_count"]),
        )
    eligible = [
        (canonical, correct, assessed)
        for canonical, (correct, assessed) in totals.items()
        if assessed >= 5
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda item: (
            Fraction(item[1], item[2]),
            -item[2],
            item[0],
        ),
    )[0]


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _essay_closure_gap(reason: str) -> EssayPlanningSnapshot:
    return EssayPlanningSnapshot(
        "closure_unavailable",
        None,
        pending_reason=reason,
        closure_readiness="unavailable",
        closure_unavailable_reason=reason,
    )


def _essay_closure_context_gap(
    context: RowMapping,
    *,
    decision_id: str,
) -> str | None:
    required = {
        "source attempt": context["source_attempt_id"],
        "source task": context["source_task_version_id"],
        "source material": context["source_material_bundle_version_id"],
        "source rubric": context["source_rubric_version_id"],
        "source answer": context["source_answer_version_id"],
        "current answer": context["current_answer_version_id"],
        "source review": context["source_review_version_id"],
        "finding decision": context["decision_artifact_version_id"],
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return "申论闭环缺少 canonical " + "、".join(missing) + "，不可生成可写任务。"
    if (
        context["source_task_state"] != "published"
        or context["source_material_completeness"] != "complete"
        or context["source_rubric_state"] != "published"
    ):
        return "申论掌握度来源题目、材料或评分规程已不再可消费。"
    grounding = context["grounding_report"]
    if (
        context["source_review_status"] != "completed"
        or context["grounding_contract_version"] != "essay-grounding-v2"
        or not isinstance(grounding, dict)
        or grounding.get("verified") is not True
        or grounding.get("eligibleForLearning") is not True
        or context["execution_fingerprint"] is None
    ):
        return "申论掌握度来源评阅未通过 canonical grounded learning 门禁。"
    if (
        str(context["decision_artifact_version_id"]) != decision_id
        or context["decision_artifact_type"] != "review_acknowledgement"
        or str(context["decision_review_version_id"]) != str(context["source_review_version_id"])
        or str(context["decision_task_version_id"]) != str(context["source_task_version_id"])
        or str(context["decision_material_bundle_version_id"])
        != str(context["source_material_bundle_version_id"])
        or str(context["decision_rubric_version_id"]) != str(context["source_rubric_version_id"])
    ):
        return "申论掌握度 head 与 finding decision 的精确来源不一致。"
    return None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _strict_cause_decision_version_id(evidence_refs: object) -> str | None:
    if not isinstance(evidence_refs, list):
        return None
    prefix = "cause-decision-version:"
    matches: list[str] = []
    for evidence_ref in evidence_refs:
        if not isinstance(evidence_ref, str) or not evidence_ref.startswith(prefix):
            continue
        candidate = evidence_ref.removeprefix(prefix)
        try:
            if str(UUID(candidate)) == candidate:
                matches.append(candidate)
        except ValueError:
            continue
    return matches[0] if len(matches) == 1 else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
