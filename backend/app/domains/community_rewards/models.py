from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID


CommunityMilestone = Literal[
    "automated_review_passed",
    "independent_evidence_sufficient",
    "conservative_effectiveness_reached",
    "report_confirmed",
    "official_derivation",
]
RewardUnit = Literal["ai_quota", "membership_seconds"]
RewardPeriodKind = Literal["day", "month", "campaign"]
RewardAwardStatus = Literal[
    "granted",
    "partially_consumed",
    "consumed",
    "reversed",
]
RewardLedgerEntryType = Literal["grant", "consume", "reversal", "expire"]
RewardEvaluationStatus = Literal[
    "granted",
    "replayed",
    "no_policy",
    "cap_reached",
    "ineligible",
]
RewardCapDimension = Literal["author", "card", "milestone", "global"]


@dataclass(frozen=True)
class CommunityMilestoneEvent:
    event_id: UUID
    author_user_id: UUID
    card_id: UUID
    version_id: UUID
    milestone: CommunityMilestone
    evidence_refs: tuple[str, ...]
    occurred_at: datetime


@dataclass(frozen=True)
class PublishRewardPolicy:
    policy_version: str
    milestone: CommunityMilestone
    reward_unit: RewardUnit
    reward_value: int
    author_period_cap: int
    card_period_cap: int
    milestone_period_cap: int
    global_period_cap: int
    period_kind: RewardPeriodKind
    rules_sha256: str


@dataclass(frozen=True)
class CommunityRewardPolicy:
    policy_id: UUID
    policy_version: str
    milestone: CommunityMilestone
    reward_unit: RewardUnit
    reward_value: int
    author_period_cap: int
    card_period_cap: int
    milestone_period_cap: int
    global_period_cap: int
    period_kind: RewardPeriodKind
    rules_sha256: str
    published_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class CommunityRewardAward:
    award_id: UUID
    event_id: UUID
    policy_id: UUID
    beneficiary_user_id: UUID
    card_id: UUID
    version_id: UUID
    milestone: CommunityMilestone
    period_start: date
    reward_unit: RewardUnit
    granted_value: int
    consumed_value: int
    reversed_value: int
    status: RewardAwardStatus
    evidence_refs: tuple[str, ...]
    entitlement_before: dict[str, object]
    entitlement_after: dict[str, object]
    cost_minor_units: int
    created_at: datetime

    @property
    def unused_value(self) -> int:
        return self.granted_value - self.consumed_value - self.reversed_value


@dataclass(frozen=True)
class CommunityRewardLedgerEntry:
    ledger_entry_id: UUID
    award_id: UUID
    beneficiary_user_id: UUID
    entry_type: RewardLedgerEntryType
    reward_unit: RewardUnit
    value_delta: int
    idempotency_key: str
    reason_code: str
    balance_before: int
    balance_after: int
    evidence_refs: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class CommunityRewardEvaluation:
    status: RewardEvaluationStatus
    award: CommunityRewardAward | None
    cap_dimension: RewardCapDimension | None = None


@dataclass(frozen=True)
class CommunityRewardLedgerSummary:
    active_policy_versions: tuple[str, ...]
    award_ids: tuple[UUID, ...]
    entry_ids: tuple[UUID, ...]
    ai_quota_balance: int
    membership_seconds_balance: int
    entitlement_version: str
