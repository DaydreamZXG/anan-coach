from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.calibration.router import get_calibration_service
from app.domains.calibration.service import CalibrationService
from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.service import (
    ExamPracticeQuestionsUnavailableError,
    KnowledgeService,
    KnowledgeMapUnavailableError,
)
from app.domains.memory.deps import get_memory_service
from app.domains.memory.service import LearnerMemoryService, LearnerProfileRequiredError
from app.domains.taskstream.schemas import TodayTaskstream, TomorrowPreview
from app.domains.taskstream.service import TaskstreamService

router = APIRouter(prefix="/api/v1/taskstream", tags=["taskstream"])


def get_taskstream_service(
    knowledge_service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    memory_service: Annotated[LearnerMemoryService, Depends(get_memory_service)],
    calibration_service: Annotated[CalibrationService, Depends(get_calibration_service)],
) -> TaskstreamService:
    return TaskstreamService(
        knowledge_service=knowledge_service,
        memory_service=memory_service,
        calibration_service=calibration_service,
    )


@router.get("/today", response_model=TodayTaskstream)
def get_today_taskstream(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[TaskstreamService, Depends(get_taskstream_service)],
) -> TodayTaskstream:
    try:
        return service.get_today(user_id=current_user.user_id)
    except LearnerProfileRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Learner profile is required before today's plan",
        ) from exc
    except (KnowledgeMapUnavailableError, ExamPracticeQuestionsUnavailableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Guokao knowledge map is required before today's plan",
        ) from exc


@router.get("/tomorrow", response_model=TomorrowPreview)
def get_tomorrow_taskstream(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[TaskstreamService, Depends(get_taskstream_service)],
) -> TomorrowPreview:
    try:
        return service.get_tomorrow(user_id=current_user.user_id)
    except LearnerProfileRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Learner profile is required before tomorrow's preview",
        ) from exc
