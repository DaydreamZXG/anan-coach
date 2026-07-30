from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.position_decision.schemas import (
    PositionDecisionResponse,
    RiskPreference,
)
from app.domains.position_decision.service import PositionDecisionService
from app.domains.positions.repository import PositionSearchError
from app.domains.positions.router import get_position_service
from app.domains.positions.service import PositionService

router = APIRouter(prefix="/api/v1/position-decisions", tags=["position-decisions"])


def _raise_release_unbound_endpoint_retired() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Release-unbound position recommendations are retired; "
            "use /api/v2/qualification/positions/workspace"
        ),
    )


def get_position_decision_service(
    position_service: Annotated[PositionService, Depends(get_position_service)],
) -> PositionDecisionService:
    return PositionDecisionService(position_service=position_service)


@router.get("/recommendations", response_model=PositionDecisionResponse)
def recommend_positions(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        PositionDecisionService,
        Depends(get_position_decision_service),
    ],
    education: str = Query(min_length=1, max_length=40),
    major: str = Query(min_length=1, max_length=120),
    political_status: str = Query(alias="politicalStatus", min_length=1, max_length=40),
    current_score: float = Query(alias="currentScore", ge=0, le=300),
    grassroots_years: int = Query(default=0, alias="grassrootsYears", ge=0, le=50),
    fresh_graduate: bool | None = Query(default=None, alias="freshGraduate"),
    risk_preference: RiskPreference = Query(default="balanced", alias="riskPreference"),
    year: int = Query(default=2026, ge=2000, le=2100),
    region: str | None = Query(default=None, min_length=1, max_length=40),
    city: str | None = Query(default=None, min_length=1, max_length=40),
    has_extra_exam: bool | None = Query(default=None, alias="hasExtraExam"),
    limit: int = Query(default=30, ge=1, le=100),
) -> PositionDecisionResponse:
    del current_user
    _raise_release_unbound_endpoint_retired()
    try:
        return service.recommend_positions(
            year=year,
            education=education,
            major=major,
            fresh_graduate=fresh_graduate,
            political_status=political_status,
            grassroots_years=grassroots_years,
            region=region,
            city=city,
            has_extra_exam=has_extra_exam,
            current_score=current_score,
            risk_preference=risk_preference,
            limit=limit,
        )
    except PositionSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
