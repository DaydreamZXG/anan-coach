from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import httpx

from app.core.config import Settings

CODE_TO_SESSION_PATH = "/sns/jscode2session"
CODE_TO_SESSION_GRANT_TYPE = "authorization_code"
WECHAT_TIMEOUT_SECONDS = 8.0


class WechatLoginError(RuntimeError):
    pass


class WechatLoginConfigurationError(WechatLoginError):
    pass


class WechatLoginTransportError(WechatLoginError):
    pass


class WechatLoginRejectedError(WechatLoginError):
    def __init__(self, *, error_code: int, error_message: str) -> None:
        super().__init__(f"Wechat login rejected with code {error_code}")
        self.error_code = error_code
        self.error_message = error_message


@dataclass(frozen=True)
class WechatIdentityClaims:
    app_id: str
    open_id: str
    union_id: str | None
    session_key_hash: str


class WechatIdentityExchange:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def exchange(self, *, code: str) -> WechatIdentityClaims:
        app_id = self._settings.wechat_mini_app_id.strip()
        app_secret = self._settings.wechat_mini_app_secret.strip()
        if not app_id or not app_secret:
            raise WechatLoginConfigurationError(
                "WECHAT_MINI_APP_ID and WECHAT_MINI_APP_SECRET are required"
            )

        try:
            with httpx.Client(
                base_url=self._settings.wechat_api_base_url,
                timeout=WECHAT_TIMEOUT_SECONDS,
                transport=self._transport,
            ) as client:
                response = client.get(
                    CODE_TO_SESSION_PATH,
                    params={
                        "appid": app_id,
                        "secret": app_secret,
                        "js_code": code,
                        "grant_type": CODE_TO_SESSION_GRANT_TYPE,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatLoginTransportError("Wechat code exchange failed") from exc

        if not isinstance(payload, dict):
            raise WechatLoginTransportError("Wechat code exchange returned invalid JSON")
        error_code = _optional_int(payload.get("errcode"))
        if error_code not in (None, 0):
            raise WechatLoginRejectedError(
                error_code=error_code,
                error_message=_optional_str(payload.get("errmsg")) or "unknown error",
            )

        open_id = _optional_str(payload.get("openid"))
        session_key = _optional_str(payload.get("session_key"))
        if not open_id or not session_key:
            raise WechatLoginTransportError("Wechat response is missing identity fields")
        return WechatIdentityClaims(
            app_id=app_id,
            open_id=open_id,
            union_id=_optional_str(payload.get("unionid")),
            session_key_hash=sha256(session_key.encode("utf-8")).hexdigest(),
        )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
