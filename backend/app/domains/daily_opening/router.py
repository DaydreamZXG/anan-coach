from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.daily_opening.repository import DailyOpeningConflict
from app.domains.daily_opening.schemas import (
    CoffeeDecisionCommand,
    CoffeeDecisionResult,
    DailyOpeningView,
    ImpressionCommand,
    ImpressionResult,
    OpeningPreferenceCommand,
    OpeningPreferenceView,
    WeatherLocationCommand,
    WeatherLocationView,
)
from app.domains.daily_opening.service import DailyOpeningService
from app.platform.http import PublicApiEnvelope, public_success_envelope


router = APIRouter(prefix="/daily-opening", tags=["daily-opening"])


def get_daily_opening_service() -> DailyOpeningService:
    return DailyOpeningService()


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> str:
    return value


@router.get("", response_model=PublicApiEnvelope[DailyOpeningView])
def get_daily_opening(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[DailyOpeningService, Depends(get_daily_opening_service)],
) -> PublicApiEnvelope[DailyOpeningView]:
    try:
        result = service.view(session, user_id=current_user.user_id)
        session.commit()
    except DailyOpeningConflict as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return public_success_envelope(request=request, response=response, data=result)


@router.get("/preferences", response_model=PublicApiEnvelope[OpeningPreferenceView])
def get_daily_opening_preferences(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[DailyOpeningService, Depends(get_daily_opening_service)],
) -> PublicApiEnvelope[OpeningPreferenceView]:
    result = service.preference(session, user_id=current_user.user_id)
    session.commit()
    return public_success_envelope(request=request, response=response, data=result)


@router.put("/preferences", response_model=PublicApiEnvelope[OpeningPreferenceView])
def update_daily_opening_preferences(
    request: Request,
    response: Response,
    command: OpeningPreferenceCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[DailyOpeningService, Depends(get_daily_opening_service)],
) -> PublicApiEnvelope[OpeningPreferenceView]:
    try:
        result = service.update_preference(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
    except DailyOpeningConflict as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return public_success_envelope(request=request, response=response, data=result)


@router.put("/weather-location", response_model=PublicApiEnvelope[WeatherLocationView])
def update_weather_location(
    request: Request,
    response: Response,
    command: WeatherLocationCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[DailyOpeningService, Depends(get_daily_opening_service)],
) -> PublicApiEnvelope[WeatherLocationView]:
    try:
        result = service.update_location(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
    except DailyOpeningConflict as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return public_success_envelope(request=request, response=response, data=result)


@router.post("/impressions", response_model=PublicApiEnvelope[ImpressionResult])
def record_daily_opening_impression(
    request: Request,
    response: Response,
    command: ImpressionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[DailyOpeningService, Depends(get_daily_opening_service)],
) -> PublicApiEnvelope[ImpressionResult]:
    try:
        result = service.record_impression(
            session,
            user_id=current_user.user_id,
            command_id=f"{current_user.user_id}:{command_id}",
            command=command,
        )
        session.commit()
    except DailyOpeningConflict as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return public_success_envelope(request=request, response=response, data=result)


@router.post("/coffee-decisions", response_model=PublicApiEnvelope[CoffeeDecisionResult])
def record_coffee_decision(
    request: Request,
    response: Response,
    command: CoffeeDecisionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[DailyOpeningService, Depends(get_daily_opening_service)],
) -> PublicApiEnvelope[CoffeeDecisionResult]:
    if command.decision != "declined_today":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
    result = service.decline_coffee(
        session,
        user_id=current_user.user_id,
        command_id=f"{current_user.user_id}:{command_id}",
    )
    session.commit()
    return public_success_envelope(request=request, response=response, data=result)
