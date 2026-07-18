from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.domains.commerce.service import CommerceService
from app.domains.commerce.stage8_repository import (
    OfferRecord,
    OrderRecord,
    RefundRecord,
    RewardFacts,
    Stage8CommerceConflict,
    Stage8CommerceRepository,
)
from app.domains.commerce.stage8_schemas import (
    CommercialPolicySnapshot,
    CommerceDashboard,
    PolicyAcceptances,
    ProcessRewardProgress,
    RefundEligibility,
    RetakeEnrollmentRequest,
    RetakeLossStructure,
    RetakeProgramResponse,
    RetakeProgramView,
    Stage8Catalog,
    WechatNotificationAck,
    WechatOffer,
    WechatOrderCreateRequest,
    WechatOrderCreateResponse,
    WechatOrderList,
    WechatOrderView,
    WechatPaymentParameters,
    WechatRefundRequest,
    WechatRefundResponse,
    WechatRefundView,
    WeeklyReflectionConfirmationView,
    WeeklyReflectionConfirmRequest,
)
from app.domains.commerce.wechat_pay import (
    WechatNotification,
    WechatOrderQueryResult,
    WechatPayGateway,
    WechatPayProtocolError,
    WechatPayTransportUncertain,
    WechatPrepayResult,
    WechatRefundResult,
)


class Stage8PaymentPending(RuntimeError):
    """A provider outcome is uncertain and must be reconciled by query."""


class Stage8RefundIneligible(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Stage8CommerceUnavailable(RuntimeError):
    """The commercial engine exists, but the active product mode forbids sales."""


class Stage8CommerceService:
    def __init__(
        self,
        *,
        repository: Stage8CommerceRepository,
        entitlement_service: CommerceService,
        gateway: WechatPayGateway,
        settings: Settings,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._entitlement_service = entitlement_service
        self._gateway = gateway
        self._settings = settings
        self._now = now or (lambda: datetime.now(UTC))
        if not 300 <= settings.wechat_pay_order_expiry_seconds <= 7200:
            raise ValueError(
                "WECHAT_PAY_ORDER_EXPIRY_SECONDS must be between 300 and 7200"
            )

    def catalog(self) -> Stage8Catalog:
        now = self._now()
        ordering_enabled = self._settings.commercial_mode == "paid"
        return Stage8Catalog(
            commercial_mode=self._settings.commercial_mode,
            ordering_enabled=ordering_enabled,
            offers=(
                [
                    self._offer_view(item)
                    for item in self._repository.list_active_offers(now=now)
                ]
                if ordering_enabled
                else []
            ),
            server_time=now,
        )

    def create_order(
        self, *, user_id: str, request: WechatOrderCreateRequest
    ) -> WechatOrderCreateResponse:
        self.require_ordering_enabled()
        now = self._now()
        offer = self._repository.offer_by_id(request.offer_version_id, now=now)
        if request.expected_amount_minor != offer.amount_minor:
            raise Stage8CommerceConflict(
                "offer amount changed; refresh catalog before payment"
            )
        self._validate_policy_acceptances(offer, request.accepted_policies)
        policies = self._repository.policies(_offer_policy_versions(offer))
        terms_snapshot: dict[str, object] = {
            "offerVersionId": str(offer.offer_version_id),
            "offerCode": offer.offer_code,
            "versionNo": offer.version_no,
            "productId": offer.product_id,
            "currency": offer.currency,
            "amountMinor": offer.amount_minor,
            "serviceDays": offer.service_days,
            "automaticRenewal": False,
            "policies": [
                {
                    "policyVersion": item.policy_version,
                    "policyKind": item.policy_kind,
                    "title": item.title,
                    "content": item.content,
                    "contentSha256": item.content_sha256,
                }
                for item in policies
            ],
            "acceptedAt": now.isoformat(),
        }
        fingerprint = _fingerprint(
            {
                "offerVersionId": str(request.offer_version_id),
                "expectedAmountMinor": request.expected_amount_minor,
                "acceptedPolicies": request.accepted_policies.model_dump(by_alias=True),
            }
        )
        order = self._repository.create_order_and_reserve_slot(
            user_id=user_id,
            command_key=request.command_key,
            request_fingerprint=fingerprint,
            offer=offer,
            payer_open_id=self._repository.wechat_open_id(
                user_id=user_id, app_id=self._settings.wechat_mini_app_id
            ),
            terms_snapshot=terms_snapshot,
            expires_at=now
            + timedelta(seconds=self._settings.wechat_pay_order_expiry_seconds),
        )
        payment: WechatPrepayResult | None = None
        if order.state == "awaiting_payment" and order.prepay_id:
            payment = self._gateway.sign_prepay(prepay_id=order.prepay_id)
        elif order.state in {"payment_uncertain", "creating"}:
            if order.state == "payment_uncertain":
                order = self._reconcile_order_record(order)
            if order.state == "creating":
                try:
                    payment = self._gateway.create_jsapi_order(
                        out_trade_no=order.out_trade_no,
                        description=order.offer_title[:127],
                        payer_open_id=order.payer_open_id,
                        amount_minor=order.amount_minor,
                        currency=order.currency,
                        expires_at=order.expires_at,
                        attach=f"order:{order.order_id}",
                    )
                except WechatPayTransportUncertain as exc:
                    self._repository.mark_order_uncertain(order_id=order.order_id)
                    raise Stage8PaymentPending(
                        "payment order outcome requires reconciliation"
                    ) from exc
                self._repository.mark_order_awaiting_payment(
                    order_id=order.order_id, prepay_id=payment.prepay_id
                )
                order = self._repository.order_by_id(
                    user_id=user_id, order_id=order.order_id
                )
        return WechatOrderCreateResponse(order=_order_view(order, payment=payment))

    def require_ordering_enabled(self) -> None:
        if self._settings.commercial_mode != "paid":
            raise Stage8CommerceUnavailable(
                "commercial ordering is disabled during free cold start"
            )

    def list_orders(self, *, user_id: str) -> WechatOrderList:
        return WechatOrderList(
            items=[
                _order_view(item)
                for item in self._repository.list_orders(user_id=user_id)
            ]
        )

    def reconcile_order(self, *, user_id: str, order_id: UUID) -> WechatOrderView:
        order = self._repository.order_by_id(user_id=user_id, order_id=order_id)
        return _order_view(self._reconcile_order_record(order))

    def close_order(self, *, user_id: str, order_id: UUID) -> WechatOrderView:
        order = self._repository.order_by_id(user_id=user_id, order_id=order_id)
        if order.state in {
            "paid",
            "partially_refunded",
            "refund_processing",
            "refunded",
            "closed",
        }:
            return _order_view(order)
        reconciled = self._reconcile_order_record(order)
        if reconciled.paid_at is not None or reconciled.state == "closed":
            return _order_view(reconciled)
        try:
            self._gateway.close_order(out_trade_no=order.out_trade_no)
        except WechatPayTransportUncertain as exc:
            self._repository.mark_order_uncertain(order_id=order.order_id)
            raise Stage8PaymentPending("close outcome requires reconciliation") from exc
        self._repository.mark_order_closed(order_id=order.order_id, at=self._now())
        return _order_view(
            self._repository.order_by_id(user_id=user_id, order_id=order_id)
        )

    def payment_notification(
        self, *, headers: Mapping[str, str], body: bytes
    ) -> WechatNotificationAck:
        notification = self._gateway.verify_notification(headers=headers, body=body)
        if notification.event_type != "TRANSACTION.SUCCESS":
            raise WechatPayProtocolError("unexpected payment notification event")
        self._validate_notification_merchant(notification)
        inserted = self._repository.record_notification(
            notification_id=notification.notification_id,
            event_type=notification.event_type,
            platform_serial=notification.platform_serial,
            body_sha256=sha256(body).hexdigest(),
            resource_type=notification.resource_type,
            resource=notification.resource,
            received_at=self._now(),
        )
        if not inserted and self._repository.notification_processed(
            notification_id=notification.notification_id
        ):
            return WechatNotificationAck()
        try:
            order = self._repository.order_by_trade_no(
                out_trade_no=_required_string(notification.resource, "out_trade_no")
            )
            provider = _payment_notification_result(notification)
            self._apply_payment_query(order, provider)
            self._repository.mark_notification_processed(
                notification_id=notification.notification_id
            )
        except Exception as exc:
            self._repository.rollback()
            self._repository.mark_notification_processed(
                notification_id=notification.notification_id,
                error_code=type(exc).__name__,
            )
            raise
        return WechatNotificationAck()

    def refund_notification(
        self, *, headers: Mapping[str, str], body: bytes
    ) -> WechatNotificationAck:
        notification = self._gateway.verify_notification(headers=headers, body=body)
        expected = {"REFUND.SUCCESS", "REFUND.ABNORMAL", "REFUND.CLOSED"}
        if notification.event_type not in expected:
            raise WechatPayProtocolError("unexpected refund notification event")
        self._validate_notification_merchant(notification)
        inserted = self._repository.record_notification(
            notification_id=notification.notification_id,
            event_type=notification.event_type,
            platform_serial=notification.platform_serial,
            body_sha256=sha256(body).hexdigest(),
            resource_type=notification.resource_type,
            resource=notification.resource,
            received_at=self._now(),
        )
        if not inserted and self._repository.notification_processed(
            notification_id=notification.notification_id
        ):
            return WechatNotificationAck()
        try:
            refund = self._repository.refund_by_number(
                out_refund_no=_required_string(notification.resource, "out_refund_no")
            )
            result = _refund_notification_result(notification)
            self._apply_refund_result(refund, result)
            self._repository.mark_notification_processed(
                notification_id=notification.notification_id
            )
        except Exception as exc:
            self._repository.rollback()
            self._repository.mark_notification_processed(
                notification_id=notification.notification_id,
                error_code=type(exc).__name__,
            )
            raise
        return WechatNotificationAck()

    def refund_eligibility(self, *, user_id: str) -> RefundEligibility | None:
        order = self._repository.latest_paid_founder_order(user_id=user_id)
        return self._refund_eligibility(order) if order is not None else None

    def request_refund(
        self, *, user_id: str, order_id: UUID, request: WechatRefundRequest
    ) -> WechatRefundResponse:
        order = self._repository.order_by_id(user_id=user_id, order_id=order_id)
        eligibility = self._refund_eligibility(order)
        if not eligibility.eligible:
            raise Stage8RefundIneligible(eligibility.blocking_code or "not_eligible")
        amount = eligibility.remaining_refundable_minor
        fingerprint = _fingerprint(
            {
                "orderId": str(order_id),
                "reason": request.reason,
                "amountMinor": amount,
                "policyVersion": "founder-refund@v1",
            }
        )
        refund = self._repository.create_refund(
            order=order,
            command_key=request.command_key,
            request_fingerprint=fingerprint,
            refund_kind="not_suitable",
            reason="用户在购买后 30 个自然日内认为服务不适合；与成绩无关",
            amount_minor=amount,
            policy_version="founder-refund@v1",
            now=self._now(),
        )
        refund = self._submit_or_reconcile_refund(order=order, refund=refund)
        return WechatRefundResponse(
            refund=_refund_view(refund),
            entitlement=self._entitlement_service.get_entitlement(user_id=user_id),
        )

    def reward_progress(self, *, user_id: str) -> ProcessRewardProgress | None:
        order = self._repository.latest_paid_founder_order(user_id=user_id)
        if order is None:
            return None
        facts = self._repository.reward_facts(order=order, as_of=self._now())
        return _reward_progress(order, facts)

    def confirm_weekly_reflection(
        self, *, user_id: str, request: WeeklyReflectionConfirmRequest
    ) -> WeeklyReflectionConfirmationView:
        order = self._repository.order_by_id(user_id=user_id, order_id=request.order_id)
        confirmation_id, week_start, confirmed_at = (
            self._repository.confirm_weekly_reflection(
                user_id=user_id,
                command_key=request.command_key,
                order=order,
                report_version_id=request.report_version_id,
                now=self._now(),
            )
        )
        facts = self._reconcile_process_rewards(order)
        return WeeklyReflectionConfirmationView(
            confirmation_id=confirmation_id,
            week_start=week_start,
            confirmed_at=confirmed_at,
            progress=_reward_progress(order, facts),
        )

    def enroll_retake(
        self, *, user_id: str, request: RetakeEnrollmentRequest
    ) -> RetakeProgramResponse:
        record = self._repository.enroll_retake(
            user_id=user_id,
            command_key=request.command_key,
            source_score_evidence_id=request.source_score_evidence_id,
            target_exam_year=request.target_exam_year,
            now=self._now(),
        )
        return RetakeProgramResponse(program=_retake_view(record))

    def dashboard(self, *, user_id: str) -> CommerceDashboard:
        orders = self._repository.list_orders(user_id=user_id)
        latest = next((item for item in orders if item.paid_at is not None), None)
        facts = (
            self._repository.reward_facts(order=latest, as_of=self._now())
            if latest is not None
            else None
        )
        retake = self._repository.current_retake_program(user_id=user_id)
        return CommerceDashboard(
            catalog=self.catalog(),
            entitlement=self._entitlement_service.get_entitlement(user_id=user_id),
            orders=[_order_view(item) for item in orders],
            refund_eligibility=self._refund_eligibility(latest)
            if latest is not None
            else None,
            reward_progress=_reward_progress(latest, facts)
            if latest is not None and facts is not None
            else None,
            retake_program=_retake_view(retake) if retake is not None else None,
        )

    def reconcile_pending(self) -> dict[str, int]:
        now = self._now()
        orders_reconciled = 0
        orders_closed = 0
        refunds_reconciled = 0
        rewards_reconciled = 0
        for order in self._repository.pending_orders(now=now):
            reconciled = self._reconcile_order_record(order)
            if reconciled.paid_at is not None:
                orders_reconciled += 1
                continue
            if reconciled.state == "closed":
                orders_closed += 1
                continue
            if reconciled.expires_at <= now:
                try:
                    self._gateway.close_order(out_trade_no=reconciled.out_trade_no)
                except WechatPayTransportUncertain:
                    self._repository.mark_order_uncertain(order_id=reconciled.order_id)
                    continue
                self._repository.mark_order_closed(order_id=reconciled.order_id, at=now)
                orders_closed += 1
        for refund in self._repository.pending_refunds():
            order = self._repository.order_by_trade_no(
                out_trade_no=self._repository.order_trade_no(order_id=refund.order_id)
            )
            self._submit_or_reconcile_refund(order=order, refund=refund)
            refunds_reconciled += 1
        for order in self._repository.reward_candidate_orders(now=now):
            self._reconcile_process_rewards(order)
            rewards_reconciled += 1
        return {
            "ordersReconciled": orders_reconciled,
            "ordersClosed": orders_closed,
            "refundsReconciled": refunds_reconciled,
            "rewardsReconciled": rewards_reconciled,
        }

    def _offer_view(self, offer: OfferRecord) -> WechatOffer:
        policies = self._repository.policies(_offer_policy_versions(offer))
        state = (
            "sold_out"
            if offer.cohort_remaining == 0
            else "paused"
            if offer.refund_rate >= 0.20
            else "review_required"
            if offer.refund_rate >= 0.15
            else "open"
        )
        return WechatOffer(
            offer_version_id=offer.offer_version_id,
            offer_code=offer.offer_code,
            version_no=offer.version_no,
            product_id=offer.product_id,
            title=offer.title,
            subtitle=offer.subtitle,
            currency="CNY",
            amount_minor=offer.amount_minor,
            service_days=offer.service_days,
            automatic_renewal=False,
            cohort_limit=offer.cohort_limit,
            cohort_paid_count=offer.cohort_paid_count,
            cohort_reserved_count=offer.cohort_reserved_count,
            cohort_remaining=offer.cohort_remaining,
            recruitment_state=state,
            feature_highlights=offer.feature_highlights,
            policies=[
                CommercialPolicySnapshot(
                    policy_version=item.policy_version,
                    policy_kind=item.policy_kind,
                    title=item.title,
                    content=item.content,
                    content_sha256=item.content_sha256,
                )
                for item in policies
            ],
        )

    def _validate_notification_merchant(self, notification: WechatNotification) -> None:
        if notification.resource_type != "encrypt-resource":
            raise WechatPayProtocolError("unexpected WeChat notification resource type")
        if (
            _required_string(notification.resource, "mchid")
            != self._settings.wechat_pay_mch_id
        ):
            raise WechatPayProtocolError("notification merchant does not match runtime")
        app_id = notification.resource.get("appid")
        if app_id is not None and app_id != self._settings.wechat_mini_app_id:
            raise WechatPayProtocolError("notification app id does not match runtime")

    def _validate_policy_acceptances(
        self, offer: OfferRecord, accepted: PolicyAcceptances
    ) -> None:
        expected = {
            "service_terms_version": offer.service_terms_version,
            "refund_policy_version": offer.refund_policy_version,
            "reward_policy_version": offer.reward_policy_version,
            "marketing_policy_version": offer.marketing_policy_version,
        }
        if any(getattr(accepted, key) != value for key, value in expected.items()):
            raise Stage8CommerceConflict(
                "commercial policy version changed; accept current policies"
            )

    def _reconcile_order_record(self, order: OrderRecord) -> OrderRecord:
        if order.state in {
            "paid",
            "partially_refunded",
            "refund_processing",
            "refunded",
            "closed",
        }:
            return order
        provider = self._gateway.query_order(out_trade_no=order.out_trade_no)
        self._validate_payment(order, provider)
        if provider.trade_state == "SUCCESS":
            self._apply_payment_query(order, provider)
        elif provider.trade_state in {"CLOSED", "REVOKED", "PAYERROR"}:
            self._repository.mark_order_closed(order_id=order.order_id, at=self._now())
        return self._repository.order_by_trade_no(out_trade_no=order.out_trade_no)

    def _apply_payment_query(
        self, order: OrderRecord, provider: WechatOrderQueryResult
    ) -> None:
        self._validate_payment(order, provider)
        if (
            provider.trade_state != "SUCCESS"
            or not provider.transaction_id
            or not provider.success_time
        ):
            raise WechatPayProtocolError(
                "payment notification does not describe a successful trade"
            )
        service_days = int(order.terms_snapshot.get("serviceDays", 0))
        if service_days <= 0:
            raise Stage8CommerceConflict("order service term snapshot is invalid")
        self._repository.apply_paid_order(
            order=order,
            transaction_id=provider.transaction_id,
            paid_at=provider.success_time,
            provider_trade_state=provider.trade_state,
            service_days=service_days,
        )

    @staticmethod
    def _validate_payment(order: OrderRecord, provider: WechatOrderQueryResult) -> None:
        if provider.out_trade_no != order.out_trade_no:
            raise WechatPayProtocolError("provider order number mismatch")
        if (
            provider.amount_minor != order.amount_minor
            or provider.currency != order.currency
        ):
            raise WechatPayProtocolError("provider payment amount mismatch")
        if provider.payer_open_id and provider.payer_open_id != order.payer_open_id:
            raise WechatPayProtocolError("provider payer identity mismatch")

    def _refund_eligibility(self, order: OrderRecord) -> RefundEligibility:
        process_rewards = sum(
            item.amount_minor
            for item in self._refunds_for_order(order)
            if item.refund_kind == "weekly_reflection_reward"
            and item.state == "succeeded"
        )
        deadline = order.paid_at + timedelta(days=30) if order.paid_at else None
        remaining = max(order.amount_minor - order.refunded_minor, 0)
        code: str | None = None
        if order.paid_at is None:
            code = "order_not_paid"
        elif self._now() > deadline:
            code = "refund_window_expired"
        elif order.state == "refund_processing":
            code = "refund_already_processing"
        elif order.state == "refunded" or remaining == 0:
            code = "already_fully_refunded"
        elif order.state not in {"paid", "partially_refunded"}:
            code = "order_state_ineligible"
        return RefundEligibility(
            eligible=code is None,
            application_deadline=deadline,
            remaining_refundable_minor=remaining,
            prior_process_rewards_minor=process_rewards,
            blocking_code=code,
            score_evidence_required=False,
        )

    def _refunds_for_order(self, order: OrderRecord) -> list[RefundRecord]:
        return self._repository.list_refunds(order_id=order.order_id)

    def _submit_or_reconcile_refund(
        self, *, order: OrderRecord, refund: RefundRecord
    ) -> RefundRecord:
        try:
            result = (
                self._gateway.query_refund(out_refund_no=refund.out_refund_no)
                if refund.state in {"processing", "provider_uncertain"}
                else self._gateway.create_refund(
                    out_trade_no=order.out_trade_no,
                    out_refund_no=refund.out_refund_no,
                    amount_minor=refund.amount_minor,
                    total_minor=order.amount_minor,
                    currency=order.currency,
                    reason="服务不适合，按已接受规则退款",
                )
            )
        except WechatPayTransportUncertain as exc:
            self._repository.mark_refund_provider_state(
                refund=refund,
                state="provider_uncertain",
                provider_refund_id=None,
                provider_status="UNKNOWN",
                processed_at=None,
            )
            raise Stage8PaymentPending(
                "refund outcome requires reconciliation"
            ) from exc
        self._apply_refund_result(refund, result)
        return self._repository.refund_by_number(out_refund_no=refund.out_refund_no)

    def _apply_refund_result(
        self, refund: RefundRecord, result: WechatRefundResult
    ) -> None:
        if result.out_refund_no != refund.out_refund_no:
            raise WechatPayProtocolError("provider refund number mismatch")
        if (
            result.amount_minor != refund.amount_minor
            or result.currency != refund.currency
        ):
            raise WechatPayProtocolError("provider refund amount mismatch")
        state = {
            "SUCCESS": "succeeded",
            "PROCESSING": "processing",
            "CLOSED": "closed",
            "ABNORMAL": "abnormal",
        }.get(result.status)
        if state is None:
            raise WechatPayProtocolError("provider returned unsupported refund status")
        self._repository.mark_refund_provider_state(
            refund=refund,
            state=state,
            provider_refund_id=result.refund_id,
            provider_status=result.status,
            processed_at=result.success_time if state == "succeeded" else None,
        )
        if refund.refund_kind == "weekly_reflection_reward":
            order = self._repository.order_by_trade_no(
                out_trade_no=self._repository.order_trade_no(order_id=refund.order_id)
            )
            self._repository.mark_weekly_cash_award(
                order=order,
                refund_id=refund.refund_id,
                evidence_refs=[f"weekly-reflection-order:{order.order_id}"],
                state="granted" if state == "succeeded" else "processing",
                now=self._now(),
            )

    def _reconcile_process_rewards(self, order: OrderRecord) -> RewardFacts:
        facts = self._repository.reward_facts(order=order, as_of=self._now())
        if facts.effective_days >= 20 and "membership_15d" not in facts.awarded_rewards:
            self._repository.grant_membership_reward(
                order=order,
                evidence_refs=[
                    f"effective-days:{facts.effective_days}",
                    f"order:{order.order_id}",
                ],
                now=self._now(),
            )
        ratio = (
            facts.core_tasks_completed / facts.core_tasks_due
            if facts.core_tasks_due > 0
            else None
        )
        if (
            ratio is not None
            and ratio >= 0.8
            and "special_diagnostic" not in facts.awarded_rewards
        ):
            self._repository.grant_special_diagnostic(
                order=order,
                evidence_refs=[
                    f"core-completion:{facts.core_tasks_completed}/{facts.core_tasks_due}"
                ],
                now=self._now(),
            )
        if (
            facts.weekly_reflections >= 4
            and "weekly_reflection_cny30" not in facts.awarded_rewards
        ):
            if order.state not in {"paid", "partially_refunded", "refund_processing"}:
                return self._repository.reward_facts(order=order, as_of=self._now())
            if order.amount_minor - order.refunded_minor < 3000:
                return self._repository.reward_facts(order=order, as_of=self._now())
            existing = next(
                (
                    item
                    for item in self._refunds_for_order(order)
                    if item.refund_kind == "weekly_reflection_reward"
                ),
                None,
            )
            refund = existing or self._repository.create_refund(
                order=order,
                command_key=f"weekly-reward:{order.order_id}",
                request_fingerprint=_fingerprint(
                    {
                        "orderId": str(order.order_id),
                        "rewardMinor": 3000,
                        "weeklyReflections": 4,
                    }
                ),
                refund_kind="weekly_reflection_reward",
                reason="完成四次周复盘的学习过程奖励",
                amount_minor=3000,
                policy_version="founder-process-reward@v1",
                now=self._now(),
            )
            if refund.state not in {"succeeded", "closed", "abnormal"}:
                self._submit_or_reconcile_refund(order=order, refund=refund)
        return self._repository.reward_facts(order=order, as_of=self._now())


def _order_view(
    order: OrderRecord, *, payment: WechatPrepayResult | None = None
) -> WechatOrderView:
    return WechatOrderView(
        order_id=order.order_id,
        out_trade_no=order.out_trade_no,
        offer_version_id=order.offer_version_id,
        title=order.offer_title,
        currency="CNY",
        amount_minor=order.amount_minor,
        refunded_minor=order.refunded_minor,
        state=order.state,
        expires_at=order.expires_at,
        paid_at=order.paid_at,
        created_at=order.created_at,
        terms_snapshot_sha256=order.terms_snapshot_sha256,
        payment=(
            WechatPaymentParameters(
                time_stamp=payment.time_stamp,
                nonce_str=payment.nonce_str,
                package=payment.package,
                sign_type="RSA",
                pay_sign=payment.pay_sign,
            )
            if payment is not None
            else None
        ),
    )


def _refund_view(refund: RefundRecord) -> WechatRefundView:
    return WechatRefundView(
        refund_id=refund.refund_id,
        order_id=refund.order_id,
        out_refund_no=refund.out_refund_no,
        refund_kind=refund.refund_kind,
        amount_minor=refund.amount_minor,
        currency="CNY",
        state=refund.state,
        policy_version=refund.policy_version,
        requested_at=refund.requested_at,
        processed_at=refund.processed_at,
    )


def _reward_progress(order: OrderRecord, facts: RewardFacts) -> ProcessRewardProgress:
    ratio = (
        facts.core_tasks_completed / facts.core_tasks_due
        if facts.core_tasks_due > 0
        else None
    )
    return ProcessRewardProgress(
        order_id=order.order_id,
        policy_version="founder-process-reward@v1",
        effective_days=facts.effective_days,
        effective_days_required=20,
        weekly_reflections=facts.weekly_reflections,
        weekly_reflections_required=4,
        core_tasks_completed=facts.core_tasks_completed,
        core_tasks_due=facts.core_tasks_due,
        core_completion_ratio=ratio,
        awarded_rewards=facts.awarded_rewards,
        pending_rewards=facts.pending_rewards,
        score_fields_used=False,
    )


def _retake_view(record: Any) -> RetakeProgramView:
    loss = record.loss_structure
    return RetakeProgramView(
        program_version_id=record.program_version_id,
        source_score_evidence_id=record.source_score_evidence_id,
        source_exam_year=record.source_exam_year,
        target_exam_year=record.target_exam_year,
        state=record.state,
        loss_structure=RetakeLossStructure.model_validate(loss),
        refund_uses_score=False,
        reward_uses_score=False,
        training_uses_score=True,
        created_at=record.created_at,
    )


def _offer_policy_versions(offer: OfferRecord) -> tuple[str, ...]:
    return (
        offer.service_terms_version,
        offer.refund_policy_version,
        offer.reward_policy_version,
        offer.marketing_policy_version,
    )


def _payment_notification_result(
    notification: WechatNotification,
) -> WechatOrderQueryResult:
    resource = notification.resource
    amount = _required_mapping(resource, "amount")
    payer = _required_mapping(resource, "payer")
    return WechatOrderQueryResult(
        out_trade_no=_required_string(resource, "out_trade_no"),
        trade_state=_required_string(resource, "trade_state"),
        transaction_id=_required_string(resource, "transaction_id"),
        success_time=_required_datetime(resource, "success_time"),
        amount_minor=_required_int(amount, "total"),
        currency=_required_string(amount, "currency"),
        payer_open_id=_required_string(payer, "openid"),
    )


def _refund_notification_result(notification: WechatNotification) -> WechatRefundResult:
    resource = notification.resource
    amount = _required_mapping(resource, "amount")
    return WechatRefundResult(
        refund_id=_required_string(resource, "refund_id"),
        out_refund_no=_required_string(resource, "out_refund_no"),
        status=_required_string(resource, "refund_status"),
        amount_minor=_required_int(amount, "refund"),
        currency=_required_string(amount, "currency"),
        success_time=(
            _required_datetime(resource, "success_time")
            if resource.get("success_time")
            else None
        ),
    )


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise WechatPayProtocolError(f"provider field {key} must be an object")
    return value


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise WechatPayProtocolError(f"provider field {key} is missing")
    return value


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WechatPayProtocolError(f"provider field {key} must be an integer")
    return value


def _required_datetime(payload: Mapping[str, Any], key: str) -> datetime:
    value = _required_string(payload, key)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise WechatPayProtocolError(f"provider field {key} is not RFC3339") from exc


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode("utf-8")).hexdigest()
