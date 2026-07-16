from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


class RewardEntitlementContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class RewardUsageReservationRecord:
    reservation_id: UUID
    reservation_key: str
    user_id: str
    feature: str
    value: int
    business_key: str
    status: Literal["reserved", "consumed", "refunded"]
    job_id: UUID | None
    award_id: UUID
    reserved_at: datetime
    consumed_at: datetime | None
    refunded_at: datetime | None


@dataclass(frozen=True)
class RewardMembershipActivationRecord:
    activation_id: UUID
    award_id: UUID
    beneficiary_user_id: UUID
    activated_from: datetime
    activated_until: datetime
    activation_seconds: int
    activation_key: str


class RewardEntitlementRepository:
    """Append-only bridge from community reward lots into Commerce execution."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_ai_reservation(
        self,
        *,
        reservation_key: str,
        for_update: bool,
    ) -> RewardUsageReservationRecord | None:
        lock = " for update of reservation" if for_update else ""
        row = (
            self._session.execute(
                text(
                    """
                    select
                      reservation.*,
                      coalesce(event.event_type, 'reserved') as terminal_type,
                      event.occurred_at as terminal_at,
                      binding.metadata ->> 'jobId' as job_id
                    from commerce.community_reward_ai_usage_reservations reservation
                    left join commerce.community_reward_ai_usage_reservation_events event
                      on event.reservation_id = reservation.reservation_id
                    left join lateral (
                      select audit.metadata
                      from commerce.entitlement_audit_logs audit
                      where audit.action = 'community_reward_ai_job_bound'
                        and audit.metadata ->> 'reservationId' = reservation.reservation_id::text
                      order by audit.created_at desc, audit.audit_log_id desc
                      limit 1
                    ) binding on true
                    where reservation.reservation_key = :reservation_key
                    """
                    + lock
                ),
                {"reservation_key": reservation_key},
            )
            .mappings()
            .one_or_none()
        )
        return _ai_reservation_from_row(row) if row is not None else None

    def reserve_ai_usage(
        self,
        *,
        user_id: str,
        feature: str,
        reservation_key: str,
        business_key: str,
        server_session_ref: str,
        reserved_at: datetime,
        commit: bool,
    ) -> RewardUsageReservationRecord | None:
        fingerprint = _reservation_fingerprint(
            user_id=user_id,
            feature=feature,
            reservation_key=reservation_key,
            business_key=business_key,
            server_session_ref=server_session_ref,
        )
        self._advisory_lock(f"community-reward:ai-key:{reservation_key}")
        existing = self.find_ai_reservation(
            reservation_key=reservation_key,
            for_update=True,
        )
        if existing is not None:
            row_fingerprint = self._session.execute(
                text(
                    """
                    select request_fingerprint
                    from commerce.community_reward_ai_usage_reservations
                    where reservation_id = cast(:reservation_id as uuid)
                    """
                ),
                {"reservation_id": str(existing.reservation_id)},
            ).scalar_one()
            if str(row_fingerprint) != fingerprint:
                raise RewardEntitlementContractError(
                    "reservation key belongs to different immutable reward input"
                )
            if commit:
                self._session.commit()
            return existing

        self._advisory_lock(f"community-reward:ai-user:{user_id}")
        lot = (
            self._session.execute(
                text(
                    """
                    select
                      award.award_id,
                      award.policy_id,
                      award.granted_value,
                      award.consumed_value,
                      award.reversed_value,
                      scope.scope_resource_id,
                      coalesce(reserved.outstanding, 0) as outstanding
                    from commerce.community_reward_awards award
                    join commerce.community_reward_policy_scopes scope
                      on scope.policy_id = award.policy_id
                     and scope.reward_unit = award.reward_unit
                     and scope.scope_kind = 'ai_features'
                    left join lateral (
                      select coalesce(sum(reservation.reserved_value), 0) as outstanding
                      from commerce.community_reward_ai_usage_reservations reservation
                      where reservation.award_id = award.award_id
                        and not exists (
                          select 1
                          from commerce.community_reward_ai_usage_reservation_events event
                          where event.reservation_id = reservation.reservation_id
                        )
                    ) reserved on true
                    where award.beneficiary_user_id = cast(:user_id as uuid)
                      and award.reward_unit = 'ai_quota'
                      and scope.eligible_ai_features ? :feature
                      and award.granted_value - award.consumed_value
                        - award.reversed_value - coalesce(reserved.outstanding, 0) >= 1
                    order by award.created_at, award.award_id
                    limit 1
                    for update of award
                    """
                ),
                {"user_id": user_id, "feature": feature},
            )
            .mappings()
            .one_or_none()
        )
        if lot is None:
            return None

        reservation_id = uuid4()
        evidence_refs = (
            f"commerce-business:{business_key}",
            f"community-reward-award:{lot['award_id']}",
        )
        self._session.execute(
            text(
                """
                insert into commerce.community_reward_ai_usage_reservations (
                  reservation_id, award_id, beneficiary_user_id, reward_unit,
                  scope_resource_id, policy_id, scope_kind, eligible_feature,
                  reserved_value, reservation_key, request_fingerprint,
                  server_session_ref, evidence_refs, reserved_at
                ) values (
                  cast(:reservation_id as uuid), cast(:award_id as uuid),
                  cast(:user_id as uuid), 'ai_quota', cast(:scope_resource_id as uuid),
                  cast(:policy_id as uuid), 'ai_features', :feature, 1,
                  :reservation_key, :request_fingerprint, :server_session_ref,
                  cast(:evidence_refs as jsonb), :reserved_at
                )
                """
            ),
            {
                "reservation_id": str(reservation_id),
                "award_id": str(lot["award_id"]),
                "user_id": user_id,
                "scope_resource_id": str(lot["scope_resource_id"]),
                "policy_id": str(lot["policy_id"]),
                "feature": feature,
                "reservation_key": reservation_key,
                "request_fingerprint": fingerprint,
                "server_session_ref": server_session_ref,
                "evidence_refs": _json(evidence_refs),
                "reserved_at": reserved_at,
            },
        )
        if commit:
            self._session.commit()
        created = self.find_ai_reservation(
            reservation_key=reservation_key,
            for_update=False,
        )
        if created is None:
            raise RewardEntitlementContractError("reward reservation was not persisted")
        return created

    def release_ai_usage(
        self,
        *,
        reservation_key: str,
        occurred_at: datetime,
        commit: bool,
    ) -> RewardUsageReservationRecord:
        return self._transition_ai_usage(
            reservation_key=reservation_key,
            event_type="released",
            occurred_at=occurred_at,
            commit=commit,
        )

    def consume_ai_usage(
        self,
        *,
        reservation_key: str,
        occurred_at: datetime,
        commit: bool,
    ) -> RewardUsageReservationRecord:
        return self._transition_ai_usage(
            reservation_key=reservation_key,
            event_type="consumed",
            occurred_at=occurred_at,
            commit=commit,
        )

    def bind_ai_usage_job(
        self,
        *,
        reservation_key: str,
        job_id: UUID,
        occurred_at: datetime,
        commit: bool,
    ) -> RewardUsageReservationRecord:
        reservation = self.find_ai_reservation(
            reservation_key=reservation_key,
            for_update=True,
        )
        if reservation is None:
            raise RewardEntitlementContractError("reward reservation does not exist")
        if reservation.job_id is not None:
            if reservation.job_id != job_id:
                raise RewardEntitlementContractError(
                    "reward reservation already belongs to another job"
                )
            if commit:
                self._session.commit()
            return reservation
        self._session.execute(
            text(
                """
                insert into commerce.entitlement_audit_logs (
                  audit_log_id, user_id, action, feature, allowed, metadata, created_at
                ) values (
                  cast(:audit_log_id as uuid), cast(:user_id as uuid),
                  'community_reward_ai_job_bound', :feature, true,
                  cast(:metadata as jsonb), :created_at
                )
                """
            ),
            {
                "audit_log_id": str(uuid4()),
                "user_id": reservation.user_id,
                "feature": reservation.feature,
                "metadata": _json(
                    {
                        "jobId": str(job_id),
                        "reservationId": str(reservation.reservation_id),
                        "reservationKey": reservation.reservation_key,
                    }
                ),
                "created_at": occurred_at,
            },
        )
        if commit:
            self._session.commit()
        bound = self.find_ai_reservation(
            reservation_key=reservation_key,
            for_update=False,
        )
        if bound is None:
            raise RewardEntitlementContractError("reward reservation disappeared")
        return bound

    def activate_membership_award(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        activation_key: str,
        membership_entitlement_ref: str,
        purchase_history_snapshot_ref: str,
        evidence_refs: tuple[str, ...],
        activated_at: datetime,
        commit: bool,
    ) -> RewardMembershipActivationRecord:
        self._advisory_lock(f"community-reward:membership:{activation_key}")
        existing = self._membership_activation_by_key(activation_key)
        if existing is not None:
            if (
                existing.award_id != award_id
                or existing.beneficiary_user_id != beneficiary_user_id
            ):
                raise RewardEntitlementContractError(
                    "activation key belongs to another reward lot"
                )
            if commit:
                self._session.commit()
            return existing

        lot = (
            self._session.execute(
                text(
                    """
                    select award.*, scope.scope_resource_id, scope.cost_metadata
                    from commerce.community_reward_awards award
                    join commerce.community_reward_policy_scopes scope
                      on scope.policy_id = award.policy_id
                     and scope.reward_unit = award.reward_unit
                     and scope.scope_kind = 'membership_activation'
                    where award.award_id = cast(:award_id as uuid)
                      and award.beneficiary_user_id = cast(:user_id as uuid)
                      and award.reward_unit = 'membership_seconds'
                    for update of award
                    """
                ),
                {"award_id": str(award_id), "user_id": str(beneficiary_user_id)},
            )
            .mappings()
            .one_or_none()
        )
        if lot is None:
            raise RewardEntitlementContractError(
                "membership reward lot or published scope does not exist"
            )
        activation_seconds = int(lot["granted_value"])
        if int(lot["consumed_value"]) or int(lot["reversed_value"]):
            raise RewardEntitlementContractError(
                "membership activation requires a complete unused reward lot"
            )

        purchase_end = self._session.execute(
            text(
                """
                select max(subscription_end)
                from commerce.user_entitlements
                where user_id = cast(:user_id as uuid)
                  and source = 'app_store'
                """
            ),
            {"user_id": str(beneficiary_user_id)},
        ).scalar_one_or_none()
        latest_reward_end = self._session.execute(
            text(
                """
                select max(activated_until)
                from commerce.community_reward_membership_activations
                where beneficiary_user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": str(beneficiary_user_id)},
        ).scalar_one_or_none()
        activated_from = max(
            value
            for value in (activated_at, purchase_end, latest_reward_end)
            if value is not None
        )
        activated_until = activated_from + timedelta(seconds=activation_seconds)
        ledger_entry_id = uuid4()
        self._session.execute(
            text(
                """
                insert into commerce.community_reward_ledger_entries (
                  ledger_entry_id, award_id, beneficiary_user_id, entry_type,
                  reward_unit, value_delta, idempotency_key, reason_code,
                  balance_before, balance_after, evidence_refs, created_at
                ) values (
                  cast(:ledger_entry_id as uuid), cast(:award_id as uuid),
                  cast(:user_id as uuid), 'consume', 'membership_seconds',
                  -:value, :idempotency_key, 'membership_reward_activation',
                  :value, 0, cast(:evidence_refs as jsonb), :created_at
                )
                """
            ),
            {
                "ledger_entry_id": str(ledger_entry_id),
                "award_id": str(award_id),
                "user_id": str(beneficiary_user_id),
                "value": activation_seconds,
                "idempotency_key": f"community-reward:membership:{activation_key}",
                "evidence_refs": _json(evidence_refs),
                "created_at": activated_at,
            },
        )
        self._session.execute(
            text(
                """
                update commerce.community_reward_awards
                set consumed_value = granted_value, status = 'consumed'
                where award_id = cast(:award_id as uuid)
                """
            ),
            {"award_id": str(award_id)},
        )
        activation_id = uuid4()
        self._session.execute(
            text(
                """
                insert into commerce.community_reward_membership_activations (
                  membership_activation_id, award_id, beneficiary_user_id,
                  reward_unit, scope_resource_id, policy_id, scope_kind,
                  ledger_entry_id, activation_seconds, activation_key,
                  membership_entitlement_ref, purchase_history_snapshot_ref,
                  prior_expires_at, activated_from, activated_until,
                  cost_metadata_snapshot, evidence_refs, activated_at
                ) values (
                  cast(:activation_id as uuid), cast(:award_id as uuid),
                  cast(:user_id as uuid), 'membership_seconds',
                  cast(:scope_resource_id as uuid), cast(:policy_id as uuid),
                  'membership_activation', cast(:ledger_entry_id as uuid),
                  :activation_seconds, :activation_key, :membership_entitlement_ref,
                  :purchase_history_snapshot_ref, :prior_expires_at, :activated_from,
                  :activated_until, cast(:cost_metadata as jsonb),
                  cast(:evidence_refs as jsonb), :activated_at
                )
                """
            ),
            {
                "activation_id": str(activation_id),
                "award_id": str(award_id),
                "user_id": str(beneficiary_user_id),
                "scope_resource_id": str(lot["scope_resource_id"]),
                "policy_id": str(lot["policy_id"]),
                "ledger_entry_id": str(ledger_entry_id),
                "activation_seconds": activation_seconds,
                "activation_key": activation_key,
                "membership_entitlement_ref": membership_entitlement_ref,
                "purchase_history_snapshot_ref": purchase_history_snapshot_ref,
                "prior_expires_at": activated_from,
                "activated_from": activated_from,
                "activated_until": activated_until,
                "cost_metadata": _json(dict(lot["cost_metadata"] or {})),
                "evidence_refs": _json(evidence_refs),
                "activated_at": activated_at,
            },
        )
        if commit:
            self._session.commit()
        return RewardMembershipActivationRecord(
            activation_id=activation_id,
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            activated_from=activated_from,
            activated_until=activated_until,
            activation_seconds=activation_seconds,
            activation_key=activation_key,
        )

    def active_membership_period(
        self,
        *,
        user_id: str,
        at: datetime,
    ) -> tuple[datetime, datetime] | None:
        row = (
            self._session.execute(
                text(
                    """
                    select min(activated_from) as starts_at, max(activated_until) as ends_at
                    from commerce.community_reward_membership_activations
                    where beneficiary_user_id = cast(:user_id as uuid)
                      and activated_until > :at
                    """
                ),
                {"user_id": user_id, "at": at},
            )
            .mappings()
            .one()
        )
        if row["ends_at"] is None:
            return None
        return row["starts_at"], row["ends_at"]

    def _transition_ai_usage(
        self,
        *,
        reservation_key: str,
        event_type: Literal["consumed", "released"],
        occurred_at: datetime,
        commit: bool,
    ) -> RewardUsageReservationRecord:
        self._advisory_lock(f"community-reward:ai-key:{reservation_key}")
        reservation = self.find_ai_reservation(
            reservation_key=reservation_key,
            for_update=True,
        )
        if reservation is None:
            raise RewardEntitlementContractError("reward reservation does not exist")
        if reservation.status != "reserved":
            expected_status = "consumed" if event_type == "consumed" else "refunded"
            if reservation.status != expected_status:
                raise RewardEntitlementContractError(
                    "reward reservation is already in the opposite terminal state"
                )
            if commit:
                self._session.commit()
            return reservation

        ledger_entry_id: UUID | None = None
        if event_type == "consumed":
            award = (
                self._session.execute(
                    text(
                        """
                        select * from commerce.community_reward_awards
                        where award_id = cast(:award_id as uuid)
                        for update
                        """
                    ),
                    {"award_id": str(reservation.award_id)},
                )
                .mappings()
                .one()
            )
            balance_before = (
                int(award["granted_value"])
                - int(award["consumed_value"])
                - int(award["reversed_value"])
            )
            if balance_before < reservation.value:
                raise RewardEntitlementContractError(
                    "reserved reward value is no longer available"
                )
            ledger_entry_id = uuid4()
            self._session.execute(
                text(
                    """
                    insert into commerce.community_reward_ledger_entries (
                      ledger_entry_id, award_id, beneficiary_user_id, entry_type,
                      reward_unit, value_delta, idempotency_key, reason_code,
                      balance_before, balance_after, evidence_refs, created_at
                    ) values (
                      cast(:ledger_entry_id as uuid), cast(:award_id as uuid),
                      cast(:user_id as uuid), 'consume', 'ai_quota', -:value,
                      :idempotency_key, 'qualified_ai_feature_execution',
                      :balance_before, :balance_after, cast(:evidence_refs as jsonb),
                      :created_at
                    )
                    """
                ),
                {
                    "ledger_entry_id": str(ledger_entry_id),
                    "award_id": str(reservation.award_id),
                    "user_id": reservation.user_id,
                    "value": reservation.value,
                    "idempotency_key": (
                        f"community-reward:ai-consume:{reservation.reservation_id}"
                    ),
                    "balance_before": balance_before,
                    "balance_after": balance_before - reservation.value,
                    "evidence_refs": _json(
                        (
                            f"community-reward-reservation:{reservation.reservation_id}",
                            f"commerce-business:{reservation.business_key}",
                        )
                    ),
                    "created_at": occurred_at,
                },
            )
            self._session.execute(
                text(
                    """
                    update commerce.community_reward_awards
                    set consumed_value = consumed_value + :value,
                        status = case
                          when consumed_value + reversed_value + :value = granted_value
                            then case when reversed_value > 0 then 'reversed' else 'consumed' end
                          else 'partially_consumed'
                        end
                    where award_id = cast(:award_id as uuid)
                    """
                ),
                {"award_id": str(reservation.award_id), "value": reservation.value},
            )

        self._session.execute(
            text(
                """
                insert into commerce.community_reward_ai_usage_reservation_events (
                  reservation_event_id, reservation_id, award_id, beneficiary_user_id,
                  reserved_value, event_type, ledger_entry_id, reason_code,
                  provider_usage_ref, evidence_refs, occurred_at
                ) values (
                  cast(:event_id as uuid), cast(:reservation_id as uuid),
                  cast(:award_id as uuid), cast(:user_id as uuid), :value,
                  :event_type, cast(:ledger_entry_id as uuid), :reason_code,
                  :provider_usage_ref, cast(:evidence_refs as jsonb), :occurred_at
                )
                """
            ),
            {
                "event_id": str(uuid4()),
                "reservation_id": str(reservation.reservation_id),
                "award_id": str(reservation.award_id),
                "user_id": reservation.user_id,
                "value": reservation.value,
                "event_type": event_type,
                "ledger_entry_id": str(ledger_entry_id) if ledger_entry_id else None,
                "reason_code": (
                    "qualified_ai_feature_execution"
                    if event_type == "consumed"
                    else "ai_execution_released"
                ),
                "provider_usage_ref": (
                    f"reservation:{reservation.reservation_id}"
                    if event_type == "consumed"
                    else None
                ),
                "evidence_refs": _json(
                    (f"commerce-business:{reservation.business_key}",)
                ),
                "occurred_at": occurred_at,
            },
        )
        if commit:
            self._session.commit()
        transitioned = self.find_ai_reservation(
            reservation_key=reservation_key,
            for_update=False,
        )
        if transitioned is None:
            raise RewardEntitlementContractError("reward reservation disappeared")
        return transitioned

    def _membership_activation_by_key(
        self,
        activation_key: str,
    ) -> RewardMembershipActivationRecord | None:
        row = (
            self._session.execute(
                text(
                    """
                    select *
                    from commerce.community_reward_membership_activations
                    where activation_key = :activation_key
                    """
                ),
                {"activation_key": activation_key},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return RewardMembershipActivationRecord(
            activation_id=UUID(str(row["membership_activation_id"])),
            award_id=UUID(str(row["award_id"])),
            beneficiary_user_id=UUID(str(row["beneficiary_user_id"])),
            activated_from=row["activated_from"],
            activated_until=row["activated_until"],
            activation_seconds=int(row["activation_seconds"]),
            activation_key=str(row["activation_key"]),
        )

    def _advisory_lock(self, key: str) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )


def _ai_reservation_from_row(row: Any) -> RewardUsageReservationRecord:
    terminal_type = str(row["terminal_type"])
    status: Literal["reserved", "consumed", "refunded"]
    if terminal_type == "consumed":
        status = "consumed"
    elif terminal_type == "released":
        status = "refunded"
    else:
        status = "reserved"
    terminal_at = row["terminal_at"]
    return RewardUsageReservationRecord(
        reservation_id=UUID(str(row["reservation_id"])),
        reservation_key=str(row["reservation_key"]),
        user_id=str(row["beneficiary_user_id"]),
        feature=str(row["eligible_feature"]),
        value=int(row["reserved_value"]),
        business_key=_business_key_from_evidence(row["evidence_refs"]),
        status=status,
        job_id=UUID(str(row["job_id"])) if row["job_id"] else None,
        award_id=UUID(str(row["award_id"])),
        reserved_at=row["reserved_at"],
        consumed_at=terminal_at if status == "consumed" else None,
        refunded_at=terminal_at if status == "refunded" else None,
    )


def _business_key_from_evidence(evidence_refs: Any) -> str:
    for value in evidence_refs or []:
        ref = str(value)
        if ref.startswith("commerce-business:"):
            return ref.removeprefix("commerce-business:")
    raise RewardEntitlementContractError(
        "reward reservation is missing its immutable commerce business key"
    )


def _reservation_fingerprint(
    *,
    user_id: str,
    feature: str,
    reservation_key: str,
    business_key: str,
    server_session_ref: str,
) -> str:
    payload = {
        "businessKey": business_key,
        "feature": feature,
        "reservationKey": reservation_key,
        "serverSessionRef": server_session_ref,
        "userId": user_id,
    }
    return sha256(_json(payload).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
