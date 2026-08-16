from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
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
from app.domains.companion.memory import (
    CompanionCoachRecheckConflict,
    CompanionMemoryConflict,
    CompanionMemoryNotFound,
    CompanionMemoryStore,
    CompanionMemoryUnsafeContent,
)
from app.domains.companion.repository import CompanionRepository
from app.domains.companion.schemas import (
    CompanionCoachRecheckCommand,
    CompanionCoachRecheckReceipt,
    CompanionCoachUnderstanding,
    CompanionAskRequest,
    CompanionAskResponse,
    CompanionMemoryItem,
    CompanionMemoryMutationResponse,
    CompanionMemorySettingRequest,
    CompanionMemorySettingResponse,
    CompanionMemoryState,
    CompanionMemoryUpdateRequest,
    CompanionMemoryWriteRequest,
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
from app.domains.companion.short_term_memory import (
    CompanionShortTermMemory,
    companion_context_scope_fingerprint,
)
from app.domains.learning_brain.agent import maybe_suggest_strategy_safely
from app.domains.learning_brain.service import LearningBrainContractError, LearningBrainService
from app.domains.qualification.repository import PositionRuleReleaseUnavailable
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
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


@v2_router.get(
    "/coach-understanding",
    response_model=PublicApiEnvelope[CompanionCoachUnderstanding],
)
def get_coach_understanding(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[CompanionCoachUnderstanding]:
    coach = LearningBrainService().get_coach_context(
        session,
        user_id=current_user.user_id,
    ).model_dump(mode="json", by_alias=True)
    result = CompanionMemoryStore(session).coach_understanding(
        user_id=current_user.user_id,
        coach_context=coach,
    )
    session.commit()
    return public_success_envelope(request=request, response=response, data=result)


@v2_router.post(
    "/coach-rechecks",
    response_model=PublicApiEnvelope[CompanionCoachRecheckReceipt],
)
def request_coach_recheck(
    command: CompanionCoachRecheckCommand,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[CompanionCoachRecheckReceipt] | JSONResponse:
    try:
        coach = LearningBrainService().get_coach_context(
            session,
            user_id=current_user.user_id,
        ).model_dump(mode="json", by_alias=True)
        result = CompanionMemoryStore(session).request_coach_recheck(
            user_id=current_user.user_id,
            command=command,
            coach_context=coach,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except CompanionMemoryNotFound as exc:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="coach_judgment_not_found",
            message=str(exc),
        )
    except CompanionCoachRecheckConflict as exc:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="coach_recheck_conflict",
            message=str(exc),
        )


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
        context_scope_fingerprint = companion_context_scope_fingerprint(command.context)
        memory = CompanionShortTermMemory(session)
        result = service.ask(
            user_id=current_user.user_id,
            learner_key=current_user.learner_key,
            idempotency_key=idempotency_key,
            request=command,
            short_term_history=memory.recent_messages(
                user_id=current_user.user_id,
                context_scope_fingerprint=context_scope_fingerprint,
            ),
        )
        if result.semantic_state == "ready":
            memory.remember(
                user_id=current_user.user_id,
                context_scope_fingerprint=context_scope_fingerprint,
                source_message_id=idempotency_key,
                user_text=command.question_text,
                assistant_text=result.answer,
            )
            session.commit()
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


@v2_router.get(
    "/memories",
    response_model=PublicApiEnvelope[CompanionMemoryState],
)
def get_companion_memories(
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    memory = CompanionMemoryStore(session).state(user_id=current_user.user_id)
    return public_success_response(request=request, data=memory)


@v2_router.post(
    "/memories",
    response_model=PublicApiEnvelope[CompanionMemoryItem],
)
def create_companion_memory(
    command: CompanionMemoryWriteRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    try:
        item = CompanionMemoryStore(session).create(
            user_id=current_user.user_id,
            idempotency_key=idempotency_key,
            category=command.category,
            content=command.content,
        )
        session.commit()
        return public_success_response(request=request, data=item)
    except CompanionMemoryNotFound:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="memory_account_not_found",
            message="当前账户无法保存记忆。",
        )
    except CompanionMemoryConflict:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="memory_command_conflict",
            message="这次保存和之前的操作冲突，请刷新后再试。",
        )
    except CompanionMemoryUnsafeContent:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="unsafe_memory_content",
            message="这段内容不能作为学习记忆，请只描述偏好或学习安排。",
        )


@v2_router.put(
    "/memories/{memory_id}",
    response_model=PublicApiEnvelope[CompanionMemoryItem],
)
def update_companion_memory(
    memory_id: UUID,
    command: CompanionMemoryUpdateRequest,
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    try:
        item = CompanionMemoryStore(session).update(
            user_id=current_user.user_id,
            memory_id=memory_id,
            revision=command.revision,
            category=command.category,
            content=command.content,
        )
        session.commit()
        return public_success_response(request=request, data=item)
    except CompanionMemoryNotFound:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="memory_not_found",
            message="这条记忆已不存在。",
        )
    except CompanionMemoryConflict:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="memory_revision_conflict",
            message="这条记忆已发生变化，请刷新后再修改。",
        )
    except CompanionMemoryUnsafeContent:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="unsafe_memory_content",
            message="这段内容不能作为学习记忆，请只描述偏好或学习安排。",
        )


@v2_router.delete(
    "/memories/{memory_id}",
    response_model=PublicApiEnvelope[CompanionMemoryMutationResponse],
)
def delete_companion_memory(
    memory_id: UUID,
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    changed = CompanionMemoryStore(session).delete(
        user_id=current_user.user_id,
        memory_id=memory_id,
    )
    session.commit()
    return public_success_response(
        request=request,
        data=CompanionMemoryMutationResponse(changedCount=changed),
    )


@v2_router.delete(
    "/memories",
    response_model=PublicApiEnvelope[CompanionMemoryMutationResponse],
)
def clear_companion_memories(
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    changed = CompanionMemoryStore(session).clear_long_term(user_id=current_user.user_id)
    session.commit()
    return public_success_response(
        request=request,
        data=CompanionMemoryMutationResponse(changedCount=changed),
    )


@v2_router.put(
    "/memory-settings",
    response_model=PublicApiEnvelope[CompanionMemorySettingResponse],
)
def put_companion_memory_settings(
    command: CompanionMemorySettingRequest,
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    enabled = CompanionMemoryStore(session).set_enabled(
        user_id=current_user.user_id,
        enabled=command.long_term_enabled,
    )
    session.commit()
    return public_success_response(
        request=request,
        data=CompanionMemorySettingResponse(longTermEnabled=enabled),
    )


@v2_router.delete(
    "/conversation-memory",
    response_model=PublicApiEnvelope[CompanionMemoryMutationResponse],
)
def clear_companion_conversation_memory(
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> JSONResponse:
    changed = CompanionMemoryStore(session).clear_short_term(user_id=current_user.user_id)
    session.commit()
    return public_success_response(
        request=request,
        data=CompanionMemoryMutationResponse(changedCount=changed),
    )


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
