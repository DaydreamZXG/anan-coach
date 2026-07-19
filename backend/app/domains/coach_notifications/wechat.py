from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings


TOKEN_PATH = "/cgi-bin/token"
SEND_PATH = "/cgi-bin/message/subscribe/send"
TIMEOUT_SECONDS = 15.0
TRANSIENT_ERROR_CODES = {-1, 40001, 42001, 45009}
TOKEN_ERROR_CODES = {40001, 42001}


class WechatSubscribeMessageError(RuntimeError):
    def __init__(
        self, *, code: int | None, transient: bool, dispatch_uncertain: bool = False
    ) -> None:
        super().__init__("WeChat subscribe-message request failed")
        self.code = code
        self.transient = transient
        self.dispatch_uncertain = dispatch_uncertain


class WechatSubscribeMessageConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WechatSendReceipt:
    accepted_at: datetime


class WechatSubscribeMessageClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        base_url = settings.wechat_api_base_url.strip().rstrip("/")
        app_id = settings.wechat_mini_app_id.strip()
        app_secret = settings.wechat_mini_app_secret.strip()
        state = settings.wechat_coach_miniprogram_state.strip()
        if urlparse(base_url).scheme != "https" or not urlparse(base_url).hostname:
            raise WechatSubscribeMessageConfigurationError(
                "WECHAT_API_BASE_URL must be an HTTPS origin"
            )
        if not app_id or not app_secret:
            raise WechatSubscribeMessageConfigurationError(
                "WeChat mini-program credentials are required for coach notifications"
            )
        if state not in {"developer", "trial", "formal"}:
            raise WechatSubscribeMessageConfigurationError(
                "WECHAT_COACH_MINIPROGRAM_STATE is invalid"
            )
        self._base_url = base_url
        self._app_id = app_id
        self._app_secret = app_secret
        self._state = state
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token: str | None = None
        self._token_expires_at = datetime.min.replace(tzinfo=UTC)

    def send(
        self,
        *,
        open_id: str,
        template_id: str,
        page: str,
        data: dict[str, dict[str, str]],
    ) -> WechatSendReceipt:
        if not open_id or not template_id or not page or not data:
            raise ValueError("WeChat subscribe-message input is incomplete")
        payload: dict[str, Any] = {
            "touser": open_id,
            "template_id": template_id,
            "page": page,
            "data": data,
            "miniprogram_state": self._state,
            "lang": "zh_CN",
        }
        for attempt in range(2):
            token = self._access_token()
            result = self._post(payload=payload, access_token=token)
            try:
                error_code = _error_code(result)
            except WechatSubscribeMessageError as exc:
                raise WechatSubscribeMessageError(
                    code=None, transient=False, dispatch_uncertain=True
                ) from exc
            if error_code == 0:
                return WechatSendReceipt(accepted_at=self._clock())
            if error_code in TOKEN_ERROR_CODES and attempt == 0:
                self._token = None
                self._token_expires_at = datetime.min.replace(tzinfo=UTC)
                continue
            raise WechatSubscribeMessageError(
                code=error_code,
                transient=error_code in TRANSIENT_ERROR_CODES,
            )
        raise WechatSubscribeMessageError(code=None, transient=True)

    def _access_token(self) -> str:
        now = self._clock()
        if self._token and now < self._token_expires_at:
            return self._token
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=TIMEOUT_SECONDS,
                transport=self._transport,
            ) as client:
                response = client.get(
                    TOKEN_PATH,
                    params={
                        "grant_type": "client_credential",
                        "appid": self._app_id,
                        "secret": self._app_secret,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatSubscribeMessageError(code=None, transient=True) from exc
        if not isinstance(payload, dict):
            raise WechatSubscribeMessageError(code=None, transient=True)
        error_code = _error_code(payload)
        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if error_code != 0:
            raise WechatSubscribeMessageError(
                code=error_code,
                transient=error_code in TRANSIENT_ERROR_CODES,
            )
        if not isinstance(token, str) or not token:
            raise WechatSubscribeMessageError(code=None, transient=True)
        if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in < 600:
            raise WechatSubscribeMessageError(code=None, transient=True)
        self._token = token
        self._token_expires_at = now + timedelta(seconds=expires_in - 300)
        return token

    def _post(
        self, *, payload: dict[str, Any], access_token: str
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=TIMEOUT_SECONDS,
                transport=self._transport,
            ) as client:
                response = client.post(
                    SEND_PATH,
                    params={"access_token": access_token},
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatSubscribeMessageError(
                code=None, transient=False, dispatch_uncertain=True
            ) from exc
        if not isinstance(result, dict):
            raise WechatSubscribeMessageError(
                code=None, transient=False, dispatch_uncertain=True
            )
        return result


def _error_code(payload: dict[str, Any]) -> int:
    value = payload.get("errcode", 0)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WechatSubscribeMessageError(code=None, transient=True)
    return value
