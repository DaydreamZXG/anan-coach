from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.service import KnowledgeService, QuestionNotFoundError
from app.domains.mock_exam.repository import MockExamRepository
from app.domains.progression.deps import get_progression_service
from app.domains.progression.service import ProgressionService
from app.domains.mock_exam.schemas import (
    MockExamReview,
    MockExamSession,
    MockExamSubmitRequest,
    MockExamSubmitResponse,
)
from app.domains.mock_exam.service import (
    MockExamNotFoundError,
    MockExamService,
    MockExamSubmissionError,
    MockExamUnavailableError,
)

router = APIRouter(prefix="/api/v1/mock-exams", tags=["mock-exams"])


def get_mock_exam_service(
    session: Annotated[Session, Depends(get_db_session)],
    knowledge_service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    progression_service: Annotated[ProgressionService, Depends(get_progression_service)],
) -> MockExamService:
    return MockExamService(
        repository=MockExamRepository(session),
        knowledge_service=knowledge_service,
        progression_service=progression_service,
    )


@router.post("/weekly", response_model=MockExamSession)
def create_weekly_mock_exam(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> MockExamSession:
    try:
        return service.create_weekly_mock(user_id=current_user.user_id)
    except MockExamUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Not enough weighted guokao questions for weekly mock exam",
        ) from exc
    except QuestionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mock exam question details are unavailable",
        ) from exc


@router.post("/{session_id}/submit", response_model=MockExamSubmitResponse)
def submit_mock_exam(
    session_id: str,
    request: MockExamSubmitRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> MockExamSubmitResponse:
    try:
        return service.submit_mock(
            user_id=current_user.user_id,
            session_id=session_id,
            request=request,
        )
    except MockExamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam not found",
        ) from exc
    except MockExamSubmissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mock exam submission is invalid",
        ) from exc


@router.get("/{session_id}/review", response_model=MockExamReview)
def get_mock_exam_review(
    session_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> MockExamReview:
    try:
        return service.get_review(user_id=current_user.user_id, session_id=session_id)
    except MockExamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam review not found",
        ) from exc


@router.post("/{session_id}/review/view", response_model=MockExamReview)
def mark_mock_exam_review_viewed(
    session_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> MockExamReview:
    try:
        return service.mark_review_viewed(
            user_id=current_user.user_id,
            session_id=session_id,
        )
    except MockExamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam review not found",
        ) from exc
