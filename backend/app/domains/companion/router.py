from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.ai.audit import AIAuditRepository
from app.domains.ai.hunyuan_companion import companion_provider
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
from app.domains.learning_brain.agent import maybe_suggest_strategy_safely
from app.domains.learning_brain.service import LearningBrainContractError, LearningBrainService
from app.domains.qualification.repository import PositionRuleReleaseUnavailable
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_response,
)

router = APIRouter(prefix="/api/v1/companion", tags=["companion"])
v2_router = APIRouter(
    prefix="/api/v2/companion",
    tags=["companion"],
    responses=COMMON_API_ERROR_RESPONSES,
)

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
        ai_provider=companion_provider(settings),
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
            detail="请求已提交，请勿重复操作。",
        ) from exc
    except CompanionCommandFailure as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc


@v2_router.post(
    "/ask",
    response_model=PublicApiEnvelope[CompanionAskResponse],
)
def ask_companion_v2(
    command: CompanionAskRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[CompanionService, Depends(get_companion_service)],
) -> JSONResponse:
    try:
        LearningBrainService().get_or_create_profile(
            session,
            user_id=current_user.user_id,
        )
        maybe_suggest_strategy_safely(
            session,
            user_id=current_user.user_id,
            trigger_kind="on_demand",
        )
        result = service.ask(
            user_id=current_user.user_id,
            learner_key=current_user.learner_key,
            idempotency_key=idempotency_key,
            request=command,
        )
        return public_success_response(request=request, data=result)
    except CompanionCommandConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请求已提交，请勿重复操作。",
        ) from exc
    except (LearningBrainContractError, PositionRuleReleaseUnavailable) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CompanionCommandFailure as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
            detail="请求已提交，请勿重复操作。",
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
            detail="外部题目不存在或已失效。",
        ) from exc
    except ExternalQuestionConfirmationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="外部题目确认失败，请稍后再试。",
        ) from exc
