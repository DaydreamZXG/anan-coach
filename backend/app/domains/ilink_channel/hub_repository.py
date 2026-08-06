from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.ilink_channel.repository import ILinkConflict


class OpenILinkHubRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def for_user(self, *, user_id: UUID, for_update: bool = False):
        suffix = " for update" if for_update else ""
        return (
            self._session.execute(
                text(
                    "select * from messaging.ilink_hub_bot_links where user_id = :user_id" + suffix
                ),
                {"user_id": str(user_id)},
            )
            .mappings()
            .one_or_none()
        )

    def for_authorization(self, *, user_id: UUID, authorization_id: UUID, for_update: bool = False):
        suffix = " for update" if for_update else ""
        return (
            self._session.execute(
                text(
                    "select * from messaging.ilink_hub_bot_links "
                    "where user_id = :user_id and authorization_id = :authorization_id" + suffix
                ),
                {
                    "user_id": str(user_id),
                    "authorization_id": str(authorization_id),
                },
            )
            .mappings()
            .one_or_none()
        )

    def for_bot(self, *, bot_id: str):
        return (
            self._session.execute(
                text(
                    "select * from messaging.ilink_hub_bot_links "
                    "where hub_bot_id = :bot_id and state = 'connected'"
                ),
                {"bot_id": bot_id},
            )
            .mappings()
            .one_or_none()
        )

    def upsert_pending(
        self,
        *,
        authorization_id: UUID,
        user_id: UUID,
        encrypted_session_id: str,
        encrypted_qr_content: str,
        key_id: str,
        expires_at: datetime,
        command_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        existing = self.for_user(user_id=user_id, for_update=True)
        if existing is None:
            return dict(
                self._session.execute(
                    text(
                        """
                        insert into messaging.ilink_hub_bot_links (
                          authorization_id, user_id, state,
                          encrypted_bind_session_id, encrypted_qr_content,
                          encryption_key_id, expires_at, revision, last_command_id,
                          created_at, updated_at
                        ) values (
                          :authorization_id, :user_id, 'waiting_scan',
                          :encrypted_session_id, :encrypted_qr_content,
                          :key_id, :expires_at, 1, :command_id, :now, :now
                        ) returning *
                        """
                    ),
                    {
                        "authorization_id": str(authorization_id),
                        "user_id": str(user_id),
                        "encrypted_session_id": encrypted_session_id,
                        "encrypted_qr_content": encrypted_qr_content,
                        "key_id": key_id,
                        "expires_at": expires_at,
                        "command_id": command_id,
                        "now": now,
                    },
                )
                .mappings()
                .one()
            )
        return dict(
            self._session.execute(
                text(
                    """
                    update messaging.ilink_hub_bot_links set
                      authorization_id = :authorization_id,
                      state = 'waiting_scan',
                      encrypted_bind_session_id = :encrypted_session_id,
                      encrypted_qr_content = :encrypted_qr_content,
                      encryption_key_id = :key_id,
                      hub_bot_id = null, expires_at = :expires_at,
                      revision = revision + 1, last_command_id = :command_id,
                      failure_code = null, updated_at = :now,
                      connected_at = null, revoked_at = null
                    where user_id = :user_id returning *
                    """
                ),
                {
                    "authorization_id": str(authorization_id),
                    "user_id": str(user_id),
                    "encrypted_session_id": encrypted_session_id,
                    "encrypted_qr_content": encrypted_qr_content,
                    "key_id": key_id,
                    "expires_at": expires_at,
                    "command_id": command_id,
                    "now": now,
                },
            )
            .mappings()
            .one()
        )

    def mark_scanned(self, *, authorization_id: UUID, now: datetime):
        return (
            self._session.execute(
                text(
                    """
                update messaging.ilink_hub_bot_links set
                  state = 'scanned', revision = revision + 1, updated_at = :now
                where authorization_id = :authorization_id
                  and state in ('waiting_scan', 'scanned')
                returning *
                """
                ),
                {"authorization_id": str(authorization_id), "now": now},
            )
            .mappings()
            .one_or_none()
        )

    def refresh_qr(
        self,
        *,
        authorization_id: UUID,
        encrypted_qr_content: str,
        expires_at: datetime,
        now: datetime,
    ):
        return (
            self._session.execute(
                text(
                    """
                update messaging.ilink_hub_bot_links set
                  state = 'waiting_scan', encrypted_qr_content = :qr,
                  expires_at = :expires_at, revision = revision + 1,
                  updated_at = :now
                where authorization_id = :authorization_id
                  and state in ('waiting_scan', 'scanned')
                returning *
                """
                ),
                {
                    "authorization_id": str(authorization_id),
                    "qr": encrypted_qr_content,
                    "expires_at": expires_at,
                    "now": now,
                },
            )
            .mappings()
            .one_or_none()
        )

    def connect(
        self,
        *,
        authorization_id: UUID,
        hub_bot_id: str,
        command_id: str,
        now: datetime,
    ):
        try:
            row = (
                self._session.execute(
                    text(
                        """
                    update messaging.ilink_hub_bot_links set
                      state = 'connected', encrypted_bind_session_id = null,
                      encrypted_qr_content = null, hub_bot_id = :hub_bot_id,
                      expires_at = null, revision = revision + 1,
                      last_command_id = :command_id, failure_code = null,
                      updated_at = :now, connected_at = :now, revoked_at = null
                    where authorization_id = :authorization_id
                      and state in ('waiting_scan', 'scanned')
                    returning *
                    """
                    ),
                    {
                        "authorization_id": str(authorization_id),
                        "hub_bot_id": hub_bot_id,
                        "command_id": command_id,
                        "now": now,
                    },
                )
                .mappings()
                .one_or_none()
            )
        except IntegrityError as exc:
            raise ILinkConflict("hub_bot_already_linked") from exc
        if row is None:
            raise ILinkConflict("hub_authorization_state_changed")
        return row

    def revoke(
        self,
        *,
        authorization_id: UUID,
        command_id: str,
        now: datetime,
    ):
        row = (
            self._session.execute(
                text(
                    """
                update messaging.ilink_hub_bot_links set
                  state = 'revoked', encrypted_bind_session_id = null,
                  encrypted_qr_content = null, hub_bot_id = null,
                  expires_at = null, revision = revision + 1,
                  last_command_id = :command_id, failure_code = null,
                  updated_at = :now, connected_at = null, revoked_at = :now
                where authorization_id = :authorization_id
                  and state <> 'revoked'
                returning *
                """
                ),
                {
                    "authorization_id": str(authorization_id),
                    "command_id": command_id,
                    "now": now,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            row = (
                self._session.execute(
                    text(
                        "select * from messaging.ilink_hub_bot_links "
                        "where authorization_id = :authorization_id"
                    ),
                    {"authorization_id": str(authorization_id)},
                )
                .mappings()
                .one_or_none()
            )
        return row
