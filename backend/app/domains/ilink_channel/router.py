from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import json

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.ilink_channel.hub_client import OpenILinkHubError
from app.domains.ilink_channel.hub_bridge import (
    HubBridgeEvent,
    HubBridgeEventError,
    process_hub_event,
    verify_hub_signature,
)
from app.domains.ilink_channel.hub_service import OpenILinkHubBotService
from app.domains.ilink_channel.hub_settings import (
    OpenILinkHubConfigurationError,
    OpenILinkHubSettings,
)
from app.domains.ilink_channel.repository import ILinkConflict, ILinkNotFound, ILinkRepository
from app.domains.ilink_channel.resolver import PhotoResolutionError, TosTransientImageStore
from app.domains.ilink_channel.schemas import (
    ILinkChannelView,
    ILinkDataDeletion,
    ILinkDataExport,
    HubBotAuthorizationView,
    IssueLinkCommand,
    RevokeLinkCommand,
    StartHubBotAuthorizationCommand,
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


def _hub_service() -> OpenILinkHubBotService:
    try:
        return OpenILinkHubBotService(
            settings=OpenILinkHubSettings.from_environment(),
            identity_settings=ILinkSettings.from_environment(),
        )
    except (OpenILinkHubConfigurationError, ILinkConfigurationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="微信岸岸正在完成连接配置，请稍后再试。",
        ) from exc


def _raise_hub_unavailable(exc: OpenILinkHubError) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="微信岸岸暂时没有连上，请重新试一次。",
    ) from exc


@router.post("/hub/events", status_code=status.HTTP_202_ACCEPTED)
async def receive_hub_event(
    request: Request,
    background_tasks: BackgroundTasks,
    x_timestamp: Annotated[str, Header(alias="X-Timestamp")],
    x_signature: Annotated[str, Header(alias="X-Signature")],
) -> dict[str, bool]:
    body = await request.body()
    if len(body) > 1_048_576:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        settings = OpenILinkHubSettings.from_environment()
        settings.validate()
        verify_hub_signature(
            body=body,
            timestamp=x_timestamp,
            signature=x_signature,
            secret=settings.bridge_webhook_secret,
        )
        event = HubBridgeEvent.parse(json.loads(body))
    except (HubBridgeEventError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OpenIlink Hub event verification failed.",
        ) from exc
    background_tasks.add_task(process_hub_event, event)
    return {"accepted": True}


@router.get("/bot", response_model=PublicApiEnvelope[HubBotAuthorizationView])
def hub_bot_view(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[OpenILinkHubBotService, Depends(_hub_service)],
) -> PublicApiEnvelope[HubBotAuthorizationView]:
    result = service.view(session, user_id=UUID(current_user.user_id))
    return public_success_envelope(request=request, response=response, data=result)


@router.post(
    "/bot/authorizations",
    response_model=PublicApiEnvelope[HubBotAuthorizationView],
)
def start_hub_bot_authorization(
    request: Request,
    response: Response,
    command: StartHubBotAuthorizationCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[OpenILinkHubBotService, Depends(_hub_service)],
) -> PublicApiEnvelope[HubBotAuthorizationView]:
    try:
        result = service.start_authorization(
            session,
            user_id=UUID(current_user.user_id),
            command_id=f"{current_user.user_id}:{idempotency_key}",
            replace_pending=command.replace_pending,
        )
        session.commit()
    except ILinkConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前微信连接状态已经变化，请刷新后再试。",
        ) from exc
    except OpenILinkHubError as exc:
        session.rollback()
        _raise_hub_unavailable(exc)
    return public_success_envelope(request=request, response=response, data=result)


@router.get(
    "/bot/authorizations/{authorization_id}",
    response_model=PublicApiEnvelope[HubBotAuthorizationView],
)
def poll_hub_bot_authorization(
    authorization_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[OpenILinkHubBotService, Depends(_hub_service)],
) -> PublicApiEnvelope[HubBotAuthorizationView]:
    try:
        result = service.poll_authorization(
            session,
            user_id=UUID(current_user.user_id),
            authorization_id=authorization_id,
        )
        session.commit()
    except ILinkNotFound as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="这次扫码已经结束，请重新生成二维码。",
        ) from exc
    except ILinkConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="连接状态已经变化，请重新读取。",
        ) from exc
    except OpenILinkHubError as exc:
        session.rollback()
        _raise_hub_unavailable(exc)
    return public_success_envelope(request=request, response=response, data=result)


@router.delete("/bot", response_model=PublicApiEnvelope[HubBotAuthorizationView])
def revoke_hub_bot(
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    service: Annotated[OpenILinkHubBotService, Depends(_hub_service)],
) -> PublicApiEnvelope[HubBotAuthorizationView]:
    try:
        result = service.revoke(
            session,
            user_id=UUID(current_user.user_id),
            command_id=f"{current_user.user_id}:{idempotency_key}",
        )
        session.commit()
    except ILinkConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="连接状态已经变化，请刷新后再试。",
        ) from exc
    except OpenILinkHubError as exc:
        session.rollback()
        _raise_hub_unavailable(exc)
    return public_success_envelope(request=request, response=response, data=result)


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
