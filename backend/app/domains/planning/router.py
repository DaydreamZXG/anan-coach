from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.planning.errors import PlanningTaskExpiredError
from app.domains.planning.http import planning_task_expired_response
from app.domains.planning.repository import PlanningContractError
from app.domains.planning.schemas import (
    DailyCapacityCommand,
    DailyCapacityCommandResult,
    TaskExecutionCommand,
    TaskExecutionCommandResult,
    TodayDashboard,
)
from app.domains.planning.service import PlanningService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/plans",
    tags=["planning"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def get_planning_service() -> PlanningService:
    return PlanningService()


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


@router.get("/today", response_model=PublicApiEnvelope[TodayDashboard])
def get_today_plan(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    planning: Annotated[PlanningService, Depends(get_planning_service)],
) -> PublicApiEnvelope[TodayDashboard]:
    try:
        result = planning.get_or_create_today(
            session,
            user_id=current_user.user_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except PlanningContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put(
    "/today/capacity",
    response_model=PublicApiEnvelope[DailyCapacityCommandResult],
)
def set_today_capacity(
    request: Request,
    response: Response,
    command: DailyCapacityCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    planning: Annotated[PlanningService, Depends(get_planning_service)],
) -> PublicApiEnvelope[DailyCapacityCommandResult]:
    try:
        result = planning.set_today_capacity(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except PlanningContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put(
    "/tasks/{task_id}/execution",
    response_model=PublicApiEnvelope[TaskExecutionCommandResult],
)
def record_task_execution(
    task_id: UUID,
    request: Request,
    response: Response,
    command: TaskExecutionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    planning: Annotated[PlanningService, Depends(get_planning_service)],
) -> PublicApiEnvelope[TaskExecutionCommandResult] | Response:
    try:
        result = planning.record_task_execution(
            session,
            user_id=current_user.user_id,
            task_id=str(task_id),
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except PlanningTaskExpiredError as exc:
        session.rollback()
        return planning_task_expired_response(request=request, error=exc)
    except PlanningContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
