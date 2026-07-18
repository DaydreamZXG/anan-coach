from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.data_rights.deps import get_data_rights_service
from app.domains.data_rights.repository import DataRightsConflict, DataRightsNotFound
from app.domains.data_rights.schemas import (
    AccountDeletionCreateRequest,
    AccountDeletionView,
    DataExportCreateRequest,
    DataExportPayload,
    DataExportView,
    PrivacyOverview,
)
from app.domains.data_rights.service import DataRightsService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/data-rights",
    tags=["data-rights"],
    responses=COMMON_API_ERROR_RESPONSES,
)


@router.get("/overview", response_model=PublicApiEnvelope[PrivacyOverview])
def overview(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[DataRightsService, Depends(get_data_rights_service)],
) -> PublicApiEnvelope[PrivacyOverview]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.overview(user_id=current_user.user_id),
    )


@router.post("/exports", response_model=PublicApiEnvelope[DataExportView])
def create_export(
    body: DataExportCreateRequest,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[DataRightsService, Depends(get_data_rights_service)],
) -> PublicApiEnvelope[DataExportView]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.create_export(user_id=current_user.user_id, request=body),
    )


@router.get("/exports/{export_id}", response_model=PublicApiEnvelope[DataExportView])
def export_status(
    export_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[DataRightsService, Depends(get_data_rights_service)],
) -> PublicApiEnvelope[DataExportView]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.export_status(user_id=current_user.user_id, export_id=export_id),
    )


@router.get(
    "/exports/{export_id}/payload",
    response_model=PublicApiEnvelope[DataExportPayload],
)
def export_payload(
    export_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[DataRightsService, Depends(get_data_rights_service)],
) -> PublicApiEnvelope[DataExportPayload]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.export_payload(user_id=current_user.user_id, export_id=export_id),
    )


@router.post("/deletions", response_model=PublicApiEnvelope[AccountDeletionView])
def request_deletion(
    body: AccountDeletionCreateRequest,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[DataRightsService, Depends(get_data_rights_service)],
) -> PublicApiEnvelope[AccountDeletionView]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.request_deletion(user_id=current_user.user_id, request=body),
    )


@router.get(
    "/deletions/{deletion_id}", response_model=PublicApiEnvelope[AccountDeletionView]
)
def deletion_status(
    deletion_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[DataRightsService, Depends(get_data_rights_service)],
) -> PublicApiEnvelope[AccountDeletionView]:
    return public_success_envelope(
        request=request,
        response=response,
        data=service.deletion_status(
            user_id=current_user.user_id, deletion_id=deletion_id
        ),
    )


def data_rights_exception_response(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, DataRightsNotFound):
        return public_error_response(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="data_rights_request_not_found",
            message="数据请求不存在。",
        )
    if isinstance(exc, DataRightsConflict):
        return public_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="data_rights_state_conflict",
            message="当前数据或账户状态不允许此操作。",
        )
    raise exc
