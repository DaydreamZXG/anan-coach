from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.planning.repository import PlanningContractError
from app.domains.xingce_learning.real_attempt.schemas import (
    CauseDecisionCommand,
    CauseFollowupCommand,
    QuestionDisputeReceipt,
    ReflectionCommand,
    ReportQuestionDisputeCommand,
    SaveCheckpointCommand,
    StartTaskSessionCommand,
    SubmitTaskSessionCommand,
    TaskSessionProjection,
)
from app.domains.xingce_learning.real_attempt.service import (
    XingceAttemptError,
    XingceAttemptService,
)
from app.domains.xingce_learning.mastery.schemas import MasteryQueuePage
from app.domains.xingce_learning.mastery.service import (
    XingceMasteryCursorError,
    XingceMasteryService,
)
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


def get_xingce_attempt_service() -> XingceAttemptService:
    return XingceAttemptService()


def get_xingce_mastery_service() -> XingceMasteryService:
    return XingceMasteryService()


def _idempotency_key(
    value: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=160)
    ],
) -> str:
    return value


@router.get(
    "/mastery-queue",
    response_model=PublicApiEnvelope[MasteryQueuePage],
)
def get_mastery_queue(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceMasteryService, Depends(get_xingce_mastery_service)],
    bucket: Annotated[
        Literal[
            "pending_reflection",
            "due_verification",
            "stable_mastery",
            "invalidated",
        ],
        Query(),
    ] = "pending_reflection",
    cursor: Annotated[str | None, Query(min_length=1, max_length=800)] = None,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 30,
) -> PublicApiEnvelope[MasteryQueuePage] | JSONResponse:
    try:
        result = service.queue(
            session,
            user_id=current_user.user_id,
            bucket=bucket,
            cursor=cursor,
            page_size=page_size,
            now=datetime.now(UTC),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except XingceMasteryCursorError:
        return public_error_response(
            request=request,
            status_code=422,
            code="xingce_mastery_cursor_invalid",
            message="复测队列游标无效，请从当前分组第一页重新加载。",
        )


@router.post(
    "/task-sessions",
    response_model=PublicApiEnvelope[TaskSessionProjection],
)
def start_or_resume_task_session(
    request: Request,
    response: Response,
    command: StartTaskSessionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceAttemptService, Depends(get_xingce_attempt_service)],
) -> PublicApiEnvelope[TaskSessionProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.start_or_resume(
            session,
            user_id=current_user.user_id,
            command_id=command_id,
            command=command,
        ),
    )


@router.get(
    "/task-sessions/{session_id}",
    response_model=PublicApiEnvelope[TaskSessionProjection],
)
def get_task_session(
    session_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceAttemptService, Depends(get_xingce_attempt_service)],
) -> PublicApiEnvelope[TaskSessionProjection] | JSONResponse:
    try:
        result = service.get_session(
            session,
            user_id=current_user.user_id,
            session_id=str(session_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except XingceAttemptError as exc:
        session.rollback()
        return _error(request, exc)


@router.post(
    "/task-sessions/{session_id}/disputes",
    response_model=PublicApiEnvelope[QuestionDisputeReceipt],
)
def report_question_dispute(
    session_id: UUID,
    request: Request,
    response: Response,
    command: ReportQuestionDisputeCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceAttemptService, Depends(get_xingce_attempt_service)],
) -> PublicApiEnvelope[QuestionDisputeReceipt] | JSONResponse:
    try:
        result = service.report_question_dispute(
            session,
            user_id=current_user.user_id,
            session_id=str(session_id),
            command_id=command_id,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except XingceAttemptError as exc:
        session.rollback()
        return _error(request, exc)


@router.put(
    "/task-sessions/{session_id}/checkpoint",
    response_model=PublicApiEnvelope[TaskSessionProjection],
)
def save_task_session_checkpoint(
    session_id: UUID,
    request: Request,
    response: Response,
    command: SaveCheckpointCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceAttemptService, Depends(get_xingce_attempt_service)],
) -> PublicApiEnvelope[TaskSessionProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.save_checkpoint(
            session,
            user_id=current_user.user_id,
            session_id=str(session_id),
            command_id=command_id,
            command=command,
        ),
    )


@router.post(
    "/task-sessions/{session_id}/submit",
    response_model=PublicApiEnvelope[TaskSessionProjection],
)
def submit_task_session(
    session_id: UUID,
    request: Request,
    response: Response,
    command: SubmitTaskSessionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceAttemptService, Depends(get_xingce_attempt_service)],
) -> PublicApiEnvelope[TaskSessionProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.submit(
            session,
            user_id=current_user.user_id,
            session_id=str(session_id),
            command_id=command_id,
            command=command,
        ),
    )


@router.put(
    "/attempt-reviews/{review_id}/reflection",
    response_model=PublicApiEnvelope[TaskSessionProjection],
)
def submit_attempt_reflection(
    review_id: UUID,
    request: Request,
    response: Response,
    command: ReflectionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceAttemptService, Depends(get_xingce_attempt_service)],
) -> PublicApiEnvelope[TaskSessionProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.submit_reflection(
            session,
            user_id=current_user.user_id,
            review_id=str(review_id),
            command_id=command_id,
            command=command,
        ),
    )


@router.put(
    "/attempt-reviews/{review_id}/cause-followup",
    response_model=PublicApiEnvelope[TaskSessionProjection],
)
def submit_attempt_cause_followup(
    review_id: UUID,
    request: Request,
    response: Response,
    command: CauseFollowupCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceAttemptService, Depends(get_xingce_attempt_service)],
) -> PublicApiEnvelope[TaskSessionProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.submit_cause_followup(
            session,
            user_id=current_user.user_id,
            review_id=str(review_id),
            command_id=command_id,
            command=command,
        ),
    )


@router.put(
    "/attempt-reviews/{review_id}/cause-decision",
    response_model=PublicApiEnvelope[TaskSessionProjection],
)
def decide_attempt_cause(
    review_id: UUID,
    request: Request,
    response: Response,
    command: CauseDecisionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceAttemptService, Depends(get_xingce_attempt_service)],
) -> PublicApiEnvelope[TaskSessionProjection] | JSONResponse:
    return _write(
        request=request,
        response=response,
        session=session,
        operation=lambda: service.decide_cause(
            session,
            user_id=current_user.user_id,
            review_id=str(review_id),
            command_id=command_id,
            command=command,
        ),
    )


def _write(
    *,
    request: Request,
    response: Response,
    session: Session,
    operation: Callable[[], TaskSessionProjection],
) -> PublicApiEnvelope[TaskSessionProjection] | JSONResponse:
    try:
        result = operation()
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except XingceAttemptError as exc:
        session.rollback()
        return _error(request, exc)
    except PlanningContractError:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=409,
            code="xingce_planning_conflict",
            message="当前行测任务对应的计划版本已经变化，请返回今日计划后重试。",
        )


def _error(request: Request, error: XingceAttemptError) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )
