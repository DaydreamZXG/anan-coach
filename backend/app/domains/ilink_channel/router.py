from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.ilink_channel.repository import ILinkConflict, ILinkNotFound, ILinkRepository
from app.domains.ilink_channel.resolver import PhotoResolutionError, TosTransientImageStore
from app.domains.ilink_channel.schemas import (
    ILinkChannelView,
    ILinkDataDeletion,
    ILinkDataExport,
    IssueLinkCommand,
    RevokeLinkCommand,
)
from app.domains.ilink_channel.service import ILinkChannelService
from app.domains.ilink_channel.settings import ILinkConfigurationError, ILinkSettings
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/ilink-channel",
    tags=["ilink-channel"],
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


def _service() -> ILinkChannelService:
    try:
        return ILinkChannelService(settings=ILinkSettings.from_environment())
    except ILinkConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="微信发图搜题尚未完成安全配置。",
        ) from exc


@router.get("", response_model=PublicApiEnvelope[ILinkChannelView])
def channel_view(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[ILinkChannelService, Depends(_service)],
) -> PublicApiEnvelope[ILinkChannelView]:
    result = service.view(session, user_id=UUID(current_user.user_id))
    return public_success_envelope(request=request, response=response, data=result)


@router.post("/link-codes", response_model=PublicApiEnvelope[ILinkChannelView])
def issue_link_code(
    request: Request,
    response: Response,
    command: IssueLinkCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[ILinkChannelService, Depends(_service)],
) -> PublicApiEnvelope[ILinkChannelView]:
    try:
        result = service.issue(
            session,
            user_id=UUID(current_user.user_id),
            command_id=f"{current_user.user_id}:{idempotency_key}",
            replace_pending=command.replace_pending,
        )
        session.commit()
    except ILinkConflict as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return public_success_envelope(request=request, response=response, data=result)


@router.post("/links/{link_id}/revoke", response_model=PublicApiEnvelope[ILinkChannelView])
def revoke_link(
    link_id: UUID,
    request: Request,
    response: Response,
    command: RevokeLinkCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[ILinkChannelService, Depends(_service)],
) -> PublicApiEnvelope[ILinkChannelView]:
    if command.expected_link_id != link_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    try:
        result = service.revoke(
            session,
            user_id=UUID(current_user.user_id),
            expected_link_id=link_id,
            command_id=f"{current_user.user_id}:{idempotency_key}",
        )
        session.commit()
    except ILinkNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    except ILinkConflict as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return public_success_envelope(request=request, response=response, data=result)


@router.get("/data-export", response_model=PublicApiEnvelope[ILinkDataExport])
def export_channel_data(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    _: Annotated[ILinkChannelService, Depends(_service)],
) -> PublicApiEnvelope[ILinkDataExport]:
    payload = ILinkRepository(session).export(user_id=UUID(current_user.user_id))
    result = ILinkDataExport(
        link=payload["link"],
        queries=tuple(payload["queries"]),
        collections=tuple(payload["collections"]),
    )
    return public_success_envelope(request=request, response=response, data=result)


@router.delete("/data", response_model=PublicApiEnvelope[ILinkDataDeletion])
def delete_channel_data(
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    _: Annotated[ILinkChannelService, Depends(_service)],
) -> PublicApiEnvelope[ILinkDataDeletion]:
    user_id = UUID(current_user.user_id)
    repository = ILinkRepository(session)
    command_id = f"{current_user.user_id}:{idempotency_key}"
    replay = repository.command_receipt(command_id=command_id)
    if replay is not None:
        payload = dict(replay["result_payload"])
        result = ILinkDataDeletion.model_validate(payload)
        return public_success_envelope(request=request, response=response, data=result)
    links, queries, collections, refs = repository.delete_user_data(user_id=user_id)
    if refs:
        try:
            storage = TosTransientImageStore(get_settings())
            for object_ref in refs:
                storage.delete(object_ref=object_ref)
        except PhotoResolutionError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="私有图片删除尚未完成。",
            ) from exc
    repository.save_command_receipt(
        command_id=command_id,
        command_type="delete_channel_data",
        user_id=None,
        request_fingerprint="0" * 64,
        result_payload={
            "deletedLinks": links,
            "deletedQueries": queries,
            "deletedCollections": collections,
        },
        now=datetime.now(UTC),
    )
    session.commit()
    result = ILinkDataDeletion(
        deletedLinks=links,
        deletedQueries=queries,
        deletedCollections=collections,
    )
    return public_success_envelope(request=request, response=response, data=result)
