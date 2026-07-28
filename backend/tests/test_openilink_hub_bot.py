from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ilink_channel.hub_client import HubBindStart, HubBindStatus
from app.domains.ilink_channel.hub_service import OpenILinkHubBotService
from app.domains.ilink_channel.hub_settings import (
    OpenILinkHubConfigurationError,
    OpenILinkHubSettings,
)
from app.domains.ilink_channel.repository import ILinkNotFound, ILinkRepository
from app.domains.ilink_channel.settings import ILinkSettings


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
    )


class FakeHubClient:
    def __init__(self) -> None:
        self.starts = 0
        self.statuses = [
            HubBindStatus(state="scanned"),
            HubBindStatus(state="connected", bot_id="hub-bot-a"),
        ]
        self.enabled_bot_ids: list[str] = []
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

    def enable_bot_ai(self, *, bot_id: str) -> None:
        self.enabled_bot_ids.append(bot_id)

    def delete_bot(self, *, bot_id: str) -> None:
        self.deleted_bot_ids.append(bot_id)


def _insert_users(session: Session) -> None:
    session.execute(
        text(
            """
            create table messaging.ilink_command_receipts (
              command_id text primary key,
              command_type text not null,
              user_id uuid references learner.users(user_id),
              request_fingerprint char(64) not null,
              result_payload jsonb not null,
              created_at timestamptz not null
            )
            """
        )
    )
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
    assert hub.enabled_bot_ids == ["hub-bot-a"]

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
    )
    with pytest.raises(OpenILinkHubConfigurationError):
        invalid.validate()


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
