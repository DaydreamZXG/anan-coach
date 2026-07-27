from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.service import (
    ExamPracticeQuestionsUnavailableError,
    KnowledgeService,
    KnowledgeMapUnavailableError,
    QuestionNotFoundError,
)
from app.domains.memory.deps import get_memory_service
from app.domains.memory.service import LearnerMemoryService, LearnerProfileRequiredError
from app.domains.practice.schemas import AnswerFeedback, AnswerSubmission, PracticeTask
from app.domains.practice.repository import PracticeRepository
from app.domains.practice.service import (
    PracticeService,
    PracticeTaskNotFoundError,
    PracticeTaskVersionError,
)
from app.domains.progression.deps import get_progression_service
from app.domains.progression.service import ProgressionService

router = APIRouter(prefix="/api/v1/practice", tags=["practice"])


def get_practice_service(
    session: Annotated[Session, Depends(get_db_session)],
    knowledge_service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    memory_service: Annotated[LearnerMemoryService, Depends(get_memory_service)],
) -> PracticeService:
    return PracticeService(
        knowledge_service=knowledge_service,
        memory_service=memory_service,
        repository=PracticeRepository(session),
    )


@router.get("/tasks/{task_id}", response_model=PracticeTask)
def get_practice_task(
    task_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
) -> PracticeTask:
    try:
        return service.get_task(task_id, user_id=current_user.user_id)
    except LearnerProfileRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Learner profile is required before practice",
        ) from exc
    except (
        KnowledgeMapUnavailableError,
        ExamPracticeQuestionsUnavailableError,
        PracticeTaskVersionError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Guokao knowledge map is required before practice",
        ) from exc
    except PracticeTaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Practice task not found",
        ) from exc


@router.post("/tasks/{task_id}/answers", response_model=AnswerFeedback)
def submit_practice_answer(
    task_id: str,
    submission: AnswerSubmission,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
    progression_service: Annotated[ProgressionService, Depends(get_progression_service)],
) -> AnswerFeedback:
    try:
        feedback = service.submit_answer(
            task_id=task_id,
            user_id=current_user.user_id,
            question_id=submission.question_id,
            selected_answer=submission.selected_answer,
            task_version=submission.task_version,
        )
    except LearnerProfileRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Learner profile is required before practice",
        ) from exc
    except PracticeTaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Practice task not found",
        ) from exc
    except PracticeTaskVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Practice task version is invalid or no longer verifiable",
        ) from exc
    except QuestionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found",
        ) from exc
    result = progression_service.record_practice_answer(
        user_id=current_user.user_id,
        correct=feedback.correct,
    )
    feedback.xp_gained = result.merit_delta
    return feedback
