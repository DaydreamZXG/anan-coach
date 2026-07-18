from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class Stage8CommerceConflict(RuntimeError):
    pass


class Stage8CommerceNotFound(LookupError):
    pass


@dataclass(frozen=True)
class OfferRecord:
    offer_version_id: UUID
    offer_code: str
    version_no: int
    product_id: str
    title: str
    subtitle: str
    feature_highlights: list[str]
    channel: str
    currency: str
    amount_minor: int
    service_days: int
    cohort_limit: int | None
    service_terms_version: str
    refund_policy_version: str
    reward_policy_version: str
    marketing_policy_version: str
    cohort_paid_count: int
    cohort_reserved_count: int
    cohort_remaining: int | None
    refund_rate: float


@dataclass(frozen=True)
class PolicyRecord:
    policy_version: str
    policy_kind: str
    title: str
    content: dict[str, object]
    content_sha256: str


@dataclass(frozen=True)
class OrderRecord:
    order_id: UUID
    user_id: UUID
    command_key: str
    request_fingerprint: str
    offer_version_id: UUID
    offer_title: str
    product_id: str
    out_trade_no: str
    payer_open_id: str
    currency: str
    amount_minor: int
    refunded_minor: int
    state: str
    terms_snapshot: dict[str, object]
    terms_snapshot_sha256: str
    payment_channel: str
    client_platform: str | None
    virtual_payload_sha256: str | None
    prepay_id: str | None
    provider_transaction_id: str | None
    provider_order_type: int | None
    provider_wx_order_id: str | None
    provider_channel_order_id: str | None
    provider_delivery_state: str | None
    expires_at: datetime
    paid_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class RefundRecord:
    refund_id: UUID
    order_id: UUID
    user_id: UUID
    command_key: str
    request_fingerprint: str
    out_refund_no: str
    refund_kind: str
    amount_minor: int
    currency: str
    policy_version: str
    state: str
    provider_refund_id: str | None
    requested_at: datetime
    processed_at: datetime | None


@dataclass(frozen=True)
class RewardFacts:
    effective_days: int
    weekly_reflections: int
    core_tasks_completed: int
    core_tasks_due: int
    awarded_rewards: list[str]
    pending_rewards: list[str]


@dataclass(frozen=True)
class RetakeRecord:
    program_version_id: UUID
    source_score_evidence_id: UUID
    source_exam_year: int
    target_exam_year: int
    state: str
    loss_structure: dict[str, object]
    created_at: datetime


class Stage8CommerceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def list_active_offers(self, *, now: datetime) -> list[OfferRecord]:
        rows = self._session.execute(
            text(
                """
                select offer.*, product.title, product.subtitle,
                       product.feature_highlights,
                       coalesce(slot.paid_count, 0)::int as paid_count,
                       coalesce(slot.reserved_count, 0)::int as reserved_count,
                       coalesce(refund.refund_count, 0)::int as refund_count
                from commerce.product_offer_versions offer
                join commerce.iap_products product on product.product_id = offer.product_id
                left join lateral (
                  select
                    count(*) filter (where state = 'consumed') as paid_count,
                    count(*) filter (where state = 'reserved') as reserved_count
                  from commerce.founder_cohort_slots slots
                  where slots.cohort_key = offer.offer_code
                ) slot on true
                left join lateral (
                  select count(*) as refund_count
                  from commerce.wechat_refunds refund
                  join commerce.wechat_orders orders on orders.order_id = refund.order_id
                  join commerce.product_offer_versions ordered_offer
                    on ordered_offer.offer_version_id = orders.offer_version_id
                  where ordered_offer.offer_code = offer.offer_code
                    and refund.refund_kind = 'not_suitable'
                    and refund.state = 'succeeded'
                ) refund on true
                where offer.state = 'active'
                  and offer.effective_from <= :now
                  and (offer.effective_until is null or offer.effective_until > :now)
                order by offer.offer_code, offer.version_no desc
                """
            ),
            {"now": now},
        ).mappings().all()
        return [_offer(row) for row in rows]

    def offer_by_id(self, offer_version_id: UUID, *, now: datetime) -> OfferRecord:
        row = self._session.execute(
            text(
                """
                select offer.*, product.title, product.subtitle,
                       product.feature_highlights,
                       coalesce(slot.paid_count, 0)::int as paid_count,
                       coalesce(slot.reserved_count, 0)::int as reserved_count,
                       coalesce(refund.refund_count, 0)::int as refund_count
                from commerce.product_offer_versions offer
                join commerce.iap_products product on product.product_id = offer.product_id
                left join lateral (
                  select count(*) filter (where state = 'consumed') as paid_count,
                         count(*) filter (where state = 'reserved') as reserved_count
                  from commerce.founder_cohort_slots slots
                  where slots.cohort_key = offer.offer_code
                ) slot on true
                left join lateral (
                  select count(*) as refund_count
                  from commerce.wechat_refunds refund
                  join commerce.wechat_orders orders on orders.order_id = refund.order_id
                  join commerce.product_offer_versions ordered_offer
                    on ordered_offer.offer_version_id = orders.offer_version_id
                  where ordered_offer.offer_code = offer.offer_code
                    and refund.refund_kind = 'not_suitable'
                    and refund.state = 'succeeded'
                ) refund on true
                where offer.offer_version_id = cast(:offer_version_id as uuid)
                  and offer.state = 'active'
                  and offer.effective_from <= :now
                  and (offer.effective_until is null or offer.effective_until > :now)
                """
            ),
            {"offer_version_id": str(offer_version_id), "now": now},
        ).mappings().one_or_none()
        if row is None:
            raise Stage8CommerceNotFound("offer")
        return _offer(row)

    def policies(self, versions: tuple[str, ...]) -> list[PolicyRecord]:
        rows = self._session.execute(
            text(
                """
                select * from commerce.commercial_policy_versions
                where policy_version = any(cast(:versions as text[]))
                order by policy_kind
                """
            ),
            {"versions": list(versions)},
        ).mappings().all()
        result = [_policy(row) for row in rows]
        if {item.policy_version for item in result} != set(versions):
            raise Stage8CommerceConflict("commercial policy snapshot is incomplete")
        return result

    def wechat_open_id(self, *, user_id: str, app_id: str) -> str:
        value = self._session.execute(
            text(
                """
                select open_id from learner.wechat_identities
                where user_id = cast(:user_id as uuid) and app_id = :app_id
                order by last_login_at desc limit 1
                """
            ),
            {"user_id": user_id, "app_id": app_id},
        ).scalar_one_or_none()
        if not value:
            raise Stage8CommerceConflict("authenticated account has no WeChat payer identity")
        return str(value)

    def first_purchase_exists(self, *, user_id: str) -> bool:
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1 from commerce.app_store_transactions
                      where user_id = cast(:user_id as uuid)
                        and verification_status in ('verified_active', 'verified_expired', 'verified_revoked')
                      union all
                      select 1 from commerce.wechat_orders
                      where user_id = cast(:user_id as uuid)
                        and paid_at is not null
                    )
                    """
                ),
                {"user_id": user_id},
            ).scalar_one()
        )

    def order_by_command(
        self, *, user_id: str, command_key: str
    ) -> OrderRecord | None:
        return self._order_query(
            "orders.user_id = cast(:user_id as uuid) and orders.command_key = :command_key",
            {"user_id": user_id, "command_key": command_key},
        )

    def order_by_id(self, *, user_id: str, order_id: UUID) -> OrderRecord:
        record = self._order_query(
            "orders.user_id = cast(:user_id as uuid) and orders.order_id = cast(:order_id as uuid)",
            {"user_id": user_id, "order_id": str(order_id)},
        )
        if record is None:
            raise Stage8CommerceNotFound("order")
        return record

    def order_by_trade_no(self, *, out_trade_no: str) -> OrderRecord:
        record = self._order_query(
            "orders.out_trade_no = :out_trade_no",
            {"out_trade_no": out_trade_no},
        )
        if record is None:
            raise Stage8CommerceNotFound("order")
        return record

    def list_orders(self, *, user_id: str, limit: int = 20) -> list[OrderRecord]:
        rows = self._session.execute(
            text(
                """
                select orders.*, product.title as offer_title, offer.product_id
                from commerce.wechat_orders orders
                join commerce.product_offer_versions offer
                  on offer.offer_version_id = orders.offer_version_id
                join commerce.iap_products product on product.product_id = offer.product_id
                where orders.user_id = cast(:user_id as uuid)
                order by orders.created_at desc
                limit :limit
                """
            ),
            {"user_id": user_id, "limit": limit},
        ).mappings().all()
        return [_order(row) for row in rows]

    def create_order_and_reserve_slot(
        self,
        *,
        user_id: str,
        command_key: str,
        request_fingerprint: str,
        offer: OfferRecord,
        payer_open_id: str,
        terms_snapshot: dict[str, object],
        expires_at: datetime,
        payment_channel: str = "wechat_jsapi",
        client_platform: str | None = None,
    ) -> OrderRecord:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"stage8-offer:{offer.offer_code}"},
        )
        existing = self.order_by_command(user_id=user_id, command_key=command_key)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise Stage8CommerceConflict(
                    "command key already belongs to different immutable order input"
                )
            return existing
        if self.first_purchase_exists(user_id=user_id):
            raise Stage8CommerceConflict("founder offer is limited to first purchase")
        active_count = int(
            self._session.execute(
                text(
                    """
                    select count(*) from commerce.founder_cohort_slots
                    where cohort_key = :cohort_key
                      and state in ('reserved', 'consumed')
                    """
                ),
                {"cohort_key": offer.offer_code},
            ).scalar_one()
        )
        if offer.cohort_limit is not None and active_count >= offer.cohort_limit:
            raise Stage8CommerceConflict("founder cohort is full")
        if offer.refund_rate >= 0.20:
            raise Stage8CommerceConflict("founder recruitment is paused for policy review")
        order_id = uuid4()
        out_trade_no = _provider_number("A8", order_id)
        terms_hash = _fingerprint(terms_snapshot)
        row = self._session.execute(
            text(
                """
                insert into commerce.wechat_orders (
                  order_id, command_key, request_fingerprint, user_id,
                  offer_version_id, out_trade_no, payer_open_id, currency,
                  amount_minor, state, terms_snapshot, terms_snapshot_sha256,
                  payment_channel, client_platform, expires_at, updated_at, created_at
                ) values (
                  cast(:order_id as uuid), :command_key, :request_fingerprint,
                  cast(:user_id as uuid), cast(:offer_version_id as uuid),
                  :out_trade_no, :payer_open_id, :currency, :amount_minor,
                  'creating', cast(:terms_snapshot as jsonb), :terms_hash,
                  :payment_channel, :client_platform, :expires_at, :now, :now
                ) returning *
                """
            ),
            {
                "order_id": str(order_id),
                "command_key": command_key,
                "request_fingerprint": request_fingerprint,
                "user_id": user_id,
                "offer_version_id": str(offer.offer_version_id),
                "out_trade_no": out_trade_no,
                "payer_open_id": payer_open_id,
                "currency": offer.currency,
                "amount_minor": offer.amount_minor,
                "terms_snapshot": _json(terms_snapshot),
                "terms_hash": terms_hash,
                "payment_channel": payment_channel,
                "client_platform": client_platform,
                "expires_at": expires_at,
                "now": datetime.now(UTC),
            },
        ).mappings().one()
        if offer.cohort_limit is not None:
            occupied = set(
                int(value)
                for value in self._session.execute(
                    text(
                        """
                        select slot_number from commerce.founder_cohort_slots
                        where cohort_key = :cohort_key
                          and state in ('reserved', 'consumed')
                        """
                    ),
                    {"cohort_key": offer.offer_code},
                ).scalars()
            )
            slot_number = next(
                (value for value in range(1, offer.cohort_limit + 1) if value not in occupied),
                None,
            )
            if slot_number is None:
                raise Stage8CommerceConflict("founder cohort is full")
            self._session.execute(
                text(
                    """
                    insert into commerce.founder_cohort_slots (
                      slot_id, offer_version_id, slot_number, user_id, order_id,
                      cohort_key, state, reserved_at
                    ) values (
                      gen_random_uuid(), cast(:offer_version_id as uuid), :slot_number,
                      cast(:user_id as uuid), cast(:order_id as uuid), :cohort_key,
                      'reserved', :now
                    )
                    """
                ),
                {
                    "offer_version_id": str(offer.offer_version_id),
                    "slot_number": slot_number,
                    "user_id": user_id,
                    "order_id": str(order_id),
                    "cohort_key": offer.offer_code,
                    "now": datetime.now(UTC),
                },
            )
        self._session.commit()
        combined = dict(row)
        combined.update({"offer_title": offer.title, "product_id": offer.product_id})
        return _order(combined)

    def mark_order_awaiting_payment(self, *, order_id: UUID, prepay_id: str) -> None:
        self._session.execute(
            text(
                """
                update commerce.wechat_orders
                set state = 'awaiting_payment', prepay_id = :prepay_id,
                    updated_at = now()
                where order_id = cast(:order_id as uuid) and state = 'creating'
                """
            ),
            {"order_id": str(order_id), "prepay_id": prepay_id},
        )
        self._session.commit()

    def mark_order_awaiting_virtual_payment(
        self,
        *,
        order_id: UUID,
        payload_sha256: str,
    ) -> None:
        self._session.execute(
            text(
                """
                update commerce.wechat_orders
                set state = 'awaiting_payment',
                    virtual_payload_sha256 = :payload_sha256,
                    updated_at = now()
                where order_id = cast(:order_id as uuid)
                  and payment_channel = 'wechat_virtual_payment'
                  and state in ('creating', 'awaiting_payment', 'payment_uncertain')
                """
            ),
            {"order_id": str(order_id), "payload_sha256": payload_sha256},
        )
        self._session.commit()

    def mark_order_uncertain(self, *, order_id: UUID) -> None:
        self._session.execute(
            text(
                """
                update commerce.wechat_orders set state = 'payment_uncertain', updated_at = now()
                where order_id = cast(:order_id as uuid)
                  and state in ('creating', 'awaiting_payment')
                """
            ),
            {"order_id": str(order_id)},
        )
        self._session.commit()

    def mark_order_closed(self, *, order_id: UUID, at: datetime) -> None:
        self._session.execute(
            text(
                """
                update commerce.wechat_orders
                set state = 'closed', closed_at = :at, updated_at = :at
                where order_id = cast(:order_id as uuid)
                  and state in ('creating', 'awaiting_payment', 'payment_uncertain')
                """
            ),
            {"order_id": str(order_id), "at": at},
        )
        self._session.execute(
            text(
                """
                update commerce.founder_cohort_slots
                set state = 'released', released_at = :at
                where order_id = cast(:order_id as uuid) and state = 'reserved'
                """
            ),
            {"order_id": str(order_id), "at": at},
        )
        self._session.commit()

    def record_notification(
        self,
        *,
        notification_id: str,
        event_type: str,
        platform_serial: str,
        body_sha256: str,
        resource_type: str,
        resource: dict[str, Any],
        received_at: datetime,
    ) -> bool:
        row = self._session.execute(
            text(
                """
                insert into commerce.wechat_payment_notifications (
                  event_id, notification_id, event_type, platform_serial,
                  body_sha256, resource_type, resource_snapshot, processed,
                  received_at
                ) values (
                  gen_random_uuid(), :notification_id, :event_type, :platform_serial,
                  :body_sha256, :resource_type, cast(:resource as jsonb), false,
                  :received_at
                ) on conflict (notification_id) do nothing
                returning event_id
                """
            ),
            {
                "notification_id": notification_id,
                "event_type": event_type,
                "platform_serial": platform_serial,
                "body_sha256": body_sha256,
                "resource_type": resource_type,
                "resource": _json(resource),
                "received_at": received_at,
            },
        ).first()
        if row is None:
            existing = self._session.execute(
                text(
                    """
                    select event_type, platform_serial, body_sha256
                    from commerce.wechat_payment_notifications
                    where notification_id = :notification_id
                    """
                ),
                {"notification_id": notification_id},
            ).mappings().one()
            if (
                str(existing["event_type"]) != event_type
                or str(existing["platform_serial"]) != platform_serial
                or str(existing["body_sha256"]) != body_sha256
            ):
                self._session.rollback()
                raise Stage8CommerceConflict(
                    "notification id was replayed with different signed content"
                )
            self._session.commit()
            return False
        return True

    def notification_processed(self, *, notification_id: str) -> bool:
        value = self._session.execute(
            text(
                "select processed from commerce.wechat_payment_notifications "
                "where notification_id = :notification_id"
            ),
            {"notification_id": notification_id},
        ).scalar_one()
        return bool(value)

    def notification_response(self, *, notification_id: str) -> dict[str, object]:
        value = self._session.execute(
            text(
                "select response_snapshot from commerce.wechat_payment_notifications "
                "where notification_id = :notification_id and processed = true"
            ),
            {"notification_id": notification_id},
        ).scalar_one_or_none()
        if not isinstance(value, dict):
            raise Stage8CommerceConflict(
                "processed payment notification has no immutable response snapshot"
            )
        return dict(value)

    def mark_notification_processed(
        self,
        *,
        notification_id: str,
        error_code: str | None = None,
        response: dict[str, object] | None = None,
    ) -> None:
        self._session.execute(
            text(
                """
                update commerce.wechat_payment_notifications
                set processed = :processed, processing_error_code = :error_code,
                    response_snapshot = coalesce(
                      cast(:response as jsonb), response_snapshot
                    ),
                    processed_at = now()
                where notification_id = :notification_id
                """
            ),
            {
                "notification_id": notification_id,
                "processed": error_code is None,
                "error_code": error_code,
                "response": _json(response) if response is not None else None,
            },
        )
        self._session.commit()

    def apply_paid_order(
        self,
        *,
        order: OrderRecord,
        transaction_id: str,
        paid_at: datetime,
        provider_trade_state: str,
        service_days: int,
        provider_order_type: int | None = None,
        provider_wx_order_id: str | None = None,
        provider_channel_order_id: str | None = None,
        provider_delivery_state: str | None = None,
    ) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"stage8-order:{order.order_id}"},
        )
        current = self.order_by_trade_no(out_trade_no=order.out_trade_no)
        if current.paid_at is not None:
            if current.provider_transaction_id != transaction_id:
                raise Stage8CommerceConflict("order already belongs to another payment")
            self._session.commit()
            return
        self._session.execute(
            text(
                """
                update commerce.wechat_orders
                set state = 'paid', provider_transaction_id = :transaction_id,
                    provider_trade_state = :provider_trade_state, paid_at = :paid_at,
                    provider_order_type = coalesce(:provider_order_type, provider_order_type),
                    provider_wx_order_id = coalesce(:provider_wx_order_id, provider_wx_order_id),
                    provider_channel_order_id = coalesce(:provider_channel_order_id, provider_channel_order_id),
                    provider_delivery_state = coalesce(:provider_delivery_state, provider_delivery_state),
                    updated_at = :paid_at
                where order_id = cast(:order_id as uuid)
                  and state in ('creating', 'awaiting_payment', 'payment_uncertain')
                """
            ),
            {
                "order_id": str(order.order_id),
                "transaction_id": transaction_id,
                "provider_trade_state": provider_trade_state,
                "provider_order_type": provider_order_type,
                "provider_wx_order_id": provider_wx_order_id,
                "provider_channel_order_id": provider_channel_order_id,
                "provider_delivery_state": provider_delivery_state,
                "paid_at": paid_at,
            },
        )
        self._session.execute(
            text(
                """
                update commerce.founder_cohort_slots
                set state = 'consumed', consumed_at = :paid_at
                where order_id = cast(:order_id as uuid) and state = 'reserved'
                """
            ),
            {"order_id": str(order.order_id), "paid_at": paid_at},
        )
        self._insert_grant(
            user_id=order.user_id,
            source_type="wechat_purchase",
            source_ref=f"wechat-order:{order.order_id}",
            product_id=order.product_id,
            starts_at=paid_at,
            ends_at=paid_at + timedelta(days=service_days),
            evidence_refs=[f"wechat-order:{order.order_id}", f"wechat-tx:{transaction_id}"],
        )
        self._session.commit()

    def create_refund(
        self,
        *,
        order: OrderRecord,
        command_key: str,
        request_fingerprint: str,
        refund_kind: str,
        reason: str,
        amount_minor: int,
        policy_version: str,
        now: datetime,
        transition_order: bool = True,
    ) -> RefundRecord:
        existing = self.refund_by_command(user_id=str(order.user_id), command_key=command_key)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise Stage8CommerceConflict(
                    "command key already belongs to different immutable refund input"
                )
            return existing
        refund_id = uuid4()
        out_refund_no = _provider_number("R8", refund_id)
        row = self._session.execute(
            text(
                """
                insert into commerce.wechat_refunds (
                  refund_id, command_key, request_fingerprint, order_id, user_id,
                  out_refund_no, refund_kind, reason, amount_minor, currency,
                  policy_version, state, requested_at, updated_at
                ) values (
                  cast(:refund_id as uuid), :command_key, :request_fingerprint,
                  cast(:order_id as uuid), cast(:user_id as uuid), :out_refund_no,
                  :refund_kind, :reason, :amount_minor, :currency, :policy_version,
                  'requested', :now, :now
                ) returning *
                """
            ),
            {
                "refund_id": str(refund_id),
                "command_key": command_key,
                "request_fingerprint": request_fingerprint,
                "order_id": str(order.order_id),
                "user_id": str(order.user_id),
                "out_refund_no": out_refund_no,
                "refund_kind": refund_kind,
                "reason": reason,
                "amount_minor": amount_minor,
                "currency": order.currency,
                "policy_version": policy_version,
                "now": now,
            },
        ).mappings().one()
        if transition_order:
            self._session.execute(
                text(
                    """
                    update commerce.wechat_orders set state = 'refund_processing', updated_at = :now
                    where order_id = cast(:order_id as uuid)
                      and state in ('paid', 'partially_refunded')
                    """
                ),
                {"order_id": str(order.order_id), "now": now},
            )
        self._session.commit()
        return _refund(row)

    def mark_virtual_delivery_state(
        self,
        *,
        order_id: UUID,
        provider_delivery_state: str,
    ) -> None:
        self._session.execute(
            text(
                """
                update commerce.wechat_orders
                set provider_delivery_state = :provider_delivery_state,
                    updated_at = now()
                where order_id = cast(:order_id as uuid)
                  and payment_channel = 'wechat_virtual_payment'
                """
            ),
            {
                "order_id": str(order_id),
                "provider_delivery_state": provider_delivery_state,
            },
        )
        self._session.commit()

    def refund_by_command(
        self, *, user_id: str, command_key: str
    ) -> RefundRecord | None:
        row = self._session.execute(
            text(
                """
                select * from commerce.wechat_refunds
                where user_id = cast(:user_id as uuid) and command_key = :command_key
                """
            ),
            {"user_id": user_id, "command_key": command_key},
        ).mappings().one_or_none()
        return _refund(row) if row is not None else None

    def refund_by_number(self, *, out_refund_no: str) -> RefundRecord:
        row = self._session.execute(
            text("select * from commerce.wechat_refunds where out_refund_no = :value"),
            {"value": out_refund_no},
        ).mappings().one_or_none()
        if row is None:
            raise Stage8CommerceNotFound("refund")
        return _refund(row)

    def list_refunds(self, *, order_id: UUID) -> list[RefundRecord]:
        rows = self._session.execute(
            text(
                """
                select * from commerce.wechat_refunds
                where order_id = cast(:order_id as uuid)
                order by requested_at, refund_id
                """
            ),
            {"order_id": str(order_id)},
        ).mappings().all()
        return [_refund(row) for row in rows]

    def order_trade_no(self, *, order_id: UUID) -> str:
        value = self._session.execute(
            text(
                "select out_trade_no from commerce.wechat_orders "
                "where order_id = cast(:order_id as uuid)"
            ),
            {"order_id": str(order_id)},
        ).scalar_one_or_none()
        if value is None:
            raise Stage8CommerceNotFound("order")
        return str(value)

    def mark_refund_provider_state(
        self,
        *,
        refund: RefundRecord,
        state: str,
        provider_refund_id: str | None,
        provider_status: str,
        processed_at: datetime | None,
    ) -> None:
        self._session.execute(
            text(
                """
                update commerce.wechat_refunds
                set state = :state, provider_refund_id = coalesce(:provider_refund_id, provider_refund_id),
                    provider_status = :provider_status, processed_at = :processed_at,
                    updated_at = now()
                where refund_id = cast(:refund_id as uuid)
                """
            ),
            {
                "refund_id": str(refund.refund_id),
                "state": state,
                "provider_refund_id": provider_refund_id,
                "provider_status": provider_status,
                "processed_at": processed_at,
            },
        )
        if state == "succeeded":
            total = int(
                self._session.execute(
                    text(
                        """
                        select coalesce(sum(amount_minor), 0)
                        from commerce.wechat_refunds
                        where order_id = cast(:order_id as uuid) and state = 'succeeded'
                        """
                    ),
                    {"order_id": str(refund.order_id)},
                ).scalar_one()
            )
            order_amount = int(
                self._session.execute(
                    text(
                        "select amount_minor from commerce.wechat_orders where order_id = cast(:order_id as uuid)"
                    ),
                    {"order_id": str(refund.order_id)},
                ).scalar_one()
            )
            order_state = "refunded" if total >= order_amount else "partially_refunded"
            self._session.execute(
                text(
                    """
                    update commerce.wechat_orders
                    set refunded_minor = :total, state = :state, updated_at = now()
                    where order_id = cast(:order_id as uuid)
                    """
                ),
                {"order_id": str(refund.order_id), "total": total, "state": order_state},
            )
            if order_state == "refunded":
                self._session.execute(
                    text(
                        """
                        update commerce.entitlement_grants
                        set state = 'revoked', revoked_at = now(),
                            revocation_reason = 'order_fully_refunded'
                        where source_type = 'wechat_purchase'
                          and source_ref = :source_ref and state = 'active'
                        """
                    ),
                    {"source_ref": f"wechat-order:{refund.order_id}"},
                )
        elif state in {"closed", "abnormal"}:
            current_total = int(
                self._session.execute(
                    text(
                        "select refunded_minor from commerce.wechat_orders where order_id = cast(:order_id as uuid)"
                    ),
                    {"order_id": str(refund.order_id)},
                ).scalar_one()
            )
            restored_state = "partially_refunded" if current_total else "paid"
            self._session.execute(
                text(
                    "update commerce.wechat_orders set state = :state, updated_at = now() "
                    "where order_id = cast(:order_id as uuid) and state = 'refund_processing'"
                ),
                {"order_id": str(refund.order_id), "state": restored_state},
            )
        self._session.commit()

    def latest_paid_founder_order(self, *, user_id: str) -> OrderRecord | None:
        return self._order_query(
            "orders.user_id = cast(:user_id as uuid) and orders.paid_at is not null",
            {"user_id": user_id},
            order_clause="orders.paid_at desc",
        )

    def pending_orders(self, *, now: datetime, limit: int = 100) -> list[OrderRecord]:
        rows = self._session.execute(
            text(
                """
                select orders.*, product.title as offer_title, offer.product_id
                from commerce.wechat_orders orders
                join commerce.product_offer_versions offer
                  on offer.offer_version_id = orders.offer_version_id
                join commerce.iap_products product on product.product_id = offer.product_id
                where orders.state = 'payment_uncertain'
                   or (orders.state in ('creating', 'awaiting_payment') and orders.expires_at <= :now)
                order by orders.expires_at, orders.order_id
                limit :limit
                """
            ),
            {"now": now, "limit": limit},
        ).mappings().all()
        return [_order(row) for row in rows]

    def pending_refunds(self, *, limit: int = 100) -> list[RefundRecord]:
        rows = self._session.execute(
            text(
                """
                select * from commerce.wechat_refunds
                where state in ('processing', 'provider_uncertain')
                order by requested_at, refund_id
                limit :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
        return [_refund(row) for row in rows]

    def virtual_refund_orders(self, *, limit: int = 100) -> list[OrderRecord]:
        rows = self._session.execute(
            text(
                """
                select orders.*, product.title as offer_title, offer.product_id
                from commerce.wechat_orders orders
                join commerce.product_offer_versions offer
                  on offer.offer_version_id = orders.offer_version_id
                join commerce.iap_products product on product.product_id = offer.product_id
                where orders.payment_channel = 'wechat_virtual_payment'
                  and exists (
                    select 1 from commerce.wechat_refunds refunds
                    where refunds.order_id = orders.order_id
                      and refunds.refund_kind = 'not_suitable'
                      and refunds.state in ('processing', 'provider_uncertain')
                  )
                order by orders.updated_at, orders.order_id
                limit :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
        return [_order(row) for row in rows]

    def reward_candidate_orders(
        self, *, now: datetime, limit: int = 100
    ) -> list[OrderRecord]:
        rows = self._session.execute(
            text(
                """
                select orders.*, product.title as offer_title, offer.product_id
                from commerce.wechat_orders orders
                join commerce.product_offer_versions offer
                  on offer.offer_version_id = orders.offer_version_id
                join commerce.iap_products product on product.product_id = offer.product_id
                where orders.paid_at is not null
                  and orders.state in ('paid', 'partially_refunded')
                  and orders.paid_at + interval '45 days' >= :now
                order by orders.paid_at, orders.order_id
                limit :limit
                """
            ),
            {"now": now, "limit": limit},
        ).mappings().all()
        return [_order(row) for row in rows]

    def reward_facts(
        self, *, order: OrderRecord, as_of: datetime
    ) -> RewardFacts:
        if order.paid_at is None:
            raise Stage8CommerceConflict("reward progress requires a paid order")
        service_end = order.paid_at + timedelta(days=30)
        end_date = min(as_of, service_end).date()
        start_date = order.paid_at.date()
        row = self._session.execute(
            text(
                """
                with latest_plan as (
                  select distinct on (local_date) plan_version_id, local_date
                  from learning.daily_plan_versions
                  where user_id = cast(:user_id as uuid)
                    and local_date between :start_date and :end_date
                  order by local_date, version_no desc
                ), current_execution as (
                  select execution.*
                  from learning.task_execution_versions execution
                  where execution.user_id = cast(:user_id as uuid)
                    and not exists (
                      select 1 from learning.task_execution_versions successor
                      where successor.supersedes_execution_version_id = execution.execution_version_id
                    )
                ), core as (
                  select task.local_date, task.task_id, execution.status
                  from latest_plan plan
                  join learning.daily_plan_tasks task
                    on task.plan_version_id = plan.plan_version_id and task.mandatory = true
                  left join current_execution execution on execution.task_id = task.task_id
                )
                select
                  count(distinct local_date) filter (where status = 'complete')::int as effective_days,
                  count(*)::int as core_due,
                  count(*) filter (where status = 'complete')::int as core_completed
                from core
                """
            ),
            {
                "user_id": str(order.user_id),
                "start_date": start_date,
                "end_date": end_date,
            },
        ).mappings().one()
        weekly = int(
            self._session.execute(
                text(
                    """
                    select count(*) from commerce.weekly_reflection_confirmations
                    where user_id = cast(:user_id as uuid)
                      and order_id = cast(:order_id as uuid)
                    """
                ),
                {"user_id": str(order.user_id), "order_id": str(order.order_id)},
            ).scalar_one()
        )
        awards = self._session.execute(
            text(
                """
                select reward_type, state from commerce.process_reward_awards
                where order_id = cast(:order_id as uuid)
                """
            ),
            {"order_id": str(order.order_id)},
        ).mappings().all()
        return RewardFacts(
            effective_days=int(row["effective_days"] or 0),
            weekly_reflections=weekly,
            core_tasks_completed=int(row["core_completed"] or 0),
            core_tasks_due=int(row["core_due"] or 0),
            awarded_rewards=sorted(
                str(item["reward_type"])
                for item in awards
                if item["state"] == "granted"
            ),
            pending_rewards=sorted(
                str(item["reward_type"])
                for item in awards
                if item["state"] in {"eligible", "processing"}
            ),
        )

    def grant_membership_reward(
        self, *, order: OrderRecord, evidence_refs: list[str], now: datetime
    ) -> None:
        exists = self._session.execute(
            text(
                """
                select 1 from commerce.process_reward_awards
                where order_id = cast(:order_id as uuid) and reward_type = 'membership_15d'
                """
            ),
            {"order_id": str(order.order_id)},
        ).first()
        if exists:
            return
        current_end = self._active_entitlement_end(user_id=order.user_id, now=now)
        starts_at = max(now, current_end) if current_end is not None else now
        grant_id = self._insert_grant(
            user_id=order.user_id,
            source_type="process_reward",
            source_ref=f"process-reward:membership:{order.order_id}",
            product_id=order.product_id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=15),
            evidence_refs=evidence_refs,
        )
        self._session.execute(
            text(
                """
                insert into commerce.process_reward_awards (
                  award_id, user_id, order_id, reward_type, state, policy_version,
                  evidence_refs, grant_id, granted_at, created_at, updated_at
                ) values (
                  gen_random_uuid(), cast(:user_id as uuid), cast(:order_id as uuid),
                  'membership_15d', 'granted', 'founder-process-reward@v1',
                  cast(:evidence_refs as jsonb), cast(:grant_id as uuid), :now, :now, :now
                )
                """
            ),
            {
                "user_id": str(order.user_id),
                "order_id": str(order.order_id),
                "evidence_refs": _json(evidence_refs),
                "grant_id": str(grant_id),
                "now": now,
            },
        )
        self._session.commit()

    def grant_special_diagnostic(
        self, *, order: OrderRecord, evidence_refs: list[str], now: datetime
    ) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.process_reward_awards (
                  award_id, user_id, order_id, reward_type, state, policy_version,
                  evidence_refs, granted_at, created_at, updated_at
                ) values (
                  gen_random_uuid(), cast(:user_id as uuid), cast(:order_id as uuid),
                  'special_diagnostic', 'granted', 'founder-process-reward@v1',
                  cast(:evidence_refs as jsonb), :now, :now, :now
                ) on conflict (order_id, reward_type) do nothing
                """
            ),
            {
                "user_id": str(order.user_id),
                "order_id": str(order.order_id),
                "evidence_refs": _json(evidence_refs),
                "now": now,
            },
        )
        self._session.commit()

    def confirm_weekly_reflection(
        self,
        *,
        user_id: str,
        command_key: str,
        order: OrderRecord,
        report_version_id: UUID,
        now: datetime,
    ) -> tuple[UUID, date, datetime]:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"weekly-reflection:{user_id}:{command_key}"},
        )
        existing = self._session.execute(
            text(
                """
                select confirmation_id, order_id, report_version_id,
                       week_start, confirmed_at
                from commerce.weekly_reflection_confirmations
                where user_id = cast(:user_id as uuid) and command_key = :command_key
                """
            ),
            {"user_id": user_id, "command_key": command_key},
        ).mappings().one_or_none()
        if existing is not None:
            if (
                UUID(str(existing["order_id"])) != order.order_id
                or UUID(str(existing["report_version_id"])) != report_version_id
            ):
                raise Stage8CommerceConflict(
                    "command key already belongs to a different weekly reflection"
                )
            self._session.commit()
            return (
                UUID(str(existing["confirmation_id"])),
                existing["week_start"],
                existing["confirmed_at"],
            )
        report = self._session.execute(
            text(
                """
                select projection_version_id, report_kind, generated_at
                from learning.report_projection_versions
                where projection_version_id = cast(:report_version_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {"report_version_id": str(report_version_id), "user_id": user_id},
        ).mappings().one_or_none()
        if report is None or str(report["report_kind"]) != "weekly":
            raise Stage8CommerceConflict("weekly reflection requires an owned weekly report")
        generated_date = report["generated_at"].date()
        week_start = generated_date - timedelta(days=generated_date.weekday())
        if order.paid_at is None or not (
            order.paid_at.date()
            <= generated_date
            <= (order.paid_at + timedelta(days=30)).date()
        ):
            raise Stage8CommerceConflict(
                "weekly report is outside the founder service period"
            )
        confirmation_id = uuid4()
        self._session.execute(
            text(
                """
                insert into commerce.weekly_reflection_confirmations (
                  confirmation_id, command_key, user_id, order_id,
                  report_version_id, week_start, evidence_refs, confirmed_at
                ) values (
                  cast(:confirmation_id as uuid), :command_key,
                  cast(:user_id as uuid), cast(:order_id as uuid),
                  cast(:report_version_id as uuid), :week_start,
                  cast(:evidence_refs as jsonb), :now
                )
                """
            ),
            {
                "confirmation_id": str(confirmation_id),
                "command_key": command_key,
                "user_id": user_id,
                "order_id": str(order.order_id),
                "report_version_id": str(report_version_id),
                "week_start": week_start,
                "evidence_refs": _json([f"weekly-report:{report_version_id}"]),
                "now": now,
            },
        )
        self._session.commit()
        return confirmation_id, week_start, now

    def mark_weekly_cash_award(
        self,
        *,
        order: OrderRecord,
        refund_id: UUID,
        evidence_refs: list[str],
        state: str,
        now: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into commerce.process_reward_awards (
                  award_id, user_id, order_id, reward_type, state, policy_version,
                  evidence_refs, refund_id, granted_at, created_at, updated_at
                ) values (
                  gen_random_uuid(), cast(:user_id as uuid), cast(:order_id as uuid),
                  'weekly_reflection_cny30', :state, 'founder-process-reward@v1',
                  cast(:evidence_refs as jsonb), cast(:refund_id as uuid),
                  case when :state = 'granted' then :now else null end, :now, :now
                ) on conflict (order_id, reward_type) do update set
                  state = excluded.state,
                  refund_id = excluded.refund_id,
                  granted_at = excluded.granted_at,
                  updated_at = excluded.updated_at
                """
            ),
            {
                "user_id": str(order.user_id),
                "order_id": str(order.order_id),
                "state": state,
                "evidence_refs": _json(evidence_refs),
                "refund_id": str(refund_id),
                "now": now,
            },
        )
        self._session.commit()

    def unlocked_features(self, *, user_id: str) -> list[str]:
        values = self._session.execute(
            text(
                """
                select reward_type from commerce.process_reward_awards
                where user_id = cast(:user_id as uuid) and state = 'granted'
                  and reward_type = 'special_diagnostic'
                """
            ),
            {"user_id": user_id},
        ).scalars()
        return sorted({str(value) for value in values})

    def current_retake_program(self, *, user_id: str) -> RetakeRecord | None:
        row = self._session.execute(
            text(
                """
                select * from learning.retake_program_versions
                where user_id = cast(:user_id as uuid) and state = 'active'
                order by version_no desc limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        return _retake(row) if row is not None else None

    def enroll_retake(
        self,
        *,
        user_id: str,
        command_key: str,
        source_score_evidence_id: UUID,
        target_exam_year: int,
        now: datetime,
    ) -> RetakeRecord:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"retake-program:{user_id}"},
        )
        existing = self._session.execute(
            text(
                """
                select * from learning.retake_program_versions
                where user_id = cast(:user_id as uuid) and command_key = :command_key
                """
            ),
            {"user_id": user_id, "command_key": command_key},
        ).mappings().one_or_none()
        if existing is not None:
            if (
                UUID(str(existing["source_score_evidence_id"]))
                != source_score_evidence_id
                or int(existing["target_exam_year"]) != target_exam_year
            ):
                raise Stage8CommerceConflict(
                    "command key already belongs to a different retake enrollment"
                )
            self._session.commit()
            return _retake(existing)
        evidence = self._session.execute(
            text(
                """
                select * from learning.score_evidence
                where evidence_id = cast(:evidence_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and verification_status in ('user_confirmed', 'system_verified')
                """
            ),
            {"evidence_id": str(source_score_evidence_id), "user_id": user_id},
        ).mappings().one_or_none()
        if evidence is None:
            raise Stage8CommerceConflict("retake program requires confirmed owned score evidence")
        source_year = int(evidence["exam_year"])
        if target_exam_year != source_year + 1:
            raise Stage8CommerceConflict("retake target year must immediately follow source year")
        active = self.current_retake_program(user_id=user_id)
        if active is not None:
            raise Stage8CommerceConflict("an active retake program already exists")
        xingce = float(evidence["xingce_score"]) if evidence["xingce_score"] is not None else None
        essay = float(evidence["shenlun_score"]) if evidence["shenlun_score"] is not None else None
        tracks = [name for name, value in (("xingce", xingce), ("essay", essay)) if value is not None]
        ranked = sorted(
            ((name, value) for name, value in (("xingce", xingce), ("essay", essay)) if value is not None),
            key=lambda item: item[1],
        )
        focus = [
            ("先用行测模块证据细分速度、正确率与错因" if name == "xingce" else "先用申论新材料复原要点、结构与表达失分")
            for name, _ in ranked
        ]
        loss_structure: dict[str, object] = {
            "xingceScore": xingce,
            "essayScore": essay,
            "availableTracks": tracks,
            "trainingFocus": focus,
            "limitation": "上年度原始分只建立失分结构；跨年度难度不同，不直接计算提分，也不参与退款或现金奖励。",
        }
        program_version_id = uuid4()
        program_id = uuid4()
        row = self._session.execute(
            text(
                """
                insert into learning.retake_program_versions (
                  program_version_id, program_id, command_key, user_id,
                  source_score_evidence_id, source_exam_year, target_exam_year,
                  version_no, state, loss_structure, score_use_boundary,
                  evidence_refs, input_fingerprint, created_at
                ) values (
                  cast(:program_version_id as uuid), cast(:program_id as uuid),
                  :command_key, cast(:user_id as uuid), cast(:evidence_id as uuid),
                  :source_year, :target_year, 1, 'active',
                  cast(:loss_structure as jsonb),
                  cast(:score_use_boundary as jsonb),
                  cast(:evidence_refs as jsonb), :input_fingerprint, :now
                ) returning *
                """
            ),
            {
                "program_version_id": str(program_version_id),
                "program_id": str(program_id),
                "command_key": command_key,
                "user_id": user_id,
                "evidence_id": str(source_score_evidence_id),
                "source_year": source_year,
                "target_year": target_exam_year,
                "loss_structure": _json(loss_structure),
                "score_use_boundary": _json(
                    {"refund": False, "cashReward": False, "training": True}
                ),
                "evidence_refs": _json([f"score-evidence:{source_score_evidence_id}"]),
                "input_fingerprint": _fingerprint(
                    {
                        "sourceScoreEvidenceId": str(source_score_evidence_id),
                        "targetExamYear": target_exam_year,
                        "lossStructure": loss_structure,
                    }
                ),
                "now": now,
            },
        ).mappings().one()
        self._session.commit()
        return _retake(row)

    def _active_entitlement_end(
        self, *, user_id: UUID, now: datetime
    ) -> datetime | None:
        return self._session.execute(
            text(
                """
                select max(ends_at) from (
                  select ends_at from commerce.entitlement_grants
                  where user_id = cast(:user_id as uuid)
                    and state = 'active' and ends_at > :now
                  union all
                  select subscription_end as ends_at from commerce.user_entitlements
                  where user_id = cast(:user_id as uuid)
                    and tier = 'pro' and subscription_end > :now
                  union all
                  select activated_until as ends_at
                  from commerce.community_reward_membership_activations
                  where beneficiary_user_id = cast(:user_id as uuid)
                    and activated_until > :now
                ) active_periods
                """
            ),
            {"user_id": str(user_id), "now": now},
        ).scalar_one()

    def _insert_grant(
        self,
        *,
        user_id: UUID,
        source_type: str,
        source_ref: str,
        product_id: str,
        starts_at: datetime,
        ends_at: datetime,
        evidence_refs: list[str],
    ) -> UUID:
        grant_id = uuid4()
        value = self._session.execute(
            text(
                """
                insert into commerce.entitlement_grants (
                  grant_id, user_id, source_type, source_ref, product_id,
                  starts_at, ends_at, state, evidence_refs, created_at
                ) values (
                  cast(:grant_id as uuid), cast(:user_id as uuid), :source_type,
                  :source_ref, :product_id, :starts_at, :ends_at, 'active',
                  cast(:evidence_refs as jsonb), now()
                ) on conflict (source_type, source_ref) do nothing
                returning grant_id
                """
            ),
            {
                "grant_id": str(grant_id),
                "user_id": str(user_id),
                "source_type": source_type,
                "source_ref": source_ref,
                "product_id": product_id,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "evidence_refs": _json(evidence_refs),
            },
        ).scalar_one_or_none()
        if value is not None:
            return UUID(str(value))
        existing = self._session.execute(
            text(
                """
                select grant_id from commerce.entitlement_grants
                where source_type = :source_type and source_ref = :source_ref
                """
            ),
            {"source_type": source_type, "source_ref": source_ref},
        ).scalar_one()
        return UUID(str(existing))

    def _order_query(
        self,
        predicate: str,
        parameters: dict[str, object],
        *,
        order_clause: str | None = None,
    ) -> OrderRecord | None:
        suffix = f" order by {order_clause} limit 1" if order_clause else ""
        row = self._session.execute(
            text(
                """
                select orders.*, product.title as offer_title, offer.product_id
                from commerce.wechat_orders orders
                join commerce.product_offer_versions offer
                  on offer.offer_version_id = orders.offer_version_id
                join commerce.iap_products product on product.product_id = offer.product_id
                where
                """
                + predicate
                + suffix
            ),
            parameters,
        ).mappings().one_or_none()
        return _order(row) if row is not None else None


def _offer(row: RowMapping) -> OfferRecord:
    limit = int(row["cohort_limit"]) if row["cohort_limit"] is not None else None
    paid = int(row["paid_count"] or 0)
    reserved = int(row["reserved_count"] or 0)
    refund_count = int(row["refund_count"] or 0)
    return OfferRecord(
        offer_version_id=UUID(str(row["offer_version_id"])),
        offer_code=str(row["offer_code"]),
        version_no=int(row["version_no"]),
        product_id=str(row["product_id"]),
        title=str(row["title"]),
        subtitle=str(row["subtitle"]),
        feature_highlights=[str(value) for value in list(row["feature_highlights"] or [])],
        channel=str(row["channel"]),
        currency=str(row["currency"]),
        amount_minor=int(row["amount_minor"]),
        service_days=int(row["service_days"]),
        cohort_limit=limit,
        service_terms_version=str(row["service_terms_version"]),
        refund_policy_version=str(row["refund_policy_version"]),
        reward_policy_version=str(row["reward_policy_version"]),
        marketing_policy_version=str(row["marketing_policy_version"]),
        cohort_paid_count=paid,
        cohort_reserved_count=reserved,
        cohort_remaining=max(limit - paid - reserved, 0) if limit is not None else None,
        refund_rate=(refund_count / paid) if paid else 0.0,
    )


def _policy(row: RowMapping) -> PolicyRecord:
    return PolicyRecord(
        policy_version=str(row["policy_version"]),
        policy_kind=str(row["policy_kind"]),
        title=str(row["title"]),
        content=dict(row["content"]),
        content_sha256=str(row["content_sha256"]),
    )


def _order(row: RowMapping | dict[str, Any]) -> OrderRecord:
    return OrderRecord(
        order_id=UUID(str(row["order_id"])),
        user_id=UUID(str(row["user_id"])),
        command_key=str(row["command_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        offer_version_id=UUID(str(row["offer_version_id"])),
        offer_title=str(row["offer_title"]),
        product_id=str(row["product_id"]),
        out_trade_no=str(row["out_trade_no"]),
        payer_open_id=str(row["payer_open_id"]),
        currency=str(row["currency"]),
        amount_minor=int(row["amount_minor"]),
        refunded_minor=int(row["refunded_minor"]),
        state=str(row["state"]),
        terms_snapshot=dict(row["terms_snapshot"]),
        terms_snapshot_sha256=str(row["terms_snapshot_sha256"]),
        payment_channel=str(row.get("payment_channel") or "wechat_jsapi"),
        client_platform=(
            str(row["client_platform"]) if row.get("client_platform") is not None else None
        ),
        virtual_payload_sha256=(
            str(row["virtual_payload_sha256"])
            if row.get("virtual_payload_sha256") is not None
            else None
        ),
        prepay_id=str(row["prepay_id"]) if row["prepay_id"] is not None else None,
        provider_transaction_id=(
            str(row["provider_transaction_id"])
            if row["provider_transaction_id"] is not None
            else None
        ),
        provider_order_type=(
            int(row["provider_order_type"])
            if row.get("provider_order_type") is not None
            else None
        ),
        provider_wx_order_id=(
            str(row["provider_wx_order_id"])
            if row.get("provider_wx_order_id") is not None
            else None
        ),
        provider_channel_order_id=(
            str(row["provider_channel_order_id"])
            if row.get("provider_channel_order_id") is not None
            else None
        ),
        provider_delivery_state=(
            str(row["provider_delivery_state"])
            if row.get("provider_delivery_state") is not None
            else None
        ),
        expires_at=row["expires_at"],
        paid_at=row["paid_at"],
        created_at=row["created_at"],
    )


def _refund(row: RowMapping) -> RefundRecord:
    return RefundRecord(
        refund_id=UUID(str(row["refund_id"])),
        order_id=UUID(str(row["order_id"])),
        user_id=UUID(str(row["user_id"])),
        command_key=str(row["command_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        out_refund_no=str(row["out_refund_no"]),
        refund_kind=str(row["refund_kind"]),
        amount_minor=int(row["amount_minor"]),
        currency=str(row["currency"]),
        policy_version=str(row["policy_version"]),
        state=str(row["state"]),
        provider_refund_id=(
            str(row["provider_refund_id"])
            if row["provider_refund_id"] is not None
            else None
        ),
        requested_at=row["requested_at"],
        processed_at=row["processed_at"],
    )


def _retake(row: RowMapping) -> RetakeRecord:
    return RetakeRecord(
        program_version_id=UUID(str(row["program_version_id"])),
        source_score_evidence_id=UUID(str(row["source_score_evidence_id"])),
        source_exam_year=int(row["source_exam_year"]),
        target_exam_year=int(row["target_exam_year"]),
        state=str(row["state"]),
        loss_structure=dict(row["loss_structure"]),
        created_at=row["created_at"],
    )


def _provider_number(prefix: str, value: UUID) -> str:
    return f"{prefix}{value.hex.upper()}"[:32]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()
