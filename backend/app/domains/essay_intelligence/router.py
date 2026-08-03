from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import RecognitionRevision
from app.core.config import get_settings
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
from app.domains.essay_intelligence.closure.schemas import (
    EssayRewriteCommand,
    EssayRewritePlanTaskCompletionCommand,
    EssayRewritePlanTaskCompletionResult,
    EssayRewriteComparison,
    EssayRewriteResult,
    EssayTransferCommand,
    EssayTransferPlanTaskCompletionCommand,
    EssayTransferPlanTaskCompletionResult,
    EssayTransferResult,
)
from app.domains.essay_intelligence.closure.service import (
    EssayClosureContractError,
    EssayClosureNotFound,
    EssayClosureService,
    EssayTransferContentGapError,
)
from app.domains.essay_intelligence.mastery.schemas import EssayMasteryProjection
from app.domains.essay_intelligence.mastery.service import EssayMasteryService
from app.domains.essay_intelligence.handwriting_schemas import (
    EssayHandwritingArtifactAccess,
    EssayHandwritingCorrectionCommand,
    EssayHandwritingDecisionCommand,
    EssayHandwritingStartCommand,
)
from app.domains.essay_intelligence.handwriting_service import EssayHandwritingService
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    RecognitionError,
    RecognitionNotFound,
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
from app.platform.http import public_error_response
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
    EssayReviewWorkerUnavailable,
    EssayReviewWorkflowService,
)
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)
from app.platform.private_object_access import TosPrivateObjectAccessSigner


router = APIRouter(
    prefix="/api/v2/essay-intelligence",
    tags=["essay-intelligence"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


def get_essay_drafting_completion_service() -> EssayDraftingCompletionService:
    return EssayDraftingCompletionService()


def get_essay_learning_artifact_service() -> EssayLearningArtifactService:
    return EssayLearningArtifactService()


def get_essay_closure_service() -> EssayClosureService:
    return EssayClosureService()


def get_essay_review_workflow_service() -> EssayReviewWorkflowService:
    return EssayReviewWorkflowService()


@router.post(
    "/handwriting-recognitions",
    response_model=PublicApiEnvelope[RecognitionRevision],
)
def start_essay_handwriting_recognition(
    request: Request,
    response: Response,
    command: EssayHandwritingStartCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = EssayHandwritingService().start(
            session,
            settings=get_settings(),
            user_id=UUID(current_user.user_id),
            command=command,
            idempotency_key=command_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except ProcessorConfigurationError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="申论手写识别服务配置无效。") from exc
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put(
    "/handwriting-recognitions/{recognition_revision_id}/candidate",
    response_model=PublicApiEnvelope[RecognitionRevision],
)
def correct_essay_handwriting_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: EssayHandwritingCorrectionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = EssayHandwritingService().correct(
            session,
            settings=get_settings(),
            user_id=UUID(current_user.user_id),
            recognition_revision_id=recognition_revision_id,
            command=command,
            idempotency_key=command_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/handwriting-recognitions/{recognition_revision_id}/confirm",
    response_model=PublicApiEnvelope[RecognitionRevision],
)
def confirm_essay_handwriting_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: EssayHandwritingDecisionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = EssayHandwritingService().confirm(
            session,
            settings=get_settings(),
            user_id=UUID(current_user.user_id),
            recognition_revision_id=recognition_revision_id,
            command=command,
            idempotency_key=command_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/handwriting-artifacts/{artifact_version_id}/access",
    response_model=PublicApiEnvelope[EssayHandwritingArtifactAccess],
)
def access_essay_handwriting_artifact(
    artifact_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayHandwritingArtifactAccess]:
    settings = get_settings()
    try:
        result = EssayHandwritingService(
            access_signer=TosPrivateObjectAccessSigner(
                settings,
                maximum_ttl_seconds=900,
            )
        ).artifact_access(
            session,
            user_id=UUID(current_user.user_id),
            artifact_version_id=artifact_version_id,
            ttl_seconds=settings.source_artifact_access_ttl_seconds,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProcessorConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="申论手写原图访问配置无效。") from exc
    except RecognitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/rewrites/{rewrite_version_id}/comparison",
    response_model=PublicApiEnvelope[EssayRewriteComparison],
)
def get_essay_rewrite_comparison(
    rewrite_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayRewriteComparison]:
    try:
        result = EssayClosureService().rewrite_comparison(
            session,
            user_id=current_user.user_id,
            rewrite_version_id=str(rewrite_version_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except EssayClosureNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayClosureContractError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


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
    workflow: Annotated[
        EssayReviewWorkflowService,
        Depends(get_essay_review_workflow_service),
    ],
) -> PublicApiEnvelope[EssayAttemptCreateResult]:
    try:
        result = workflow.create_attempt(
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
    workflow: Annotated[
        EssayReviewWorkflowService,
        Depends(get_essay_review_workflow_service),
    ],
) -> PublicApiEnvelope[EssayAnswerCreateResult]:
    try:
        result = workflow.create_answer(
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
    "/rewrites",
    response_model=PublicApiEnvelope[EssayRewriteResult],
    status_code=status.HTTP_201_CREATED,
)
def record_essay_rewrite(
    request: Request,
    response: Response,
    command: EssayRewriteCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayRewriteResult]:
    try:
        result = EssayClosureService().record_rewrite(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except (EssayClosureNotFound, EssayWorkflowNotFound) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (EssayClosureContractError, EssayWorkflowContractError) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/rewrites/complete-plan-task",
    response_model=PublicApiEnvelope[EssayRewritePlanTaskCompletionResult],
)
def record_essay_rewrite_and_complete_plan_task(
    request: Request,
    response: Response,
    command: EssayRewritePlanTaskCompletionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    closure: Annotated[EssayClosureService, Depends(get_essay_closure_service)],
) -> PublicApiEnvelope[EssayRewritePlanTaskCompletionResult] | Response:
    try:
        result = closure.record_rewrite_and_complete_plan_task(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except (EssayClosureNotFound, EssayWorkflowNotFound) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PlanningTaskExpiredError as exc:
        session.rollback()
        return planning_task_expired_response(request=request, error=exc)
    except (
        EssayClosureContractError,
        EssayWorkflowContractError,
        PlanningContractError,
    ) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/transfers",
    response_model=PublicApiEnvelope[EssayTransferResult],
    status_code=status.HTTP_201_CREATED,
)
def record_essay_transfer(
    request: Request,
    response: Response,
    command: EssayTransferCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayTransferResult]:
    try:
        result = EssayClosureService().record_transfer(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayTransferContentGapError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail.model_dump(by_alias=True, mode="json"),
        ) from exc
    except EssayClosureNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EssayClosureContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/transfers/complete-plan-task",
    response_model=PublicApiEnvelope[EssayTransferPlanTaskCompletionResult],
)
def record_essay_transfer_and_complete_plan_task(
    request: Request,
    response: Response,
    command: EssayTransferPlanTaskCompletionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    closure: Annotated[EssayClosureService, Depends(get_essay_closure_service)],
) -> PublicApiEnvelope[EssayTransferPlanTaskCompletionResult] | Response:
    try:
        result = closure.record_transfer_and_complete_plan_task(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayTransferContentGapError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail.model_dump(by_alias=True, mode="json"),
        ) from exc
    except EssayClosureNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PlanningTaskExpiredError as exc:
        session.rollback()
        return planning_task_expired_response(request=request, error=exc)
    except (EssayClosureContractError, PlanningContractError) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/mastery",
    response_model=PublicApiEnvelope[EssayMasteryProjection],
)
def get_essay_mastery(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[EssayMasteryProjection]:
    result = EssayMasteryService().projection(
        session,
        user_id=current_user.user_id,
    )
    return public_success_envelope(request=request, response=response, data=result)


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
    workflow: Annotated[
        EssayReviewWorkflowService,
        Depends(get_essay_review_workflow_service),
    ],
) -> PublicApiEnvelope[EssayReviewJobView]:
    try:
        result = workflow.queue_review(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
            commerce_service=commerce_service,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayReviewWorkerUnavailable as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
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
    workflow: Annotated[
        EssayReviewWorkflowService,
        Depends(get_essay_review_workflow_service),
    ],
) -> PublicApiEnvelope[EssayCurrentReviewEnsureResult]:
    try:
        result = workflow.ensure_current_review(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
            commerce_service=commerce_service,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayReviewWorkerUnavailable as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
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
    workflow: Annotated[
        EssayReviewWorkflowService,
        Depends(get_essay_review_workflow_service),
    ],
) -> PublicApiEnvelope[EssayReviewJobView]:
    try:
        result = workflow.regenerate_review_job(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            source_job_id=job_id,
            commerce_service=commerce_service,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except EssayReviewWorkerUnavailable as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
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
    workflow: Annotated[
        EssayReviewWorkflowService,
        Depends(get_essay_review_workflow_service),
    ],
) -> PublicApiEnvelope[EssayReviewJobView]:
    try:
        result = workflow.get_job(
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
    workflow: Annotated[
        EssayReviewWorkflowService,
        Depends(get_essay_review_workflow_service),
    ],
) -> PublicApiEnvelope[EssayReviewResponse]:
    try:
        result = workflow.get_review(
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
    except EssayArtifactContractError:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="essay_artifact_conflict",
            message="这份学习记录没有保存成功，当前填写内容仍然保留，请检查后重试。",
        )
    except PlanningContractError:
        session.rollback()
        return public_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code="plan_task_conflict",
            message="今日计划刚刚发生变化，当前填写内容仍然保留，请刷新今日页后继续。",
        )


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
