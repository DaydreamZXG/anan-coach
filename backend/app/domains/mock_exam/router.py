from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.service import KnowledgeService
from app.domains.mock_exam.repository import MockExamRepository
from app.domains.progression.deps import get_progression_service
from app.domains.progression.service import ProgressionService
from app.domains.task_editing.router import (
    get_task_editing_service,
    task_edit_lease_token,
)
from app.domains.task_editing.schemas import TaskEditLeaseToken
from app.domains.task_editing.service import TaskEditingError, TaskEditingService
from app.domains.mock_exam.schemas import (
    MockExamReview,
    MockExamCheckpointRequest,
    MockExamCheckpointReceipt,
    MockExamSession,
    MockExamStartRequest,
    MockExamSubmitRequest,
    MockExamSubmitResponse,
    MockExamTrajectoryEventCommand,
    MockExamTrajectoryEventReceipt,
)
from app.domains.planning.repository import PlanningContractError
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
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)

router = APIRouter(prefix="/api/v1/mock-exams", tags=["mock-exams"])
v2_router = APIRouter(
    prefix="/api/v2/mock-exams",
    tags=["mock-exams-v2"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def _required_idempotency_key(
    value: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=160)
    ],
) -> str:
    return value


def get_mock_exam_service(
    session: Annotated[Session, Depends(get_db_session)],
    knowledge_service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    progression_service: Annotated[
        ProgressionService, Depends(get_progression_service)
    ],
) -> MockExamService:
    return MockExamService(
        repository=MockExamRepository(session),
        knowledge_service=knowledge_service,
        progression_service=progression_service,
    )


@router.post("/weekly", response_model=MockExamSession)
def create_weekly_mock_exam(
    http_request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
    command: Annotated[MockExamStartRequest | None, Body()] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=8, max_length=160),
    ] = None,
) -> MockExamSession | JSONResponse:
    try:
        return service.create_weekly_mock(
            user_id=current_user.user_id,
            command=command,
            command_id=idempotency_key,
        )
    except MockExamUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Not enough weighted guokao questions for weekly mock exam",
        ) from exc
    except PlanningContractError:
        return public_error_response(
            request=http_request,
            status_code=status.HTTP_409_CONFLICT,
            code="mock_plan_task_unavailable",
            message="这项模考任务已经变化，请返回今日计划后重新进入。",
        )


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
    command: MockExamCheckpointRequest,
    http_request: Request,
    idempotency_key: Annotated[str, Depends(_required_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db_session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
    editing: Annotated[TaskEditingService, Depends(get_task_editing_service)],
    lease: Annotated[TaskEditLeaseToken | None, Depends(task_edit_lease_token)],
) -> MockExamCheckpointReceipt | JSONResponse:
    try:
        editing.authorize_write(
            db_session,
            user_id=current_user.user_id,
            resource_type="mock_exam_session",
            resource_id=session_id,
            token=lease,
            expected_revision=command.expected_revision,
        )
        return service.save_checkpoint(
            user_id=current_user.user_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            request=command,
        )
    except MockExamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam not found",
        ) from exc
    except MockExamCheckpointError as exc:
        return public_error_response(
            request=http_request,
            status_code=status.HTTP_409_CONFLICT,
            code=exc.code,
            message=str(exc),
        )
    except TaskEditingError:
        raise


@router.post("/{session_id}/submit", response_model=MockExamSubmitResponse)
def submit_mock_exam(
    session_id: str,
    command: MockExamSubmitRequest,
    http_request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db_session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
    editing: Annotated[TaskEditingService, Depends(get_task_editing_service)],
    lease: Annotated[TaskEditLeaseToken | None, Depends(task_edit_lease_token)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=8, max_length=160),
    ] = None,
) -> MockExamSubmitResponse | JSONResponse:
    try:
        if command.expected_revision is None:
            raise MockExamSubmissionError("Mock submission requires expectedRevision")
        editing.authorize_write(
            db_session,
            user_id=current_user.user_id,
            resource_type="mock_exam_session",
            resource_id=session_id,
            token=lease,
            expected_revision=command.expected_revision,
        )
        return service.submit_mock(
            user_id=current_user.user_id,
            session_id=session_id,
            request=command,
            idempotency_key=idempotency_key,
        )
    except MockExamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mock exam not found",
        ) from exc
    except (
        MockExamSubmissionError,
        MockExamCheckpointError,
        PlanningContractError,
    ) as exc:
        code = getattr(exc, "code", "mock_plan_completion_invalid")
        message = (
            "本场模考与今日计划的归属没有确认成功，请返回今日计划后重新进入。"
            if isinstance(exc, PlanningContractError)
            else str(exc)
        )
        return public_error_response(
            request=http_request,
            status_code=status.HTTP_409_CONFLICT,
            code=code,
            message=message,
        )
    except TaskEditingError:
        raise


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


@v2_router.post(
    "/{session_id}/trajectory-events",
    response_model=PublicApiEnvelope[MockExamTrajectoryEventReceipt],
)
def record_mock_exam_trajectory_event(
    session_id: str,
    request: Request,
    response: Response,
    command: MockExamTrajectoryEventCommand,
    command_id: Annotated[str, Depends(_required_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> PublicApiEnvelope[MockExamTrajectoryEventReceipt]:
    try:
        result = service.record_trajectory_event(
            user_id=current_user.user_id,
            session_id=session_id,
            command_id=command_id,
            command=command,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except MockExamNotFoundError as exc:
        raise HTTPException(status_code=404, detail="模考会话不存在。") from exc
    except MockExamCheckpointError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
