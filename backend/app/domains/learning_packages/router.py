from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import OfflinePacketVersion
from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.content_catalog.licensing.service import ContentLicenseService
from app.domains.learning_packages.composition import build_learning_package_service
from app.domains.learning_packages.schemas import (
    CreatePacketCommand,
    PacketAccessProjection,
    PacketListSnapshot,
    RenewPacketCommand,
    RetryPacketCommand,
)
from app.domains.learning_packages.service import LearningPackageError, LearningPackageService
from app.platform.http import COMMON_API_ERROR_RESPONSES, PublicApiEnvelope, public_success_envelope


router = APIRouter(
    prefix="/api/v2/external-learning/packets",
    tags=["external-learning-packets"],
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


def _service(session: Session) -> LearningPackageService:
    return LearningPackageService(
        session=session,
        license_evaluator=ContentLicenseService(session=session),
    )


@router.post("", response_model=PublicApiEnvelope[OfflinePacketVersion])
def create_packet(
    request: Request,
    response: Response,
    command: CreatePacketCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[OfflinePacketVersion]:
    try:
        packet = _service(session).request_packet(
            user_id=current_user.user_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=packet)
    except LearningPackageError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("", response_model=PublicApiEnvelope[PacketListSnapshot])
def list_packets(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PacketListSnapshot]:
    snapshot = _service(session).list_snapshot(user_id=current_user.user_id)
    return public_success_envelope(request=request, response=response, data=snapshot)


@router.post(
    "/{packet_id}/retry", response_model=PublicApiEnvelope[OfflinePacketVersion]
)
def retry_packet(
    packet_id: UUID,
    request: Request,
    response: Response,
    command: RetryPacketCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[OfflinePacketVersion]:
    try:
        packet = _service(session).retry_packet(
            user_id=current_user.user_id,
            packet_id=packet_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=packet)
    except LearningPackageError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/{packet_id}/renew", response_model=PublicApiEnvelope[OfflinePacketVersion]
)
def renew_packet(
    packet_id: UUID,
    request: Request,
    response: Response,
    command: RenewPacketCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[OfflinePacketVersion]:
    try:
        packet = _service(session).renew_packet(
            user_id=current_user.user_id,
            packet_id=packet_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=packet)
    except LearningPackageError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/{packet_version_id}/access",
    response_model=PublicApiEnvelope[PacketAccessProjection],
)
def access_packet(
    packet_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[PacketAccessProjection]:
    settings = get_settings()
    try:
        grant = build_learning_package_service(
            session=session, settings=settings
        ).access_packet(
            user_id=current_user.user_id,
            packet_version_id=packet_version_id,
            ttl=timedelta(seconds=settings.packet_access_ttl_seconds),
        )
        return public_success_envelope(request=request, response=response, data=grant)
    except (LearningPackageError, ValueError) as exc:
        if isinstance(exc, LearningPackageError):
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        raise HTTPException(status_code=503, detail="学习包访问签发服务配置无效。") from exc


@router.get("/{packet_version_id}", response_model=PublicApiEnvelope[OfflinePacketVersion])
def get_packet(
    packet_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[OfflinePacketVersion]:
    try:
        packet = _service(session).get_packet(
            user_id=current_user.user_id, packet_version_id=packet_version_id
        )
        return public_success_envelope(request=request, response=response, data=packet)
    except LearningPackageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
