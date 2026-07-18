from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from app.domains.commerce.app_store import (
    AppStoreTransactionVerificationError,
    AppStoreSignedTransactionVerifier,
    VerifiedAppStoreTransaction,
)
from app.domains.commerce.catalog import FEATURE_QUOTAS, quota_for_feature
from app.domains.commerce.repository import (
    CommerceQuotaExceededError,
    CommerceRepository,
    FeatureUsageReservation,
    ProductRecord,
)
from app.domains.commerce.schemas import (
    AppStoreTransactionRequest,
    AppStoreTransactionResponse,
    AppStoreNotificationRequest,
    AppStoreNotificationResponse,
    CommerceCatalogResponse,
    CommerceEntitlementResponse,
    CommerceFeatureQuota,
    CommerceProduct,
)


class CommerceProductNotFoundError(LookupError):
    pass


class CommerceTransactionMismatchError(RuntimeError):
    pass


class CommerceService:
    def __init__(
        self,
        *,
        repository: CommerceRepository,
        app_store_verifier: AppStoreSignedTransactionVerifier,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._app_store_verifier = app_store_verifier
        self._now = now or (lambda: datetime.now(UTC))

    def get_catalog(self) -> CommerceCatalogResponse:
        self._repository.sync_product_catalog()
        return CommerceCatalogResponse(
            products=[_product_response(product) for product in self._repository.list_products()],
            free_quotas=_quota_responses(tier="free", usage={}),
            pro_quotas=_quota_responses(tier="pro", usage={}),
        )

    def get_entitlement(self, *, user_id: str) -> CommerceEntitlementResponse:
        self._repository.sync_product_catalog()
        now = self._now()
        entitlement = self._repository.get_entitlement(user_id=user_id, at=now)
        usage = self._repository.list_usage_for_periods(
            user_id=user_id,
            usage_dates_by_feature={
                quota.feature: _usage_period_start(period=quota.period, now=now)
                for quota in FEATURE_QUOTAS
            },
        )
        return CommerceEntitlementResponse(
            tier="pro" if entitlement.tier == "pro" and entitlement.is_active else "free",
            source=entitlement.source,
            is_active=entitlement.is_active,
            subscription_start=entitlement.subscription_start,
            subscription_end=entitlement.subscription_end,
            feature_quotas=_quota_responses(tier=entitlement.tier, usage=usage),
            product_ids=entitlement.product_ids,
            unlocked_features=self._repository.unlocked_features(user_id=user_id),
            server_time=now,
        )

    def reserve_feature_usage(
        self,
        *,
        user_id: str,
        feature: str,
        reservation_key: str,
        business_key: str,
        server_session_ref: str | None = None,
        commit: bool = True,
    ) -> FeatureUsageReservation:
        now = self._now()
        entitlement = self._repository.get_entitlement(user_id=user_id, at=now)
        tier = "pro" if entitlement.tier == "pro" and entitlement.is_active else "free"
        quota = quota_for_feature(feature)
        usage_date = _usage_period_start(period=quota.period, now=now)
        try:
            return self._repository.reserve_feature_usage(
                user_id=user_id,
                feature=feature,
                reservation_key=reservation_key,
                business_key=business_key,
                usage_date=usage_date,
                limit=quota.pro_limit if tier == "pro" else quota.free_limit,
                tier=tier,
                server_session_ref=server_session_ref,
                commit=commit,
            )
        except CommerceQuotaExceededError as exc:
            reward_reservation = self._repository.reserve_reward_feature_usage(
                user_id=user_id,
                feature=feature,
                reservation_key=reservation_key,
                business_key=business_key,
                server_session_ref=(
                    server_session_ref or f"commerce-session:{business_key}"
                ),
                reserved_at=now,
                commit=commit,
            )
            if reward_reservation is not None:
                return reward_reservation
            raise CommerceQuotaExceededError(
                exc.feature,
                used=exc.used,
                limit=exc.limit,
                usage_date=exc.usage_date or usage_date,
                retry_after=_next_usage_period_start(
                    period=quota.period,
                    usage_date=exc.usage_date or usage_date,
                ),
            ) from exc

    def activate_reward_membership(
        self,
        *,
        award_id: UUID,
        beneficiary_user_id: UUID,
        activation_key: str,
        membership_entitlement_ref: str,
        purchase_history_snapshot_ref: str,
        evidence_refs: tuple[str, ...],
        commit: bool = True,
    ):
        if not evidence_refs:
            raise ValueError("membership reward activation requires evidence")
        return self._repository.activate_reward_membership(
            award_id=award_id,
            beneficiary_user_id=beneficiary_user_id,
            activation_key=activation_key,
            membership_entitlement_ref=membership_entitlement_ref,
            purchase_history_snapshot_ref=purchase_history_snapshot_ref,
            evidence_refs=evidence_refs,
            activated_at=self._now(),
            commit=commit,
        )

    def refund_feature_usage(
        self,
        *,
        reservation: FeatureUsageReservation,
        commit: bool = True,
    ) -> FeatureUsageReservation:
        return self._repository.refund_feature_usage(
            reservation_key=reservation.reservation_key,
            commit=commit,
        )

    def consume_feature_usage(
        self,
        *,
        reservation: FeatureUsageReservation,
        commit: bool = True,
    ) -> FeatureUsageReservation:
        return self._repository.consume_feature_usage(
            reservation_key=reservation.reservation_key,
            commit=commit,
        )

    def bind_feature_usage_reservation_job(
        self,
        *,
        reservation: FeatureUsageReservation,
        job_id: UUID,
        commit: bool = True,
    ) -> FeatureUsageReservation:
        return self._repository.bind_feature_usage_reservation_job(
            reservation_key=reservation.reservation_key,
            job_id=job_id,
            commit=commit,
        )

    def register_app_store_transaction(
        self,
        *,
        user_id: str,
        request: AppStoreTransactionRequest,
    ) -> AppStoreTransactionResponse:
        self._repository.sync_product_catalog()
        product = self._repository.get_product(request.product_id)
        if product is None:
            raise CommerceProductNotFoundError(request.product_id)

        transaction = self._app_store_verifier.verify(request.signed_transaction_info)
        if transaction.product_id != request.product_id:
            raise CommerceTransactionMismatchError("productId does not match signed transaction")
        if transaction.transaction_id != request.transaction_id:
            raise CommerceTransactionMismatchError("transactionId does not match signed transaction")
        if transaction.original_transaction_id != request.original_transaction_id:
            raise CommerceTransactionMismatchError(
                "originalTransactionId does not match signed transaction"
            )
        if not _transaction_belongs_to_user(transaction=transaction, user_id=user_id):
            raise CommerceTransactionMismatchError(
                "appAccountToken does not match authenticated user"
            )

        verification_status = _transaction_status(transaction)
        self._repository.record_verified_transaction(
            user_id=user_id,
            product_id=transaction.product_id,
            original_transaction_id=transaction.original_transaction_id,
            transaction_id=transaction.transaction_id,
            web_order_line_item_id=transaction.web_order_line_item_id,
            environment=transaction.environment,
            purchase_date=transaction.purchase_date,
            expires_date=transaction.expires_date,
            revocation_date=transaction.revocation_date,
            signed_transaction_info=request.signed_transaction_info,
            verification_status=verification_status,
        )
        if verification_status == "verified_active":
            if transaction.expires_date is None:
                raise AppStoreTransactionVerificationError(
                    "Subscription transaction must include expiresDate"
                )
        self._repository.reconcile_pro_entitlement(user_id=user_id)
        self._repository.log_entitlement_event(
            user_id=user_id,
            action="app_store_transaction_registered",
            feature=None,
            allowed=verification_status == "verified_active",
            metadata={
                "productId": transaction.product_id,
                "transactionId": transaction.transaction_id,
                "verificationStatus": verification_status,
            },
            commit=True,
        )
        return AppStoreTransactionResponse(
            transaction_id=transaction.transaction_id,
            original_transaction_id=transaction.original_transaction_id,
            verification_status=verification_status,  # type: ignore[arg-type]
            entitlement=self.get_entitlement(user_id=user_id),
        )

    def register_app_store_notification(
        self,
        *,
        request: AppStoreNotificationRequest,
    ) -> AppStoreNotificationResponse:
        self._repository.sync_product_catalog()
        notification = self._app_store_verifier.verify_notification(request.signed_payload)
        inserted = self._repository.record_notification_event(
            notification_uuid=notification.notification_uuid,
            notification_type=notification.notification_type,
            subtype=notification.subtype,
            signed_payload=request.signed_payload,
        )
        if not inserted:
            self._repository.commit()
            return AppStoreNotificationResponse(
                notification_uuid=notification.notification_uuid,
                notification_type=notification.notification_type,
                processed=False,
            )

        processed = False
        event_user_id: str | None = None
        if notification.signed_transaction_info:
            transaction = self._app_store_verifier.verify(notification.signed_transaction_info)
            product = self._repository.get_product(transaction.product_id)
            if product is not None and transaction.app_account_token is not None:
                verification_status = _transaction_status(transaction)
                user_id = str(UUID(transaction.app_account_token))
                event_user_id = user_id
                self._repository.record_verified_transaction(
                    user_id=user_id,
                    product_id=transaction.product_id,
                    original_transaction_id=transaction.original_transaction_id,
                    transaction_id=transaction.transaction_id,
                    web_order_line_item_id=transaction.web_order_line_item_id,
                    environment=transaction.environment,
                    purchase_date=transaction.purchase_date,
                    expires_date=transaction.expires_date,
                    revocation_date=transaction.revocation_date,
                    signed_transaction_info=notification.signed_transaction_info,
                    verification_status=verification_status,
                )
                if verification_status == "verified_active" and transaction.expires_date is None:
                    raise AppStoreTransactionVerificationError(
                        "Subscription transaction must include expiresDate"
                    )
                self._repository.reconcile_pro_entitlement(user_id=user_id)
                processed = True
        if processed:
            self._repository.mark_notification_event_processed(
                notification_uuid=notification.notification_uuid,
            )
        if event_user_id is not None:
            self._repository.log_entitlement_event(
                user_id=event_user_id,
                action="app_store_notification_received",
                feature=None,
                allowed=processed,
                metadata={
                    "notificationUUID": notification.notification_uuid,
                    "notificationType": notification.notification_type,
                    "subtype": notification.subtype,
                    "inserted": inserted,
                    "processed": processed,
                },
                commit=True,
            )
        else:
            self._repository.commit()
        return AppStoreNotificationResponse(
            notification_uuid=notification.notification_uuid,
            notification_type=notification.notification_type,
            processed=processed,
        )


def _transaction_status(transaction: VerifiedAppStoreTransaction) -> str:
    if transaction.revocation_date is not None:
        return "verified_revoked"
    if transaction.expires_date is not None and transaction.expires_date <= datetime.now(UTC):
        return "verified_expired"
    return "verified_active"


def _usage_period_start(*, period: str, now: datetime) -> date:
    if period == "day":
        return now.date()
    if period == "month":
        return now.date().replace(day=1)
    raise ValueError(f"Unsupported commerce quota period: {period}")


def _next_usage_period_start(*, period: str, usage_date: date) -> datetime:
    if period == "day":
        next_date = usage_date + timedelta(days=1)
    elif period == "month":
        next_date = (usage_date.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        raise ValueError(f"Unsupported commerce quota period: {period}")
    return datetime.combine(next_date, time.min, tzinfo=UTC)


def _transaction_belongs_to_user(
    *,
    transaction: VerifiedAppStoreTransaction,
    user_id: str,
) -> bool:
    if transaction.app_account_token is None:
        return False
    try:
        return UUID(transaction.app_account_token) == UUID(user_id)
    except ValueError:
        return False


def _product_response(product: ProductRecord) -> CommerceProduct:
    return CommerceProduct(
        product_id=product.product_id,
        tier=product.tier,  # type: ignore[arg-type]
        billing_period=product.billing_period,  # type: ignore[arg-type]
        title=product.title,
        subtitle=product.subtitle,
        price_band=product.price_band,
        sort_order=product.sort_order,
        feature_highlights=product.feature_highlights,
    )


def _quota_responses(
    *,
    tier: str,
    usage: dict[str, int],
) -> list[CommerceFeatureQuota]:
    responses: list[CommerceFeatureQuota] = []
    for quota in FEATURE_QUOTAS:
        limit = quota.pro_limit if tier == "pro" else quota.free_limit
        used = usage.get(quota.feature, 0)
        responses.append(
            CommerceFeatureQuota(
                feature=quota.feature,
                title=quota.title,
                limit=limit,
                used=used,
                remaining=max(0, limit - used),
                period=quota.period,  # type: ignore[arg-type]
            )
        )
    return responses
