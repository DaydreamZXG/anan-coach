from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.retake.schemas import (
    RetakeEnrollmentRequest,
    RetakeEnrollmentResult,
    RetakeWorkspace,
)
from app.domains.retake.service import RetakeContractError, RetakeService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/retake",
    tags=["retake-learning"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


@router.get("", response_model=PublicApiEnvelope[RetakeWorkspace])
def get_retake_workspace(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RetakeWorkspace] | JSONResponse:
    try:
        workspace = RetakeService().workspace(
            session,
            user_id=current_user.user_id,
        )
        return public_success_envelope(request=request, response=response, data=workspace)
    except RetakeContractError as exc:
        session.rollback()
        return _error_response(request, exc)


@router.post(
    "/programs",
    response_model=PublicApiEnvelope[RetakeEnrollmentResult],
)
def enroll_retake_program(
    request: Request,
    response: Response,
    body: RetakeEnrollmentRequest,
    idempotency_key: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RetakeEnrollmentResult] | JSONResponse:
    try:
        result = RetakeService().enroll(
            session,
            user_id=current_user.user_id,
            command_key=f"{current_user.user_id}:{idempotency_key}",
            source_score_evidence_id=body.source_score_evidence_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RetakeContractError as exc:
        session.rollback()
        return _error_response(request, exc)


def _error_response(request: Request, exc: RetakeContractError) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=status.HTTP_409_CONFLICT,
        code=exc.code,
        message=exc.message,
    )
