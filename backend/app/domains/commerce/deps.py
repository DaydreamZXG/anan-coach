from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.commerce.stage8_repository import Stage8CommerceRepository
from app.domains.commerce.stage8_service import Stage8CommerceService
from app.domains.commerce.wechat_pay import (
    WechatPayConfigurationError,
    WechatPayGateway,
)
from app.domains.commerce.wechat_virtual_payment import (
    Stage8VirtualPaymentService,
    WechatVirtualPaymentClient,
    WechatVirtualPaymentGateway,
)


def get_commerce_repository(
    session: Annotated[Session, Depends(get_db_session)],
) -> CommerceRepository:
    return CommerceRepository(session)


def get_app_store_transaction_verifier() -> AppStoreSignedTransactionVerifier:
    return AppStoreSignedTransactionVerifier(get_settings())


def get_commerce_service(
    repository: Annotated[CommerceRepository, Depends(get_commerce_repository)],
    app_store_verifier: Annotated[
        AppStoreSignedTransactionVerifier,
        Depends(get_app_store_transaction_verifier),
    ],
) -> CommerceService:
    settings = get_settings()
    return CommerceService(
        repository=repository,
        app_store_verifier=app_store_verifier,
        commercial_mode=settings.commercial_mode,
        usage_access_mode=settings.usage_access_mode,
    )


def get_stage8_commerce_repository(
    session: Annotated[Session, Depends(get_db_session)],
) -> Stage8CommerceRepository:
    return Stage8CommerceRepository(session)


def get_wechat_pay_gateway() -> WechatPayGateway:
    return _RetiredMiniProgramJsapiGateway()


def get_wechat_virtual_payment_gateway() -> WechatVirtualPaymentGateway:
    return WechatVirtualPaymentClient(get_settings())


def get_stage8_commerce_service(
    repository: Annotated[
        Stage8CommerceRepository, Depends(get_stage8_commerce_repository)
    ],
    entitlement_service: Annotated[CommerceService, Depends(get_commerce_service)],
    gateway: Annotated[WechatPayGateway, Depends(get_wechat_pay_gateway)],
) -> Stage8CommerceService:
    return Stage8CommerceService(
        repository=repository,
        entitlement_service=entitlement_service,
        gateway=gateway,
        settings=get_settings(),
    )


def get_stage8_virtual_payment_service(
    repository: Annotated[
        Stage8CommerceRepository, Depends(get_stage8_commerce_repository)
    ],
    entitlement_service: Annotated[CommerceService, Depends(get_commerce_service)],
    gateway: Annotated[
        WechatVirtualPaymentGateway,
        Depends(get_wechat_virtual_payment_gateway),
    ],
) -> Stage8VirtualPaymentService:
    return Stage8VirtualPaymentService(
        repository=repository,
        entitlement_service=entitlement_service,
        gateway=gateway,
        settings=get_settings(),
    )


class _RetiredMiniProgramJsapiGateway:
    """Fail closed if a retired ordinary-payment mutation is reached."""

    @staticmethod
    def _retired() -> None:
        raise WechatPayConfigurationError(
            "ordinary Mini Program JSAPI payment is retired for virtual goods"
        )

    def create_jsapi_order(self, **_kwargs: object) -> object:
        self._retired()

    def sign_prepay(self, **_kwargs: object) -> object:
        self._retired()

    def query_order(self, **_kwargs: object) -> object:
        self._retired()

    def close_order(self, **_kwargs: object) -> None:
        self._retired()

    def create_refund(self, **_kwargs: object) -> object:
        self._retired()

    def query_refund(self, **_kwargs: object) -> object:
        self._retired()

    def verify_notification(self, **_kwargs: object) -> object:
        self._retired()
