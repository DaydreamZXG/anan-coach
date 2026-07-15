from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.knowledge.router import get_knowledge_service
from app.domains.knowledge.service import KnowledgeService, QuestionNotFoundError
from app.domains.memory.router import _profile_response
from app.domains.memory.schemas import LearnerProfile
from app.domains.onboarding.repository import (
    OnboardingRepository,
)
from app.domains.onboarding.schemas import (
    OnboardingCompleteResponse,
    OnboardingProfileUpdate,
    PlacementSubmitRequest,
    PlacementSubmitResponse,
    PlacementTestTask,
)
from app.domains.onboarding.service import (
    OnboardingService,
    PlacementAssemblyError,
)
from app.domains.onboarding.progress_repository import OnboardingProgressError
from app.domains.onboarding.progress_service import OnboardingProgressService
from app.domains.onboarding.v2_schemas import (
    OnboardingProgress,
    OnboardingResultProjection,
    OnboardingResultAcknowledgement,
    QualificationMilestoneCommand,
    StudyPreferenceResult,
    StudyPreferenceWrite,
    WeeklyStudyPreference,
)
from app.domains.positions.router import get_position_service
from app.domains.positions.service import PositionNotFoundError, PositionService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
)

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])
v2_router = APIRouter(
    prefix="/api/v2/onboarding",
    tags=["onboarding-v2"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def _v2_idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


def get_onboarding_service(
    session: Annotated[Session, Depends(get_db_session)],
    knowledge_service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    position_service: Annotated[PositionService, Depends(get_position_service)],
) -> OnboardingService:
    return OnboardingService(
        repository=OnboardingRepository(session),
        knowledge_service=knowledge_service,
        position_service=position_service,
    )


@router.get("/profile", response_model=LearnerProfile)
def get_onboarding_profile(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[OnboardingService, Depends(get_onboarding_service)],
) -> LearnerProfile:
    return _profile_response(service.get_profile(user_id=current_user.user_id))


@router.put("/profile", response_model=LearnerProfile)
def update_onboarding_profile(
    update: OnboardingProfileUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[OnboardingService, Depends(get_onboarding_service)],
) -> LearnerProfile:
    try:
        return _profile_response(
            service.update_profile(user_id=current_user.user_id, update=update)
        )
    except PositionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Position not found",
        ) from exc


@router.post(
    "/complete",
    response_model=OnboardingCompleteResponse,
    deprecated=True,
    responses={status.HTTP_410_GONE: {"description": "Use the v2 onboarding owner."}},
)
def complete_onboarding(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> JSONResponse:
    del current_user
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={
            "code": "onboarding_v1_retired",
            "message": "请使用 v2 建档进度、学习时间与结果确认命令。",
        },
    )


@router.get("/placement-test", response_model=PlacementTestTask, deprecated=True)
def get_placement_test(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[OnboardingService, Depends(get_onboarding_service)],
) -> PlacementTestTask:
    del current_user
    try:
        return service.get_placement_test()
    except PlacementAssemblyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post(
    "/placement-test/answers",
    response_model=PlacementSubmitResponse,
    deprecated=True,
)
def submit_placement_test(
    request: PlacementSubmitRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[OnboardingService, Depends(get_onboarding_service)],
) -> PlacementSubmitResponse:
    try:
        return service.submit_placement_test(
            user_id=current_user.user_id,
            answers={
                answer.question_id: answer.selected_answer for answer in request.answers
            },
        )
    except QuestionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found",
        ) from exc


@v2_router.get(
    "/progress",
    response_model=PublicApiEnvelope[OnboardingProgress],
)
def get_onboarding_progress(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[OnboardingProgress]:
    progress = OnboardingProgressService().get_progress(
        session,
        user_id=current_user.user_id,
    )
    session.commit()
    return public_success_envelope(request=request, response=response, data=progress)


@v2_router.post(
    "/qualification/complete",
    response_model=PublicApiEnvelope[OnboardingProgress],
)
def complete_qualification_milestone(
    request: Request,
    response: Response,
    command: QualificationMilestoneCommand,
    idempotency_key: Annotated[str, Depends(_v2_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[OnboardingProgress] | JSONResponse:
    try:
        progress = OnboardingProgressService().complete_qualification_milestone(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{idempotency_key}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=progress)
    except OnboardingProgressError as exc:
        session.rollback()
        return _onboarding_error_response(request, exc)


@v2_router.get(
    "/study-preference",
    response_model=PublicApiEnvelope[WeeklyStudyPreference],
)
def get_weekly_study_preference(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[WeeklyStudyPreference] | JSONResponse:
    try:
        preference = OnboardingProgressService().get_study_preference(
            session,
            user_id=current_user.user_id,
        )
        return public_success_envelope(request=request, response=response, data=preference)
    except OnboardingProgressError as exc:
        session.rollback()
        return _onboarding_error_response(request, exc)


@v2_router.get(
    "/result",
    response_model=PublicApiEnvelope[OnboardingResultProjection],
)
def get_onboarding_result(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[OnboardingResultProjection] | JSONResponse:
    try:
        result = OnboardingProgressService().get_result_projection(
            session,
            user_id=current_user.user_id,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except OnboardingProgressError as exc:
        session.rollback()
        return _onboarding_error_response(request, exc)


@v2_router.put(
    "/study-preference",
    response_model=PublicApiEnvelope[StudyPreferenceResult],
)
def put_weekly_study_preference(
    request: Request,
    response: Response,
    command: StudyPreferenceWrite,
    idempotency_key: Annotated[str, Depends(_v2_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[StudyPreferenceResult] | JSONResponse:
    try:
        result = OnboardingProgressService().record_study_preference(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{idempotency_key}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except OnboardingProgressError as exc:
        session.rollback()
        return _onboarding_error_response(request, exc)


@v2_router.post(
    "/complete",
    response_model=PublicApiEnvelope[OnboardingProgress],
)
def acknowledge_onboarding_result(
    request: Request,
    response: Response,
    command: OnboardingResultAcknowledgement,
    idempotency_key: Annotated[str, Depends(_v2_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[OnboardingProgress] | JSONResponse:
    try:
        progress = OnboardingProgressService().acknowledge_result(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{idempotency_key}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=progress)
    except OnboardingProgressError as exc:
        session.rollback()
        return _onboarding_error_response(request, exc)


def _onboarding_error_response(
    request: Request,
    exc: OnboardingProgressError,
) -> JSONResponse:
    status_code = (
        status.HTTP_404_NOT_FOUND
        if exc.code in {"qualification_profile_missing", "study_preference_missing"}
        else status.HTTP_409_CONFLICT
    )
    return public_error_response(
        request=request,
        status_code=status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )
