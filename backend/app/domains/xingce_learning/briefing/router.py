from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.xingce_learning.briefing.schemas import XingceTaskBriefing
from app.domains.xingce_learning.briefing.service import (
    XingceBriefingError,
    XingceBriefingService,
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


def get_xingce_briefing_service() -> XingceBriefingService:
    return XingceBriefingService()


@router.get(
    "/plan-tasks/{plan_task_id}/briefing",
    response_model=PublicApiEnvelope[XingceTaskBriefing],
)
def get_xingce_task_briefing(
    plan_task_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[XingceBriefingService, Depends(get_xingce_briefing_service)],
) -> PublicApiEnvelope[XingceTaskBriefing] | JSONResponse:
    try:
        result = service.get(
            session,
            user_id=current_user.user_id,
            plan_task_id=str(plan_task_id),
            now=datetime.now(UTC),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except XingceBriefingError:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=409,
            code="xingce_briefing_unavailable",
            message="当前行测任务不存在、已过期或证据版本已经变化。",
        )
