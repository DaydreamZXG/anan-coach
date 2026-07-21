from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Callable
from urllib.parse import urlencode
import webbrowser

import httpx

from app.domains.ilink_channel.upstream import (
    ILINK_APP_CLIENT_VERSION,
    ILINK_APP_ID,
    ILINK_BASE_URL,
    ILINK_BOT_TYPE,
    ILINK_QR_CREATE_ENDPOINT,
    ILINK_QR_STATUS_ENDPOINT,
    ILinkLoginStatus,
    UPSTREAM_PACKAGE,
    UPSTREAM_VERSION,
    redirect_host_to_base_url,
    require_tencent_ilink_url,
    require_tencent_qr_browser_url,
)


MAX_RESPONSE_BYTES = 64 * 1024
MAX_QR_CHALLENGES = 3


class ILinkOperatorLoginError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class QRChallenge:
    challenge_id: str
    browser_url: str


@dataclass(frozen=True)
class QRStatus:
    status: ILinkLoginStatus
    bot_token: str | None = None
    account_id: str | None = None
    user_id: str | None = None
    base_url: str | None = None
    redirect_host: str | None = None


@dataclass(frozen=True)
class ILinkOperatorCredential:
    schema_version: int
    account_id: str
    bot_token: str
    base_url: str
    user_id: str | None
    upstream_package: str
    upstream_version: str
    issued_at: str


class ILinkOperatorLoginClient:
    def __init__(
        self,
        *,
        client_factory: Callable[..., httpx.Client] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_factory = client_factory or httpx.Client
        self._sleep = sleep
        self._monotonic = monotonic

    def start(self) -> QRChallenge:
        query = urlencode({"bot_type": ILINK_BOT_TYPE})
        payload = self._request_json(
            "POST",
            f"{ILINK_BASE_URL}/{ILINK_QR_CREATE_ENDPOINT}?{query}",
            json_body={"local_token_list": []},
            timeout_seconds=15,
        )
        challenge_id = payload.get("qrcode")
        browser_url = payload.get("qrcode_img_content")
        if not isinstance(challenge_id, str) or not challenge_id.strip():
            raise ILinkOperatorLoginError("qr_challenge_missing")
        if not isinstance(browser_url, str) or not browser_url.strip():
            raise ILinkOperatorLoginError("qr_browser_url_missing")
        try:
            require_tencent_qr_browser_url(browser_url)
        except ValueError as exc:
            raise ILinkOperatorLoginError("qr_browser_url_unapproved") from exc
        return QRChallenge(challenge_id=challenge_id, browser_url=browser_url)

    def poll_once(
        self,
        *,
        base_url: str,
        challenge_id: str,
        verify_code: str | None = None,
    ) -> QRStatus:
        try:
            require_tencent_ilink_url(base_url, allow_path=False)
        except ValueError as exc:
            raise ILinkOperatorLoginError("poll_origin_unapproved") from exc
        query_values = {"qrcode": challenge_id}
        if verify_code:
            query_values["verify_code"] = verify_code
        try:
            payload = self._request_json(
                "GET",
                f"{base_url}/{ILINK_QR_STATUS_ENDPOINT}?{urlencode(query_values)}",
                json_body=None,
                timeout_seconds=35,
            )
        except ILinkOperatorLoginError as exc:
            if exc.code == "ilink_operator_timeout":
                return QRStatus(status=ILinkLoginStatus.WAIT)
            raise
        try:
            status = ILinkLoginStatus(payload.get("status"))
        except (TypeError, ValueError) as exc:
            raise ILinkOperatorLoginError("qr_status_unknown") from exc
        return QRStatus(
            status=status,
            bot_token=_optional_text(payload.get("bot_token")),
            account_id=_optional_text(payload.get("ilink_bot_id")),
            user_id=_optional_text(payload.get("ilink_user_id")),
            base_url=_optional_text(payload.get("baseurl")),
            redirect_host=_optional_text(payload.get("redirect_host")),
        )

    def provision(
        self,
        *,
        timeout_seconds: int = 480,
        open_browser: Callable[[str], bool] = webbrowser.open,
        request_verify_code: Callable[[], str] | None = None,
    ) -> ILinkOperatorCredential:
        if not 60 <= timeout_seconds <= 900:
            raise ILinkOperatorLoginError("login_timeout_outside_policy")
        deadline = self._monotonic() + timeout_seconds
        challenge_count = 1
        challenge = self.start()
        _open_qr_browser(challenge.browser_url, open_browser=open_browser)
        polling_base_url = ILINK_BASE_URL
        verify_code: str | None = None

        while self._monotonic() < deadline:
            status = self.poll_once(
                base_url=polling_base_url,
                challenge_id=challenge.challenge_id,
                verify_code=verify_code,
            )
            if status.status == ILinkLoginStatus.CONFIRMED:
                if not status.account_id or not status.bot_token:
                    raise ILinkOperatorLoginError("confirmed_credentials_missing")
                credential_base_url = status.base_url or polling_base_url
                try:
                    require_tencent_ilink_url(credential_base_url, allow_path=False)
                except ValueError as exc:
                    raise ILinkOperatorLoginError("credential_origin_unapproved") from exc
                return ILinkOperatorCredential(
                    schema_version=1,
                    account_id=status.account_id,
                    bot_token=status.bot_token,
                    base_url=credential_base_url,
                    user_id=status.user_id,
                    upstream_package=UPSTREAM_PACKAGE,
                    upstream_version=UPSTREAM_VERSION,
                    issued_at=datetime.now(timezone.utc).isoformat(),
                )
            if status.status == ILinkLoginStatus.NEED_VERIFY_CODE:
                if request_verify_code is None:
                    raise ILinkOperatorLoginError("verify_code_required")
                verify_code = request_verify_code().strip()
                if not verify_code.isdigit() or not 4 <= len(verify_code) <= 8:
                    raise ILinkOperatorLoginError("verify_code_invalid")
                continue
            if status.status == ILinkLoginStatus.SCANNED:
                verify_code = None
            elif status.status == ILinkLoginStatus.SCANNED_REDIRECT:
                if not status.redirect_host:
                    raise ILinkOperatorLoginError("redirect_host_missing")
                try:
                    polling_base_url = redirect_host_to_base_url(status.redirect_host)
                except ValueError as exc:
                    raise ILinkOperatorLoginError("redirect_host_unapproved") from exc
            elif status.status in {
                ILinkLoginStatus.EXPIRED,
                ILinkLoginStatus.VERIFY_CODE_BLOCKED,
            }:
                challenge_count += 1
                if challenge_count > MAX_QR_CHALLENGES:
                    raise ILinkOperatorLoginError("qr_refresh_limit_reached")
                verify_code = None
                polling_base_url = ILINK_BASE_URL
                challenge = self.start()
                _open_qr_browser(challenge.browser_url, open_browser=open_browser)
            elif status.status == ILinkLoginStatus.ALREADY_BOUND:
                raise ILinkOperatorLoginError("bot_already_bound")
            self._sleep(1)
        raise ILinkOperatorLoginError("qr_login_timed_out")

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None,
        timeout_seconds: float,
    ) -> dict[str, object]:
        headers = {
            "Accept": "application/json",
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            with self._client_factory(
                timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10)),
                follow_redirects=False,
            ) as client:
                response = client.request(method, url, headers=headers, json=json_body)
                if response.is_redirect:
                    raise ILinkOperatorLoginError("unexpected_http_redirect")
                response.raise_for_status()
        except ILinkOperatorLoginError:
            raise
        except httpx.TimeoutException as exc:
            raise ILinkOperatorLoginError("ilink_operator_timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise ILinkOperatorLoginError("ilink_operator_http_rejected") from exc
        except httpx.NetworkError as exc:
            raise ILinkOperatorLoginError("ilink_operator_network_failed") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ILinkOperatorLoginError("ilink_operator_response_too_large")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ILinkOperatorLoginError("ilink_operator_response_invalid") from exc
        if not isinstance(payload, dict):
            raise ILinkOperatorLoginError("ilink_operator_response_invalid")
        return payload


def write_credential_file(
    credential: ILinkOperatorCredential,
    destination: Path,
    *,
    repository_root: Path | None,
) -> Path:
    destination = destination.expanduser().absolute()
    parent = destination.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_real_directory(parent)
    resolved_destination = parent.resolve(strict=True) / destination.name
    if repository_root is not None:
        resolved_repository = repository_root.resolve(strict=True)
        if resolved_destination == resolved_repository or resolved_repository in resolved_destination.parents:
            raise ILinkOperatorLoginError("credential_output_inside_repository")
    if destination.exists() or destination.is_symlink():
        raise ILinkOperatorLoginError("credential_output_exists")

    payload = json.dumps(
        asdict(credential), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    temporary = parent / f".ilink-credential-{secrets.token_hex(12)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination, follow_symlinks=False)
        _fsync_directory(parent)
    except FileExistsError as exc:
        raise ILinkOperatorLoginError("credential_output_exists") from exc
    except OSError as exc:
        raise ILinkOperatorLoginError("credential_output_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    if stat.S_IMODE(destination.lstat().st_mode) != 0o600:
        try:
            destination.unlink()
        finally:
            raise ILinkOperatorLoginError("credential_output_mode_invalid")
    return destination


def _open_qr_browser(url: str, *, open_browser: Callable[[str], bool]) -> None:
    try:
        opened = open_browser(url)
    except Exception as exc:
        raise ILinkOperatorLoginError("qr_browser_open_failed") from exc
    if not opened:
        raise ILinkOperatorLoginError("qr_browser_open_failed")


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _require_private_real_directory(path: Path) -> None:
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise ILinkOperatorLoginError("credential_parent_symlink_rejected")
    mode = stat.S_IMODE(resolved.stat().st_mode)
    if not resolved.is_dir() or mode & 0o077:
        raise ILinkOperatorLoginError("credential_parent_not_private")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
