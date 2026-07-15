from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.task_help.repository import TaskHelpRepository
from app.domains.task_help.schemas import TaskHelpRequest, TaskHelpResponse
from app.domains.task_help.service import TaskHelpError, TaskHelpService
from app.platform.http import (
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)


router = APIRouter(prefix="/api/v2/task-help", tags=["task-help"])


def get_task_help_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> TaskHelpService:
    return TaskHelpService(TaskHelpRepository(session))


@router.post("", response_model=PublicApiEnvelope[TaskHelpResponse])
def get_task_help(
    body: TaskHelpRequest,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[TaskHelpService, Depends(get_task_help_service)],
) -> PublicApiEnvelope[TaskHelpResponse] | JSONResponse:
    try:
        result = service.get_help(user_id=current_user.user_id, request=body)
        return public_success_envelope(request=request, response=response, data=result)
    except TaskHelpError as exc:
        return public_error_response(
            request=request,
            status_code=exc.status_code,
            code=exc.code,
            message=str(exc),
        )
