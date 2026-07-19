from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.task_editing.schemas import (
    BeginTaskEditConflictCommand,
    ClaimTaskEditLeaseCommand,
    ResolveTaskEditConflictCommand,
    TakeOverTaskEditLeaseCommand,
    TaskEditLeaseProjection,
    TaskEditLeaseToken,
    TaskResourceType,
)
from app.domains.task_editing.service import TaskEditingError, TaskEditingService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/task-editing",
    tags=["task-editing"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def get_task_editing_service() -> TaskEditingService:
    return TaskEditingService()


def task_edit_lease_token(
    lease_id: Annotated[str | None, Header(alias="X-Task-Edit-Lease")] = None,
    generation: Annotated[
        int | None, Header(alias="X-Task-Edit-Generation", ge=1)
    ] = None,
) -> TaskEditLeaseToken | None:
    if lease_id is None or generation is None:
        return None
    return TaskEditLeaseToken(lease_id=lease_id, generation=generation)


@router.post("/leases", response_model=PublicApiEnvelope[TaskEditLeaseProjection])
def claim_task_edit_lease(
    request: Request,
    response: Response,
    command: ClaimTaskEditLeaseCommand,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[TaskEditingService, Depends(get_task_editing_service)],
) -> PublicApiEnvelope[TaskEditLeaseProjection] | JSONResponse:
    return _write(
        request,
        response,
        session,
        lambda: service.claim(session, user_id=current_user.user_id, command=command),
    )


@router.post(
    "/leases/{resource_type}/{resource_id}/takeover",
    response_model=PublicApiEnvelope[TaskEditLeaseProjection],
)
def take_over_task_edit_lease(
    resource_type: TaskResourceType,
    resource_id: UUID,
    request: Request,
    response: Response,
    command: TakeOverTaskEditLeaseCommand,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[TaskEditingService, Depends(get_task_editing_service)],
) -> PublicApiEnvelope[TaskEditLeaseProjection] | JSONResponse:
    return _write(
        request,
        response,
        session,
        lambda: service.take_over(
            session,
            user_id=current_user.user_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
            command=command,
        ),
    )


@router.post(
    "/leases/{resource_type}/{resource_id}/conflict",
    response_model=PublicApiEnvelope[TaskEditLeaseProjection],
)
def begin_task_edit_conflict(
    resource_type: TaskResourceType,
    resource_id: UUID,
    request: Request,
    response: Response,
    command: BeginTaskEditConflictCommand,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[TaskEditingService, Depends(get_task_editing_service)],
) -> PublicApiEnvelope[TaskEditLeaseProjection] | JSONResponse:
    return _write(
        request,
        response,
        session,
        lambda: service.begin_conflict(
            session,
            user_id=current_user.user_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
            command=command,
        ),
    )


@router.post(
    "/leases/{resource_type}/{resource_id}/resolve",
    response_model=PublicApiEnvelope[TaskEditLeaseProjection],
)
def resolve_task_edit_conflict(
    resource_type: TaskResourceType,
    resource_id: UUID,
    request: Request,
    response: Response,
    command: ResolveTaskEditConflictCommand,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[TaskEditingService, Depends(get_task_editing_service)],
) -> PublicApiEnvelope[TaskEditLeaseProjection] | JSONResponse:
    return _write(
        request,
        response,
        session,
        lambda: service.resolve(
            session,
            user_id=current_user.user_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
            command=command,
        ),
    )


def _write(
    request: Request,
    response: Response,
    session: Session,
    operation: Callable[[], TaskEditLeaseProjection],
) -> PublicApiEnvelope[TaskEditLeaseProjection] | JSONResponse:
    try:
        result = operation()
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except TaskEditingError as exc:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )


def task_editing_exception_response(
    request: Request, exc: TaskEditingError
) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )
