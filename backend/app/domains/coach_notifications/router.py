from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.coach_notifications.repository import (
    CoachNotificationConflict,
    CoachNotificationNotFound,
    CoachNotificationRepository,
)
from app.domains.coach_notifications.schemas import (
    CoachNotificationOpenCommand,
    CoachNotificationOpenView,
    CoachNotificationSettings,
    CoachPreferenceView,
    DisableCoachNotificationsCommand,
    UpdateCoachPreferenceCommand,
    WechatSubscriptionEventCommand,
    WechatSubscriptionEventView,
)
from app.domains.coach_notifications.service import CoachNotificationService
from app.domains.coach_notifications.template import CoachTemplateConfigurationError
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/coach-notifications",
    tags=["coach-notifications"],
    responses=COMMON_API_ERROR_RESPONSES,
)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$",
    ),
]


def _service(session: Session) -> CoachNotificationService:
    return CoachNotificationService(CoachNotificationRepository(session), get_settings())


@router.get("/settings", response_model=PublicApiEnvelope[CoachNotificationSettings])
def get_settings_view(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[CoachNotificationSettings]:
    try:
        result = _service(session).settings(user_id=current_user.user_id)
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except CoachTemplateConfigurationError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/preferences", response_model=PublicApiEnvelope[CoachPreferenceView])
def update_preference(
    command: UpdateCoachPreferenceCommand,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[CoachPreferenceView]:
    try:
        result = _service(session).update_preference(
            user_id=current_user.user_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except CoachNotificationConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/preferences/disable", response_model=PublicApiEnvelope[CoachPreferenceView]
)
def disable_notifications(
    command: DisableCoachNotificationsCommand,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[CoachPreferenceView]:
    try:
        result = _service(session).disable(
            user_id=current_user.user_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except CoachNotificationConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/wechat-subscriptions",
    response_model=PublicApiEnvelope[WechatSubscriptionEventView],
)
def record_wechat_subscription(
    command: WechatSubscriptionEventCommand,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[WechatSubscriptionEventView]:
    try:
        result = _service(session).record_subscription_event(
            user_id=current_user.user_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except CoachNotificationConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CoachTemplateConfigurationError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/deliveries/{delivery_id}/opened",
    response_model=PublicApiEnvelope[CoachNotificationOpenView],
)
def record_delivery_open(
    delivery_id: UUID,
    command: CoachNotificationOpenCommand,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[CoachNotificationOpenView]:
    try:
        result = _service(session).record_open(
            user_id=current_user.user_id,
            delivery_id=delivery_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except CoachNotificationNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="督学消息不存在。") from exc
    except CoachNotificationConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
