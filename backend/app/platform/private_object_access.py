from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

import tos

from app.core.config import Settings


class TosPrivateObjectAccessSigner:
    """Signs owner-authorized private objects without exposing object credentials."""

    def __init__(self, settings: Settings, *, maximum_ttl_seconds: int = 900) -> None:
        if not all(
            value.strip()
            for value in (
                settings.tos_access_key,
                settings.tos_secret_key,
                settings.tos_endpoint,
                settings.tos_region,
                settings.tos_bucket,
            )
        ):
            raise ValueError("TOS private object access configuration is incomplete")
        if not 1 <= maximum_ttl_seconds <= 900:
            raise ValueError("private object access maximum TTL must be within 15 minutes")
        self._bucket = settings.tos_bucket
        self._maximum_ttl_seconds = maximum_ttl_seconds
        self._client = tos.TosClientV2(
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
        )

    def sign_download(self, *, artifact_ref: str, expires_at: datetime) -> str:
        parsed = urlsplit(artifact_ref)
        if (
            parsed.scheme != "tos"
            or parsed.netloc != self._bucket
            or not parsed.path.startswith("/private/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("artifact is not in the approved private TOS bucket")
        seconds = int((expires_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
        if not 1 <= seconds <= self._maximum_ttl_seconds:
            raise ValueError("private object access authorization has an invalid TTL")
        output = self._client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get,
            self._bucket,
            parsed.path.removeprefix("/"),
            expires=seconds,
        )
        url = str(output.signed_url)
        if not url.startswith("https://"):
            raise ValueError("private object access authorization must use HTTPS")
        return url
