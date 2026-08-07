from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ilink_channel.hub_client import (
    HubBindStart,
    HubBindStatus,
    OpenILinkHubClient,
)
from app.domains.ilink_channel.hub_bridge import (
    HubBridgeEvent,
    _published_reply_image_url,
    _without_missing_explanation_image_references,
    verify_hub_signature,
)
from app.domains.ilink_channel.hub_service import OpenILinkHubBotService
from app.domains.ilink_channel.hub_settings import (
    OpenILinkHubConfigurationError,
    OpenILinkHubSettings,
)
from app.domains.ilink_channel.repository import ILinkNotFound, ILinkRepository
from app.domains.ilink_channel.protocol import (
    InboundMessage,
    MediaReference,
    ValidatedImage,
)
from app.domains.ilink_channel.resolver import StoredImage
from app.domains.ilink_channel.schemas import AnswerProjection
from app.domains.ilink_channel.service import ILinkChannelService
from app.domains.ilink_channel.settings import ILinkSettings
from app.domains.ilink_channel.workflow import ILinkPhotoWorkflow


NOW = datetime(2026, 7, 27, 15, 0, tzinfo=UTC)
USER_A = UUID("00000000-0000-0000-0000-00000000a101")
USER_B = UUID("00000000-0000-0000-0000-00000000a102")


def identity_settings() -> ILinkSettings:
    return ILinkSettings(
        enabled=True,
        bot_account_id="anan-bot-1",
        bot_token="",
        base_url="https://ilinkai.weixin.qq.com",
        cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
        app_id="bot",
        app_client_version=132102,
        bot_agent="Anan/1.0",
        identity_key_id="ilink-key-v1",
        identity_encryption_key=base64.b64encode(b"e" * 32).decode(),
        identity_hmac_key="h" * 40,
        binding_ttl_seconds=600,
        long_poll_timeout_seconds=35,
        request_timeout_seconds=15,
        maximum_image_bytes=10 * 1024 * 1024,
        transient_retention_seconds=900,
        query_retention_days=30,
        recognition_route="",
    )


def hub_settings() -> OpenILinkHubSettings:
    return OpenILinkHubSettings(
        enabled=True,
        base_url="http://127.0.0.1:9800",
        service_username="anan-service",
        service_password="p" * 32,
        authorization_ttl_seconds=600,
        request_timeout_seconds=45,
        bridge_webhook_secret="b" * 40,
    )


class FakeHubClient:
    def __init__(self) -> None:
        self.starts = 0
        self.statuses = [
            HubBindStatus(state="scanned"),
            HubBindStatus(state="connected", bot_id="hub-bot-a"),
        ]
        self.configured_bot_ids: list[str] = []
        self.deleted_bot_ids: list[str] = []

    def start_bind(self) -> HubBindStart:
        self.starts += 1
        return HubBindStart(
            session_id=f"bind-service-{self.starts}",
            qr_content=f"https://weixin.qq.com/x/anan-{self.starts}",
        )

    def poll_bind(self, *, session_id: str) -> HubBindStatus:
        assert session_id.startswith("bind-service-")
        return self.statuses.pop(0)

    def configure_control_bridge(self, *, bot_id: str) -> None:
        self.configured_bot_ids.append(bot_id)

    def delete_bot(self, *, bot_id: str) -> None:
        self.deleted_bot_ids.append(bot_id)


def _insert_users(session: Session) -> None:
    for user_id in (USER_A, USER_B):
        session.execute(
            text(
                "insert into learner.users (user_id, display_name, account_status) "
                "values (:user_id, :display_name, 'active')"
            ),
            {"user_id": str(user_id), "display_name": f"user-{user_id}"},
        )
    session.commit()


def test_hub_bot_authorization_is_account_isolated_and_revocable(
    db_session: Session,
) -> None:
    _insert_users(db_session)
    hub = FakeHubClient()
    service = OpenILinkHubBotService(
        settings=hub_settings(),
        identity_settings=identity_settings(),
        client=hub,
        clock=lambda: NOW,
    )

    pending = service.start_authorization(
        db_session,
        user_id=USER_A,
        command_id="user-a:hub-start-1",
        replace_pending=False,
    )
    db_session.commit()
    assert pending.state == "waiting_scan"
    assert pending.qr_image_data_url
    assert pending.qr_image_data_url.startswith("data:image/png;base64,")
    assert "weixin.qq.com" not in pending.qr_image_data_url

    replay = service.start_authorization(
        db_session,
        user_id=USER_A,
        command_id="user-a:hub-start-1",
        replace_pending=False,
    )
    assert replay.authorization_id == pending.authorization_id
    assert hub.starts == 1

    with pytest.raises(ILinkNotFound):
        service.poll_authorization(
            db_session,
            user_id=USER_B,
            authorization_id=pending.authorization_id,
        )

    scanned = service.poll_authorization(
        db_session,
        user_id=USER_A,
        authorization_id=pending.authorization_id,
    )
    db_session.commit()
    assert scanned.state == "scanned"

    connected = service.poll_authorization(
        db_session,
        user_id=USER_A,
        authorization_id=pending.authorization_id,
    )
    db_session.commit()
    assert connected.state == "connected"
    assert connected.qr_image_data_url is None
    assert hub.configured_bot_ids == ["hub-bot-a"]

    revoked = service.revoke(
        db_session,
        user_id=USER_A,
        command_id="user-a:hub-revoke-1",
    )
    db_session.commit()
    assert revoked.state == "revoked"
    assert hub.deleted_bot_ids == ["hub-bot-a"]
    assert service.view(db_session, user_id=USER_B).state == "unbound"


def test_hub_origin_is_private_loopback_only() -> None:
    invalid = OpenILinkHubSettings(
        enabled=True,
        base_url="https://hub.example.com",
        service_username="anan-service",
        service_password="p" * 32,
        authorization_ttl_seconds=600,
        request_timeout_seconds=45,
        bridge_webhook_secret="b" * 40,
    )
    with pytest.raises(OpenILinkHubConfigurationError):
        invalid.validate()


def test_hub_bridge_signature_and_event_contract() -> None:
    body = (
        b'{"type":"event","trace_id":"tr-1","installation_id":"inst-1",'
        b'"bot":{"id":"bot-1"},"event":{"id":"evt-1",'
        b'"type":"message.text","data":{"sender":{"id":"wx-1","role":"user"},'
        b'"content":"today"}}}'
    )
    import hashlib
    import hmac

    signature = "sha256=" + hmac.new(
        b"b" * 40,
        b"1786000000:" + body,
        hashlib.sha256,
    ).hexdigest()
    verify_hub_signature(
        body=body,
        timestamp="1786000000",
        signature=signature,
        secret="b" * 40,
        now=datetime.fromtimestamp(1786000000, tz=UTC),
    )
    event = HubBridgeEvent.parse(__import__("json").loads(body))
    assert event.bot_id == "bot-1"
    assert event.sender_id == "wx-1"
    assert event.content == "today"
    assert event.source_message_id == "evt-1"

    image_event = HubBridgeEvent.parse(
        {
            "type": "event",
            "trace_id": "tr-image",
            "installation_id": "inst-1",
            "bot": {"id": "bot-1"},
            "event": {
                "id": "evt-image",
                "type": "message.image",
                "data": {
                    "message_id": 7491305165594995592,
                    "sender": {"id": "wx-1", "role": "user"},
                },
            },
        }
    )
    assert image_event.source_message_id == "7491305165594995592"


def test_hub_client_waits_until_image_media_is_ready() -> None:
    message_reads = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal message_reads
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                headers={"set-cookie": "session=test-session; Path=/; HttpOnly"},
                json={"ok": True},
            )
        assert request.url.path == "/api/bots/hub-bot-a/messages"
        message_reads += 1
        ready = message_reads == 2
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "message_id": 7491305165594995592,
                        "direction": "inbound",
                        "media_status": "ready" if ready else "processing",
                        "item_list": (
                            [
                                {
                                    "type": "image",
                                    "media": {
                                        "url": (
                                            "https://novac2c.cdn.weixin.qq.com/"
                                            "c2c/download?id=1"
                                        ),
                                        "aes_key": "media-key",
                                        "encrypt_query_param": "encrypted-query",
                                    },
                                }
                            ]
                            if ready
                            else []
                        ),
                    }
                ]
            },
        )

    client = OpenILinkHubClient(
        hub_settings(),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
        sleeper=sleeps.append,
    )
    media = client.wait_for_image_media(
        bot_id="hub-bot-a", message_id="7491305165594995592"
    )

    assert media.encrypted_query_param == "encrypted-query"
    assert media.aes_key == "media-key"
    assert message_reads == 2
    assert sleeps == [0.2]


def test_hub_client_sends_an_image_url_through_the_bridge() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                headers={"set-cookie": "session=test-session; Path=/; HttpOnly"},
                json={"ok": True},
            )
        if request.url.path == "/api/bots/hub-bot-a/apps":
            return httpx.Response(
                200,
                json=[{"app_slug": "bridge", "app_token": "bridge-token"}],
            )
        assert request.url.path == "/bot/v1/message/send"
        return httpx.Response(200, json={"ok": True})

    client = OpenILinkHubClient(
        hub_settings(),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    client.send_image_url(
        bot_id="hub-bot-a",
        sender_id="wx-user-a",
        url=(
            "https://img.xingguoxian.cn/data/question-bank-export/files/"
            "source-question-bank/fenbi-final-analysis-v1-20260613-assets/"
            "paper/question.png"
        ),
        trace_id="trace-image-1",
    )

    outbound = requests[-1]
    assert outbound.headers["authorization"] == "Bearer bridge-token"
    assert __import__("json").loads(outbound.content) == {
        "type": "image",
        "url": (
            "https://img.xingguoxian.cn/data/question-bank-export/files/"
            "source-question-bank/fenbi-final-analysis-v1-20260613-assets/"
            "paper/question.png"
        ),
        "filename": "question.png",
        "to": "wx-user-a",
        "trace_id": "trace-image-1",
    }


def test_hub_reply_resolves_the_representative_published_question_image(
    db_session: Session,
) -> None:
    content_version_id = "f" * 64
    question_id = "hub-image-question"
    published_key = (
        "data/question-bank-export/files/source-question-bank/"
        "fenbi-final-analysis-v1-20260613-assets/paper/question.png"
    )
    snapshot = {
        "question_id": question_id,
        "analysis_source": "original_analysis",
    }
    db_session.execute(
        text(
            "insert into product.questions (question_id, domain, status, stem) "
            "values (:question_id, 'xingce', 'active', 'hub image question')"
        ),
        {"question_id": question_id},
    )
    db_session.execute(
        text(
            """
            insert into product.question_explanations (
              question_id, preferred_analysis, analysis_source,
              final_analysis_asset_refs
            ) values (
              :question_id, 'analysis', 'original_analysis',
              cast(:asset_refs as jsonb)
            )
            """
        ),
        {
            "question_id": question_id,
            "asset_refs": __import__("json").dumps(
                {
                    "source": "original_analysis",
                    "assets": [
                        {"object_key": "data/not-published.png"},
                        {"object_key": published_key},
                    ],
                }
            ),
        },
    )
    db_session.execute(
        text(
            """
            insert into product.question_content_versions (
              content_version_id, question_id, track, snapshot_payload,
              normalized_search_text, created_at
            ) values (
              :content_version_id, :question_id, 'xingce', cast(:snapshot as jsonb),
              'hub image question', :now
            )
            """
        ),
        {
            "content_version_id": content_version_id,
            "question_id": question_id,
            "snapshot": __import__("json").dumps(snapshot),
            "now": NOW,
        },
    )

    assert _published_reply_image_url(
        db_session,
        content_version_id=content_version_id,
    ) == f"https://img.xingguoxian.cn/{published_key}"


def test_hub_reply_removes_image_directions_only_when_the_asset_is_missing() -> None:
    reply = (
        "答案：B\n\n解析：将原展开图按如下图所示进行编号。"
        "C项不一致（如图中绿线所示），排除。"
    )

    result = _without_missing_explanation_image_references(reply)

    assert result == "答案：B\n\n解析：将原展开图进行编号。C项不一致，排除。"
    assert "如图" not in result


class _HubPhotoStorage:
    def put(self, *, user_id, query_id, image):
        return StoredImage(
            object_ref=f"tos://bucket/private/ilink/{user_id}/{query_id}/image.png",
            content_sha256="a" * 64,
            mime_type=image.mime_type,
        )

    def delete(self, *, object_ref):
        del object_ref


class _HubPhotoResolver:
    def __init__(self) -> None:
        self.storage = _HubPhotoStorage()

    def resolve(self, _session, **_kwargs):
        return AnswerProjection(
            answerKind="ai_candidate",
            answerText="C",
            explanation="题干中的对应关系支持 C。",
            track="xingce",
            recognizedPayload={"prompt": "example"},
            modelProvenance={"policy": "single-primary-candidate@v1"},
        )


class _HubPhotoProtocol:
    def download_image(self, _media):
        return ValidatedImage(
            payload=b"valid-image",
            mime_type="image/png",
            width=100,
            height=100,
        )


def test_hub_photo_query_is_account_owned_and_supports_followup_commands(
    db_session: Session,
) -> None:
    _insert_users(db_session)
    authorization_id = UUID("00000000-0000-0000-0000-00000000b201")
    db_session.execute(
        text(
            """
            insert into messaging.ilink_hub_bot_links (
              authorization_id, user_id, state, encryption_key_id, hub_bot_id,
              revision, last_command_id, created_at, updated_at, connected_at
            ) values (
              :authorization_id, :user_id, 'connected', 'key-v1', 'hub-bot-photo',
              1, 'connect-photo', :now, :now, :now
            )
            """
        ),
        {
            "authorization_id": str(authorization_id),
            "user_id": str(USER_A),
            "now": NOW,
        },
    )
    db_session.commit()
    photo_settings = replace(
        identity_settings(),
        bot_token="official-token-secret",
        recognition_route="photo-query-approved-v1",
    )
    workflow = ILinkPhotoWorkflow(
        core_settings=Settings(),
        channel_settings=photo_settings,
        resolver=_HubPhotoResolver(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    reply = workflow.process_hub_photo(
        db_session,
        user_id=USER_A,
        authorization_id=authorization_id,
        bot_id="hub-bot-photo",
        message=InboundMessage(
            message_id="image-message-1",
            sender_id="wx-user-a",
            context_token="",
            run_id=None,
            text="",
            image=MediaReference(
                encrypted_query_param="encrypted-query",
                full_url="https://novac2c.cdn.weixin.qq.com/c2c/download?id=1",
                aes_key=base64.b64encode(b"k" * 16).decode(),
            ),
        ),
        client=_HubPhotoProtocol(),  # type: ignore[arg-type]
    )

    assert "答案：C" in reply.reply_text
    assert "不用进小程序" not in reply.reply_text
    assert reply.content_version_id is None
    query = db_session.execute(
        text(
            "select link_id, hub_authorization_id from messaging.ilink_photo_queries "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).mappings().one()
    assert query["link_id"] is None
    assert UUID(str(query["hub_authorization_id"])) == authorization_id

    collected = ILinkChannelService(
        settings=photo_settings,
        clock=lambda: NOW,
    ).command_latest(
        db_session,
        user_id=USER_A,
        command="收录",
        source_message_id="collect-message-1",
    )
    db_session.commit()
    assert collected.link_id is None
    assert "已收录" in collected.reply_text


def test_hub_command_lock_is_transaction_scoped(db_session: Session) -> None:
    locked = db_session.execute(
        text("select pg_try_advisory_xact_lock(hashtextextended(:command_id, 0))"),
        {"command_id": "user-a:hub-start-race"},
    ).scalar_one()
    assert locked is True

    ILinkRepository(db_session).lock_command(command_id="user-a:hub-start-race")
    still_held = db_session.execute(
        text("select pg_try_advisory_xact_lock(hashtextextended(:command_id, 0))"),
        {"command_id": "user-a:hub-start-race"},
    ).scalar_one()
    assert still_held is True
