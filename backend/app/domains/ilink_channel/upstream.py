from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit


UPSTREAM_PACKAGE = "@tencent-weixin/openclaw-weixin"
UPSTREAM_VERSION = "2.4.6"
UPSTREAM_NPM_INTEGRITY = (
    "sha512-qw9k3PLTiMWGNjjsknHgcTManH1w4j+Ji1ArWIaYLKCq3aFRsVwcqnPi127bvOoVMJGW4dbyJ8NECEMgoO+iRw=="
)
UPSTREAM_TARBALL_SHA256 = (
    "ef1c3600ca2fc0ee9076c1327af1e0d5d2e8e19fbb61e9f56c961fcde0bd07f6"
)

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_BOT_TYPE = "3"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = 132102
ILINK_QR_CREATE_ENDPOINT = "ilink/bot/get_bot_qrcode"
ILINK_QR_STATUS_ENDPOINT = "ilink/bot/get_qrcode_status"


class ILinkLoginStatus(str, Enum):
    WAIT = "wait"
    SCANNED = "scaned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    SCANNED_REDIRECT = "scaned_but_redirect"
    NEED_VERIFY_CODE = "need_verifycode"
    VERIFY_CODE_BLOCKED = "verify_code_blocked"
    ALREADY_BOUND = "binded_redirect"


def build_client_version(version: str) -> int:
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("invalid_semantic_version")
    major, minor, patch = (int(part) for part in parts)
    if any(part < 0 or part > 255 for part in (major, minor, patch)):
        raise ValueError("semantic_version_component_out_of_range")
    return (major << 16) | (minor << 8) | patch


def require_tencent_ilink_url(value: str, *, allow_path: bool) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not _is_tencent_ilink_host(host)
        or (not allow_path and (parsed.path.rstrip("/") or parsed.query))
    ):
        raise ValueError("unapproved_tencent_ilink_url")
    return value


def require_tencent_qr_browser_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not (host == "weixin.qq.com" or host.endswith(".weixin.qq.com"))
        or not parsed.path.rstrip("/")
    ):
        raise ValueError("unapproved_tencent_qr_browser_url")
    return value


def redirect_host_to_base_url(value: str) -> str:
    if not value or any(character in value for character in "/\\?#@:"):
        raise ValueError("unapproved_tencent_ilink_redirect")
    host = value.casefold().rstrip(".")
    if not _is_tencent_ilink_host(host):
        raise ValueError("unapproved_tencent_ilink_redirect")
    return f"https://{host}"


def _is_tencent_ilink_host(host: str) -> bool:
    return host == "ilinkai.weixin.qq.com" or (
        host.endswith(".weixin.qq.com")
        and host.split(".", 1)[0].startswith("ilink")
        and all(label and label.replace("-", "").isalnum() for label in host.split("."))
    )
