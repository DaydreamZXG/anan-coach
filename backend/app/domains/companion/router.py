from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.ai.audit import AIAuditRepository
from app.domains.ai.provider import AIProvider
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.commerce.deps import get_commerce_service
from app.domains.commerce.service import CommerceService
from app.domains.companion.commands import CompanionCommandConflict
from app.domains.companion.repository import CompanionRepository
from app.domains.companion.schemas import (
    CompanionAskRequest,
    CompanionAskResponse,
    ExternalQuestionConfirmRequest,
    ExternalQuestionConfirmResponse,
    ExternalQuestionResolveRequest,
    ExternalQuestionResolveResponse,
)
from app.domains.companion.service import (
    CompanionCommandFailure,
    CompanionService,
    ExternalQuestionConfirmationError,
    ExternalQuestionIntakeNotFoundError,
)

router = APIRouter(prefix="/api/v1/companion", tags=["companion"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$",
    ),
]


def get_companion_service(
    session: Annotated[Session, Depends(get_db_session)],
    commerce_service: Annotated[CommerceService, Depends(get_commerce_service)],
) -> CompanionService:
    settings = get_settings()
    return CompanionService(
        repository=CompanionRepository(session),
        audit_repository=AIAuditRepository(session),
        commerce_service=commerce_service,
        ai_provider=AIProvider(settings),
        session=session,
        settings=settings,
    )


@router.post("/ask", response_model=CompanionAskResponse)
def ask_companion(
    request: CompanionAskRequest,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[CompanionService, Depends(get_companion_service)],
) -> CompanionAskResponse:
    try:
        return service.ask(
            user_id=current_user.user_id,
            learner_key=current_user.learner_key,
            idempotency_key=idempotency_key,
            request=request,
        )
    except CompanionCommandConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key was already used with a different request",
        ) from exc
    except CompanionCommandFailure as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc


@router.post(
    "/external-questions/resolve",
    response_model=ExternalQuestionResolveResponse,
)
def resolve_external_question(
    request: ExternalQuestionResolveRequest,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[CompanionService, Depends(get_companion_service)],
) -> ExternalQuestionResolveResponse:
    try:
        return service.resolve_external_question(
            user_id=current_user.user_id,
            learner_key=current_user.learner_key,
            idempotency_key=idempotency_key,
            request=request,
        )
    except CompanionCommandConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key was already used with a different request",
        ) from exc
    except CompanionCommandFailure as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc


@router.post(
    "/external-questions/{intake_id}/confirm",
    response_model=ExternalQuestionConfirmResponse,
)
def confirm_external_question(
    intake_id: str,
    request: ExternalQuestionConfirmRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[CompanionService, Depends(get_companion_service)],
) -> ExternalQuestionConfirmResponse:
    try:
        return service.confirm_external_question(
            user_id=current_user.user_id,
            intake_id=intake_id,
            request=request,
        )
    except ExternalQuestionIntakeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="External question intake not found",
        ) from exc
    except ExternalQuestionConfirmationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="External question intake cannot be confirmed",
        ) from exc
