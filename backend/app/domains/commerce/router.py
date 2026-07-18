from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.commerce.app_store import (
    AppStoreTransactionVerificationError,
    AppStoreVerificationConfigurationError,
)
from app.domains.commerce.deps import get_commerce_service
from app.domains.commerce.schemas import (
    AppStoreTransactionRequest,
    AppStoreTransactionResponse,
    AppStoreNotificationRequest,
    AppStoreNotificationResponse,
    CommerceCatalogResponse,
    CommerceEntitlementResponse,
)
from app.domains.commerce.service import (
    CommerceProductNotFoundError,
    CommerceService,
    CommerceTransactionMismatchError,
    CommerceUnavailableError,
)

router = APIRouter(prefix="/api/v1/commerce", tags=["commerce"])


@router.get("/catalog", response_model=CommerceCatalogResponse)
def get_catalog(
    service: Annotated[CommerceService, Depends(get_commerce_service)],
) -> CommerceCatalogResponse:
    return service.get_catalog()


@router.get("/entitlement", response_model=CommerceEntitlementResponse)
def get_entitlement(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[CommerceService, Depends(get_commerce_service)],
) -> CommerceEntitlementResponse:
    return service.get_entitlement(user_id=current_user.user_id)


@router.post(
    "/app-store/transactions",
    response_model=AppStoreTransactionResponse,
)
def register_app_store_transaction(
    request: AppStoreTransactionRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[CommerceService, Depends(get_commerce_service)],
) -> AppStoreTransactionResponse:
    try:
        return service.register_app_store_transaction(
            user_id=current_user.user_id,
            request=request,
        )
    except CommerceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Commercial purchasing is not open",
        ) from exc
    except CommerceProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="IAP product is not configured",
        ) from exc
    except (
        CommerceTransactionMismatchError,
        AppStoreTransactionVerificationError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="App Store transaction verification failed",
        ) from exc
    except AppStoreVerificationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App Store transaction verification is not configured",
        ) from exc


@router.post(
    "/app-store/notifications",
    response_model=AppStoreNotificationResponse,
)
def register_app_store_notification(
    request: AppStoreNotificationRequest,
    service: Annotated[CommerceService, Depends(get_commerce_service)],
) -> AppStoreNotificationResponse:
    try:
        return service.register_app_store_notification(request=request)
    except (
        CommerceTransactionMismatchError,
        AppStoreTransactionVerificationError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="App Store notification verification failed",
        ) from exc
    except AppStoreVerificationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App Store notification verification is not configured",
        ) from exc
