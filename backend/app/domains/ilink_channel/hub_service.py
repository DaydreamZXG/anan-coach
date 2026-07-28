from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Protocol
from uuid import UUID, uuid4

import qrcode
from sqlalchemy.orm import Session

from app.domains.ilink_channel.crypto import ILinkCipherError, ILinkIdentityCipher
from app.domains.ilink_channel.hub_client import (
    HubBindStart,
    HubBindStatus,
    OpenILinkHubClient,
    OpenILinkHubError,
)
from app.domains.ilink_channel.hub_repository import OpenILinkHubRepository
from app.domains.ilink_channel.hub_settings import OpenILinkHubSettings
from app.domains.ilink_channel.repository import (
    ILinkConflict,
    ILinkNotFound,
    ILinkRepository,
)
from app.domains.ilink_channel.schemas import HubBotAuthorizationView
from app.domains.ilink_channel.settings import ILinkSettings


class OpenILinkHubGateway(Protocol):
    def start_bind(self) -> HubBindStart: ...

    def poll_bind(self, *, session_id: str) -> HubBindStatus: ...

    def enable_bot_ai(self, *, bot_id: str) -> None: ...

    def delete_bot(self, *, bot_id: str) -> None: ...


class OpenILinkHubBotService:
    def __init__(
        self,
        *,
        settings: OpenILinkHubSettings,
        identity_settings: ILinkSettings,
        client: OpenILinkHubGateway | None = None,
        clock=None,
    ) -> None:
        settings.validate()
        identity_settings.validate_binding()
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client = client or OpenILinkHubClient(settings)
        self._cipher = ILinkIdentityCipher(
            encryption_key=identity_settings.encryption_key_bytes(),
            hmac_key=identity_settings.identity_hmac_key,
            key_id=identity_settings.identity_key_id,
        )

    def view(self, session: Session, *, user_id: UUID) -> HubBotAuthorizationView:
        row = OpenILinkHubRepository(session).for_user(user_id=user_id)
        return self._view_from_row(row)

    def start_authorization(
        self,
        session: Session,
        *,
        user_id: UUID,
        command_id: str,
        replace_pending: bool,
    ) -> HubBotAuthorizationView:
        now = self._clock()
        repository = OpenILinkHubRepository(session)
        receipts = ILinkRepository(session)
        request_fingerprint = self._cipher.fingerprint_payload(
            {"userId": str(user_id), "replacePending": replace_pending},
            purpose="hub-start-command",
        )
        receipts.lock_command(command_id=command_id)
        replay = receipts.command_receipt(command_id=command_id)
        if replay is not None:
            if replay["request_fingerprint"] != request_fingerprint:
                raise ILinkConflict("idempotency_conflict")
            return self.view(session, user_id=user_id)

        existing = repository.for_user(user_id=user_id, for_update=True)
        if existing is not None and existing["state"] in {
            "connected",
            "reauth_required",
        }:
            raise ILinkConflict("hub_bot_must_be_revoked_first")
        if (
            existing is not None
            and existing["state"] in {"waiting_scan", "scanned"}
            and existing["expires_at"] > now
            and not replace_pending
        ):
            result = self._view_from_row(existing)
            self._save_receipt(
                receipts,
                command_id=command_id,
                user_id=user_id,
                fingerprint=request_fingerprint,
                result=result,
                now=now,
            )
            return result

        started = self._client.start_bind()
        authorization_id = uuid4()
        expires_at = now + timedelta(seconds=self._settings.authorization_ttl_seconds)
        row = repository.upsert_pending(
            authorization_id=authorization_id,
            user_id=user_id,
            encrypted_session_id=self._cipher.encrypt(
                started.session_id,
                purpose=f"hub-bind-session:{authorization_id}",
            ),
            encrypted_qr_content=self._cipher.encrypt(
                started.qr_content,
                purpose=f"hub-bind-qr:{authorization_id}",
            ),
            key_id=self._cipher.key_id,
            expires_at=expires_at,
            command_id=command_id,
            now=now,
        )
        result = self._view_from_row(row)
        self._save_receipt(
            receipts,
            command_id=command_id,
            user_id=user_id,
            fingerprint=request_fingerprint,
            result=result,
            now=now,
        )
        return result

    def poll_authorization(
        self,
        session: Session,
        *,
        user_id: UUID,
        authorization_id: UUID,
    ) -> HubBotAuthorizationView:
        now = self._clock()
        repository = OpenILinkHubRepository(session)
        row = repository.for_authorization(
            user_id=user_id,
            authorization_id=authorization_id,
            for_update=True,
        )
        if row is None:
            raise ILinkNotFound("hub_authorization_not_found")
        if row["state"] not in {"waiting_scan", "scanned"}:
            return self._view_from_row(row)
        if row["expires_at"] <= now:
            return self._view_from_row(row)

        session_id = self._decrypt(
            row["encrypted_bind_session_id"],
            purpose=f"hub-bind-session:{authorization_id}",
        )
        status = self._client.poll_bind(session_id=session_id)
        if status.state == "wait":
            return self._view_from_row(row)
        if status.state == "scanned":
            updated = repository.mark_scanned(
                authorization_id=authorization_id,
                now=now,
            )
            return self._view_from_row(updated or row)
        if status.state == "refreshed":
            if not status.qr_content:
                raise OpenILinkHubError("bind_qr_missing")
            updated = repository.refresh_qr(
                authorization_id=authorization_id,
                encrypted_qr_content=self._cipher.encrypt(
                    status.qr_content,
                    purpose=f"hub-bind-qr:{authorization_id}",
                ),
                expires_at=now + timedelta(seconds=self._settings.authorization_ttl_seconds),
                now=now,
            )
            if updated is None:
                raise ILinkConflict("hub_authorization_state_changed")
            return self._view_from_row(updated)
        if status.state == "connected":
            if not status.bot_id:
                raise OpenILinkHubError("bound_bot_missing")
            self._client.enable_bot_ai(bot_id=status.bot_id)
            updated = repository.connect(
                authorization_id=authorization_id,
                hub_bot_id=status.bot_id,
                command_id=f"hub-connect:{authorization_id}",
                now=now,
            )
            return self._view_from_row(updated)
        raise OpenILinkHubError("bind_status_failed")

    def revoke(
        self,
        session: Session,
        *,
        user_id: UUID,
        command_id: str,
    ) -> HubBotAuthorizationView:
        now = self._clock()
        repository = OpenILinkHubRepository(session)
        receipts = ILinkRepository(session)
        request_fingerprint = self._cipher.fingerprint_payload(
            {"userId": str(user_id)},
            purpose="hub-revoke-command",
        )
        receipts.lock_command(command_id=command_id)
        replay = receipts.command_receipt(command_id=command_id)
        if replay is not None:
            if replay["request_fingerprint"] != request_fingerprint:
                raise ILinkConflict("idempotency_conflict")
            return self.view(session, user_id=user_id)

        row = repository.for_user(user_id=user_id, for_update=True)
        if row is None:
            result = self._view_from_row(None)
        else:
            bot_id = row["hub_bot_id"]
            if bot_id and row["state"] in {"connected", "reauth_required"}:
                self._client.delete_bot(bot_id=str(bot_id))
            updated = repository.revoke(
                authorization_id=UUID(str(row["authorization_id"])),
                command_id=command_id,
                now=now,
            )
            result = self._view_from_row(updated)
        self._save_receipt(
            receipts,
            command_id=command_id,
            user_id=user_id,
            fingerprint=request_fingerprint,
            result=result,
            now=now,
        )
        return result

    def _view_from_row(self, row) -> HubBotAuthorizationView:
        if row is None:
            return HubBotAuthorizationView(
                state="unbound",
                authorizationId=None,
                qrImageDataUrl=None,
                expiresAt=None,
                statusCopy="还没有连接微信岸岸",
                usageCopy="连接后，可以直接在微信里给岸岸发送文字或题目图片。",
            )

        state = str(row["state"])
        authorization_id = UUID(str(row["authorization_id"]))
        expires_at = row["expires_at"]
        if (
            state in {"waiting_scan", "scanned"}
            and expires_at is not None
            and expires_at <= self._clock()
        ):
            return HubBotAuthorizationView(
                state="reauth_required",
                authorizationId=authorization_id,
                qrImageDataUrl=None,
                expiresAt=expires_at,
                statusCopy="二维码已过期，请重新生成",
                usageCopy="重新生成后，用当前微信扫码并确认即可。",
            )

        qr_data_url = None
        if state in {"waiting_scan", "scanned"}:
            qr_content = self._decrypt(
                row["encrypted_qr_content"],
                purpose=f"hub-bind-qr:{authorization_id}",
            )
            qr_data_url = _qr_data_url(qr_content)

        copy_by_state = {
            "waiting_scan": (
                "等待微信扫码",
                "长按二维码识别，或用另一台设备扫码，然后在微信里确认。",
            ),
            "scanned": (
                "已经扫码，请在微信里确认",
                "确认后保持本页打开，岸岸会自动完成连接。",
            ),
            "connected": (
                "微信岸岸已连接",
                "打开微信里的岸岸 Bot 会话，就能发送文字或题目图片。",
            ),
            "reauth_required": (
                "微信连接需要重新确认",
                "重新扫码后即可继续使用。",
            ),
            "revoked": (
                "微信岸岸已解除连接",
                "需要时可以重新扫码连接。",
            ),
        }
        status_copy, usage_copy = copy_by_state.get(
            state,
            ("连接状态异常", "请重新生成二维码后再试一次。"),
        )
        return HubBotAuthorizationView(
            state=state,
            authorizationId=authorization_id,
            qrImageDataUrl=qr_data_url,
            expiresAt=expires_at,
            statusCopy=status_copy,
            usageCopy=usage_copy,
        )

    def _decrypt(self, value: object, *, purpose: str) -> str:
        if not isinstance(value, str) or not value:
            raise OpenILinkHubError("authorization_data_missing")
        try:
            return self._cipher.decrypt(value, purpose=purpose)
        except ILinkCipherError as exc:
            raise OpenILinkHubError("authorization_data_invalid") from exc

    @staticmethod
    def _save_receipt(
        repository: ILinkRepository,
        *,
        command_id: str,
        user_id: UUID,
        fingerprint: str,
        result: HubBotAuthorizationView,
        now: datetime,
    ) -> None:
        repository.save_command_receipt(
            command_id=command_id,
            command_type="hub_bot_authorization",
            user_id=user_id,
            request_fingerprint=fingerprint,
            result_payload=result.model_dump(by_alias=True, mode="json"),
            now=now,
        )


def _qr_data_url(content: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(content)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    payload = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{payload}"
