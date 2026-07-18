from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.learning_brain.schemas import (
    ErrorPatternDetail,
    LearningBrainProfileVersion,
    ModuleAbility,
    StrategyCandidateCommand,
    StrategyCandidateResult,
    StrategyDecisionCommand,
    StrategyDecisionResult,
)
from app.domains.learning_brain.service import (
    LearningBrainContractError,
    LearningBrainNotFoundError,
    LearningBrainService,
)
from app.domains.qualification.repository import PositionRuleReleaseUnavailable
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/learning-brain",
    tags=["learning-brain"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def get_learning_brain_service() -> LearningBrainService:
    return LearningBrainService()


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


@router.get(
    "/profile",
    response_model=PublicApiEnvelope[LearningBrainProfileVersion],
)
def get_learning_brain_profile(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[LearningBrainService, Depends(get_learning_brain_service)],
) -> PublicApiEnvelope[LearningBrainProfileVersion]:
    try:
        result = service.get_or_create_profile(session, user_id=current_user.user_id)
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except (LearningBrainContractError, PositionRuleReleaseUnavailable) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/modules/{track}/{module_key}",
    response_model=PublicApiEnvelope[ModuleAbility],
)
def get_learning_brain_module(
    track: Literal["xingce", "essay"],
    module_key: str,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[LearningBrainService, Depends(get_learning_brain_service)],
) -> PublicApiEnvelope[ModuleAbility]:
    try:
        result = service.get_module(
            session,
            user_id=current_user.user_id,
            track=track,
            module_key=module_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except LearningBrainNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (LearningBrainContractError, PositionRuleReleaseUnavailable) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/error-patterns/{pattern_id}",
    response_model=PublicApiEnvelope[ErrorPatternDetail],
)
def get_learning_brain_error_pattern(
    pattern_id: str,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[LearningBrainService, Depends(get_learning_brain_service)],
) -> PublicApiEnvelope[ErrorPatternDetail]:
    try:
        result = service.get_error_pattern(
            session,
            user_id=current_user.user_id,
            pattern_id=pattern_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except LearningBrainNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (LearningBrainContractError, PositionRuleReleaseUnavailable) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/strategy-candidates",
    response_model=PublicApiEnvelope[StrategyCandidateResult],
)
def create_learning_strategy_candidate(
    command: StrategyCandidateCommand,
    request: Request,
    response: Response,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[LearningBrainService, Depends(get_learning_brain_service)],
) -> PublicApiEnvelope[StrategyCandidateResult]:
    try:
        result = service.create_strategy_candidate(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except (LearningBrainContractError, PositionRuleReleaseUnavailable) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/strategy-candidates/{candidate_id}/decision",
    response_model=PublicApiEnvelope[StrategyDecisionResult],
)
def decide_learning_strategy_candidate(
    candidate_id: UUID,
    command: StrategyDecisionCommand,
    request: Request,
    response: Response,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[LearningBrainService, Depends(get_learning_brain_service)],
) -> PublicApiEnvelope[StrategyDecisionResult]:
    try:
        result = service.decide_strategy_candidate(
            session,
            user_id=current_user.user_id,
            candidate_id=str(candidate_id),
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except LearningBrainNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LearningBrainContractError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
