from __future__ import annotations

import base64
from datetime import UTC, datetime
from email.utils import format_datetime
from hashlib import sha256
import hmac
from urllib.parse import urlencode, urlsplit

from app.domains.xfyun.config import XfyunCredentials


def websocket_auth_url(
    base_url: str,
    credentials: XfyunCredentials,
    *,
    now: datetime | None = None,
) -> str:
    parsed = urlsplit(base_url)
    date = format_datetime((now or datetime.now(UTC)).astimezone(UTC), usegmt=True)
    signature_origin = (
        f"host: {parsed.netloc}\n"
        f"date: {date}\n"
        f"GET {parsed.path} HTTP/1.1"
    )
    signature = base64.b64encode(
        hmac.new(
            credentials.api_secret.encode(),
            signature_origin.encode(),
            "sha256",
        ).digest()
    ).decode()
    authorization_origin = (
        f'api_key="{credentials.api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    query = urlencode(
        {
            "authorization": base64.b64encode(authorization_origin.encode()).decode(),
            "date": date,
            "host": parsed.netloc,
        }
    )
    return f"{base_url}?{query}"


def formula_headers(
    body: bytes,
    credentials: XfyunCredentials,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    host = "rest-api.xfyun.cn"
    path = "/v2/itr"
    date = format_datetime((now or datetime.now(UTC)).astimezone(UTC), usegmt=True)
    digest = "SHA-256=" + base64.b64encode(sha256(body).digest()).decode()
    signature_origin = (
        f"host: {host}\n"
        f"date: {date}\n"
        f"POST {path} HTTP/1.1\n"
        f"digest: {digest}"
    )
    signature = base64.b64encode(
        hmac.new(
            credentials.api_secret.encode(),
            signature_origin.encode(),
            "sha256",
        ).digest()
    ).decode()
    authorization_origin = (
        f'api_key="{credentials.api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line digest", signature="{signature}"'
    )
    return {
        "Authorization": authorization_origin,
        "Content-Type": "application/json",
        "Accept": "application/json,version=1.0",
        "Host": host,
        "Date": date,
        "Digest": digest,
    }
