from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import hmac
import json
import logging
import time
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.domains.companion.channel import (
    AccountScopedCompanionResponder,
    ChannelCompanionUnavailable,
)
from app.domains.ilink_channel.control_plane import ILinkControlPlane
from app.domains.ilink_channel.hub_client import OpenILinkHubClient, OpenILinkHubError
from app.domains.ilink_channel.hub_repository import OpenILinkHubRepository
from app.domains.ilink_channel.hub_settings import OpenILinkHubSettings
from app.domains.ilink_channel.protocol import (
    ILinkProtocolClient,
    ILinkProtocolError,
    InboundMessage,
    MediaReference,
)
from app.domains.ilink_channel.repository import (
    ILinkConflict,
    ILinkNotFound,
    ILinkRepository,
)
from app.domains.ilink_channel.service import ILinkChannelService
from app.domains.ilink_channel.settings import ILinkConfigurationError, ILinkSettings
from app.domains.ilink_channel.workflow import ILinkPhotoWorkflow
from app.domains.ilink_channel.resolver import PhotoResolutionError


logger = logging.getLogger(__name__)
_MAX_CLOCK_SKEW_SECONDS = 300


class HubBridgeEventError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HubBridgeEvent:
    event_id: str
    event_type: str
    trace_id: str
    installation_id: str
    bot_id: str
    sender_id: str
    source_message_id: str
    content: str

    @classmethod
    def parse(cls, payload: object) -> HubBridgeEvent:
        if not isinstance(payload, dict) or payload.get("type") != "event":
            raise HubBridgeEventError("hub bridge envelope is invalid")
        event = payload.get("event")
        bot = payload.get("bot")
        if not isinstance(event, dict) or not isinstance(bot, dict):
            raise HubBridgeEventError("hub bridge event is incomplete")
        data = event.get("data")
        if not isinstance(data, dict):
            raise HubBridgeEventError("hub bridge event data is invalid")
        sender = data.get("sender")
        if not isinstance(sender, dict) or sender.get("role") != "user":
            raise HubBridgeEventError("hub bridge sender is invalid")
        values = {
            "event_id": event.get("id"),
            "event_type": event.get("type"),
            "trace_id": payload.get("trace_id"),
            "installation_id": payload.get("installation_id"),
            "bot_id": bot.get("id"),
            "sender_id": sender.get("id"),
        }
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise HubBridgeEventError("hub bridge identity fields are invalid")
        event_type = str(values["event_type"])
        if not event_type.startswith("message."):
            raise HubBridgeEventError("hub bridge event type is unsupported")
        content = data.get("content")
        source_message_id = data.get("message_id")
        return cls(
            **{key: str(value).strip() for key, value in values.items()},
            source_message_id=(
                str(source_message_id).strip()
                if isinstance(source_message_id, (str, int))
                and not isinstance(source_message_id, bool)
                and str(source_message_id).strip()
                else str(values["event_id"]).strip()
            ),
            content=content.strip() if isinstance(content, str) else "",
        )


def verify_hub_signature(
    *,
    body: bytes,
    timestamp: str,
    signature: str,
    secret: str,
    now: datetime | None = None,
) -> None:
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise HubBridgeEventError("hub bridge timestamp is invalid") from exc
    current = int((now or datetime.now(UTC)).timestamp())
    if abs(current - sent_at) > _MAX_CLOCK_SKEW_SECONDS:
        raise HubBridgeEventError("hub bridge timestamp is stale")
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b":" + body,
        "sha256",
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HubBridgeEventError("hub bridge signature is invalid")


def process_hub_event(event: HubBridgeEvent) -> None:
    settings = get_settings()
    hub_settings = OpenILinkHubSettings.from_environment()
    client = OpenILinkHubClient(hub_settings)
    reply = ""
    user_id: UUID | None = None
    delivered = False
    try:
        with get_session_factory()() as session:
            link = OpenILinkHubRepository(session).for_bot(bot_id=event.bot_id)
            if link is None:
                logger.warning("hub bridge event has no connected account", extra={"bot_id": event.bot_id})
                return
            user_id = UUID(str(link["user_id"]))
            receipts = ILinkRepository(session)
            command_id = f"hub-event:{event.event_id}"
            receipts.lock_command(command_id=command_id)
            if receipts.command_receipt(command_id=command_id) is not None:
                return
            if event.event_type == "message.image":
                try:
                    reply = _process_image(
                        session,
                        event=event,
                        link=link,
                        user_id=user_id,
                        hub_client=client,
                        core_settings=settings,
                    )
                except (
                    ILinkConfigurationError,
                    ILinkProtocolError,
                    OpenILinkHubError,
                    PhotoResolutionError,
                ):
                    session.rollback()
                    reply = "这张图我没能完整取回来，请把原图重发一次；不用进小程序。"
            elif event.event_type != "message.text" or not event.content:
                reply = "这类消息我暂时读不了；可以直接发文字或裁剪好的单题截图。"
            else:
                if event.content in {"收录", "重做", "删除"}:
                    reply = _process_query_command(
                        session,
                        user_id=user_id,
                        event=event,
                    )
                else:
                    control = ILinkControlPlane().handle(
                        session,
                        user_id=user_id,
                        source_message_id=event.source_message_id,
                        text=event.content,
                    )
                    if control is not None:
                        reply = control.reply
                    else:
                        try:
                            reply = AccountScopedCompanionResponder(settings=settings).reply(
                                session,
                                user_id=user_id,
                                source_message_id=event.source_message_id,
                                question=event.content,
                            )
                        except ChannelCompanionUnavailable:
                            session.rollback()
                            reply = "岸岸这次没有回答完成，请稍后把原问题再发一次。"
            session.commit()
        _send_with_retry(client=client, event=event, reply=reply)
        delivered = True
        with get_session_factory()() as session:
            receipts = ILinkRepository(session)
            command_id = f"hub-event:{event.event_id}"
            receipts.lock_command(command_id=command_id)
            if receipts.command_receipt(command_id=command_id) is None:
                receipts.save_command_receipt(
                    command_id=command_id,
                    command_type="hub_bridge_event",
                    user_id=user_id,
                    request_fingerprint=_event_fingerprint(event),
                    result_payload={
                        "delivered": True,
                        "replySha256": sha256(reply.encode("utf-8")).hexdigest(),
                    },
                    now=datetime.now(UTC),
                )
                session.commit()
    except Exception:
        logger.exception(
            "hub bridge event processing failed",
            extra={"event_id": event.event_id, "trace_id": event.trace_id},
        )
        if event.event_type == "message.image" and not delivered:
            try:
                _send_with_retry(
                    client=client,
                    event=event,
                    reply="这次图片处理中断了，已经保留题目；请稍后原图重发一次。",
                )
            except OpenILinkHubError:
                logger.exception(
                    "hub bridge fallback reply failed",
                    extra={"event_id": event.event_id, "trace_id": event.trace_id},
                )


def _send_with_retry(
    *, client: OpenILinkHubClient, event: HubBridgeEvent, reply: str
) -> None:
    last_error: OpenILinkHubError | None = None
    for delay in (0.0, 0.5, 2.0):
        if delay:
            time.sleep(delay)
        try:
            client.send_text(
                bot_id=event.bot_id,
                sender_id=event.sender_id,
                content=reply,
                trace_id=event.trace_id,
            )
            return
        except OpenILinkHubError as exc:
            last_error = exc
    raise last_error or OpenILinkHubError("bridge_send_failed")


def _process_image(
    session: Session,
    *,
    event: HubBridgeEvent,
    link,
    user_id: UUID,
    hub_client: OpenILinkHubClient,
    core_settings: Settings,
) -> str:
    media = hub_client.wait_for_image_media(
        bot_id=event.bot_id,
        message_id=event.source_message_id,
    )
    channel_settings = ILinkSettings.from_environment()
    protocol_client = ILinkProtocolClient(channel_settings)
    return ILinkPhotoWorkflow(
        core_settings=core_settings,
        channel_settings=channel_settings,
    ).process_hub_photo(
        session,
        user_id=user_id,
        authorization_id=UUID(str(link["authorization_id"])),
        bot_id=event.bot_id,
        message=InboundMessage(
            message_id=event.source_message_id,
            sender_id=event.sender_id,
            context_token="",
            run_id=None,
            text=event.content,
            image=MediaReference(
                encrypted_query_param=media.encrypted_query_param,
                full_url=media.url,
                aes_key=media.aes_key,
            ),
        ),
        client=protocol_client,
    )


def _process_query_command(
    session: Session,
    *,
    user_id: UUID,
    event: HubBridgeEvent,
) -> str:
    try:
        view = ILinkChannelService(
            settings=ILinkSettings.from_environment()
        ).command_latest(
            session,
            user_id=user_id,
            command=event.content,
            source_message_id=event.source_message_id,
        )
        return view.reply_text
    except ILinkNotFound:
        session.rollback()
        return "还没有可操作的图片题，先直接发一张单题截图给我。"
    except ILinkConflict:
        session.rollback()
        return "这次操作没有改动记录，请先重发原图后再试。"


def _event_fingerprint(event: HubBridgeEvent) -> str:
    payload = {
        "botId": event.bot_id,
        "content": event.content,
        "eventId": event.event_id,
        "eventType": event.event_type,
        "senderId": event.sender_id,
        "sourceMessageId": event.source_message_id,
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
