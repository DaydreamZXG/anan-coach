from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.memory.deps import get_memory_service
from app.domains.memory.repository import (
    LearnerMemoryRecord,
    LearnerProfileRecord,
)
from app.domains.memory.schemas import (
    LearnerMemory,
    LearnerMemorySummary,
    LearnerProfile,
    LearnerProfileUpdate,
)
from app.domains.memory.service import LearnerMemoryService

router = APIRouter(prefix="/api/v1/learner", tags=["learner"])


@router.get("/memory", response_model=LearnerMemory)
def get_memory(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[LearnerMemoryService, Depends(get_memory_service)],
) -> LearnerMemory:
    return _memory_response(service.get_memory(user_id=current_user.user_id))


@router.put(
    "/profile",
    response_model=LearnerProfile,
    deprecated=True,
    responses={status.HTTP_410_GONE: {"description": "Use v2 onboarding commands."}},
)
def update_profile(
    update: LearnerProfileUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[LearnerMemoryService, Depends(get_memory_service)],
) -> JSONResponse:
    del update, current_user, service
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={
            "code": "learner_profile_v1_retired",
            "message": "请使用 v2 资格建档和学习时间命令。",
        },
    )


def _memory_response(record: LearnerMemoryRecord) -> LearnerMemory:
    return LearnerMemory(
        profile=_profile_response(record.profile),
        summary=LearnerMemorySummary(
            practice_answers=record.summary.practice_answers,
            wrong_answers=record.summary.wrong_answers,
            essay_attempts=record.summary.essay_attempts,
        ),
    )


def _profile_response(record: LearnerProfileRecord) -> LearnerProfile:
    return LearnerProfile(
        target_exam=record.target_exam,
        target_region=record.target_region,
        position_group=record.position_group,
        daily_minutes=record.daily_minutes,
        education=record.education,
        major=record.major,
        fresh_graduate=record.fresh_graduate,
        political_status=record.political_status,
        grassroots_years=record.grassroots_years,
        study_mode=record.study_mode,  # type: ignore[arg-type]
        target_position_id=record.target_position_id,
        target_score=record.target_score,
        baseline_source=record.baseline_source,  # type: ignore[arg-type]
        baseline_xingce_score=record.baseline_xingce_score,
        baseline_shenlun_score=record.baseline_shenlun_score,
        baseline_weak_modules=record.baseline_weak_modules or [],
        onboarding_complete=record.onboarding_complete,
    )
