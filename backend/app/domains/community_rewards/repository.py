from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.community_rewards.models import (
    CommunityMilestone,
    CommunityMilestoneEvent,
    CommunityRewardAward,
    CommunityRewardEvaluation,
    CommunityRewardLedgerEntry,
    CommunityRewardLedgerSummary,
    CommunityRewardPolicy,
    RewardAwardStatus,
    RewardCapDimension,
    RewardLedgerEntryType,
    RewardPeriodKind,
    RewardUnit,
)


class CommunityRewardPolicyConflictError(RuntimeError):
    pass


class CommunityRewardCampaignPeriodRequiredError(ValueError):
    pass


class CommunityRewardEventNotFoundError(LookupError):
    pass


class CommunityRewardAwardNotFoundError(LookupError):
    pass


class CommunityRewardLotOwnershipError(PermissionError):
    pass


class CommunityRewardUnusedValueExceededError(RuntimeError):
    pass


class CommunityRewardIdempotencyConflictError(RuntimeError):
    pass


class CommunityRewardRepository(Protocol):
    def publish_policy(
        self, policy: CommunityRewardPolicy
    ) -> CommunityRewardPolicy: ...

    def evaluate_event(
        self,
        *,
        event_id: UUID,
        evaluated_at: datetime,
        campaign_period_start: date | None,
        eligible: bool,
    ) -> CommunityRewardEvaluation: ...

    def consume_award(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        value: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
        occurred_at: datetime,
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]: ...

    def reverse_unused_award_value(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        value: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
        occurred_at: datetime,
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]: ...

    def list_awards(
        self,
        *,
        beneficiary_user_id: UUID,
    ) -> list[CommunityRewardAward]: ...

    def list_ledger_entries(
        self,
        *,
        beneficiary_user_id: UUID,
    ) -> list[CommunityRewardLedgerEntry]: ...

    def ledger_summary(
        self,
        *,
        beneficiary_user_id: UUID,
        at: datetime,
    ) -> CommunityRewardLedgerSummary: ...


class InMemoryCommunityRewardRepository:
    """Thread-safe contract repository used by deterministic acceptance tests."""

    def __init__(
        self,
        *,
        milestone_events: tuple[CommunityMilestoneEvent, ...] = (),
    ) -> None:
        self._lock = RLock()
        self._events = {event.event_id: event for event in milestone_events}
        self._policies_by_version: dict[str, CommunityRewardPolicy] = {}
        self._published_policy_by_milestone: dict[
            CommunityMilestone,
            CommunityRewardPolicy,
        ] = {}
        self._awards: dict[UUID, CommunityRewardAward] = {}
        self._award_by_event: dict[UUID, UUID] = {}
        self._terminal_evaluations: dict[UUID, CommunityRewardEvaluation] = {}
        self._cap_buckets: dict[tuple[UUID, str, str, date], tuple[int, int]] = {}
        self._ledger: dict[UUID, CommunityRewardLedgerEntry] = {}
        self._ledger_by_idempotency_key: dict[str, UUID] = {}

    def add_milestone_event(self, event: CommunityMilestoneEvent) -> None:
        with self._lock:
            existing = self._events.get(event.event_id)
            if existing is not None and existing != event:
                raise CommunityRewardIdempotencyConflictError(
                    "event_id was reused with different milestone evidence"
                )
            self._events[event.event_id] = event

    def publish_policy(self, policy: CommunityRewardPolicy) -> CommunityRewardPolicy:
        with self._lock:
            existing = self._policies_by_version.get(policy.policy_version)
            if existing is not None:
                if _policy_economic_identity(existing) != _policy_economic_identity(
                    policy
                ):
                    raise CommunityRewardPolicyConflictError(
                        "policy_version was reused with different economic terms"
                    )
                return existing
            active = self._published_policy_by_milestone.get(policy.milestone)
            if active is not None:
                raise CommunityRewardPolicyConflictError(
                    f"milestone already has published policy {active.policy_version}"
                )
            self._policies_by_version[policy.policy_version] = policy
            self._published_policy_by_milestone[policy.milestone] = policy
            return policy

    def evaluate_event(
        self,
        *,
        event_id: UUID,
        evaluated_at: datetime,
        campaign_period_start: date | None,
        eligible: bool,
    ) -> CommunityRewardEvaluation:
        with self._lock:
            existing_award_id = self._award_by_event.get(event_id)
            if existing_award_id is not None:
                return CommunityRewardEvaluation(
                    status="replayed",
                    award=self._awards[existing_award_id],
                )
            event = self._events.get(event_id)
            if event is None:
                raise CommunityRewardEventNotFoundError(str(event_id))
            terminal = self._terminal_evaluations.get(event_id)
            if terminal is not None:
                return terminal
            if not eligible:
                result = CommunityRewardEvaluation(status="ineligible", award=None)
                self._terminal_evaluations[event_id] = result
                return result
            policy = self._published_policy_by_milestone.get(event.milestone)
            if policy is None or policy.published_at > event.occurred_at:
                result = CommunityRewardEvaluation(status="no_policy", award=None)
                self._terminal_evaluations[event_id] = result
                return result
            period_start = _period_start(
                period_kind=policy.period_kind,
                occurred_at=event.occurred_at,
                campaign_period_start=campaign_period_start,
            )
            buckets = _cap_bucket_specs(event=event, policy=policy)
            for dimension, scope_key, limit_value in buckets:
                key = (policy.policy_id, dimension, scope_key, period_start)
                granted_value, stored_limit = self._cap_buckets.get(
                    key, (0, limit_value)
                )
                if stored_limit != limit_value:
                    raise CommunityRewardPolicyConflictError(
                        "cap bucket limit differs from its immutable policy"
                    )
                if granted_value + policy.reward_value > limit_value:
                    result = CommunityRewardEvaluation(
                        status="cap_reached",
                        award=None,
                        cap_dimension=cast(RewardCapDimension, dimension),
                    )
                    self._terminal_evaluations[event_id] = result
                    return result
            for dimension, scope_key, limit_value in buckets:
                key = (policy.policy_id, dimension, scope_key, period_start)
                granted_value, _ = self._cap_buckets.get(key, (0, limit_value))
                self._cap_buckets[key] = (
                    granted_value + policy.reward_value,
                    limit_value,
                )
            award = _new_award(
                event=event,
                policy=policy,
                period_start=period_start,
                created_at=evaluated_at,
            )
            grant_entry = _new_ledger_entry(
                award=award,
                entry_type="grant",
                value_delta=policy.reward_value,
                idempotency_key=f"community-reward:grant:{event.event_id}",
                reason_code="community_milestone_policy_grant",
                balance_before=0,
                balance_after=policy.reward_value,
                evidence_refs=event.evidence_refs,
                created_at=evaluated_at,
            )
            self._awards[award.award_id] = award
            self._award_by_event[event.event_id] = award.award_id
            self._ledger[grant_entry.ledger_entry_id] = grant_entry
            self._ledger_by_idempotency_key[grant_entry.idempotency_key] = (
                grant_entry.ledger_entry_id
            )
            return CommunityRewardEvaluation(status="granted", award=award)

    def consume_award(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        value: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
        occurred_at: datetime,
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]:
        return self._change_lot(
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            entry_type="consume",
            value=value,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            occurred_at=occurred_at,
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
        occurred_at: datetime,
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]:
        return self._change_lot(
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            entry_type="reversal",
            value=value,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            occurred_at=occurred_at,
        )

    def _change_lot(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        entry_type: LiteralEntryType,
        value: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
        occurred_at: datetime,
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]:
        with self._lock:
            existing_entry_id = self._ledger_by_idempotency_key.get(idempotency_key)
            if existing_entry_id is not None:
                entry = self._ledger[existing_entry_id]
                _validate_ledger_replay(
                    entry=entry,
                    award_id=award_id,
                    beneficiary_user_id=beneficiary_user_id,
                    entry_type=entry_type,
                    value=value,
                    reason_code=reason_code,
                    evidence_refs=evidence_refs,
                )
                return self._awards[award_id], entry
            award = self._awards.get(award_id)
            if award is None:
                raise CommunityRewardAwardNotFoundError(str(award_id))
            _assert_lot_owner(award=award, beneficiary_user_id=beneficiary_user_id)
            if value > award.unused_value:
                raise CommunityRewardUnusedValueExceededError(
                    "lot mutation cannot exceed unused originating award value"
                )
            balance_before = award.unused_value
            if entry_type == "consume":
                updated = replace(award, consumed_value=award.consumed_value + value)
            else:
                updated = replace(award, reversed_value=award.reversed_value + value)
            updated = replace(updated, status=_award_status(updated))
            entry = _new_ledger_entry(
                award=updated,
                entry_type=entry_type,
                value_delta=-value,
                idempotency_key=idempotency_key,
                reason_code=reason_code,
                balance_before=balance_before,
                balance_after=balance_before - value,
                evidence_refs=evidence_refs,
                created_at=occurred_at,
            )
            self._awards[award_id] = updated
            self._ledger[entry.ledger_entry_id] = entry
            self._ledger_by_idempotency_key[idempotency_key] = entry.ledger_entry_id
            return updated, entry

    def list_awards(
        self,
        *,
        beneficiary_user_id: UUID,
    ) -> list[CommunityRewardAward]:
        with self._lock:
            return sorted(
                (
                    award
                    for award in self._awards.values()
                    if award.beneficiary_user_id == beneficiary_user_id
                ),
                key=lambda award: (award.created_at, str(award.award_id)),
                reverse=True,
            )

    def list_ledger_entries(
        self,
        *,
        beneficiary_user_id: UUID,
    ) -> list[CommunityRewardLedgerEntry]:
        with self._lock:
            return sorted(
                (
                    entry
                    for entry in self._ledger.values()
                    if entry.beneficiary_user_id == beneficiary_user_id
                ),
                key=lambda entry: (entry.created_at, str(entry.ledger_entry_id)),
            )

    def ledger_summary(
        self,
        *,
        beneficiary_user_id: UUID,
        at: datetime,
    ) -> CommunityRewardLedgerSummary:
        with self._lock:
            awards = self.list_awards(beneficiary_user_id=beneficiary_user_id)
            entries = self.list_ledger_entries(beneficiary_user_id=beneficiary_user_id)
            versions = tuple(
                sorted(
                    policy.policy_version
                    for policy in self._published_policy_by_milestone.values()
                    if policy.published_at <= at
                )
            )
            return _ledger_summary(
                active_policy_versions=versions,
                awards=awards,
                entries=entries,
                outstanding_ai_reservations=0,
            )


LiteralEntryType = RewardLedgerEntryType


class SqlCommunityRewardRepository:
    """PostgreSQL repository matching migration 20260716_28 exactly."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def publish_policy(self, policy: CommunityRewardPolicy) -> CommunityRewardPolicy:
        try:
            existing = self._policy_by_version(policy.policy_version)
            if existing is not None:
                if _policy_economic_identity(existing) != _policy_economic_identity(
                    policy
                ):
                    raise CommunityRewardPolicyConflictError(
                        "policy_version was reused with different economic terms"
                    )
                self._session.commit()
                return existing
            active = self._published_policy_for_milestone(policy.milestone)
            if active is not None:
                raise CommunityRewardPolicyConflictError(
                    f"milestone already has published policy {active.policy_version}"
                )
            self._session.execute(
                text(
                    """
                    insert into commerce.community_reward_policies (
                      policy_id, policy_version, milestone, reward_unit, reward_value,
                      author_period_cap, card_period_cap, milestone_period_cap,
                      global_period_cap, period_kind, state, rules_sha256,
                      published_at, created_at
                    ) values (
                      cast(:policy_id as uuid), :policy_version, :milestone, :reward_unit,
                      :reward_value, :author_period_cap, :card_period_cap,
                      :milestone_period_cap, :global_period_cap, :period_kind,
                      'published', :rules_sha256, :published_at, :created_at
                    )
                    """
                ),
                _policy_params(policy),
            )
            self._session.commit()
            return policy
        except Exception:
            self._session.rollback()
            raise

    def evaluate_event(
        self,
        *,
        event_id: UUID,
        evaluated_at: datetime,
        campaign_period_start: date | None,
        eligible: bool,
    ) -> CommunityRewardEvaluation:
        try:
            self._advisory_lock(f"community-reward:event:{event_id}")
            existing = self._award_by_event(event_id)
            if existing is not None:
                self._session.commit()
                return CommunityRewardEvaluation(status="replayed", award=existing)
            event = self._milestone_event(event_id)
            if event is None:
                raise CommunityRewardEventNotFoundError(str(event_id))
            terminal = self._terminal_evaluation(event_id)
            if terminal is not None:
                self._session.commit()
                return terminal
            if not eligible:
                result = CommunityRewardEvaluation(status="ineligible", award=None)
                self._store_terminal_evaluation(
                    event=event,
                    result=result,
                    evaluated_at=evaluated_at,
                )
                self._session.commit()
                return result
            policy = self._published_policy_for_milestone_at(
                event.milestone,
                occurred_at=event.occurred_at,
            )
            if policy is None:
                result = CommunityRewardEvaluation(status="no_policy", award=None)
                self._store_terminal_evaluation(
                    event=event,
                    result=result,
                    evaluated_at=evaluated_at,
                )
                self._session.commit()
                return result
            period_start = _period_start(
                period_kind=policy.period_kind,
                occurred_at=event.occurred_at,
                campaign_period_start=campaign_period_start,
            )
            buckets = _cap_bucket_specs(event=event, policy=policy)
            for dimension, scope_key, _ in sorted(buckets):
                self._advisory_lock(
                    f"community-reward:cap:{policy.policy_id}:{dimension}:"
                    f"{scope_key}:{period_start.isoformat()}"
                )
            for dimension, scope_key, limit_value in buckets:
                row = (
                    self._session.execute(
                        text(
                            """
                        select granted_value, limit_value
                        from commerce.community_reward_cap_buckets
                        where policy_id = cast(:policy_id as uuid)
                          and dimension = :dimension
                          and scope_key = :scope_key
                          and period_start = :period_start
                        for update
                        """
                        ),
                        {
                            "policy_id": str(policy.policy_id),
                            "dimension": dimension,
                            "scope_key": scope_key,
                            "period_start": period_start,
                        },
                    )
                    .mappings()
                    .first()
                )
                granted_value = int(row["granted_value"]) if row else 0
                if row is not None and int(row["limit_value"]) != limit_value:
                    raise CommunityRewardPolicyConflictError(
                        "cap bucket limit differs from its immutable policy"
                    )
                if granted_value + policy.reward_value > limit_value:
                    result = CommunityRewardEvaluation(
                        status="cap_reached",
                        award=None,
                        cap_dimension=cast(RewardCapDimension, dimension),
                    )
                    self._store_terminal_evaluation(
                        event=event,
                        result=result,
                        evaluated_at=evaluated_at,
                    )
                    self._session.commit()
                    return result
            for dimension, scope_key, limit_value in buckets:
                self._session.execute(
                    text(
                        """
                        insert into commerce.community_reward_cap_buckets (
                          policy_id, dimension, scope_key, period_start,
                          granted_value, limit_value, updated_at
                        ) values (
                          cast(:policy_id as uuid), :dimension, :scope_key,
                          :period_start, :reward_value, :limit_value, :updated_at
                        )
                        on conflict (policy_id, dimension, scope_key, period_start)
                        do update set
                          granted_value = commerce.community_reward_cap_buckets.granted_value
                            + excluded.granted_value,
                          updated_at = excluded.updated_at
                        """
                    ),
                    {
                        "policy_id": str(policy.policy_id),
                        "dimension": dimension,
                        "scope_key": scope_key,
                        "period_start": period_start,
                        "reward_value": policy.reward_value,
                        "limit_value": limit_value,
                        "updated_at": evaluated_at,
                    },
                )
            award = _new_award(
                event=event,
                policy=policy,
                period_start=period_start,
                created_at=evaluated_at,
            )
            self._insert_award(award)
            self._insert_ledger_entry(
                _new_ledger_entry(
                    award=award,
                    entry_type="grant",
                    value_delta=policy.reward_value,
                    idempotency_key=f"community-reward:grant:{event.event_id}",
                    reason_code="community_milestone_policy_grant",
                    balance_before=0,
                    balance_after=policy.reward_value,
                    evidence_refs=event.evidence_refs,
                    created_at=evaluated_at,
                )
            )
            self._session.commit()
            return CommunityRewardEvaluation(status="granted", award=award)
        except Exception:
            self._session.rollback()
            raise

    def consume_award(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        value: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
        occurred_at: datetime,
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]:
        return self._change_lot(
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            entry_type="consume",
            value=value,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            occurred_at=occurred_at,
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
        occurred_at: datetime,
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]:
        return self._change_lot(
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            entry_type="reversal",
            value=value,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            occurred_at=occurred_at,
        )

    def _change_lot(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        entry_type: LiteralEntryType,
        value: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
        occurred_at: datetime,
    ) -> tuple[CommunityRewardAward, CommunityRewardLedgerEntry]:
        try:
            self._advisory_lock(f"community-reward:award:{award_id}")
            existing = self._ledger_by_idempotency_key(idempotency_key)
            if existing is not None:
                _validate_ledger_replay(
                    entry=existing,
                    award_id=award_id,
                    beneficiary_user_id=beneficiary_user_id,
                    entry_type=entry_type,
                    value=value,
                    reason_code=reason_code,
                    evidence_refs=evidence_refs,
                )
                award = self._award_by_id(award_id, for_update=False)
                if award is None:
                    raise CommunityRewardAwardNotFoundError(str(award_id))
                self._session.commit()
                return award, existing
            award = self._award_by_id(award_id, for_update=True)
            if award is None:
                raise CommunityRewardAwardNotFoundError(str(award_id))
            _assert_lot_owner(award=award, beneficiary_user_id=beneficiary_user_id)
            reserved_value = 0
            if entry_type == "reversal" and award.reward_unit == "ai_quota":
                reserved_value = int(
                    self._session.execute(
                        text(
                            """
                            select coalesce(sum(reservation.reserved_value), 0)
                            from commerce.community_reward_ai_usage_reservations reservation
                            where reservation.award_id = cast(:award_id as uuid)
                              and not exists (
                                select 1
                                from commerce.community_reward_ai_usage_reservation_events event
                                where event.reservation_id = reservation.reservation_id
                              )
                            """
                        ),
                        {"award_id": str(award_id)},
                    ).scalar_one()
                )
            if value > award.unused_value - reserved_value:
                raise CommunityRewardUnusedValueExceededError(
                    "lot mutation cannot exceed unreserved originating award value"
                )
            balance_before = award.unused_value
            if entry_type == "consume":
                updated = replace(award, consumed_value=award.consumed_value + value)
            else:
                updated = replace(award, reversed_value=award.reversed_value + value)
            updated = replace(updated, status=_award_status(updated))
            self._session.execute(
                text(
                    """
                    update commerce.community_reward_awards
                    set consumed_value = :consumed_value,
                        reversed_value = :reversed_value,
                        status = :status
                    where award_id = cast(:award_id as uuid)
                    """
                ),
                {
                    "award_id": str(award_id),
                    "consumed_value": updated.consumed_value,
                    "reversed_value": updated.reversed_value,
                    "status": updated.status,
                },
            )
            entry = _new_ledger_entry(
                award=updated,
                entry_type=entry_type,
                value_delta=-value,
                idempotency_key=idempotency_key,
                reason_code=reason_code,
                balance_before=balance_before,
                balance_after=balance_before - value,
                evidence_refs=evidence_refs,
                created_at=occurred_at,
            )
            self._insert_ledger_entry(entry)
            self._session.commit()
            return updated, entry
        except Exception:
            self._session.rollback()
            raise

    def list_awards(
        self,
        *,
        beneficiary_user_id: UUID,
    ) -> list[CommunityRewardAward]:
        rows = (
            self._session.execute(
                text(
                    """
                select * from commerce.community_reward_awards
                where beneficiary_user_id = cast(:beneficiary_user_id as uuid)
                order by created_at desc, award_id desc
                """
                ),
                {"beneficiary_user_id": str(beneficiary_user_id)},
            )
            .mappings()
            .all()
        )
        return [_award_from_row(row) for row in rows]

    def list_ledger_entries(
        self,
        *,
        beneficiary_user_id: UUID,
    ) -> list[CommunityRewardLedgerEntry]:
        rows = (
            self._session.execute(
                text(
                    """
                select * from commerce.community_reward_ledger_entries
                where beneficiary_user_id = cast(:beneficiary_user_id as uuid)
                order by created_at, ledger_entry_id
                """
                ),
                {"beneficiary_user_id": str(beneficiary_user_id)},
            )
            .mappings()
            .all()
        )
        return [_ledger_from_row(row) for row in rows]

    def ledger_summary(
        self,
        *,
        beneficiary_user_id: UUID,
        at: datetime,
    ) -> CommunityRewardLedgerSummary:
        awards = self.list_awards(beneficiary_user_id=beneficiary_user_id)
        entries = self.list_ledger_entries(beneficiary_user_id=beneficiary_user_id)
        versions = tuple(
            str(value)
            for value in self._session.execute(
                text(
                    """
                    select policy_version
                    from commerce.community_reward_policies
                    where state = 'published' and published_at <= :at
                    order by policy_version
                    """
                ),
                {"at": at},
            ).scalars()
        )
        outstanding = int(
            self._session.execute(
                text(
                    """
                    select coalesce(sum(reservation.reserved_value), 0)
                    from commerce.community_reward_ai_usage_reservations reservation
                    where reservation.beneficiary_user_id = cast(:user_id as uuid)
                      and not exists (
                        select 1
                        from commerce.community_reward_ai_usage_reservation_events event
                        where event.reservation_id = reservation.reservation_id
                      )
                    """
                ),
                {"user_id": str(beneficiary_user_id)},
            ).scalar_one()
        )
        return _ledger_summary(
            active_policy_versions=versions,
            awards=awards,
            entries=entries,
            outstanding_ai_reservations=outstanding,
        )

    def _advisory_lock(self, lock_key: str) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )

    def _policy_by_version(self, policy_version: str) -> CommunityRewardPolicy | None:
        row = (
            self._session.execute(
                text(
                    """
                select * from commerce.community_reward_policies
                where policy_version = :policy_version
                """
                ),
                {"policy_version": policy_version},
            )
            .mappings()
            .first()
        )
        return _policy_from_row(row) if row else None

    def _published_policy_for_milestone(
        self,
        milestone: CommunityMilestone,
    ) -> CommunityRewardPolicy | None:
        row = (
            self._session.execute(
                text(
                    """
                select * from commerce.community_reward_policies
                where milestone = :milestone and state = 'published'
                """
                ),
                {"milestone": milestone},
            )
            .mappings()
            .first()
        )
        return _policy_from_row(row) if row else None

    def _published_policy_for_milestone_at(
        self,
        milestone: CommunityMilestone,
        *,
        occurred_at: datetime,
    ) -> CommunityRewardPolicy | None:
        row = (
            self._session.execute(
                text(
                    """
                    select * from commerce.community_reward_policies
                    where milestone = :milestone
                      and state in ('published', 'retired')
                      and published_at <= :occurred_at
                    order by published_at desc, created_at desc, policy_id desc
                    limit 1
                    """
                ),
                {"milestone": milestone, "occurred_at": occurred_at},
            )
            .mappings()
            .first()
        )
        return _policy_from_row(row) if row else None

    def _terminal_evaluation(
        self,
        event_id: UUID,
    ) -> CommunityRewardEvaluation | None:
        row = (
            self._session.execute(
                text(
                    """
                    select metadata
                    from commerce.entitlement_audit_logs
                    where action = 'community_reward_evaluation_terminal'
                      and metadata ->> 'eventId' = :event_id
                    order by created_at, audit_log_id
                    limit 1
                    """
                ),
                {"event_id": str(event_id)},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        metadata = dict(row["metadata"] or {})
        cap_dimension = metadata.get("capDimension")
        return CommunityRewardEvaluation(
            status=cast(Any, metadata["status"]),
            award=None,
            cap_dimension=(
                cast(RewardCapDimension, cap_dimension)
                if cap_dimension is not None
                else None
            ),
        )

    def _store_terminal_evaluation(
        self,
        *,
        event: CommunityMilestoneEvent,
        result: CommunityRewardEvaluation,
        evaluated_at: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.entitlement_audit_logs (
                  audit_log_id, user_id, action, feature, allowed, metadata, created_at
                ) values (
                  cast(:audit_log_id as uuid), cast(:user_id as uuid),
                  'community_reward_evaluation_terminal', null, false,
                  cast(:metadata as jsonb), :created_at
                )
                """
            ),
            {
                "audit_log_id": str(uuid4()),
                "user_id": str(event.author_user_id),
                "metadata": _json(
                    {
                        "eventId": str(event.event_id),
                        "milestone": event.milestone,
                        "occurredAt": event.occurred_at.isoformat(),
                        "status": result.status,
                        "capDimension": result.cap_dimension,
                    }
                ),
                "created_at": evaluated_at,
            },
        )

    def _milestone_event(self, event_id: UUID) -> CommunityMilestoneEvent | None:
        row = (
            self._session.execute(
                text(
                    """
                select * from product.community_method_card_milestone_events
                where event_id = cast(:event_id as uuid)
                """
                ),
                {"event_id": str(event_id)},
            )
            .mappings()
            .first()
        )
        return _event_from_row(row) if row else None

    def _award_by_event(self, event_id: UUID) -> CommunityRewardAward | None:
        row = (
            self._session.execute(
                text(
                    """
                select * from commerce.community_reward_awards
                where event_id = cast(:event_id as uuid)
                for update
                """
                ),
                {"event_id": str(event_id)},
            )
            .mappings()
            .first()
        )
        return _award_from_row(row) if row else None

    def _award_by_id(
        self,
        award_id: UUID,
        *,
        for_update: bool,
    ) -> CommunityRewardAward | None:
        suffix = " for update" if for_update else ""
        row = (
            self._session.execute(
                text(
                    "select * from commerce.community_reward_awards "
                    "where award_id = cast(:award_id as uuid)" + suffix
                ),
                {"award_id": str(award_id)},
            )
            .mappings()
            .first()
        )
        return _award_from_row(row) if row else None

    def _ledger_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> CommunityRewardLedgerEntry | None:
        row = (
            self._session.execute(
                text(
                    """
                select * from commerce.community_reward_ledger_entries
                where idempotency_key = :idempotency_key
                """
                ),
                {"idempotency_key": idempotency_key},
            )
            .mappings()
            .first()
        )
        return _ledger_from_row(row) if row else None

    def _insert_award(self, award: CommunityRewardAward) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.community_reward_awards (
                  award_id, event_id, policy_id, beneficiary_user_id, card_id,
                  version_id, milestone, period_start, reward_unit, granted_value,
                  consumed_value, reversed_value, status, evidence_refs,
                  entitlement_before, entitlement_after, cost_minor_units, created_at
                ) values (
                  cast(:award_id as uuid), cast(:event_id as uuid), cast(:policy_id as uuid),
                  cast(:beneficiary_user_id as uuid), cast(:card_id as uuid),
                  cast(:version_id as uuid), :milestone, :period_start, :reward_unit,
                  :granted_value, :consumed_value, :reversed_value, :status,
                  cast(:evidence_refs as jsonb), cast(:entitlement_before as jsonb),
                  cast(:entitlement_after as jsonb), :cost_minor_units, :created_at
                )
                """
            ),
            {
                "award_id": str(award.award_id),
                "event_id": str(award.event_id),
                "policy_id": str(award.policy_id),
                "beneficiary_user_id": str(award.beneficiary_user_id),
                "card_id": str(award.card_id),
                "version_id": str(award.version_id),
                "milestone": award.milestone,
                "period_start": award.period_start,
                "reward_unit": award.reward_unit,
                "granted_value": award.granted_value,
                "consumed_value": award.consumed_value,
                "reversed_value": award.reversed_value,
                "status": award.status,
                "evidence_refs": _json(award.evidence_refs),
                "entitlement_before": _json(award.entitlement_before),
                "entitlement_after": _json(award.entitlement_after),
                "cost_minor_units": award.cost_minor_units,
                "created_at": award.created_at,
            },
        )

    def _insert_ledger_entry(self, entry: CommunityRewardLedgerEntry) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.community_reward_ledger_entries (
                  ledger_entry_id, award_id, beneficiary_user_id, entry_type,
                  reward_unit, value_delta, idempotency_key, reason_code,
                  balance_before, balance_after, evidence_refs, created_at
                ) values (
                  cast(:ledger_entry_id as uuid), cast(:award_id as uuid),
                  cast(:beneficiary_user_id as uuid), :entry_type, :reward_unit,
                  :value_delta, :idempotency_key, :reason_code, :balance_before,
                  :balance_after, cast(:evidence_refs as jsonb), :created_at
                )
                """
            ),
            {
                "ledger_entry_id": str(entry.ledger_entry_id),
                "award_id": str(entry.award_id),
                "beneficiary_user_id": str(entry.beneficiary_user_id),
                "entry_type": entry.entry_type,
                "reward_unit": entry.reward_unit,
                "value_delta": entry.value_delta,
                "idempotency_key": entry.idempotency_key,
                "reason_code": entry.reason_code,
                "balance_before": entry.balance_before,
                "balance_after": entry.balance_after,
                "evidence_refs": _json(entry.evidence_refs),
                "created_at": entry.created_at,
            },
        )


def _period_start(
    *,
    period_kind: RewardPeriodKind,
    occurred_at: datetime,
    campaign_period_start: date | None,
) -> date:
    if period_kind == "day":
        return occurred_at.date()
    if period_kind == "month":
        return occurred_at.date().replace(day=1)
    if campaign_period_start is None:
        raise CommunityRewardCampaignPeriodRequiredError(
            "campaign_period_start is required by campaign reward policy"
        )
    return campaign_period_start


def _cap_bucket_specs(
    *,
    event: CommunityMilestoneEvent,
    policy: CommunityRewardPolicy,
) -> tuple[tuple[str, str, int], ...]:
    return (
        ("author", str(event.author_user_id), policy.author_period_cap),
        ("card", str(event.card_id), policy.card_period_cap),
        ("milestone", event.milestone, policy.milestone_period_cap),
        ("global", "global", policy.global_period_cap),
    )


def _new_award(
    *,
    event: CommunityMilestoneEvent,
    policy: CommunityRewardPolicy,
    period_start: date,
    created_at: datetime,
) -> CommunityRewardAward:
    return CommunityRewardAward(
        award_id=uuid4(),
        event_id=event.event_id,
        policy_id=policy.policy_id,
        beneficiary_user_id=event.author_user_id,
        card_id=event.card_id,
        version_id=event.version_id,
        milestone=event.milestone,
        period_start=period_start,
        reward_unit=policy.reward_unit,
        granted_value=policy.reward_value,
        consumed_value=0,
        reversed_value=0,
        status="granted",
        evidence_refs=event.evidence_refs,
        entitlement_before={
            "beneficiaryUserId": str(event.author_user_id),
            "rewardUnit": policy.reward_unit,
            "originatingAwardBalance": 0,
        },
        entitlement_after={
            "beneficiaryUserId": str(event.author_user_id),
            "rewardUnit": policy.reward_unit,
            "originatingAwardBalance": policy.reward_value,
        },
        cost_minor_units=0,
        created_at=created_at,
    )


def _new_ledger_entry(
    *,
    award: CommunityRewardAward,
    entry_type: RewardLedgerEntryType,
    value_delta: int,
    idempotency_key: str,
    reason_code: str,
    balance_before: int,
    balance_after: int,
    evidence_refs: tuple[str, ...],
    created_at: datetime,
) -> CommunityRewardLedgerEntry:
    return CommunityRewardLedgerEntry(
        ledger_entry_id=uuid4(),
        award_id=award.award_id,
        beneficiary_user_id=award.beneficiary_user_id,
        entry_type=entry_type,
        reward_unit=award.reward_unit,
        value_delta=value_delta,
        idempotency_key=idempotency_key,
        reason_code=reason_code,
        balance_before=balance_before,
        balance_after=balance_after,
        evidence_refs=evidence_refs,
        created_at=created_at,
    )


def _award_status(award: CommunityRewardAward) -> RewardAwardStatus:
    if award.unused_value > 0:
        if award.consumed_value or award.reversed_value:
            return "partially_consumed"
        return "granted"
    if award.reversed_value:
        return "reversed"
    return "consumed"


def _assert_lot_owner(
    *,
    award: CommunityRewardAward,
    beneficiary_user_id: UUID,
) -> None:
    if award.beneficiary_user_id != beneficiary_user_id:
        raise CommunityRewardLotOwnershipError(
            "community reward lots are beneficiary-bound and non-transferable"
        )


def _validate_ledger_replay(
    *,
    entry: CommunityRewardLedgerEntry,
    award_id: UUID,
    beneficiary_user_id: UUID,
    entry_type: LiteralEntryType,
    value: int,
    reason_code: str,
    evidence_refs: tuple[str, ...],
) -> None:
    expected = (
        award_id,
        beneficiary_user_id,
        entry_type,
        -value,
        reason_code,
        evidence_refs,
    )
    actual = (
        entry.award_id,
        entry.beneficiary_user_id,
        entry.entry_type,
        entry.value_delta,
        entry.reason_code,
        entry.evidence_refs,
    )
    if actual != expected:
        raise CommunityRewardIdempotencyConflictError(
            "idempotency_key was reused for a different lot mutation"
        )


def _policy_economic_identity(policy: CommunityRewardPolicy) -> tuple[object, ...]:
    return (
        policy.policy_version,
        policy.milestone,
        policy.reward_unit,
        policy.reward_value,
        policy.author_period_cap,
        policy.card_period_cap,
        policy.milestone_period_cap,
        policy.global_period_cap,
        policy.period_kind,
        policy.rules_sha256,
    )


def _policy_params(policy: CommunityRewardPolicy) -> dict[str, object]:
    return {
        "policy_id": str(policy.policy_id),
        "policy_version": policy.policy_version,
        "milestone": policy.milestone,
        "reward_unit": policy.reward_unit,
        "reward_value": policy.reward_value,
        "author_period_cap": policy.author_period_cap,
        "card_period_cap": policy.card_period_cap,
        "milestone_period_cap": policy.milestone_period_cap,
        "global_period_cap": policy.global_period_cap,
        "period_kind": policy.period_kind,
        "rules_sha256": policy.rules_sha256,
        "published_at": policy.published_at,
        "created_at": policy.created_at,
    }


def _policy_from_row(row: Any) -> CommunityRewardPolicy:
    return CommunityRewardPolicy(
        policy_id=UUID(str(row["policy_id"])),
        policy_version=str(row["policy_version"]),
        milestone=cast(CommunityMilestone, row["milestone"]),
        reward_unit=cast(RewardUnit, row["reward_unit"]),
        reward_value=int(row["reward_value"]),
        author_period_cap=int(row["author_period_cap"]),
        card_period_cap=int(row["card_period_cap"]),
        milestone_period_cap=int(row["milestone_period_cap"]),
        global_period_cap=int(row["global_period_cap"]),
        period_kind=cast(RewardPeriodKind, row["period_kind"]),
        rules_sha256=str(row["rules_sha256"]),
        published_at=row["published_at"],
        created_at=row["created_at"],
    )


def _event_from_row(row: Any) -> CommunityMilestoneEvent:
    return CommunityMilestoneEvent(
        event_id=UUID(str(row["event_id"])),
        author_user_id=UUID(str(row["author_user_id"])),
        card_id=UUID(str(row["card_id"])),
        version_id=UUID(str(row["version_id"])),
        milestone=cast(CommunityMilestone, row["milestone"]),
        evidence_refs=tuple(str(value) for value in (row["evidence_refs"] or [])),
        occurred_at=row["occurred_at"],
    )


def _award_from_row(row: Any) -> CommunityRewardAward:
    return CommunityRewardAward(
        award_id=UUID(str(row["award_id"])),
        event_id=UUID(str(row["event_id"])),
        policy_id=UUID(str(row["policy_id"])),
        beneficiary_user_id=UUID(str(row["beneficiary_user_id"])),
        card_id=UUID(str(row["card_id"])),
        version_id=UUID(str(row["version_id"])),
        milestone=cast(CommunityMilestone, row["milestone"]),
        period_start=row["period_start"],
        reward_unit=cast(RewardUnit, row["reward_unit"]),
        granted_value=int(row["granted_value"]),
        consumed_value=int(row["consumed_value"]),
        reversed_value=int(row["reversed_value"]),
        status=cast(RewardAwardStatus, row["status"]),
        evidence_refs=tuple(str(value) for value in (row["evidence_refs"] or [])),
        entitlement_before=dict(row["entitlement_before"] or {}),
        entitlement_after=dict(row["entitlement_after"] or {}),
        cost_minor_units=int(row["cost_minor_units"]),
        created_at=row["created_at"],
    )


def _ledger_from_row(row: Any) -> CommunityRewardLedgerEntry:
    return CommunityRewardLedgerEntry(
        ledger_entry_id=UUID(str(row["ledger_entry_id"])),
        award_id=UUID(str(row["award_id"])),
        beneficiary_user_id=UUID(str(row["beneficiary_user_id"])),
        entry_type=cast(RewardLedgerEntryType, row["entry_type"]),
        reward_unit=cast(RewardUnit, row["reward_unit"]),
        value_delta=int(row["value_delta"]),
        idempotency_key=str(row["idempotency_key"]),
        reason_code=str(row["reason_code"]),
        balance_before=int(row["balance_before"]),
        balance_after=int(row["balance_after"]),
        evidence_refs=tuple(str(value) for value in (row["evidence_refs"] or [])),
        created_at=row["created_at"],
    )


def _ledger_summary(
    *,
    active_policy_versions: tuple[str, ...],
    awards: list[CommunityRewardAward],
    entries: list[CommunityRewardLedgerEntry],
    outstanding_ai_reservations: int,
) -> CommunityRewardLedgerSummary:
    ai_balance = sum(
        award.unused_value for award in awards if award.reward_unit == "ai_quota"
    ) - outstanding_ai_reservations
    membership_balance = sum(
        award.unused_value
        for award in awards
        if award.reward_unit == "membership_seconds"
    )
    award_ids = tuple(award.award_id for award in awards)
    entry_ids = tuple(entry.ledger_entry_id for entry in entries)
    version_payload = {
        "activePolicyVersions": active_policy_versions,
        "aiQuotaBalance": max(0, ai_balance),
        "awardIds": tuple(str(value) for value in award_ids),
        "entryIds": tuple(str(value) for value in entry_ids),
        "membershipSecondsBalance": membership_balance,
    }
    entitlement_version = sha256(
        json.dumps(
            version_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return CommunityRewardLedgerSummary(
        active_policy_versions=active_policy_versions,
        award_ids=award_ids,
        entry_ids=entry_ids,
        ai_quota_balance=max(0, ai_balance),
        membership_seconds_balance=membership_balance,
        entitlement_version=entitlement_version,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
