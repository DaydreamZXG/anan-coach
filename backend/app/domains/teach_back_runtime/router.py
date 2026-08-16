from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.teach_back_runtime.schemas import (
    StartTeachBackCommand,
    TeachBackSessionProjection,
    TeachBackTransferCommand,
    TeachBackTransferReceipt,
)
from app.domains.teach_back_runtime.service import (
    TeachBackRuntimeError,
    TeachBackRuntimeService,
)
from app.domains.xingce_learning.mastery.service import XingceMasteryConflict
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/xingce",
    tags=["xingce-learning"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def get_teach_back_service() -> TeachBackRuntimeService:
    return TeachBackRuntimeService()


@router.post(
    "/attempt-reviews/{review_id}/teach-back",
    response_model=PublicApiEnvelope[TeachBackSessionProjection],
)
def start_teach_back(
    review_id: UUID,
    command: StartTeachBackCommand,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[TeachBackRuntimeService, Depends(get_teach_back_service)],
) -> PublicApiEnvelope[TeachBackSessionProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.start(
            session,
            user_id=current_user.user_id,
            review_id=str(review_id),
            command=command,
        ),
    )


@router.get(
    "/teach-back/{session_id}",
    response_model=PublicApiEnvelope[TeachBackSessionProjection],
)
def get_teach_back(
    session_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[TeachBackRuntimeService, Depends(get_teach_back_service)],
) -> PublicApiEnvelope[TeachBackSessionProjection] | JSONResponse:
    try:
        result = service.get(
            session, user_id=current_user.user_id, session_id=str(session_id)
        )
        return public_success_envelope(request=request, response=response, data=result)
    except TeachBackRuntimeError as exc:
        session.rollback()
        return _error(request, exc)


@router.post(
    "/teach-back/{session_id}/transfer",
    response_model=PublicApiEnvelope[TeachBackTransferReceipt],
)
def submit_teach_back_transfer(
    session_id: UUID,
    command: TeachBackTransferCommand,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[TeachBackRuntimeService, Depends(get_teach_back_service)],
) -> PublicApiEnvelope[TeachBackTransferReceipt] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.transfer(
            session,
            user_id=current_user.user_id,
            session_id=str(session_id),
            command=command,
        ),
    )


def _write(*, request: Request, response: Response, session: Session, operation):
    try:
        result = operation()
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except TeachBackRuntimeError as exc:
        session.rollback()
        return _error(request, exc)
    except XingceMasteryConflict as exc:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=409,
            code="teach_back_mastery_conflict",
            message=str(exc),
        )


def _error(request: Request, exc: TeachBackRuntimeError) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
    )
