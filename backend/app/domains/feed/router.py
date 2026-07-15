from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.feed.schemas import TodayFeed
from app.domains.feed.service import FeedService
from app.domains.knowledge.service import (
    ExamPracticeQuestionsUnavailableError,
    KnowledgeMapUnavailableError,
)
from app.domains.memory.deps import get_memory_service
from app.domains.memory.service import LearnerMemoryService, LearnerProfileRequiredError
from app.domains.taskstream.router import get_taskstream_service
from app.domains.taskstream.service import TaskstreamService

router = APIRouter(prefix="/api/v1/feed", tags=["feed"])


def get_feed_service(
    taskstream_service: Annotated[TaskstreamService, Depends(get_taskstream_service)],
    memory_service: Annotated[LearnerMemoryService, Depends(get_memory_service)],
) -> FeedService:
    return FeedService(
        taskstream_service=taskstream_service,
        memory_service=memory_service,
    )


@router.get("/today", response_model=TodayFeed)
def get_today_feed(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[FeedService, Depends(get_feed_service)],
) -> TodayFeed:
    try:
        return service.get_today(user_id=current_user.user_id)
    except LearnerProfileRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Learner profile is required before today's feed",
        ) from exc
    except (KnowledgeMapUnavailableError, ExamPracticeQuestionsUnavailableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Guokao knowledge map is required before today's feed",
        ) from exc
