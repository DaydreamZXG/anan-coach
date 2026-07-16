from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.learning_media.composition import build_learning_media_storage
from app.domains.learning_media.schemas import (
    LearningMediaUploadProjection,
    StartLearningMediaUploadCommand,
)
from app.domains.learning_media.service import LearningMediaError, LearningMediaService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/learning-media",
    tags=["learning-media"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def get_learning_media_service() -> LearningMediaService:
    return LearningMediaService(storage=build_learning_media_storage(get_settings()))


IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$",
    ),
]


@router.post(
    "/uploads",
    response_model=PublicApiEnvelope[LearningMediaUploadProjection],
)
def start_learning_media_upload(
    request: Request,
    response: Response,
    command: StartLearningMediaUploadCommand,
    command_id: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[LearningMediaService, Depends(get_learning_media_service)],
) -> PublicApiEnvelope[LearningMediaUploadProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.start(
            session,
            user_id=current_user.user_id,
            command_id=command_id,
            command=command,
        ),
    )


@router.post(
    "/uploads/{upload_session_id}/complete",
    response_model=PublicApiEnvelope[LearningMediaUploadProjection],
)
def complete_learning_media_upload(
    upload_session_id: UUID,
    request: Request,
    response: Response,
    command_id: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[LearningMediaService, Depends(get_learning_media_service)],
) -> PublicApiEnvelope[LearningMediaUploadProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.complete(
            session,
            user_id=current_user.user_id,
            upload_session_id=str(upload_session_id),
            command_id=command_id,
        ),
    )


@router.get(
    "/uploads/{upload_session_id}",
    response_model=PublicApiEnvelope[LearningMediaUploadProjection],
)
def get_learning_media_upload(
    upload_session_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[LearningMediaService, Depends(get_learning_media_service)],
) -> PublicApiEnvelope[LearningMediaUploadProjection] | JSONResponse:
    try:
        result = service.get(
            session,
            user_id=current_user.user_id,
            upload_session_id=str(upload_session_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except LearningMediaError as exc:
        session.rollback()
        return _error(request, exc)


def _write(
    *,
    request: Request,
    response: Response,
    session: Session,
    operation: Callable[[], LearningMediaUploadProjection],
) -> PublicApiEnvelope[LearningMediaUploadProjection] | JSONResponse:
    try:
        result = operation()
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except LearningMediaError as exc:
        session.rollback()
        return _error(request, exc)


def _error(request: Request, error: LearningMediaError) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )
