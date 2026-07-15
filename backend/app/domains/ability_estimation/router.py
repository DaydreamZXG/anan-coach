from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.ability_estimation.schemas import (
    AbilityEstimateCurrent,
    ReportedScoreEvidenceRequest,
    ReportedScoreEvidenceResult,
    ScoreEvidenceHistory,
)
from app.domains.ability_estimation.service import (
    AbilityEstimateContractError,
    AbilityEstimateService,
)
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.qualification.repository import PositionRuleReleaseUnavailable
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2",
    tags=["ability-estimation"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


@router.get(
    "/ability-estimates/current",
    response_model=PublicApiEnvelope[AbilityEstimateCurrent],
)
def get_current_ability_estimate(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[AbilityEstimateCurrent]:
    try:
        result = AbilityEstimateService().get_current(
            session,
            user_id=current_user.user_id,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionRuleReleaseUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/score-evidence",
    response_model=PublicApiEnvelope[ScoreEvidenceHistory],
)
def get_score_evidence_history(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ScoreEvidenceHistory]:
    try:
        result = AbilityEstimateService().list_score_evidence(
            session,
            user_id=current_user.user_id,
        )
        return public_success_envelope(request=request, response=response, data=result)
    except PositionRuleReleaseUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/score-evidence/reported",
    response_model=PublicApiEnvelope[ReportedScoreEvidenceResult],
)
def report_score_evidence(
    request: Request,
    response: Response,
    report: ReportedScoreEvidenceRequest,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ReportedScoreEvidenceResult]:
    try:
        result = AbilityEstimateService().record_reported_score(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            report=report,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except (AbilityEstimateContractError, PositionRuleReleaseUnavailable) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
