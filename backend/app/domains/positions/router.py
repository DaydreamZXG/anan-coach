from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.positions.repository import PositionRepository, PositionSearchError
from app.domains.positions.schemas import (
    MajorTreeResponse,
    PositionDetail,
    PositionSearchResponse,
    PositionTargetScoreResponse,
    ProvinceListResponse,
)
from app.domains.positions.service import PositionNotFoundError, PositionService

router = APIRouter(prefix="/api/v1/positions", tags=["positions"])


def get_position_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> PositionService:
    return PositionService(PositionRepository(session))


@router.get("/search", response_model=PositionSearchResponse)
def search_positions(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[PositionService, Depends(get_position_service)],
    education: str = Query(min_length=1, max_length=40),
    major: str = Query(min_length=1, max_length=120),
    political_status: str = Query(alias="politicalStatus", min_length=1, max_length=40),
    grassroots_years: int = Query(default=0, alias="grassrootsYears", ge=0, le=50),
    fresh_graduate: bool | None = Query(default=None, alias="freshGraduate"),
    year: int = Query(default=2026, ge=2000, le=2100),
    region: str | None = Query(default=None, min_length=1, max_length=40),
    city: str | None = Query(default=None, min_length=1, max_length=40),
    has_extra_exam: bool | None = Query(default=None, alias="hasExtraExam"),
    limit: int = Query(default=20, ge=1, le=100),
) -> PositionSearchResponse:
    del current_user
    try:
        return service.search_positions(
            year=year,
            education=education,
            major=major,
            fresh_graduate=fresh_graduate,
            political_status=political_status,
            grassroots_years=grassroots_years,
            region=region,
            city=city,
            has_extra_exam=has_extra_exam,
            limit=limit,
        )
    except PositionSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/regions", response_model=ProvinceListResponse)
def list_regions(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[PositionService, Depends(get_position_service)],
) -> ProvinceListResponse:
    del current_user
    return service.list_regions()


@router.get("/majors", response_model=MajorTreeResponse)
def list_majors(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[PositionService, Depends(get_position_service)],
) -> MajorTreeResponse:
    del current_user
    return service.list_majors()


@router.get("/{position_id}", response_model=PositionDetail)
def get_position_detail(
    position_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[PositionService, Depends(get_position_service)],
) -> PositionDetail:
    del current_user
    try:
        return service.get_position_detail(position_id=position_id)
    except PositionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Position not found",
        ) from exc


@router.get("/{position_id}/target-score", response_model=PositionTargetScoreResponse)
def get_position_target_score(
    position_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[PositionService, Depends(get_position_service)],
) -> PositionTargetScoreResponse:
    del current_user
    try:
        return service.get_target_score_response(position_id=position_id)
    except PositionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Position not found",
        ) from exc
