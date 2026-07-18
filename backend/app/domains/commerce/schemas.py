from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommerceBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


CommerceTier = Literal["free", "pro"]
CommerceBillingPeriod = Literal["monthly", "yearly"]
CommerceQuotaPeriod = Literal["day", "month"]
CommerceAccessMode = Literal["unlimited", "entitlement_quota"]
CommerceTransactionStatus = Literal[
    "verified_active",
    "verified_expired",
    "verified_revoked",
]


class CommerceFeatureQuota(CommerceBaseModel):
    feature: str
    title: str
    access_mode: CommerceAccessMode = Field(alias="accessMode")
    limit: int | None
    used: int
    remaining: int | None
    period: CommerceQuotaPeriod


class CommerceProduct(CommerceBaseModel):
    product_id: str = Field(alias="productId")
    tier: CommerceTier
    billing_period: CommerceBillingPeriod = Field(alias="billingPeriod")
    title: str
    subtitle: str
    price_band: str = Field(alias="priceBand")
    sort_order: int = Field(alias="sortOrder")
    feature_highlights: list[str] = Field(alias="featureHighlights")


class CommerceCatalogResponse(CommerceBaseModel):
    commercial_mode: Literal["free_cold_start", "paid"] = Field(alias="commercialMode")
    purchasing_enabled: bool = Field(alias="purchasingEnabled")
    products: list[CommerceProduct]
    free_quotas: list[CommerceFeatureQuota] = Field(alias="freeQuotas")
    pro_quotas: list[CommerceFeatureQuota] = Field(alias="proQuotas")


class CommerceEntitlementResponse(CommerceBaseModel):
    tier: CommerceTier
    source: str
    is_active: bool = Field(alias="isActive")
    subscription_start: datetime | None = Field(default=None, alias="subscriptionStart")
    subscription_end: datetime | None = Field(default=None, alias="subscriptionEnd")
    feature_quotas: list[CommerceFeatureQuota] = Field(alias="featureQuotas")
    product_ids: list[str] = Field(alias="productIds")
    unlocked_features: list[str] = Field(default_factory=list, alias="unlockedFeatures")
    server_time: datetime = Field(alias="serverTime")


class AppStoreTransactionRequest(CommerceBaseModel):
    product_id: str = Field(alias="productId", min_length=3, max_length=120)
    transaction_id: str = Field(alias="transactionId", min_length=3, max_length=120)
    original_transaction_id: str = Field(
        alias="originalTransactionId", min_length=3, max_length=120
    )
    signed_transaction_info: str = Field(alias="signedTransactionInfo", min_length=32)


class AppStoreTransactionResponse(CommerceBaseModel):
    transaction_id: str = Field(alias="transactionId")
    original_transaction_id: str = Field(alias="originalTransactionId")
    verification_status: CommerceTransactionStatus = Field(alias="verificationStatus")
    entitlement: CommerceEntitlementResponse


class AppStoreNotificationRequest(CommerceBaseModel):
    signed_payload: str = Field(alias="signedPayload", min_length=32)


class AppStoreNotificationResponse(CommerceBaseModel):
    notification_uuid: str = Field(alias="notificationUUID")
    notification_type: str = Field(alias="notificationType")
    processed: bool
