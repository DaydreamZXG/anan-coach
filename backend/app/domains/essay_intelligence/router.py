from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.essay_intelligence.learning_repository import (
    EssayArtifactContractError,
    EssayArtifactNotFound,
)
from app.domains.essay_intelligence.learning_schemas import (
    EssayArtifactPlanTaskCompletionCommand,
    EssayArtifactPlanTaskCompletionResult,
    EssayLearningArtifactCommand,
    EssayLearningArtifactVersion,
    EssayTaskArtifactState,
    EssayTaskWorkspace,
)
from app.domains.essay_intelligence.learning_service import (
    EssayLearningArtifactService,
)
from app.domains.essay_intelligence.drafting_schemas import (
    EssayDraftingCompletionCommand,
    EssayDraftingCompletionResult,
)
from app.domains.essay_intelligence.drafting_service import (
    EssayDraftingCompletionService,
)
from app.domains.planning.errors import PlanningTaskExpiredError
from app.domains.planning.http import planning_task_expired_response
from app.domains.planning.repository import PlanningContractError
from app.domains.commerce.deps import get_commerce_service
from app.domains.commerce.repository import CommerceQuotaExceededError
from app.domains.commerce.service import CommerceService
from app.domains.essay_intelligence.schemas import EssayReviewResponse
from app.domains.essay_intelligence.workflow_repository import (
    EssayWorkflowContractError,
    EssayWorkflowNotFound,
)
from app.domains.essay_intelligence.workflow_schemas import (
    EssayAnswerCreateCommand,
    EssayAnswerCreateResult,
    EssayAttemptCreateCommand,
    EssayAttemptCreateResult,
    EssayCurrentReviewEnsureCommand,
    EssayCurrentReviewEnsureResult,
    EssayReviewJobView,
    EssayReviewQueueCommand,
)
from app.domains.essay_intelligence.workflow_service import (
    EssayReviewWorkflowService,
)
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/essay-intelligence",
    tags=["essay-intelligence"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def get_essay_drafting_completion_service() -> EssayDraftingCompletionService:
    return EssayDraftingCompletionService()


def get_essay_learning_artifact_service() -> EssayLearningArtifactService:
    return EssayLearningArtifactService()


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


@router.post(
    "/attempts",
    response_model=PublicApiEnvelope[EssayAttemptCreateResult],
    status_code=status.HTTP_201_CREATED,
)
def create_essay_attempt(
    request: Request,
    response: Response,
    command: EssayAttemptCreateCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayAttemptCreateResult]:
    try:
        result = EssayReviewWorkflowService().create_attempt(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayWorkflowNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayWorkflowContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/answers",
    response_model=PublicApiEnvelope[EssayAnswerCreateResult],
    status_code=status.HTTP_201_CREATED,
)
def create_essay_answer(
    request: Request,
    response: Response,
    command: EssayAnswerCreateCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayAnswerCreateResult]:
    try:
        result = EssayReviewWorkflowService().create_answer(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayWorkflowNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayWorkflowContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/drafting/complete-plan-task",
    response_model=PublicApiEnvelope[EssayDraftingCompletionResult],
)
def complete_essay_drafting_plan_task(
    request: Request,
    response: Response,
    command: EssayDraftingCompletionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    drafting: Annotated[
        EssayDraftingCompletionService,
        Depends(get_essay_drafting_completion_service),
    ],
) -> PublicApiEnvelope[EssayDraftingCompletionResult] | Response:
    try:
        result = drafting.complete(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayWorkflowNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PlanningTaskExpiredError as exc:
        session.rollback()
        return planning_task_expired_response(request=request, error=exc)
    except (
        EssayWorkflowContractError,
        EssayArtifactContractError,
        PlanningContractError,
    ) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/reviews",
    response_model=PublicApiEnvelope[EssayReviewJobView],
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_grounded_essay_review(
    request: Request,
    response: Response,
    command: EssayReviewQueueCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    commerce_service: Annotated[CommerceService, Depends(get_commerce_service)],
) -> PublicApiEnvelope[EssayReviewJobView]:
    try:
        result = EssayReviewWorkflowService().queue_review(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
            commerce_service=commerce_service,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayWorkflowNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayWorkflowContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CommerceQuotaExceededError as exc:
        # A denied execution still advances the durable intent to waiting_quota
        # and records its outbox/audit evidence. No job or reservation is created.
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Essay review quota exceeded",
        ) from exc


@router.post(
    "/reviews/current/ensure",
    response_model=PublicApiEnvelope[EssayCurrentReviewEnsureResult],
    status_code=status.HTTP_202_ACCEPTED,
)
def ensure_current_grounded_essay_review(
    request: Request,
    response: Response,
    command: EssayCurrentReviewEnsureCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    commerce_service: Annotated[CommerceService, Depends(get_commerce_service)],
) -> PublicApiEnvelope[EssayCurrentReviewEnsureResult]:
    try:
        result = EssayReviewWorkflowService().ensure_current_review(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
            commerce_service=commerce_service,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayWorkflowNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayWorkflowContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/review-jobs/{job_id}/regenerate",
    response_model=PublicApiEnvelope[EssayReviewJobView],
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate_grounded_essay_review(
    job_id: UUID,
    request: Request,
    response: Response,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    commerce_service: Annotated[CommerceService, Depends(get_commerce_service)],
) -> PublicApiEnvelope[EssayReviewJobView]:
    try:
        result = EssayReviewWorkflowService().regenerate_review_job(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            source_job_id=job_id,
            commerce_service=commerce_service,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayWorkflowNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayWorkflowContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CommerceQuotaExceededError as exc:
        # Regeneration keeps the prior immutable job and appends a quota-waiting
        # intent successor. A later replay can append the next queued execution.
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Essay review quota exceeded",
        ) from exc


@router.get(
    "/review-jobs/{job_id}",
    response_model=PublicApiEnvelope[EssayReviewJobView],
)
def get_grounded_essay_review_job(
    job_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayReviewJobView]:
    try:
        result = EssayReviewWorkflowService().get_job(
            session,
            user_id=current_user.user_id,
            job_id=job_id,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except EssayWorkflowNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayWorkflowContractError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/reviews/{review_version_id}",
    response_model=PublicApiEnvelope[EssayReviewResponse],
)
def get_grounded_essay_review(
    review_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayReviewResponse]:
    try:
        result = EssayReviewWorkflowService().get_review(
            session,
            user_id=current_user.user_id,
            review_version_id=review_version_id,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except EssayWorkflowNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/artifacts",
    response_model=PublicApiEnvelope[EssayLearningArtifactVersion],
)
def record_learning_artifact(
    request: Request,
    response: Response,
    command: EssayLearningArtifactCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayLearningArtifactVersion]:
    try:
        result = EssayLearningArtifactService().record(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayArtifactContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/artifacts/complete-plan-task",
    response_model=PublicApiEnvelope[EssayArtifactPlanTaskCompletionResult],
)
def record_artifact_and_complete_plan_task(
    request: Request,
    response: Response,
    command: EssayArtifactPlanTaskCompletionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    artifacts: Annotated[
        EssayLearningArtifactService,
        Depends(get_essay_learning_artifact_service),
    ],
) -> PublicApiEnvelope[EssayArtifactPlanTaskCompletionResult] | Response:
    try:
        result = artifacts.record_and_complete_plan_task(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except PlanningTaskExpiredError as exc:
        session.rollback()
        return planning_task_expired_response(request=request, error=exc)
    except (EssayArtifactContractError, PlanningContractError) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/artifacts/{artifact_version_id}",
    response_model=PublicApiEnvelope[EssayLearningArtifactVersion],
)
def get_learning_artifact(
    artifact_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayLearningArtifactVersion]:
    try:
        result = EssayLearningArtifactService().get_owned(
            session,
            user_id=current_user.user_id,
            artifact_version_id=str(artifact_version_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except EssayArtifactNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/tasks/{task_version_id}/artifacts/current",
    response_model=PublicApiEnvelope[EssayTaskArtifactState],
)
def get_current_task_artifacts(
    task_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayTaskArtifactState]:
    try:
        result = EssayLearningArtifactService().current_for_task(
            session,
            user_id=current_user.user_id,
            task_version_id=str(task_version_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except EssayArtifactNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/tasks/{task_version_id}/workspace",
    response_model=PublicApiEnvelope[EssayTaskWorkspace],
)
def get_task_workspace(
    task_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayTaskWorkspace]:
    try:
        result = EssayLearningArtifactService().workspace(
            session,
            user_id=current_user.user_id,
            task_version_id=str(task_version_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except EssayArtifactNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayArtifactContractError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
