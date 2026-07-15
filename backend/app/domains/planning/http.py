from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.domains.planning.errors import PlanningTaskExpiredError
from app.platform.http import public_error_response


def planning_task_expired_response(
    *,
    request: Request,
    error: PlanningTaskExpiredError,
) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=status.HTTP_409_CONFLICT,
        code=error.code,
        message="该任务所属的每日计划已过期，请刷新今日计划后继续。",
        details={
            "taskId": error.task_id,
            "planVersionId": error.plan_version_id,
            "expiredAt": error.expired_at.isoformat(),
        },
    )
