from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.community_rewards.models import (
    CommunityRewardAward,
    CommunityRewardEvaluation,
    CommunityRewardLedgerEntry,
    CommunityRewardLedgerSummary,
)


class CommunityRewardBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CommunityRewardAwardResponse(CommunityRewardBaseModel):
    award_id: UUID = Field(alias="awardId")
    event_id: UUID = Field(alias="eventId")
    policy_id: UUID = Field(alias="policyId")
    card_id: UUID = Field(alias="cardId")
    version_id: UUID = Field(alias="versionId")
    milestone: str
    period_start: date = Field(alias="periodStart")
    reward_unit: Literal["ai_quota", "membership_seconds"] = Field(alias="rewardUnit")
    granted_value: int = Field(alias="grantedValue")
    consumed_value: int = Field(alias="consumedValue")
    reversed_value: int = Field(alias="reversedValue")
    unused_value: int = Field(alias="unusedValue")
    status: str
    created_at: datetime = Field(alias="createdAt")

    @classmethod
    def from_record(cls, record: CommunityRewardAward) -> CommunityRewardAwardResponse:
        return cls(
            award_id=record.award_id,
            event_id=record.event_id,
            policy_id=record.policy_id,
            card_id=record.card_id,
            version_id=record.version_id,
            milestone=record.milestone,
            period_start=record.period_start,
            reward_unit=record.reward_unit,
            granted_value=record.granted_value,
            consumed_value=record.consumed_value,
            reversed_value=record.reversed_value,
            unused_value=record.unused_value,
            status=record.status,
            created_at=record.created_at,
        )


class CommunityRewardEvaluationResponse(CommunityRewardBaseModel):
    status: Literal[
        "granted",
        "replayed",
        "no_policy",
        "cap_reached",
        "ineligible",
    ]
    cap_dimension: Literal["author", "card", "milestone", "global"] | None = Field(
        default=None,
        alias="capDimension",
    )
    award: CommunityRewardAwardResponse | None

    @classmethod
    def from_record(
        cls,
        record: CommunityRewardEvaluation,
    ) -> CommunityRewardEvaluationResponse:
        return cls(
            status=record.status,
            cap_dimension=record.cap_dimension,
            award=(
                CommunityRewardAwardResponse.from_record(record.award)
                if record.award is not None
                else None
            ),
        )


class CommunityRewardLotMutationRequest(CommunityRewardBaseModel):
    value: int = Field(gt=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=240)
    reason_code: str = Field(alias="reasonCode", min_length=1, max_length=120)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=20)


class CommunityRewardLedgerEntryResponse(CommunityRewardBaseModel):
    ledger_entry_id: UUID = Field(alias="ledgerEntryId")
    award_id: UUID = Field(alias="awardId")
    entry_type: Literal["grant", "consume", "reversal", "expire"] = Field(
        alias="entryType"
    )
    reward_unit: Literal["ai_quota", "membership_seconds"] = Field(alias="rewardUnit")
    value_delta: int = Field(alias="valueDelta")
    idempotency_key: str = Field(alias="idempotencyKey")
    reason_code: str = Field(alias="reasonCode")
    balance_before: int = Field(alias="balanceBefore")
    balance_after: int = Field(alias="balanceAfter")
    evidence_refs: list[str] = Field(alias="evidenceRefs")
    created_at: datetime = Field(alias="createdAt")

    @classmethod
    def from_record(
        cls,
        record: CommunityRewardLedgerEntry,
    ) -> CommunityRewardLedgerEntryResponse:
        return cls(
            ledger_entry_id=record.ledger_entry_id,
            award_id=record.award_id,
            entry_type=record.entry_type,
            reward_unit=record.reward_unit,
            value_delta=record.value_delta,
            idempotency_key=record.idempotency_key,
            reason_code=record.reason_code,
            balance_before=record.balance_before,
            balance_after=record.balance_after,
            evidence_refs=list(record.evidence_refs),
            created_at=record.created_at,
        )


class CommunityRewardLedgerResponse(CommunityRewardBaseModel):
    """Frozen public v2 ledger projection; detailed records stay internal."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    active_policy_versions: list[str] = Field(alias="activePolicyVersions")
    award_ids: list[UUID] = Field(alias="awardIds")
    entry_ids: list[UUID] = Field(alias="entryIds")
    ai_quota_balance: int = Field(alias="aiQuotaBalance", ge=0)
    membership_seconds_balance: int = Field(
        alias="membershipSecondsBalance",
        ge=0,
    )
    entitlement_version: str = Field(alias="entitlementVersion", min_length=64)

    @classmethod
    def from_record(
        cls,
        record: CommunityRewardLedgerSummary,
    ) -> CommunityRewardLedgerResponse:
        return cls(
            active_policy_versions=list(record.active_policy_versions),
            award_ids=list(record.award_ids),
            entry_ids=list(record.entry_ids),
            ai_quota_balance=record.ai_quota_balance,
            membership_seconds_balance=record.membership_seconds_balance,
            entitlement_version=record.entitlement_version,
        )
