from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable
from urllib.parse import urlsplit

import httpx
from websockets.sync.client import connect

from app.domains.ilink_channel.hub_settings import OpenILinkHubSettings
from app.domains.ilink_channel.upstream import require_tencent_qr_browser_url


class OpenILinkHubError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HubBindStart:
    session_id: str
    qr_content: str


@dataclass(frozen=True)
class HubBindStatus:
    state: str
    bot_id: str | None = None
    qr_content: str | None = None
    message: str | None = None


class OpenILinkHubClient:
    def __init__(
        self,
        settings: OpenILinkHubSettings,
        *,
        client_factory: Callable[..., httpx.Client] | None = None,
        websocket_connect=connect,
    ) -> None:
        settings.validate()
        self._settings = settings
        self._client_factory = client_factory or httpx.Client
        self._websocket_connect = websocket_connect

    def start_bind(self) -> HubBindStart:
        session_cookie = self._login()
        payload = self._request_json(
            "POST",
            "/api/bots/bind/start",
            session_cookie=session_cookie,
            json_body={},
        )
        session_id = _required_text(payload.get("session_id"), "bind_session_missing")
        qr_content = _required_text(payload.get("qr_url"), "bind_qr_missing")
        try:
            require_tencent_qr_browser_url(qr_content)
        except ValueError as exc:
            raise OpenILinkHubError("bind_qr_unapproved") from exc
        return HubBindStart(session_id=session_id, qr_content=qr_content)

    def poll_bind(self, *, session_id: str) -> HubBindStatus:
        if not session_id.startswith("bind-") or len(session_id) > 160:
            raise OpenILinkHubError("bind_session_invalid")
        session_cookie = self._login()
        parsed = urlsplit(self._settings.base_url)
        ws_url = f"ws://{parsed.hostname}:{parsed.port}/api/bots/bind/status/{session_id}"
        try:
            with self._websocket_connect(
                ws_url,
                additional_headers={"Cookie": f"session={session_cookie}"},
                open_timeout=10,
                close_timeout=2,
                proxy=None,
            ) as socket:
                raw = socket.recv(timeout=self._settings.request_timeout_seconds)
        except Exception as exc:
            raise OpenILinkHubError("bind_status_unavailable") from exc
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise OpenILinkHubError("bind_status_invalid") from exc
        if not isinstance(payload, dict):
            raise OpenILinkHubError("bind_status_invalid")
        event = payload.get("event")
        if event == "error":
            return HubBindStatus(
                state="error",
                message=(
                    payload.get("message") if isinstance(payload.get("message"), str) else None
                ),
            )
        if event != "status":
            raise OpenILinkHubError("bind_status_invalid")
        state = _required_text(payload.get("status"), "bind_status_invalid")
        if state not in {"wait", "scanned", "refreshed", "connected"}:
            raise OpenILinkHubError("bind_status_unknown")
        qr_content = (
            _required_text(payload.get("qr_url"), "bind_qr_missing")
            if state == "refreshed"
            else None
        )
        if qr_content is not None:
            try:
                require_tencent_qr_browser_url(qr_content)
            except ValueError as exc:
                raise OpenILinkHubError("bind_qr_unapproved") from exc
        bot_id = (
            _required_text(payload.get("bot_id"), "bound_bot_missing")
            if state == "connected"
            else None
        )
        return HubBindStatus(state=state, bot_id=bot_id, qr_content=qr_content)

    def configure_control_bridge(self, *, bot_id: str) -> None:
        session_cookie = self._login()
        self._request_json(
            "PUT",
            f"/api/bots/{bot_id}/ai",
            session_cookie=session_cookie,
            json_body={"enabled": False},
        )
        installations = self._request_list(
            "GET",
            f"/api/bots/{bot_id}/apps",
            session_cookie=session_cookie,
        )
        bridge = next(
            (item for item in installations if item.get("app_slug") == "bridge"),
            None,
        )
        if bridge is None:
            builtins = self._request_list(
                "GET",
                "/api/marketplace/builtin",
                session_cookie=session_cookie,
            )
            app = next((item for item in builtins if item.get("slug") == "bridge"), None)
            if app is None:
                raise OpenILinkHubError("bridge_app_missing")
            app_id = _required_text(app.get("id"), "bridge_app_missing")
            bridge = self._request_json(
                "POST",
                f"/api/apps/{app_id}/install",
                session_cookie=session_cookie,
                json_body={"bot_id": bot_id},
            )
        app_id = _required_text(bridge.get("app_id"), "bridge_app_invalid")
        installation_id = _required_text(bridge.get("id"), "bridge_installation_invalid")
        self._request_json(
            "PUT",
            f"/api/apps/{app_id}/installations/{installation_id}",
            session_cookie=session_cookie,
            json_body={
                "enabled": True,
                "config": {
                    "forward_url": (
                        "http://127.0.0.1:8000/api/v2/ilink-channel/hub/events"
                    )
                },
            },
        )

    def send_text(
        self,
        *,
        bot_id: str,
        sender_id: str,
        content: str,
        trace_id: str,
    ) -> None:
        session_cookie = self._login()
        installations = self._request_list(
            "GET",
            f"/api/bots/{bot_id}/apps",
            session_cookie=session_cookie,
        )
        bridge = next(
            (item for item in installations if item.get("app_slug") == "bridge"),
            None,
        )
        if bridge is None:
            raise OpenILinkHubError("bridge_installation_missing")
        app_token = _required_text(bridge.get("app_token"), "bridge_token_missing")
        try:
            with self._client_factory(
                timeout=httpx.Timeout(self._settings.request_timeout_seconds, connect=5),
                follow_redirects=False,
            ) as client:
                response = client.post(
                    f"{self._settings.base_url}/bot/v1/message/send",
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {app_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "type": "text",
                        "content": content,
                        "to": sender_id,
                        "trace_id": trace_id,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenILinkHubError("bridge_send_failed") from exc

    def delete_bot(self, *, bot_id: str) -> None:
        session_cookie = self._login()
        self._request_json(
            "DELETE",
            f"/api/bots/{bot_id}",
            session_cookie=session_cookie,
            json_body=None,
            allow_not_found=True,
        )

    def _login(self) -> str:
        try:
            with self._client_factory(
                timeout=httpx.Timeout(10, connect=5),
                follow_redirects=False,
            ) as client:
                response = client.post(
                    f"{self._settings.base_url}/api/auth/login",
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json={
                        "username": self._settings.service_username,
                        "password": self._settings.service_password,
                    },
                )
                response.raise_for_status()
                token = response.cookies.get("session")
        except (httpx.HTTPError, KeyError) as exc:
            raise OpenILinkHubError("hub_login_failed") from exc
        if not token:
            raise OpenILinkHubError("hub_session_missing")
        return token

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        session_cookie: str,
        json_body: dict[str, object] | None,
        allow_not_found: bool = False,
    ) -> dict[str, object]:
        payload = self._request_payload(
            method,
            path,
            session_cookie=session_cookie,
            json_body=json_body,
            allow_not_found=allow_not_found,
        )
        if not isinstance(payload, dict):
            raise OpenILinkHubError("hub_response_invalid")
        return payload

    def _request_list(
        self,
        method: str,
        path: str,
        *,
        session_cookie: str,
        json_body: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        payload = self._request_payload(
            method,
            path,
            session_cookie=session_cookie,
            json_body=json_body,
        )
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise OpenILinkHubError("hub_response_invalid")
        return payload

    def _request_payload(
        self,
        method: str,
        path: str,
        *,
        session_cookie: str,
        json_body: dict[str, object] | None,
        allow_not_found: bool = False,
    ) -> object:
        try:
            with self._client_factory(
                timeout=httpx.Timeout(self._settings.request_timeout_seconds, connect=5),
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method,
                    f"{self._settings.base_url}{path}",
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Cookie": f"session={session_cookie}",
                    },
                    json=json_body,
                )
                if allow_not_found and response.status_code == 404:
                    return {}
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OpenILinkHubError("hub_request_failed") from exc
        return payload


def _required_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpenILinkHubError(code)
    return value.strip()
