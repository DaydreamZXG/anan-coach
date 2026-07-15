from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.service import KnowledgeService
from app.domains.mock_exam.repository import MockExamRepository
from app.domains.progression.deps import get_progression_service
from app.domains.progression.service import ProgressionService
from app.domains.mock_exam.schemas import (
    MockExamReview,
    MockExamCheckpointRequest,
    MockExamCheckpointReceipt,
    MockExamSession,
    MockExamSubmitRequest,
    MockExamSubmitResponse,
)
from app.domains.mock_exam.service import (
    MockExamNotFoundError,
    MockExamCheckpointError,
    MockExamDisputeError,
    MockExamService,
    MockExamSubmissionError,
    MockExamUnavailableError,
)
from app.domains.xingce_learning.real_attempt.schemas import (
    QuestionDisputeReceipt,
    ReportQuestionDisputeCommand,
)

router = APIRouter(prefix="/api/v1/mock-exams", tags=["mock-exams"])


def _required_idempotency_key(
    value: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=160)
    ],
) -> str:
    return value


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


@router.get("/{session_id}", response_model=MockExamSession)
def get_mock_exam_session(
    session_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> MockExamSession:
    try:
        return service.get_session(
            user_id=current_user.user_id,
            session_id=session_id,
        )
    except (MockExamNotFoundError, MockExamUnavailableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam session not found or not recoverable",
        ) from exc


@router.put("/{session_id}/checkpoint", response_model=MockExamCheckpointReceipt)
def save_mock_exam_checkpoint(
    session_id: str,
    request: MockExamCheckpointRequest,
    idempotency_key: Annotated[str, Depends(_required_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> MockExamCheckpointReceipt:
    try:
        return service.save_checkpoint(
            user_id=current_user.user_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            request=request,
        )
    except MockExamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam not found",
        ) from exc
    except MockExamCheckpointError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post("/{session_id}/submit", response_model=MockExamSubmitResponse)
def submit_mock_exam(
    session_id: str,
    request: MockExamSubmitRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=8, max_length=160),
    ] = None,
) -> MockExamSubmitResponse:
    try:
        return service.submit_mock(
            user_id=current_user.user_id,
            session_id=session_id,
            request=request,
            idempotency_key=idempotency_key,
        )
    except MockExamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam not found",
        ) from exc
    except (MockExamSubmissionError, MockExamCheckpointError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mock exam submission is invalid",
        ) from exc


@router.post("/{session_id}/disputes", response_model=QuestionDisputeReceipt)
def report_mock_exam_question_dispute(
    session_id: str,
    command: ReportQuestionDisputeCommand,
    idempotency_key: Annotated[str, Depends(_required_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> QuestionDisputeReceipt:
    try:
        return service.report_question_dispute(
            user_id=current_user.user_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            command=command,
        )
    except MockExamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam not found",
        ) from exc
    except MockExamDisputeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
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
