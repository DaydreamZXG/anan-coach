from __future__ import annotations

from base64 import urlsafe_b64decode
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse


class DailyOpeningConfigurationError(RuntimeError):
    """Raised when an enabled external provider is not safely configured."""


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DailyOpeningSettings:
    weatherkit_enabled: bool
    weatherkit_team_id: str
    weatherkit_key_id: str
    weatherkit_service_id: str
    weatherkit_private_key_path: Path | None
    weatherkit_base_url: str
    weatherkit_language: str
    luckin_mcp_enabled: bool
    luckin_mcp_endpoint: str
    luckin_token_key_id: str
    luckin_token_encryption_key: str

    @classmethod
    def from_env(cls) -> "DailyOpeningSettings":
        key_path = os.getenv("WEATHERKIT_PRIVATE_KEY_PATH", "").strip()
        settings = cls(
            weatherkit_enabled=_enabled(os.getenv("WEATHERKIT_ENABLED")),
            weatherkit_team_id=os.getenv("WEATHERKIT_TEAM_ID", "").strip(),
            weatherkit_key_id=os.getenv("WEATHERKIT_KEY_ID", "").strip(),
            weatherkit_service_id=os.getenv("WEATHERKIT_SERVICE_ID", "").strip(),
            weatherkit_private_key_path=Path(key_path) if key_path else None,
            weatherkit_base_url=os.getenv(
                "WEATHERKIT_BASE_URL", "https://weatherkit.apple.com"
            ).rstrip("/"),
            weatherkit_language=os.getenv("WEATHERKIT_LANGUAGE", "zh-Hans").strip(),
            luckin_mcp_enabled=_enabled(os.getenv("LUCKIN_MCP_ENABLED")),
            luckin_mcp_endpoint=os.getenv(
                "LUCKIN_MCP_ENDPOINT", "https://gwmcp.lkcoffee.com/order/user/mcp"
            ).strip(),
            luckin_token_key_id=os.getenv("LUCKIN_TOKEN_KEY_ID", "").strip(),
            luckin_token_encryption_key=os.getenv(
                "LUCKIN_TOKEN_ENCRYPTION_KEY", ""
            ).strip(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.weatherkit_base_url != "https://weatherkit.apple.com":
            raise DailyOpeningConfigurationError(
                "WeatherKit endpoint must be https://weatherkit.apple.com"
            )
        if self.weatherkit_enabled:
            missing = [
                name
                for name, value in (
                    ("WEATHERKIT_TEAM_ID", self.weatherkit_team_id),
                    ("WEATHERKIT_KEY_ID", self.weatherkit_key_id),
                    ("WEATHERKIT_SERVICE_ID", self.weatherkit_service_id),
                    ("WEATHERKIT_PRIVATE_KEY_PATH", self.weatherkit_private_key_path),
                )
                if not value
            ]
            if missing:
                raise DailyOpeningConfigurationError(
                    "WeatherKit is enabled but configuration is missing: "
                    + ", ".join(missing)
                )
            assert self.weatherkit_private_key_path is not None
            if not self.weatherkit_private_key_path.is_file():
                raise DailyOpeningConfigurationError(
                    "WEATHERKIT_PRIVATE_KEY_PATH must reference a readable file"
                )
        parsed = urlparse(self.luckin_mcp_endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "gwmcp.lkcoffee.com"
            or parsed.path != "/order/user/mcp"
            or parsed.query
            or parsed.fragment
        ):
            raise DailyOpeningConfigurationError(
                "Luckin MCP endpoint must be the official HTTPS endpoint"
            )
        if self.luckin_mcp_enabled and (
            not self.luckin_token_key_id or not self.luckin_token_encryption_key
        ):
            raise DailyOpeningConfigurationError(
                "Luckin MCP is enabled but envelope-encryption configuration is missing"
            )
        if self.luckin_mcp_enabled:
            try:
                encoded = self.luckin_token_encryption_key
                encryption_key = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except Exception as exc:
                raise DailyOpeningConfigurationError(
                    "LUCKIN_TOKEN_ENCRYPTION_KEY must be valid base64url"
                ) from exc
            if len(encryption_key) != 32:
                raise DailyOpeningConfigurationError(
                    "LUCKIN_TOKEN_ENCRYPTION_KEY must contain exactly 32 bytes"
                )
