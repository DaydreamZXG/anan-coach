from __future__ import annotations

from dataclasses import dataclass
import base64
import os
from urllib.parse import urlsplit


DEFAULT_ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_ILINK_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


class ILinkConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ILinkSettings:
    enabled: bool
    bot_account_id: str
    bot_token: str
    base_url: str
    cdn_base_url: str
    app_id: str
    app_client_version: int
    bot_agent: str
    identity_key_id: str
    identity_encryption_key: str
    identity_hmac_key: str
    binding_ttl_seconds: int
    long_poll_timeout_seconds: float
    request_timeout_seconds: float
    maximum_image_bytes: int
    transient_retention_seconds: int
    query_retention_days: int
    recognition_route: str

    @classmethod
    def from_environment(cls) -> "ILinkSettings":
        return cls(
            enabled=_bool("ILINK_CHANNEL_ENABLED", False),
            bot_account_id=os.getenv("ILINK_BOT_ACCOUNT_ID", "").strip(),
            bot_token=os.getenv("ILINK_BOT_TOKEN", "").strip(),
            base_url=os.getenv("ILINK_BASE_URL", DEFAULT_ILINK_BASE_URL).rstrip("/"),
            cdn_base_url=os.getenv(
                "ILINK_CDN_BASE_URL", DEFAULT_ILINK_CDN_BASE_URL
            ).rstrip("/"),
            app_id=os.getenv("ILINK_APP_ID", "bot").strip(),
            app_client_version=_integer("ILINK_APP_CLIENT_VERSION", 132102),
            bot_agent=os.getenv("ILINK_BOT_AGENT", "Anan/1.0").strip(),
            identity_key_id=os.getenv("ILINK_IDENTITY_KEY_ID", "").strip(),
            identity_encryption_key=os.getenv(
                "ILINK_IDENTITY_ENCRYPTION_KEY", ""
            ).strip(),
            identity_hmac_key=os.getenv("ILINK_IDENTITY_HMAC_KEY", "").strip(),
            binding_ttl_seconds=_integer("ILINK_BINDING_TTL_SECONDS", 600),
            long_poll_timeout_seconds=_float("ILINK_LONG_POLL_TIMEOUT_SECONDS", 35),
            request_timeout_seconds=_float("ILINK_REQUEST_TIMEOUT_SECONDS", 15),
            maximum_image_bytes=_integer(
                "ILINK_MAXIMUM_IMAGE_BYTES", 10 * 1024 * 1024
            ),
            transient_retention_seconds=_integer(
                "ILINK_TRANSIENT_RETENTION_SECONDS", 900
            ),
            query_retention_days=_integer("ILINK_QUERY_RETENTION_DAYS", 30),
            recognition_route=os.getenv(
                "ILINK_RECOGNITION_ROUTE",
                os.getenv("RECOGNITION_DEFAULT_IMPORT_ROUTE", ""),
            ).strip(),
        )

    def validate_binding(self) -> None:
        self._validate_common(require_token=False)

    def validate_worker(self) -> None:
        self._validate_common(require_token=True)
        if not self.recognition_route:
            raise ILinkConfigurationError("ILINK_RECOGNITION_ROUTE is required")

    def encryption_key_bytes(self) -> bytes:
        try:
            value = base64.b64decode(self.identity_encryption_key, validate=True)
        except ValueError as exc:
            raise ILinkConfigurationError(
                "ILINK_IDENTITY_ENCRYPTION_KEY must be base64"
            ) from exc
        if len(value) != 32:
            raise ILinkConfigurationError(
                "ILINK_IDENTITY_ENCRYPTION_KEY must decode to 32 bytes"
            )
        return value

    def _validate_common(self, *, require_token: bool) -> None:
        if not self.enabled:
            raise ILinkConfigurationError("iLink channel is not activated")
        for value, name in (
            (self.bot_account_id, "ILINK_BOT_ACCOUNT_ID"),
            (self.app_id, "ILINK_APP_ID"),
            (self.bot_agent, "ILINK_BOT_AGENT"),
            (self.identity_key_id, "ILINK_IDENTITY_KEY_ID"),
            (self.identity_hmac_key, "ILINK_IDENTITY_HMAC_KEY"),
        ):
            if not value:
                raise ILinkConfigurationError(f"{name} is required")
        if require_token and not self.bot_token:
            raise ILinkConfigurationError("ILINK_BOT_TOKEN is required")
        if len(self.identity_hmac_key.encode("utf-8")) < 32:
            raise ILinkConfigurationError(
                "ILINK_IDENTITY_HMAC_KEY must contain at least 32 bytes"
            )
        self.encryption_key_bytes()
        _require_exact_https_origin(
            self.base_url,
            expected_host="ilinkai.weixin.qq.com",
            expected_path="",
            name="ILINK_BASE_URL",
        )
        _require_exact_https_origin(
            self.cdn_base_url,
            expected_host="novac2c.cdn.weixin.qq.com",
            expected_path="/c2c",
            name="ILINK_CDN_BASE_URL",
        )
        if not 60 <= self.binding_ttl_seconds <= 1800:
            raise ILinkConfigurationError("ILINK_BINDING_TTL_SECONDS is outside policy")
        if not 1 <= self.maximum_image_bytes <= 20 * 1024 * 1024:
            raise ILinkConfigurationError("ILINK_MAXIMUM_IMAGE_BYTES is outside policy")
        if not 5 <= self.long_poll_timeout_seconds <= 60:
            raise ILinkConfigurationError(
                "ILINK_LONG_POLL_TIMEOUT_SECONDS is outside policy"
            )
        if not 1 <= self.request_timeout_seconds <= 30:
            raise ILinkConfigurationError("ILINK_REQUEST_TIMEOUT_SECONDS is outside policy")
        if not 60 <= self.transient_retention_seconds <= 3600:
            raise ILinkConfigurationError(
                "ILINK_TRANSIENT_RETENTION_SECONDS is outside policy"
            )
        if not 1 <= self.query_retention_days <= 365:
            raise ILinkConfigurationError("ILINK_QUERY_RETENTION_DAYS is outside policy")
        if self.app_client_version < 1:
            raise ILinkConfigurationError("ILINK_APP_CLIENT_VERSION is invalid")


def _require_exact_https_origin(
    value: str, *, expected_host: str, expected_path: str, name: str
) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != expected_host
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != expected_path
    ):
        raise ILinkConfigurationError(f"{name} differs from the approved official origin")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ILinkConfigurationError(f"{name} must be a boolean")


def _integer(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ILinkConfigurationError(f"{name} must be an integer") from exc


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ILinkConfigurationError(f"{name} must be a number") from exc
