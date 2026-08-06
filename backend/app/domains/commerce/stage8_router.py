from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.commerce.deps import (
    get_stage8_commerce_service,
    get_stage8_virtual_payment_service,
)
from app.domains.commerce.stage8_repository import (
    Stage8CommerceConflict,
    Stage8CommerceNotFound,
)
from app.domains.commerce.stage8_schemas import (
    CommerceDashboard,
    ProcessRewardProgress,
    RefundEligibility,
    Stage8Catalog,
    VirtualPaymentOrderCreateRequest,
    WechatOrderCreateResponse,
    WechatOrderList,
    WechatOrderView,
    WechatRefundRequest,
    WechatRefundResponse,
    WeeklyReflectionConfirmationView,
    WeeklyReflectionConfirmRequest,
)
from app.domains.commerce.wechat_virtual_payment import Stage8VirtualPaymentService
from app.domains.commerce.stage8_service import (
    Stage8CommerceService,
    Stage8CommerceUnavailable,
    Stage8PaymentPending,
    Stage8RefundIneligible,
)
from app.domains.commerce.wechat_pay import (
    WechatPayConfigurationError,
    WechatPayProtocolError,
)
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/commerce",
    tags=["commerce-v2"],
    responses=COMMON_API_ERROR_RESPONSES,
)


@router.get("/catalog", response_model=PublicApiEnvelope[Stage8Catalog])
def get_catalog(
    request: Request,
    response: Response,
    service: Annotated[Stage8CommerceService, Depends(get_stage8_commerce_service)],
) -> PublicApiEnvelope[Stage8Catalog]:
    return public_success_envelope(
        request=request, response=response, data=service.catalog()
    )


@router.get("/dashboard", response_model=PublicApiEnvelope[CommerceDashboard])
def get_dashboard(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[Stage8CommerceService, Depends(get_stage8_commerce_service)],
) -> PublicApiEnvelope[CommerceDashboard]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.dashboard(user_id=current_user.user_id),
    )


@router.post(
    "/virtual-payment/orders",
    response_model=PublicApiEnvelope[WechatOrderCreateResponse],
)
def create_virtual_payment_order(
    body: VirtualPaymentOrderCreateRequest,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        Stage8VirtualPaymentService,
        Depends(get_stage8_virtual_payment_service),
    ],
) -> PublicApiEnvelope[WechatOrderCreateResponse]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.create_order(user_id=current_user.user_id, request=body),
    )


@router.get("/orders", response_model=PublicApiEnvelope[WechatOrderList])
def list_orders(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[Stage8CommerceService, Depends(get_stage8_commerce_service)],
) -> PublicApiEnvelope[WechatOrderList]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.list_orders(user_id=current_user.user_id),
    )


@router.post(
    "/virtual-payment/orders/{order_id}/reconcile",
    response_model=PublicApiEnvelope[WechatOrderView],
)
def reconcile_virtual_payment_order(
    order_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        Stage8VirtualPaymentService,
        Depends(get_stage8_virtual_payment_service),
    ],
) -> PublicApiEnvelope[WechatOrderView]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.reconcile_order(
            user_id=current_user.user_id,
            order_id=order_id,
        ),
    )


@router.get(
    "/refund-eligibility",
    response_model=PublicApiEnvelope[RefundEligibility | None],
)
def get_refund_eligibility(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[Stage8CommerceService, Depends(get_stage8_commerce_service)],
) -> PublicApiEnvelope[RefundEligibility | None]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.refund_eligibility(user_id=current_user.user_id),
    )


@router.post(
    "/virtual-payment/orders/{order_id}/refunds",
    response_model=PublicApiEnvelope[WechatRefundResponse],
)
def request_virtual_payment_refund(
    order_id: UUID,
    body: WechatRefundRequest,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        Stage8VirtualPaymentService,
        Depends(get_stage8_virtual_payment_service),
    ],
) -> PublicApiEnvelope[WechatRefundResponse]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.request_refund(
            user_id=current_user.user_id,
            order_id=order_id,
            request=body,
        ),
    )


@router.get(
    "/process-rewards",
    response_model=PublicApiEnvelope[ProcessRewardProgress | None],
)
def get_process_rewards(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[Stage8CommerceService, Depends(get_stage8_commerce_service)],
) -> PublicApiEnvelope[ProcessRewardProgress | None]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.reward_progress(user_id=current_user.user_id),
    )


@router.post(
    "/weekly-reflections",
    response_model=PublicApiEnvelope[WeeklyReflectionConfirmationView],
)
def confirm_weekly_reflection(
    body: WeeklyReflectionConfirmRequest,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[Stage8CommerceService, Depends(get_stage8_commerce_service)],
) -> PublicApiEnvelope[WeeklyReflectionConfirmationView]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.confirm_weekly_reflection(
            user_id=current_user.user_id, request=body
        ),
    )


@router.get("/wechat/virtual-payment/events", response_class=PlainTextResponse)
def verify_virtual_payment_event_endpoint(
    request: Request,
    service: Annotated[
        Stage8VirtualPaymentService,
        Depends(get_stage8_virtual_payment_service),
    ],
) -> PlainTextResponse:
    echo = service.verify_event_endpoint(query=dict(request.query_params))
    return PlainTextResponse(echo)


@router.post("/wechat/virtual-payment/events")
async def receive_virtual_payment_event(
    request: Request,
    service: Annotated[
        Stage8VirtualPaymentService,
        Depends(get_stage8_virtual_payment_service),
    ],
) -> JSONResponse:
    encrypted_response = service.process_event(
        query=dict(request.query_params),
        body=await request.body(),
    )
    return JSONResponse(encrypted_response)


def stage8_exception_response(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, Stage8CommerceUnavailable):
        return public_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="commerce_not_open",
            message="当前未开放购买，不会创建订单。",
        )
    if isinstance(exc, Stage8CommerceNotFound):
        return public_error_response(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="commerce_resource_not_found",
            message="订单或商业资源不存在。",
        )
    if isinstance(exc, (Stage8CommerceConflict, Stage8RefundIneligible)):
        return public_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code=(
                exc.code
                if isinstance(exc, Stage8RefundIneligible)
                else "commerce_state_conflict"
            ),
            message="当前订单或权益状态不允许此操作。",
        )
    if isinstance(exc, Stage8PaymentPending):
        return public_error_response(
            request=request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="provider_result_pending",
            message="支付渠道结果正在核对，请稍后刷新订单。",
        )
    if isinstance(exc, WechatPayConfigurationError):
        return public_error_response(
            request=request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="wechat_pay_not_configured",
            message="微信支付商户配置尚未完成。",
        )
    if isinstance(exc, WechatPayProtocolError):
        return public_error_response(
            request=request,
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="wechat_pay_verification_failed",
            message="微信支付返回内容未通过服务端校验。",
        )
    raise exc
