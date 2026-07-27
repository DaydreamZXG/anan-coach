from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit


class OpenILinkHubConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenILinkHubSettings:
    enabled: bool
    base_url: str
    service_username: str
    service_password: str
    authorization_ttl_seconds: int
    request_timeout_seconds: float

    @classmethod
    def from_environment(cls) -> "OpenILinkHubSettings":
        return cls(
            enabled=_bool("OPENILINK_HUB_ENABLED", False),
            base_url=os.getenv("OPENILINK_HUB_BASE_URL", "http://127.0.0.1:9800").rstrip("/"),
            service_username=os.getenv("OPENILINK_HUB_SERVICE_USERNAME", "").strip(),
            service_password=os.getenv("OPENILINK_HUB_SERVICE_PASSWORD", "").strip(),
            authorization_ttl_seconds=_integer("OPENILINK_HUB_AUTHORIZATION_TTL_SECONDS", 600),
            request_timeout_seconds=_float("OPENILINK_HUB_REQUEST_TIMEOUT_SECONDS", 45),
        )

    def validate(self) -> None:
        if not self.enabled:
            raise OpenILinkHubConfigurationError("OpenIlink Hub is not enabled")
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port != 9800
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path.rstrip("/")
            or parsed.query
            or parsed.fragment
        ):
            raise OpenILinkHubConfigurationError(
                "OPENILINK_HUB_BASE_URL must be the private loopback Hub origin"
            )
        if not self.service_username or len(self.service_password) < 16:
            raise OpenILinkHubConfigurationError("OpenIlink Hub service credentials are incomplete")
        if not 300 <= self.authorization_ttl_seconds <= 900:
            raise OpenILinkHubConfigurationError(
                "OpenIlink Hub authorization TTL is outside policy"
            )
        if not 10 <= self.request_timeout_seconds <= 60:
            raise OpenILinkHubConfigurationError("OpenIlink Hub request timeout is outside policy")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise OpenILinkHubConfigurationError(f"{name} must be a boolean")


def _integer(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise OpenILinkHubConfigurationError(f"{name} must be an integer") from exc


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError as exc:
        raise OpenILinkHubConfigurationError(f"{name} must be a number") from exc
