from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest

from app.domains.community_rewards.models import (
    CommunityMilestone,
    CommunityMilestoneEvent,
    PublishRewardPolicy,
    RewardPeriodKind,
    RewardUnit,
)
from app.domains.community_rewards.repository import (
    CommunityRewardIdempotencyConflictError,
    CommunityRewardLotOwnershipError,
    CommunityRewardUnusedValueExceededError,
    InMemoryCommunityRewardRepository,
)
from app.domains.community_rewards.service import (
    CommunityRewardService,
    reward_policy_rules_sha256,
)


NOW = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)


def test_stage35_reward_policy_replay_caps_and_unused_reversal() -> None:
    author_a = uuid4()
    author_b = uuid4()
    card_a = uuid4()
    card_b = uuid4()

    no_policy_event = _event(
        author=author_a,
        card=card_a,
        milestone="official_derivation",
    )
    author_cap_event_a = _event(
        author=author_a,
        card=card_a,
        milestone="automated_review_passed",
    )
    author_cap_event_b = _event(
        author=author_a,
        card=card_b,
        milestone="automated_review_passed",
    )
    card_cap_event_a = _event(
        author=author_a,
        card=card_a,
        milestone="independent_evidence_sufficient",
    )
    card_cap_event_b = _event(
        author=author_b,
        card=card_a,
        milestone="independent_evidence_sufficient",
    )
    milestone_cap_event_a = _event(
        author=author_a,
        card=card_a,
        milestone="conservative_effectiveness_reached",
    )
    milestone_cap_event_b = _event(
        author=author_b,
        card=card_b,
        milestone="conservative_effectiveness_reached",
    )
    global_cap_event_a = _event(
        author=author_a,
        card=card_a,
        milestone="report_confirmed",
    )
    global_cap_event_b = _event(
        author=author_b,
        card=card_b,
        milestone="report_confirmed",
    )
    repository = InMemoryCommunityRewardRepository(
        milestone_events=(
            no_policy_event,
            author_cap_event_a,
            author_cap_event_b,
            card_cap_event_a,
            card_cap_event_b,
            milestone_cap_event_a,
            milestone_cap_event_b,
            global_cap_event_a,
            global_cap_event_b,
        )
    )
    service = CommunityRewardService(repository, now=lambda: NOW)

    no_policy = service.evaluate_milestone(event_id=no_policy_event.event_id)
    assert no_policy.status == "no_policy"
    assert no_policy.award is None
    assert service.list_awards(beneficiary_user_id=author_a) == []

    author_policy = _publish_policy(
        service,
        policy_version="test-author-cap-v1",
        milestone="automated_review_passed",
        reward_unit="ai_quota",
        reward_value=10,
        author_period_cap=10,
        card_period_cap=100,
        milestone_period_cap=100,
        global_period_cap=100,
    )
    replayed_policy = _publish_policy(
        service,
        policy_version="test-author-cap-v1",
        milestone="automated_review_passed",
        reward_unit="ai_quota",
        reward_value=10,
        author_period_cap=10,
        card_period_cap=100,
        milestone_period_cap=100,
        global_period_cap=100,
    )
    assert replayed_policy.policy_id == author_policy.policy_id

    barrier = Barrier(2)

    def grant_same_event() -> tuple[str, UUID | None]:
        barrier.wait()
        result = service.evaluate_milestone(event_id=author_cap_event_a.event_id)
        return result.status, result.award.award_id if result.award else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        race_results = list(executor.map(lambda _: grant_same_event(), range(2)))
    assert {result[0] for result in race_results} == {"granted", "replayed"}
    assert len({result[1] for result in race_results}) == 1

    event_replay = service.evaluate_milestone(event_id=author_cap_event_a.event_id)
    author_cap = service.evaluate_milestone(event_id=author_cap_event_b.event_id)
    assert event_replay.status == "replayed"
    assert author_cap.status == "cap_reached"
    assert author_cap.cap_dimension == "author"

    _publish_policy(
        service,
        policy_version="test-card-cap-v1",
        milestone="independent_evidence_sufficient",
        reward_unit="membership_seconds",
        reward_value=20,
        author_period_cap=100,
        card_period_cap=20,
        milestone_period_cap=100,
        global_period_cap=100,
    )
    membership_award = service.evaluate_milestone(event_id=card_cap_event_a.event_id)
    card_cap = service.evaluate_milestone(event_id=card_cap_event_b.event_id)
    assert membership_award.status == "granted"
    assert membership_award.award is not None
    assert membership_award.award.reward_unit == "membership_seconds"
    assert card_cap.status == "cap_reached"
    assert card_cap.cap_dimension == "card"

    _publish_policy(
        service,
        policy_version="test-milestone-cap-v1",
        milestone="conservative_effectiveness_reached",
        reward_unit="ai_quota",
        reward_value=5,
        author_period_cap=100,
        card_period_cap=100,
        milestone_period_cap=5,
        global_period_cap=100,
    )
    assert (
        service.evaluate_milestone(event_id=milestone_cap_event_a.event_id).status
        == "granted"
    )
    milestone_cap = service.evaluate_milestone(event_id=milestone_cap_event_b.event_id)
    assert milestone_cap.status == "cap_reached"
    assert milestone_cap.cap_dimension == "milestone"

    _publish_policy(
        service,
        policy_version="test-global-cap-v1",
        milestone="report_confirmed",
        reward_unit="membership_seconds",
        reward_value=7,
        author_period_cap=100,
        card_period_cap=100,
        milestone_period_cap=100,
        global_period_cap=7,
    )
    assert (
        service.evaluate_milestone(event_id=global_cap_event_a.event_id).status
        == "granted"
    )
    global_cap = service.evaluate_milestone(event_id=global_cap_event_b.event_id)
    assert global_cap.status == "cap_reached"
    assert global_cap.cap_dimension == "global"

    ai_award = event_replay.award
    assert ai_award is not None
    consumed_award, consume_entry = service.consume_from_award(
        award_id=ai_award.award_id,
        beneficiary_user_id=author_a,
        value=4,
        idempotency_key="test-consume-author-a-1",
        reason_code="qualified_ai_feature_execution",
        evidence_refs=("execution:test-1",),
    )
    replayed_consumption, replayed_entry = service.consume_from_award(
        award_id=ai_award.award_id,
        beneficiary_user_id=author_a,
        value=4,
        idempotency_key="test-consume-author-a-1",
        reason_code="qualified_ai_feature_execution",
        evidence_refs=("execution:test-1",),
    )
    assert consumed_award.consumed_value == 4
    assert consumed_award.unused_value == 6
    assert replayed_consumption == consumed_award
    assert replayed_entry == consume_entry

    with pytest.raises(CommunityRewardIdempotencyConflictError):
        service.consume_from_award(
            award_id=ai_award.award_id,
            beneficiary_user_id=author_a,
            value=5,
            idempotency_key="test-consume-author-a-1",
            reason_code="qualified_ai_feature_execution",
            evidence_refs=("execution:test-1",),
        )
    with pytest.raises(CommunityRewardLotOwnershipError):
        service.consume_from_award(
            award_id=ai_award.award_id,
            beneficiary_user_id=author_b,
            value=1,
            idempotency_key="test-transfer-forbidden",
            reason_code="qualified_ai_feature_execution",
            evidence_refs=("execution:test-2",),
        )

    reversed_award, reversal_entry = service.reverse_unused_award_value(
        award_id=ai_award.award_id,
        beneficiary_user_id=author_a,
        value=6,
        idempotency_key="test-reverse-unused-author-a-1",
        reason_code="source_milestone_invalidated",
        evidence_refs=("moderation:decision-1",),
    )
    assert reversed_award.consumed_value == 4
    assert reversed_award.reversed_value == 6
    assert reversed_award.unused_value == 0
    assert reversed_award.status == "reversed"
    assert reversal_entry.balance_before == 6
    assert reversal_entry.balance_after == 0
    with pytest.raises(CommunityRewardUnusedValueExceededError):
        service.reverse_unused_award_value(
            award_id=ai_award.award_id,
            beneficiary_user_id=author_a,
            value=1,
            idempotency_key="test-reverse-consumed-forbidden",
            reason_code="source_milestone_invalidated",
            evidence_refs=("moderation:decision-1",),
        )

    lot_ledger = [
        entry
        for entry in service.list_ledger_entries(beneficiary_user_id=author_a)
        if entry.award_id == ai_award.award_id
    ]
    ledger_by_type = {entry.entry_type: entry for entry in lot_ledger}
    assert set(ledger_by_type) == {"grant", "consume", "reversal"}
    assert ledger_by_type["grant"].value_delta == 10
    assert ledger_by_type["grant"].balance_after == 10
    assert ledger_by_type["consume"].value_delta == -4
    assert ledger_by_type["consume"].balance_after == 6
    assert ledger_by_type["reversal"].value_delta == -6
    assert ledger_by_type["reversal"].balance_after == 0


def test_reward_evaluation_uses_event_time_and_terminal_denials_do_not_backfill() -> None:
    author = uuid4()
    card = uuid4()
    old_event = replace(
        _event(author=author, card=card, milestone="automated_review_passed"),
        occurred_at=NOW - timedelta(days=1),
    )
    excluded_event = _event(
        author=author,
        card=uuid4(),
        milestone="independent_evidence_sufficient",
    )
    repository = InMemoryCommunityRewardRepository(
        milestone_events=(old_event, excluded_event)
    )
    clock = [NOW]
    service = CommunityRewardService(repository, now=lambda: clock[0])

    # A policy published after the milestone cannot grant retroactively.
    _publish_policy(
        service,
        policy_version="future-policy-v1",
        milestone="automated_review_passed",
        reward_unit="ai_quota",
        reward_value=1,
        author_period_cap=10,
        card_period_cap=10,
        milestone_period_cap=10,
        global_period_cap=10,
    )
    denied = service.evaluate_milestone(event_id=old_event.event_id)
    assert denied.status == "no_policy"

    # The terminal result remains stable even if processing is replayed later.
    clock[0] = NOW + timedelta(days=30)
    assert service.evaluate_milestone(event_id=old_event.event_id) == denied

    excluded = service.evaluate_milestone(
        event_id=excluded_event.event_id,
        eligible=False,
    )
    assert excluded.status == "ineligible"
    assert service.evaluate_milestone(event_id=excluded_event.event_id) == excluded


def _event(
    *,
    author: UUID,
    card: UUID,
    milestone: CommunityMilestone,
) -> CommunityMilestoneEvent:
    event_id = uuid4()
    return CommunityMilestoneEvent(
        event_id=event_id,
        author_user_id=author,
        card_id=card,
        version_id=uuid4(),
        milestone=milestone,
        evidence_refs=(f"test-evidence:{event_id}",),
        occurred_at=NOW,
    )


def _publish_policy(
    service: CommunityRewardService,
    *,
    policy_version: str,
    milestone: CommunityMilestone,
    reward_unit: RewardUnit,
    reward_value: int,
    author_period_cap: int,
    card_period_cap: int,
    milestone_period_cap: int,
    global_period_cap: int,
    period_kind: RewardPeriodKind = "month",
):
    command = PublishRewardPolicy(
        policy_version=policy_version,
        milestone=milestone,
        reward_unit=reward_unit,
        reward_value=reward_value,
        author_period_cap=author_period_cap,
        card_period_cap=card_period_cap,
        milestone_period_cap=milestone_period_cap,
        global_period_cap=global_period_cap,
        period_kind=period_kind,
        rules_sha256="pending",
    )
    command = replace(command, rules_sha256=reward_policy_rules_sha256(command))
    return service.publish_policy(command)
