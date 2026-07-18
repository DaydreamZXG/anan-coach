from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.commerce.catalog import PRODUCTS, ProductConfig


class CommerceQuotaExceededError(RuntimeError):
    def __init__(
        self,
        feature: str,
        *,
        used: int | None = None,
        limit: int | None = None,
        usage_date: date | None = None,
        retry_after: datetime | None = None,
    ) -> None:
        super().__init__(feature)
        self.feature = feature
        self.used = used
        self.limit = limit
        self.usage_date = usage_date
        self.retry_after = retry_after


class CommerceQuotaRefundError(RuntimeError):
    pass


class CommerceReservationContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProductRecord:
    product_id: str
    tier: str
    billing_period: str
    title: str
    subtitle: str
    price_band: str
    sort_order: int
    feature_highlights: list[str]


@dataclass(frozen=True)
class EntitlementRecord:
    tier: str
    source: str
    subscription_start: datetime | None
    subscription_end: datetime | None
    product_ids: list[str]

    @property
    def is_active(self) -> bool:
        if self.tier == "free":
            return True
        if self.subscription_end is None:
            return False
        return self.subscription_end > datetime.now(UTC)


@dataclass(frozen=True)
class FeatureUsageReservation:
    reservation_id: UUID
    reservation_key: str
    user_id: str
    feature: str
    used: int
    limit: int
    usage_date: date
    business_key: str
    status: str
    job_id: UUID | None
    reserved_at: datetime
    consumed_at: datetime | None
    refunded_at: datetime | None
    source: str = "catalog"
    award_id: UUID | None = None


class CommerceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def sync_product_catalog(self) -> None:
        for product in PRODUCTS:
            self._upsert_product(product)
        self._session.commit()

    def commit(self) -> None:
        self._session.commit()

    def list_products(self) -> list[ProductRecord]:
        rows = self._session.execute(
            text(
                """
                select
                  product_id,
                  tier,
                  billing_period,
                  title,
                  subtitle,
                  price_band,
                  sort_order,
                  feature_highlights
                from commerce.iap_products
                where is_active = true and platform = 'ios'
                order by sort_order, product_id
                """
            )
        ).mappings().all()
        return [_product_from_row(row) for row in rows]

    def get_product(self, product_id: str) -> ProductRecord | None:
        row = self._session.execute(
            text(
                """
                select
                  product_id,
                  tier,
                  billing_period,
                  title,
                  subtitle,
                  price_band,
                  sort_order,
                  feature_highlights
                from commerce.iap_products
                where product_id = :product_id
                  and is_active = true
                  and platform = 'ios'
                """
            ),
            {"product_id": product_id},
        ).mappings().first()
        if row is None:
            return None
        return _product_from_row(row)

    def get_entitlement(
        self,
        *,
        user_id: str,
        at: datetime | None = None,
    ) -> EntitlementRecord:
        at = at or datetime.now(UTC)
        row = self._session.execute(
            text(
                """
                select
                  tier,
                  source,
                  subscription_start,
                  subscription_end,
                  product_ids
                from commerce.user_entitlements
                where user_id = cast(:user_id as uuid)
                  and tier <> 'free'
                  and subscription_end is not null
                  and subscription_end > :at
                order by subscription_end desc
                limit 1
                """
            ),
            {"user_id": user_id, "at": at},
        ).mappings().first()
        from app.domains.commerce.reward_entitlements import (
            RewardEntitlementRepository,
        )

        reward_period = RewardEntitlementRepository(
            self._session
        ).active_membership_period(user_id=user_id, at=at)
        grant_rows = self._session.execute(
            text(
                """
                select source_type, product_id, starts_at, ends_at
                from commerce.entitlement_grants
                where user_id = cast(:user_id as uuid)
                  and state = 'active'
                  and starts_at <= :at
                  and ends_at > :at
                order by ends_at desc, grant_id
                """
            ),
            {"user_id": user_id, "at": at},
        ).mappings().all()
        if row is None and reward_period is None and not grant_rows:
            return EntitlementRecord(
                tier="free",
                source="free",
                subscription_start=None,
                subscription_end=None,
                product_ids=[],
            )
        purchase_start = row["subscription_start"] if row is not None else None
        purchase_end = row["subscription_end"] if row is not None else None
        reward_start = reward_period[0] if reward_period is not None else None
        reward_end = reward_period[1] if reward_period is not None else None
        grant_starts = [item["starts_at"] for item in grant_rows]
        grant_ends = [item["ends_at"] for item in grant_rows]
        starts = [
            value
            for value in (purchase_start, reward_start, *grant_starts)
            if value is not None
        ]
        ends = [
            value
            for value in (purchase_end, reward_end, *grant_ends)
            if value is not None
        ]
        sources = set()
        if row is not None:
            sources.add(str(row["source"]))
        if reward_period is not None:
            sources.add("reward")
        sources.update(str(item["source_type"]) for item in grant_rows)
        product_ids = set(list(row["product_ids"] or []) if row is not None else [])
        product_ids.update(str(item["product_id"]) for item in grant_rows)
        return EntitlementRecord(
            tier="pro",
            source=next(iter(sources)) if len(sources) == 1 else "merged",
            subscription_start=min(starts),
            subscription_end=max(ends),
            product_ids=sorted(product_ids),
        )

    def unlocked_features(self, *, user_id: str) -> list[str]:
        values = self._session.execute(
            text(
                """
                select reward_type
                from commerce.process_reward_awards
                where user_id = cast(:user_id as uuid)
                  and state = 'granted'
                  and reward_type = 'special_diagnostic'
                """
            ),
            {"user_id": user_id},
        ).scalars()
        return sorted({str(value) for value in values})

    def list_usage_for_periods(
        self,
        *,
        user_id: str,
        usage_dates_by_feature: dict[str, date],
    ) -> dict[str, int]:
        usage: dict[str, int] = {}
        for feature, usage_date in usage_dates_by_feature.items():
            row = self._session.execute(
                text(
                    """
                    select used_count
                    from commerce.entitlement_usage_daily
                    where user_id = cast(:user_id as uuid)
                      and feature = :feature
                      and usage_date = :usage_date
                    """
                ),
                {
                    "user_id": user_id,
                    "feature": feature,
                    "usage_date": usage_date,
                },
            ).mappings().first()
            if row is not None:
                usage[feature] = int(row["used_count"])
        return usage

    def reserve_feature_usage(
        self,
        *,
        user_id: str,
        feature: str,
        reservation_key: str,
        business_key: str,
        usage_date: date,
        limit: int,
        tier: str,
        server_session_ref: str | None = None,
        commit: bool = True,
    ) -> FeatureUsageReservation:
        _validate_reservation_identity(
            reservation_key=reservation_key,
            business_key=business_key,
            feature=feature,
        )
        if limit < 1:
            self.log_entitlement_event(
                user_id=user_id,
                action="quota_denied",
                feature=feature,
                allowed=False,
                metadata={
                    "tier": tier,
                    "limit": limit,
                    "reservationKey": reservation_key,
                    "businessKey": business_key,
                },
                commit=commit,
            )
            raise CommerceQuotaExceededError(
                feature,
                used=0,
                limit=limit,
                usage_date=usage_date,
            )

        # A transaction-scoped advisory lock prevents two concurrent deliveries
        # of the same business operation from incrementing the aggregate counter
        # before either can publish its unique reservation row.
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": reservation_key},
        )
        existing = self._reservation_by_key(
            reservation_key=reservation_key,
            for_update=True,
        )
        if existing is not None:
            if (
                existing.user_id != user_id
                or existing.feature != feature
                or existing.business_key != business_key
            ):
                raise CommerceReservationContractError(
                    "Reservation key already belongs to different immutable input"
                )
            if commit:
                self._session.commit()
            return existing
        reward_existing = self._reward_reservation_by_key(
            reservation_key=reservation_key,
            for_update=True,
        )
        if reward_existing is not None:
            validated = self.reserve_reward_feature_usage(
                user_id=user_id,
                feature=feature,
                reservation_key=reservation_key,
                business_key=business_key,
                server_session_ref=(
                    server_session_ref or f"commerce-session:{business_key}"
                ),
                reserved_at=datetime.now(UTC),
                commit=commit,
            )
            if validated is None:
                raise CommerceReservationContractError(
                    "Reward reservation disappeared during replay"
                )
            return validated

        row = self._session.execute(
            text(
                """
                insert into commerce.entitlement_usage_daily (
                  user_id,
                  feature,
                  usage_date,
                  used_count,
                  limit_count,
                  updated_at
                ) values (
                  cast(:user_id as uuid),
                  :feature,
                  :usage_date,
                  1,
                  :limit_count,
                  now()
                )
                on conflict (user_id, feature, usage_date) do update set
                  used_count = commerce.entitlement_usage_daily.used_count + 1,
                  limit_count = excluded.limit_count,
                  updated_at = now()
                where commerce.entitlement_usage_daily.used_count
                  < excluded.limit_count
                returning used_count, limit_count
                """
            ),
            {
                "user_id": user_id,
                "feature": feature,
                "usage_date": usage_date,
                "limit_count": limit,
            },
        ).mappings().first()
        if row is None:
            current_used = int(
                self._session.execute(
                    text(
                        """
                        select used_count
                          from commerce.entitlement_usage_daily
                         where user_id = cast(:user_id as uuid)
                           and feature = :feature
                           and usage_date = :usage_date
                        """
                    ),
                    {
                        "user_id": user_id,
                        "feature": feature,
                        "usage_date": usage_date,
                    },
                ).scalar_one_or_none()
                or 0
            )
            self.log_entitlement_event(
                user_id=user_id,
                action="quota_denied",
                feature=feature,
                allowed=False,
                metadata={
                    "tier": tier,
                    "limit": limit,
                    "used": current_used,
                    "reservationKey": reservation_key,
                    "businessKey": business_key,
                },
                commit=commit,
            )
            raise CommerceQuotaExceededError(
                feature,
                used=current_used,
                limit=limit,
                usage_date=usage_date,
            )

        reservation_id = uuid4()
        reservation_row = self._session.execute(
            text(
                """
                insert into commerce.feature_usage_reservations (
                  reservation_id,
                  reservation_key,
                  user_id,
                  feature,
                  usage_date,
                  limit_count,
                  usage_count_at_reservation,
                  status,
                  business_key,
                  job_id,
                  reserved_at,
                  consumed_at,
                  refunded_at,
                  updated_at
                ) values (
                  cast(:reservation_id as uuid),
                  :reservation_key,
                  cast(:user_id as uuid),
                  :feature,
                  :usage_date,
                  :limit_count,
                  :used_count,
                  'reserved',
                  :business_key,
                  null,
                  now(),
                  null,
                  null,
                  now()
                )
                returning *
                """
            ),
            {
                "reservation_id": str(reservation_id),
                "reservation_key": reservation_key,
                "user_id": user_id,
                "feature": feature,
                "usage_date": usage_date,
                "limit_count": int(row["limit_count"]),
                "used_count": int(row["used_count"]),
                "business_key": business_key,
            },
        ).mappings().one()
        self.log_entitlement_event(
            user_id=user_id,
            action="quota_reserved",
            feature=feature,
            allowed=True,
            metadata={
                "tier": tier,
                "limit": limit,
                "used": int(row["used_count"]),
                "reservationId": str(reservation_id),
                "reservationKey": reservation_key,
                "businessKey": business_key,
            },
            commit=False,
        )
        if commit:
            self._session.commit()
        return _feature_usage_reservation_from_row(
            reservation_row,
            current_used_count=int(row["used_count"]),
        )

    def reserve_reward_feature_usage(
        self,
        *,
        user_id: str,
        feature: str,
        reservation_key: str,
        business_key: str,
        server_session_ref: str,
        reserved_at: datetime,
        commit: bool = True,
    ) -> FeatureUsageReservation | None:
        base_existing = self._reservation_by_key(
            reservation_key=reservation_key,
            for_update=True,
        )
        if base_existing is not None:
            if (
                base_existing.user_id != user_id
                or base_existing.feature != feature
                or base_existing.business_key != business_key
            ):
                raise CommerceReservationContractError(
                    "Reservation key already belongs to different immutable input"
                )
            if commit:
                self._session.commit()
            return base_existing
        from app.domains.commerce.reward_entitlements import (
            RewardEntitlementRepository,
        )

        record = RewardEntitlementRepository(self._session).reserve_ai_usage(
            user_id=user_id,
            feature=feature,
            reservation_key=reservation_key,
            business_key=business_key,
            server_session_ref=server_session_ref,
            reserved_at=reserved_at,
            commit=commit,
        )
        return _reward_feature_usage_reservation(record) if record is not None else None

    def refund_feature_usage(
        self,
        *,
        reservation_key: str,
        commit: bool = True,
    ) -> FeatureUsageReservation:
        reservation = self._reservation_by_key(
            reservation_key=reservation_key,
            for_update=True,
        )
        if reservation is None:
            from app.domains.commerce.reward_entitlements import (
                RewardEntitlementContractError,
                RewardEntitlementRepository,
            )

            try:
                released = RewardEntitlementRepository(self._session).release_ai_usage(
                    reservation_key=reservation_key,
                    occurred_at=datetime.now(UTC),
                    commit=commit,
                )
            except RewardEntitlementContractError as exc:
                raise CommerceQuotaRefundError(str(exc)) from exc
            return _reward_feature_usage_reservation(released)
        if reservation.status == "refunded":
            if commit:
                self._session.commit()
            return reservation
        if reservation.status == "consumed":
            raise CommerceQuotaRefundError(
                "Consumed feature usage cannot be refunded"
            )

        row = self._session.execute(
            text(
                """
                update commerce.entitlement_usage_daily
                set
                  used_count = greatest(used_count - 1, 0),
                  updated_at = now()
                where user_id = cast(:user_id as uuid)
                  and feature = :feature
                  and usage_date = :usage_date
                  and used_count > 0
                returning used_count, limit_count
                """
            ),
            {
                "user_id": reservation.user_id,
                "feature": reservation.feature,
                "usage_date": reservation.usage_date,
            },
        ).mappings().first()
        if row is None:
            raise CommerceQuotaRefundError(
                "Reserved feature usage has no matching aggregate count"
            )
        transitioned = self._session.execute(
            text(
                """
                update commerce.feature_usage_reservations
                   set status = 'refunded',
                       refunded_at = now(),
                       updated_at = now()
                 where reservation_id = cast(:reservation_id as uuid)
                   and status = 'reserved'
                returning *
                """
            ),
            {"reservation_id": str(reservation.reservation_id)},
        ).mappings().one_or_none()
        if transitioned is None:
            raise CommerceQuotaRefundError(
                "Feature usage reservation changed during refund"
            )
        self.log_entitlement_event(
            user_id=reservation.user_id,
            action="quota_refunded",
            feature=reservation.feature,
            allowed=True,
            metadata={
                "used": int(row["used_count"]),
                "limit": int(row["limit_count"]),
                "reservationId": str(reservation.reservation_id),
                "reservationKey": reservation.reservation_key,
            },
            commit=False,
        )
        if commit:
            self._session.commit()
        return _feature_usage_reservation_from_row(
            transitioned,
            current_used_count=int(row["used_count"]),
        )

    def consume_feature_usage(
        self,
        *,
        reservation_key: str,
        commit: bool = True,
    ) -> FeatureUsageReservation:
        reservation = self._reservation_by_key(
            reservation_key=reservation_key,
            for_update=True,
        )
        if reservation is None:
            from app.domains.commerce.reward_entitlements import (
                RewardEntitlementContractError,
                RewardEntitlementRepository,
            )

            try:
                consumed = RewardEntitlementRepository(self._session).consume_ai_usage(
                    reservation_key=reservation_key,
                    occurred_at=datetime.now(UTC),
                    commit=commit,
                )
            except RewardEntitlementContractError as exc:
                raise CommerceReservationContractError(str(exc)) from exc
            return _reward_feature_usage_reservation(consumed)
        if reservation.status == "consumed":
            if commit:
                self._session.commit()
            return reservation
        if reservation.status == "refunded":
            raise CommerceReservationContractError(
                "Refunded feature usage cannot be consumed"
            )
        transitioned = self._session.execute(
            text(
                """
                update commerce.feature_usage_reservations
                   set status = 'consumed',
                       consumed_at = now(),
                       updated_at = now()
                 where reservation_id = cast(:reservation_id as uuid)
                   and status = 'reserved'
                returning *
                """
            ),
            {"reservation_id": str(reservation.reservation_id)},
        ).mappings().one_or_none()
        if transitioned is None:
            raise CommerceReservationContractError(
                "Feature usage reservation changed during consumption"
            )
        self.log_entitlement_event(
            user_id=reservation.user_id,
            action="quota_consumed",
            feature=reservation.feature,
            allowed=True,
            metadata={
                "used": reservation.used,
                "limit": reservation.limit,
                "reservationId": str(reservation.reservation_id),
                "reservationKey": reservation.reservation_key,
            },
            commit=False,
        )
        if commit:
            self._session.commit()
        return _feature_usage_reservation_from_row(
            transitioned,
            current_used_count=reservation.used,
        )

    def bind_feature_usage_reservation_job(
        self,
        *,
        reservation_key: str,
        job_id: UUID,
        commit: bool = True,
    ) -> FeatureUsageReservation:
        reservation = self._reservation_by_key(
            reservation_key=reservation_key,
            for_update=True,
        )
        if reservation is None:
            from app.domains.commerce.reward_entitlements import (
                RewardEntitlementContractError,
                RewardEntitlementRepository,
            )

            try:
                bound = RewardEntitlementRepository(self._session).bind_ai_usage_job(
                    reservation_key=reservation_key,
                    job_id=job_id,
                    occurred_at=datetime.now(UTC),
                    commit=commit,
                )
            except RewardEntitlementContractError as exc:
                raise CommerceReservationContractError(str(exc)) from exc
            return _reward_feature_usage_reservation(bound)
        if reservation.job_id is not None:
            if reservation.job_id != job_id:
                raise CommerceReservationContractError(
                    "Feature usage reservation already belongs to another job"
                )
            if commit:
                self._session.commit()
            return reservation
        row = self._session.execute(
            text(
                """
                update commerce.feature_usage_reservations
                   set job_id = cast(:job_id as uuid), updated_at = now()
                 where reservation_id = cast(:reservation_id as uuid)
                   and job_id is null
                returning *
                """
            ),
            {
                "reservation_id": str(reservation.reservation_id),
                "job_id": str(job_id),
            },
        ).mappings().one_or_none()
        if row is None:
            raise CommerceReservationContractError(
                "Feature usage reservation changed during job binding"
            )
        if commit:
            self._session.commit()
        return _feature_usage_reservation_from_row(
            row,
            current_used_count=reservation.used,
        )

    def _reservation_by_key(
        self,
        *,
        reservation_key: str,
        for_update: bool,
    ) -> FeatureUsageReservation | None:
        lock_clause = " for update of r" if for_update else ""
        row = self._session.execute(
            text(
                """
                select r.*, u.used_count as current_used_count
                  from commerce.feature_usage_reservations r
                  join commerce.entitlement_usage_daily u
                    on u.user_id = r.user_id
                   and u.feature = r.feature
                   and u.usage_date = r.usage_date
                 where r.reservation_key = :reservation_key
                """
                + lock_clause
            ),
            {"reservation_key": reservation_key},
        ).mappings().one_or_none()
        if row is None:
            return None
        return _feature_usage_reservation_from_row(
            row,
            current_used_count=int(row["current_used_count"]),
        )

    def feature_usage_reservation_by_key(
        self, *, reservation_key: str, for_update: bool = False
    ) -> FeatureUsageReservation | None:
        """Resolve either catalog or reward quota reservation by stable key."""
        return self._reservation_by_key(
            reservation_key=reservation_key, for_update=for_update
        ) or self._reward_reservation_by_key(
            reservation_key=reservation_key, for_update=for_update
        )

    def _reward_reservation_by_key(
        self,
        *,
        reservation_key: str,
        for_update: bool,
    ) -> FeatureUsageReservation | None:
        from app.domains.commerce.reward_entitlements import (
            RewardEntitlementRepository,
        )

        record = RewardEntitlementRepository(self._session).find_ai_reservation(
            reservation_key=reservation_key,
            for_update=for_update,
        )
        return _reward_feature_usage_reservation(record) if record is not None else None

    def activate_reward_membership(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        activation_key: str,
        membership_entitlement_ref: str,
        purchase_history_snapshot_ref: str,
        evidence_refs: tuple[str, ...],
        activated_at: datetime,
        commit: bool = True,
    ):
        from app.domains.commerce.reward_entitlements import (
            RewardEntitlementRepository,
        )

        return RewardEntitlementRepository(self._session).activate_membership_award(
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            activation_key=activation_key,
            membership_entitlement_ref=membership_entitlement_ref,
            purchase_history_snapshot_ref=purchase_history_snapshot_ref,
            evidence_refs=evidence_refs,
            activated_at=activated_at,
            commit=commit,
        )

    def record_verified_transaction(
        self,
        *,
        user_id: str,
        product_id: str,
        original_transaction_id: str,
        transaction_id: str,
        web_order_line_item_id: str | None,
        environment: str,
        purchase_date: datetime,
        expires_date: datetime | None,
        revocation_date: datetime | None,
        signed_transaction_info: str,
        verification_status: str,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.app_store_transactions (
                  transaction_row_id,
                  user_id,
                  product_id,
                  original_transaction_id,
                  transaction_id,
                  web_order_line_item_id,
                  environment,
                  purchase_date,
                  expires_date,
                  revocation_date,
                  signed_transaction_info,
                  verification_status,
                  created_at
                ) values (
                  cast(:transaction_row_id as uuid),
                  cast(:user_id as uuid),
                  :product_id,
                  :original_transaction_id,
                  :transaction_id,
                  :web_order_line_item_id,
                  :environment,
                  :purchase_date,
                  :expires_date,
                  :revocation_date,
                  :signed_transaction_info,
                  :verification_status,
                  now()
                )
                on conflict (transaction_id) do update set
                  user_id = excluded.user_id,
                  product_id = excluded.product_id,
                  original_transaction_id = excluded.original_transaction_id,
                  web_order_line_item_id = excluded.web_order_line_item_id,
                  environment = excluded.environment,
                  purchase_date = excluded.purchase_date,
                  expires_date = excluded.expires_date,
                  revocation_date = excluded.revocation_date,
                  signed_transaction_info = excluded.signed_transaction_info,
                  verification_status = excluded.verification_status
                """
            ),
            {
                "transaction_row_id": str(uuid4()),
                "user_id": user_id,
                "product_id": product_id,
                "original_transaction_id": original_transaction_id,
                "transaction_id": transaction_id,
                "web_order_line_item_id": web_order_line_item_id,
                "environment": environment,
                "purchase_date": purchase_date,
                "expires_date": expires_date,
                "revocation_date": revocation_date,
                "signed_transaction_info": signed_transaction_info,
                "verification_status": verification_status,
            },
        )

    def reconcile_pro_entitlement(self, *, user_id: str) -> None:
        active_rows = self._session.execute(
            text(
                """
                select product_id, purchase_date, expires_date
                from commerce.app_store_transactions active_transaction
                where user_id = cast(:user_id as uuid)
                  and verification_status = 'verified_active'
                  and revocation_date is null
                  and expires_date is not null
                  and expires_date > now()
                  and not exists (
                    select 1
                    from commerce.app_store_transactions revoked_transaction
                    where revoked_transaction.user_id = active_transaction.user_id
                      and revoked_transaction.verification_status = 'verified_revoked'
                      and (
                        revoked_transaction.transaction_id = active_transaction.transaction_id
                        or (
                          revoked_transaction.original_transaction_id =
                            active_transaction.original_transaction_id
                          and revoked_transaction.product_id = active_transaction.product_id
                          and revoked_transaction.expires_date = active_transaction.expires_date
                        )
                      )
                  )
                """
            ),
            {"user_id": user_id},
        ).mappings().all()
        if active_rows:
            self._set_pro_entitlement(
                user_id=user_id,
                subscription_start=min(row["purchase_date"] for row in active_rows),
                subscription_end=max(row["expires_date"] for row in active_rows),
                product_ids=sorted({str(row["product_id"]) for row in active_rows}),
            )
            return

        ended_at = self._session.execute(
            text(
                """
                select max(coalesce(revocation_date, expires_date, purchase_date))
                from commerce.app_store_transactions
                where user_id = cast(:user_id as uuid)
                  and verification_status in ('verified_expired', 'verified_revoked')
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        self._expire_user_entitlement(
            user_id=user_id,
            ended_at=ended_at or datetime.now(UTC),
        )

    def _set_pro_entitlement(
        self,
        *,
        user_id: str,
        subscription_start: datetime,
        subscription_end: datetime,
        product_ids: list[str],
    ) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.user_entitlements (
                  entitlement_id,
                  user_id,
                  tier,
                  source,
                  subscription_start,
                  subscription_end,
                  product_ids,
                  features,
                  created_at,
                  updated_at
                ) values (
                  cast(:entitlement_id as uuid),
                  cast(:user_id as uuid),
                  'pro',
                  'app_store',
                  :subscription_start,
                  :subscription_end,
                  cast(:product_ids as jsonb),
                  cast(:features as jsonb),
                  now(),
                  now()
                )
                on conflict (user_id) do update set
                  tier = 'pro',
                  source = 'app_store',
                  subscription_start = excluded.subscription_start,
                  subscription_end = excluded.subscription_end,
                  product_ids = excluded.product_ids,
                  features = excluded.features,
                  updated_at = now()
                """
            ),
            {
                "entitlement_id": str(uuid4()),
                "user_id": user_id,
                "subscription_start": subscription_start,
                "subscription_end": subscription_end,
                "product_ids": json.dumps(product_ids),
                "features": json.dumps({"tier": "pro"}, ensure_ascii=False),
            },
        )

    def log_entitlement_event(
        self,
        *,
        user_id: str,
        action: str,
        feature: str | None,
        allowed: bool | None,
        metadata: dict[str, Any],
        commit: bool,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.entitlement_audit_logs (
                  audit_log_id,
                  user_id,
                  action,
                  feature,
                  allowed,
                  metadata,
                  created_at
                ) values (
                  cast(:audit_log_id as uuid),
                  cast(:user_id as uuid),
                  :action,
                  :feature,
                  :allowed,
                  cast(:metadata as jsonb),
                  now()
                )
                """
            ),
            {
                "audit_log_id": str(uuid4()),
                "user_id": user_id,
                "action": action,
                "feature": feature,
                "allowed": allowed,
                "metadata": json.dumps(metadata, ensure_ascii=False),
            },
        )
        if commit:
            self._session.commit()

    def record_notification_event(
        self,
        *,
        notification_uuid: str,
        notification_type: str,
        subtype: str | None,
        signed_payload: str,
    ) -> bool:
        row = self._session.execute(
            text(
                """
                insert into commerce.app_store_notification_events (
                  event_id,
                  notification_uuid,
                  notification_type,
                  subtype,
                  signed_payload,
                  processed_at,
                  created_at
                ) values (
                  cast(:event_id as uuid),
                  :notification_uuid,
                  :notification_type,
                  :subtype,
                  :signed_payload,
                  null,
                  now()
                )
                on conflict (notification_uuid) do nothing
                returning notification_uuid
                """
            ),
            {
                "event_id": str(uuid4()),
                "notification_uuid": notification_uuid,
                "notification_type": notification_type,
                "subtype": subtype,
                "signed_payload": signed_payload,
            },
        ).mappings().first()
        return row is not None

    def mark_notification_event_processed(self, *, notification_uuid: str) -> None:
        self._session.execute(
            text(
                """
                update commerce.app_store_notification_events
                set processed_at = now()
                where notification_uuid = :notification_uuid
                  and processed_at is null
                """
            ),
            {"notification_uuid": notification_uuid},
        )

    def _expire_user_entitlement(
        self,
        *,
        user_id: str,
        ended_at: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                update commerce.user_entitlements
                set
                  subscription_end = least(
                    coalesce(subscription_end, :ended_at),
                    :ended_at
                  ),
                  updated_at = now()
                where user_id = cast(:user_id as uuid)
                  and tier = 'pro'
                """
            ),
            {
                "user_id": user_id,
                "ended_at": ended_at,
            },
        )

    def _upsert_product(self, product: ProductConfig) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.iap_products (
                  product_row_id,
                  platform,
                  product_id,
                  tier,
                  billing_period,
                  title,
                  subtitle,
                  price_band,
                  sort_order,
                  feature_highlights,
                  is_active,
                  created_at,
                  updated_at
                ) values (
                  cast(:product_row_id as uuid),
                  'ios',
                  :product_id,
                  :tier,
                  :billing_period,
                  :title,
                  :subtitle,
                  :price_band,
                  :sort_order,
                  cast(:feature_highlights as jsonb),
                  true,
                  now(),
                  now()
                )
                on conflict (product_id) do update set
                  tier = excluded.tier,
                  billing_period = excluded.billing_period,
                  title = excluded.title,
                  subtitle = excluded.subtitle,
                  price_band = excluded.price_band,
                  sort_order = excluded.sort_order,
                  feature_highlights = excluded.feature_highlights,
                  is_active = true,
                  updated_at = now()
                where commerce.iap_products.tier is distinct from excluded.tier
                  or commerce.iap_products.billing_period is distinct from excluded.billing_period
                  or commerce.iap_products.title is distinct from excluded.title
                  or commerce.iap_products.subtitle is distinct from excluded.subtitle
                  or commerce.iap_products.price_band is distinct from excluded.price_band
                  or commerce.iap_products.sort_order is distinct from excluded.sort_order
                  or commerce.iap_products.feature_highlights is distinct from excluded.feature_highlights
                  or commerce.iap_products.is_active is distinct from true
                """
            ),
            {
                "product_row_id": str(uuid4()),
                "product_id": product.product_id,
                "tier": product.tier,
                "billing_period": product.billing_period,
                "title": product.title,
                "subtitle": product.subtitle,
                "price_band": product.price_band,
                "sort_order": product.sort_order,
                "feature_highlights": json.dumps(
                    list(product.feature_highlights),
                    ensure_ascii=False,
                ),
            },
        )


def _product_from_row(row: Any) -> ProductRecord:
    highlights = row["feature_highlights"]
    if not isinstance(highlights, list):
        highlights = []
    return ProductRecord(
        product_id=str(row["product_id"]),
        tier=str(row["tier"]),
        billing_period=str(row["billing_period"]),
        title=str(row["title"]),
        subtitle=str(row["subtitle"]),
        price_band=str(row["price_band"]),
        sort_order=int(row["sort_order"]),
        feature_highlights=[str(item) for item in highlights],
    )


def _validate_reservation_identity(
    *,
    reservation_key: str,
    business_key: str,
    feature: str,
) -> None:
    if not 1 <= len(reservation_key) <= 300:
        raise CommerceReservationContractError(
            "reservation_key must contain between 1 and 300 characters"
        )
    if not 1 <= len(business_key) <= 300:
        raise CommerceReservationContractError(
            "business_key must contain between 1 and 300 characters"
        )
    if not 1 <= len(feature) <= 120:
        raise CommerceReservationContractError(
            "feature must contain between 1 and 120 characters"
        )


def _feature_usage_reservation_from_row(
    row: Any,
    *,
    current_used_count: int,
) -> FeatureUsageReservation:
    return FeatureUsageReservation(
        reservation_id=UUID(str(row["reservation_id"])),
        reservation_key=str(row["reservation_key"]),
        user_id=str(row["user_id"]),
        feature=str(row["feature"]),
        used=current_used_count,
        limit=int(row["limit_count"]),
        usage_date=row["usage_date"],
        business_key=str(row["business_key"]),
        status=str(row["status"]),
        job_id=UUID(str(row["job_id"])) if row["job_id"] is not None else None,
        reserved_at=row["reserved_at"],
        consumed_at=row["consumed_at"],
        refunded_at=row["refunded_at"],
    )


def _reward_feature_usage_reservation(record: Any) -> FeatureUsageReservation:
    return FeatureUsageReservation(
        reservation_id=record.reservation_id,
        reservation_key=record.reservation_key,
        user_id=record.user_id,
        feature=record.feature,
        used=record.value,
        limit=record.value,
        usage_date=record.reserved_at.date(),
        business_key=record.business_key,
        status=record.status,
        job_id=record.job_id,
        reserved_at=record.reserved_at,
        consumed_at=record.consumed_at,
        refunded_at=record.refunded_at,
        source="community_reward",
        award_id=record.award_id,
    )
