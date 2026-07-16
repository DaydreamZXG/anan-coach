from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fsrs import Rating

from app.domains.xingce_learning.mastery.scheduler import (
    VerificationEvidence,
    advance_schedule,
    initial_card,
    rating_from_evidence,
)


def test_verification_rating_never_promotes_one_group_to_easy() -> None:
    assert rating_from_evidence(
        [VerificationEvidence("correct", "high") for _ in range(3)]
    ) is Rating.Good
    assert rating_from_evidence(
        [VerificationEvidence("correct", "medium") for _ in range(3)]
    ) is Rating.Good
    assert rating_from_evidence(
        [VerificationEvidence("correct", None) for _ in range(3)]
    ) is Rating.Hard
    assert rating_from_evidence(
        [
            VerificationEvidence("correct", "high"),
            VerificationEvidence("guessed_correct", "low"),
        ]
    ) is Rating.Hard
    assert rating_from_evidence(
        [
            VerificationEvidence("correct", "high"),
            VerificationEvidence("incorrect", "high"),
        ]
    ) is Rating.Again
    assert rating_from_evidence(
        [
            VerificationEvidence(
                "incorrect",
                "high",
                method_correct_calculation_error=True,
            ),
            VerificationEvidence("correct", "medium"),
        ]
    ) is Rating.Hard


def test_active_abandon_defers_without_changing_mastery_rung() -> None:
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    card = initial_card(mastery_id=str(uuid4()), due_at=now)
    deferred = advance_schedule(
        card_payload=card,
        current_stage="first_review",
        evidence=[
            VerificationEvidence("unanswered", None, disposition="active_abandon"),
            VerificationEvidence("correct", "high"),
            VerificationEvidence("correct", "medium"),
        ],
        reviewed_at=now,
    )

    assert deferred.state == "provisionally_mastered"
    assert deferred.stage == "first_review"
    assert deferred.due_at == now + timedelta(days=1)
    assert deferred.rating is Rating.Hard
    assert deferred.reason_code == "verification_deferred_active_abandon"
    assert deferred.review_log["fsrsMemoryUpdated"] is False


def test_real_wrong_answer_still_fails_when_another_item_is_abandoned() -> None:
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    failed = advance_schedule(
        card_payload=initial_card(mastery_id=str(uuid4()), due_at=now),
        current_stage="first_review",
        evidence=[
            VerificationEvidence("unanswered", None, disposition="active_abandon"),
            VerificationEvidence("incorrect", "high"),
        ],
        reviewed_at=now,
    )

    assert failed.state == "reflected"
    assert failed.stage == "immediate"
    assert failed.reason_code == "verification_rating_again"


def test_unknown_scheduler_policy_cannot_be_silently_replayed() -> None:
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    with pytest.raises(ValueError, match="unsupported scheduler policy"):
        advance_schedule(
            card_payload=initial_card(mastery_id=str(uuid4()), due_at=now),
            current_stage="immediate",
            evidence=[VerificationEvidence("correct", "high")],
            reviewed_at=now,
            policy_version="xingce-evidence-ladder-unknown",
        )


def test_mastery_ladder_requires_immediate_first_and_delayed_reviews() -> None:
    started_at = datetime(2026, 7, 16, 8, tzinfo=UTC)
    card = initial_card(mastery_id=str(uuid4()), due_at=started_at)
    evidence = [VerificationEvidence("correct", "high") for _ in range(3)]

    immediate = advance_schedule(
        card_payload=card,
        current_stage="immediate",
        evidence=evidence,
        reviewed_at=started_at,
    )
    assert immediate.state == "provisionally_mastered"
    assert immediate.stage == "first_review"
    assert started_at + timedelta(days=1) <= immediate.due_at <= (
        started_at + timedelta(days=3)
    )

    first = advance_schedule(
        card_payload=immediate.card,
        current_stage=immediate.stage,
        evidence=evidence,
        reviewed_at=immediate.due_at,
    )
    assert first.state == "provisionally_mastered"
    assert first.stage == "delayed_review"
    assert first.due_at + timedelta(0) >= immediate.due_at + timedelta(days=7)
    assert first.due_at <= immediate.due_at + timedelta(days=14)

    delayed = advance_schedule(
        card_payload=first.card,
        current_stage=first.stage,
        evidence=evidence,
        reviewed_at=first.due_at,
    )
    assert delayed.state == "stable_mastered"
    assert delayed.stage == "delayed_review"
    assert delayed.due_at is None


def test_failed_verification_returns_to_reflected_without_same_day_loop() -> None:
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    failed = advance_schedule(
        card_payload=initial_card(mastery_id=str(uuid4()), due_at=now),
        current_stage="immediate",
        evidence=[VerificationEvidence("incorrect", "high")],
        reviewed_at=now,
    )

    assert failed.state == "reflected"
    assert failed.stage == "immediate"
    assert failed.due_at == now + timedelta(days=1)
