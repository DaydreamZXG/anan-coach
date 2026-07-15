from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.essay.repository import EssayRepository
from app.domains.essay.schemas import EssayTask
from app.domains.essay.service import (
    EssayService,
    EssayTaskNotFoundError,
)

router = APIRouter(prefix="/api/v1/essay", tags=["essay-legacy-read"])

LEGACY_ESSAY_WRITE_RETIREMENT = {
    "code": "essay_v1_write_retired",
    "message": "Legacy essay AI review and rewrite writes are permanently retired.",
    "owner": "essay_intelligence_v2",
    "replacement": {
        "plan": "/api/v2/plans/today",
        "workspace": "/api/v2/essay-intelligence/tasks/{taskVersionId}/workspace",
        "completePlanTask": "/api/v2/essay-intelligence/artifacts/complete-plan-task",
    },
}

_DEPRECATION_HEADERS = {
    "Deprecation": "true",
    "Sunset": "Mon, 13 Jul 2026 00:00:00 GMT",
    "Link": '</api/v2/plans/today>; rel="successor-version"',
}


def get_essay_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> EssayService:
    return EssayService(
        essay_repository=EssayRepository(session),
    )


@router.get("/tasks/{task_id}", response_model=EssayTask, deprecated=True)
def get_essay_task(
    task_id: str,
    response: Response,
    service: Annotated[EssayService, Depends(get_essay_service)],
) -> EssayTask:
    try:
        task = service.get_task(task_id)
        response.headers.update(_DEPRECATION_HEADERS)
        return task
    except EssayTaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Essay task not found",
        ) from exc


@router.post(
    "/tasks/{task_id}/review",
    deprecated=True,
    responses={status.HTTP_410_GONE: {"description": "Essay v1 writer retired"}},
)
def review_essay_answer(task_id: str) -> JSONResponse:
    return _legacy_write_retired_response(task_id)


@router.post(
    "/tasks/{task_id}/rewrite",
    deprecated=True,
    responses={status.HTTP_410_GONE: {"description": "Essay v1 writer retired"}},
)
def compare_essay_rewrite(task_id: str) -> JSONResponse:
    return _legacy_write_retired_response(task_id)


def _legacy_write_retired_response(task_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={
            "detail": {**LEGACY_ESSAY_WRITE_RETIREMENT, "legacyTaskId": task_id}
        },
        headers=_DEPRECATION_HEADERS,
    )
