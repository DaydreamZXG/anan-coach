from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from app.domains.community_rewards.models import (
    CommunityRewardAward,
    CommunityRewardEvaluation,
    CommunityRewardLedgerEntry,
    CommunityRewardLedgerSummary,
    CommunityRewardPolicy,
    PublishRewardPolicy,
)
from app.domains.community_rewards.repository import CommunityRewardRepository


class CommunityRewardPolicyError(ValueError):
    pass


def reward_policy_rules_sha256(command: PublishRewardPolicy) -> str:
    """Hash every economic term; callers cannot approve an incomplete policy."""

    payload = {
        "economicTerms": {
            "authorPeriodCap": command.author_period_cap,
            "cardPeriodCap": command.card_period_cap,
            "globalPeriodCap": command.global_period_cap,
            "milestonePeriodCap": command.milestone_period_cap,
            "rewardValue": command.reward_value,
        },
        "policyVersion": command.policy_version,
        "scope": {
            "milestone": command.milestone,
            "rewardUnit": command.reward_unit,
        },
        "window": {"periodKind": command.period_kind},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class CommunityRewardService:
    def __init__(
        self,
        repository: CommunityRewardRepository,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or (lambda: datetime.now(UTC))

    def publish_policy(self, command: PublishRewardPolicy) -> CommunityRewardPolicy:
        _validate_policy(command)
        published_at = self._now()
        policy = CommunityRewardPolicy(
            policy_id=uuid4(),
            policy_version=command.policy_version,
            milestone=command.milestone,
            reward_unit=command.reward_unit,
            reward_value=command.reward_value,
            author_period_cap=command.author_period_cap,
            card_period_cap=command.card_period_cap,
            milestone_period_cap=command.milestone_period_cap,
            global_period_cap=command.global_period_cap,
            period_kind=command.period_kind,
            rules_sha256=command.rules_sha256,
            published_at=published_at,
            created_at=published_at,
        )
        return self._repository.publish_policy(policy)

    def evaluate_milestone(
        self,
        *,
        event_id: UUID,
        campaign_period_start: date | None = None,
        eligible: bool = True,
    ) -> CommunityRewardEvaluation:
        return self._repository.evaluate_event(
            event_id=event_id,
            evaluated_at=self._now(),
            campaign_period_start=campaign_period_start,
            eligible=eligible,
        )

    def consume_from_award(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        value: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]:
        _validate_lot_mutation(
            value=value,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
        )
        return self._repository.consume_award(
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            value=value,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            occurred_at=self._now(),
        )

    def reverse_unused_award_value(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        value: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]:
        _validate_lot_mutation(
            value=value,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
        )
        return self._repository.reverse_unused_award_value(
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            value=value,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            occurred_at=self._now(),
        )

    def list_awards(self, *, beneficiary_user_id: UUID) -> list[CommunityRewardAward]:
        return self._repository.list_awards(beneficiary_user_id=beneficiary_user_id)

    def list_ledger_entries(
        self,
        *,
        beneficiary_user_id: UUID,
    ) -> list[CommunityRewardLedgerEntry]:
        return self._repository.list_ledger_entries(
            beneficiary_user_id=beneficiary_user_id
        )

    def get_ledger_summary(
        self,
        *,
        beneficiary_user_id: UUID,
    ) -> CommunityRewardLedgerSummary:
        return self._repository.ledger_summary(
            beneficiary_user_id=beneficiary_user_id,
            at=self._now(),
        )


def _validate_policy(command: PublishRewardPolicy) -> None:
    if not command.policy_version.strip():
        raise CommunityRewardPolicyError("policy_version is required")
    values = {
        "reward_value": command.reward_value,
        "author_period_cap": command.author_period_cap,
        "card_period_cap": command.card_period_cap,
        "milestone_period_cap": command.milestone_period_cap,
        "global_period_cap": command.global_period_cap,
    }
    for name, value in values.items():
        if value <= 0:
            raise CommunityRewardPolicyError(f"{name} must be positive")
    for cap_name in (
        "author_period_cap",
        "card_period_cap",
        "milestone_period_cap",
        "global_period_cap",
    ):
        if getattr(command, cap_name) < command.reward_value:
            raise CommunityRewardPolicyError(
                f"{cap_name} cannot be lower than reward_value"
            )
    expected_hash = reward_policy_rules_sha256(command)
    if command.rules_sha256 != expected_hash:
        raise CommunityRewardPolicyError(
            "rules_sha256 must cover the complete published economic policy"
        )


def _validate_lot_mutation(
    *,
    value: int,
    idempotency_key: str,
    reason_code: str,
    evidence_refs: tuple[str, ...],
) -> None:
    if value <= 0:
        raise ValueError("value must be positive")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    if not reason_code.strip():
        raise ValueError("reason_code is required")
    if not evidence_refs:
        raise ValueError("at least one evidence reference is required")
