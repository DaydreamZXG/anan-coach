from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.core.config import CommercialMode
from app.domains.commerce.schemas import CommerceBaseModel, CommerceEntitlementResponse


class CommercialPolicySnapshot(CommerceBaseModel):
    policy_version: str = Field(alias="policyVersion")
    policy_kind: Literal["service_terms", "refund", "process_reward", "marketing"] = (
        Field(alias="policyKind")
    )
    title: str
    content: dict[str, object]
    content_sha256: str = Field(alias="contentSha256", min_length=64, max_length=64)


class WechatOffer(CommerceBaseModel):
    offer_version_id: UUID = Field(alias="offerVersionId")
    offer_code: str = Field(alias="offerCode")
    version_no: int = Field(alias="versionNo", ge=1)
    product_id: str = Field(alias="productId")
    title: str
    subtitle: str
    currency: Literal["CNY"]
    amount_minor: int = Field(alias="amountMinor", gt=0)
    service_days: int = Field(alias="serviceDays", gt=0)
    automatic_renewal: Literal[False] = Field(alias="automaticRenewal")
    cohort_limit: int | None = Field(default=None, alias="cohortLimit", gt=0)
    cohort_paid_count: int = Field(alias="cohortPaidCount", ge=0)
    cohort_reserved_count: int = Field(alias="cohortReservedCount", ge=0)
    cohort_remaining: int | None = Field(default=None, alias="cohortRemaining", ge=0)
    recruitment_state: Literal["open", "review_required", "paused", "sold_out"] = Field(
        alias="recruitmentState"
    )
    feature_highlights: list[str] = Field(alias="featureHighlights")
    policies: list[CommercialPolicySnapshot]


class Stage8Catalog(CommerceBaseModel):
    commercial_mode: CommercialMode = Field(alias="commercialMode")
    ordering_enabled: bool = Field(alias="orderingEnabled")
    offers: list[WechatOffer]
    server_time: datetime = Field(alias="serverTime")


class PolicyAcceptances(CommerceBaseModel):
    service_terms_version: str = Field(alias="serviceTermsVersion")
    refund_policy_version: str = Field(alias="refundPolicyVersion")
    reward_policy_version: str = Field(alias="rewardPolicyVersion")
    marketing_policy_version: str = Field(alias="marketingPolicyVersion")


class WechatOrderCreateRequest(CommerceBaseModel):
    command_key: str = Field(alias="commandKey", min_length=8, max_length=160)
    offer_version_id: UUID = Field(alias="offerVersionId")
    expected_amount_minor: int = Field(alias="expectedAmountMinor", gt=0)
    accepted_policies: PolicyAcceptances = Field(alias="acceptedPolicies")


WechatClientPlatform = Literal["ios", "android", "harmony", "windows"]


class VirtualPaymentOrderCreateRequest(CommerceBaseModel):
    command_key: str = Field(alias="commandKey", min_length=8, max_length=160)
    offer_version_id: UUID = Field(alias="offerVersionId")
    expected_amount_minor: int = Field(alias="expectedAmountMinor", gt=0)
    accepted_policies: PolicyAcceptances = Field(alias="acceptedPolicies")
    payment_login_code: str = Field(
        alias="paymentLoginCode", min_length=1, max_length=256
    )
    client_platform: WechatClientPlatform = Field(alias="clientPlatform")


class WechatPaymentParameters(CommerceBaseModel):
    time_stamp: str = Field(alias="timeStamp")
    nonce_str: str = Field(alias="nonceStr")
    package: str
    sign_type: Literal["RSA"] = Field(alias="signType")
    pay_sign: str = Field(alias="paySign")


class WechatVirtualPaymentParameters(CommerceBaseModel):
    sign_data: str = Field(alias="signData", min_length=2)
    pay_sig: str = Field(alias="paySig", min_length=64, max_length=64)
    signature: str = Field(min_length=64, max_length=64)
    mode: Literal["short_series_goods"]


WechatOrderState = Literal[
    "creating",
    "awaiting_payment",
    "paid",
    "partially_refunded",
    "refund_processing",
    "refunded",
    "closed",
    "payment_uncertain",
]


class WechatOrderView(CommerceBaseModel):
    order_id: UUID = Field(alias="orderId")
    out_trade_no: str = Field(alias="outTradeNo")
    offer_version_id: UUID = Field(alias="offerVersionId")
    title: str
    currency: Literal["CNY"]
    amount_minor: int = Field(alias="amountMinor")
    refunded_minor: int = Field(alias="refundedMinor")
    state: WechatOrderState
    expires_at: datetime = Field(alias="expiresAt")
    paid_at: datetime | None = Field(default=None, alias="paidAt")
    created_at: datetime = Field(alias="createdAt")
    terms_snapshot_sha256: str = Field(alias="termsSnapshotSha256")
    payment: WechatPaymentParameters | None = None
    payment_channel: Literal["wechat_jsapi", "wechat_virtual_payment"] = Field(
        default="wechat_jsapi", alias="paymentChannel"
    )
    client_platform: WechatClientPlatform | None = Field(
        default=None, alias="clientPlatform"
    )
    virtual_payment: WechatVirtualPaymentParameters | None = Field(
        default=None, alias="virtualPayment"
    )


class WechatOrderCreateResponse(CommerceBaseModel):
    order: WechatOrderView


class WechatOrderList(CommerceBaseModel):
    items: list[WechatOrderView]


class WechatRefundRequest(CommerceBaseModel):
    command_key: str = Field(alias="commandKey", min_length=8, max_length=160)
    reason: Literal["not_suitable"]


WechatRefundState = Literal[
    "requested", "processing", "succeeded", "closed", "abnormal", "provider_uncertain"
]


class WechatRefundView(CommerceBaseModel):
    refund_id: UUID = Field(alias="refundId")
    order_id: UUID = Field(alias="orderId")
    out_refund_no: str = Field(alias="outRefundNo")
    refund_kind: Literal[
        "not_suitable", "weekly_reflection_reward", "late_payment_reversal"
    ] = Field(alias="refundKind")
    amount_minor: int = Field(alias="amountMinor", gt=0)
    currency: Literal["CNY"]
    state: WechatRefundState
    policy_version: str = Field(alias="policyVersion")
    requested_at: datetime = Field(alias="requestedAt")
    processed_at: datetime | None = Field(default=None, alias="processedAt")


class WechatRefundResponse(CommerceBaseModel):
    refund: WechatRefundView
    entitlement: CommerceEntitlementResponse
    provider_action: Literal["none", "app_store_required"] = Field(
        default="none", alias="providerAction"
    )
    provider_action_url: str | None = Field(default=None, alias="providerActionUrl")
    provider_action_message: str | None = Field(
        default=None, alias="providerActionMessage"
    )


class WechatNotificationAck(CommerceBaseModel):
    code: Literal["SUCCESS"] = "SUCCESS"
    message: Literal["成功"] = "成功"


class ProcessRewardProgress(CommerceBaseModel):
    order_id: UUID = Field(alias="orderId")
    policy_version: str = Field(alias="policyVersion")
    effective_days: int = Field(alias="effectiveDays", ge=0)
    effective_days_required: int = Field(alias="effectiveDaysRequired", gt=0)
    weekly_reflections: int = Field(alias="weeklyReflections", ge=0)
    weekly_reflections_required: int = Field(alias="weeklyReflectionsRequired", gt=0)
    core_tasks_completed: int = Field(alias="coreTasksCompleted", ge=0)
    core_tasks_due: int = Field(alias="coreTasksDue", ge=0)
    core_completion_ratio: float | None = Field(
        default=None, alias="coreCompletionRatio"
    )
    awarded_rewards: list[str] = Field(alias="awardedRewards")
    pending_rewards: list[str] = Field(alias="pendingRewards")
    score_fields_used: Literal[False] = Field(alias="scoreFieldsUsed")


class WeeklyReflectionConfirmRequest(CommerceBaseModel):
    command_key: str = Field(alias="commandKey", min_length=8, max_length=160)
    order_id: UUID = Field(alias="orderId")
    report_version_id: UUID = Field(alias="reportVersionId")


class WeeklyReflectionConfirmationView(CommerceBaseModel):
    confirmation_id: UUID = Field(alias="confirmationId")
    week_start: date = Field(alias="weekStart")
    confirmed_at: datetime = Field(alias="confirmedAt")
    progress: ProcessRewardProgress


class RefundEligibility(CommerceBaseModel):
    eligible: bool
    application_deadline: datetime | None = Field(
        default=None, alias="applicationDeadline"
    )
    remaining_refundable_minor: int = Field(alias="remainingRefundableMinor", ge=0)
    prior_process_rewards_minor: int = Field(alias="priorProcessRewardsMinor", ge=0)
    blocking_code: str | None = Field(default=None, alias="blockingCode")
    score_evidence_required: Literal[False] = Field(alias="scoreEvidenceRequired")


class CommerceDashboard(CommerceBaseModel):
    catalog: Stage8Catalog
    entitlement: CommerceEntitlementResponse
    orders: list[WechatOrderView]
    refund_eligibility: RefundEligibility | None = Field(
        default=None, alias="refundEligibility"
    )
    reward_progress: ProcessRewardProgress | None = Field(
        default=None, alias="rewardProgress"
    )
    @model_validator(mode="after")
    def active_order_has_commercial_state(self) -> "CommerceDashboard":
        if self.reward_progress is not None and not any(
            item.order_id == self.reward_progress.order_id for item in self.orders
        ):
            raise ValueError("reward progress must belong to a returned order")
        return self
