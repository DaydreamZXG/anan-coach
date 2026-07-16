from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Sequence
from uuid import UUID

from fsrs import Card, Rating, Scheduler


MasteryState = Literal[
    "reflected",
    "provisionally_mastered",
    "stable_mastered",
    "decayed",
]
VerificationStage = Literal["immediate", "first_review", "delayed_review"]

SCHEDULER_ALGORITHM_VERSION = "py-fsrs-6.3.1"
SCHEDULER_POLICY_VERSION = "xingce-evidence-ladder-v2"

# Freeze the upstream defaults used by py-fsrs 6.3.1. A library upgrade must not
# silently change existing learners' schedules.
FSRS_PARAMETERS = (
    0.212,
    1.2931,
    2.3065,
    8.2956,
    6.4133,
    0.8334,
    3.0194,
    0.001,
    1.8722,
    0.1666,
    0.796,
    1.4835,
    0.0614,
    0.2629,
    1.6483,
    0.6014,
    1.8729,
    0.5425,
    0.0912,
    0.0658,
    0.1542,
)


@dataclass(frozen=True)
class VerificationEvidence:
    result_code: str
    confidence: str | None
    disposition: Literal["answered", "active_abandon"] = "answered"
    method_correct_calculation_error: bool = False


@dataclass(frozen=True)
class ScheduleAdvance:
    state: MasteryState
    stage: VerificationStage
    due_at: datetime | None
    rating: Rating
    card: dict[str, object]
    review_log: dict[str, object]
    reason_code: str


def initial_card(*, mastery_id: str, due_at: datetime) -> dict[str, object]:
    due = _utc(due_at)
    return Card(card_id=_card_id(mastery_id), due=due).to_dict()


def rating_from_evidence(evidence: Sequence[VerificationEvidence]) -> Rating:
    if not evidence:
        raise ValueError("verification evidence cannot be empty")
    if any(
        item.disposition == "answered"
        and item.result_code in {"incorrect", "unanswered", "timeout", "skipped"}
        and not item.method_correct_calculation_error
        for item in evidence
    ):
        return Rating.Again
    result_codes = {
        item.result_code
        for item in evidence
        if item.disposition == "answered"
        and not item.method_correct_calculation_error
    }
    if not result_codes.issubset({"correct", "guessed_correct"}):
        raise ValueError("verification evidence contains an unsupported result code")
    if (
        any(item.disposition == "active_abandon" for item in evidence)
        or any(item.method_correct_calculation_error for item in evidence)
        or "guessed_correct" in result_codes
        or any(
            item.disposition == "answered"
            and not item.method_correct_calculation_error
            and item.confidence not in {"medium", "high"}
            for item in evidence
        )
    ):
        return Rating.Hard
    # A single high-confidence group is evidence for progression, never enough
    # for Easy/stable mastery. Stable mastery is a later state-machine decision.
    return Rating.Good


def advance_schedule(
    *,
    card_payload: dict[str, object],
    current_stage: VerificationStage,
    evidence: Sequence[VerificationEvidence],
    reviewed_at: datetime,
    parameters: Sequence[float] = FSRS_PARAMETERS,
    policy_version: str = SCHEDULER_POLICY_VERSION,
) -> ScheduleAdvance:
    if policy_version != SCHEDULER_POLICY_VERSION:
        raise ValueError(f"unsupported scheduler policy version: {policy_version}")
    reviewed = _utc(reviewed_at)
    rating = rating_from_evidence(evidence)
    if (
        rating is not Rating.Again
        and any(item.disposition == "active_abandon" for item in evidence)
    ):
        # An explicit abandon is missing evidence, not negative evidence. Keep the
        # learner on the same rung and ask again on a later day without mutating
        # the FSRS memory state as though a review had actually happened.
        due_at = reviewed + timedelta(days=1)
        payload = dict(card_payload)
        payload["due"] = due_at.isoformat()
        return ScheduleAdvance(
            state=(
                "reflected"
                if current_stage == "immediate"
                else "provisionally_mastered"
            ),
            stage=current_stage,
            due_at=due_at,
            rating=rating,
            card=payload,
            review_log={
                "policyVersion": policy_version,
                "outcome": "deferred_active_abandon",
                "reviewedAt": reviewed.isoformat(),
                "fsrsMemoryUpdated": False,
            },
            reason_code="verification_deferred_active_abandon",
        )
    card, review_log = _scheduler(parameters).review_card(
        Card.from_dict(card_payload),
        rating,
        reviewed,
    )
    proposed_due = _utc(card.due)

    if rating is Rating.Again:
        state: MasteryState = "reflected"
        stage: VerificationStage = "immediate"
        due_at = reviewed + timedelta(days=1)
    elif current_stage == "immediate":
        state = "provisionally_mastered"
        stage = "first_review"
        due_at = _clamp_due(proposed_due, reviewed, minimum_days=1, maximum_days=3)
    elif current_stage == "first_review":
        state = "provisionally_mastered"
        stage = "delayed_review"
        due_at = _clamp_due(proposed_due, reviewed, minimum_days=7, maximum_days=14)
    elif rating is Rating.Hard:
        state = "provisionally_mastered"
        stage = "delayed_review"
        due_at = _clamp_due(proposed_due, reviewed, minimum_days=7, maximum_days=14)
    else:
        state = "stable_mastered"
        stage = "delayed_review"
        due_at = None

    payload = dict(card.to_dict())
    payload["due"] = due_at.isoformat() if due_at is not None else proposed_due.isoformat()
    return ScheduleAdvance(
        state=state,
        stage=stage,
        due_at=due_at,
        rating=rating,
        card=payload,
        review_log=dict(review_log.to_dict()),
        reason_code=f"verification_rating_{rating.name.lower()}",
    )


def _scheduler(parameters: Sequence[float]) -> Scheduler:
    return Scheduler(
        parameters=tuple(parameters),
        learning_steps=(),
        relearning_steps=(),
        enable_fuzzing=False,
    )


def _card_id(mastery_id: str) -> int:
    # py-fsrs only needs a stable integer identity. Keep it within signed bigint
    # for cross-language replay and database tooling.
    return UUID(mastery_id).int % 9_000_000_000_000_000_000


def _clamp_due(
    proposed_due: datetime,
    reviewed_at: datetime,
    *,
    minimum_days: int,
    maximum_days: int,
) -> datetime:
    minimum = reviewed_at + timedelta(days=minimum_days)
    maximum = reviewed_at + timedelta(days=maximum_days)
    return max(minimum, min(maximum, proposed_due))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
