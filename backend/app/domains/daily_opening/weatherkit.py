from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any, Callable

import httpx
import jwt

from app.domains.daily_opening.settings import (
    DailyOpeningConfigurationError,
    DailyOpeningSettings,
)


class WeatherKitProviderError(RuntimeError):
    """A strict WeatherKit request or response failed."""


@dataclass(frozen=True)
class WeatherKitSnapshot:
    condition_code: str
    current_celsius: float
    minimum_celsius: float
    maximum_celsius: float
    precipitation_chance: float
    attribution_url: str
    provider_name: str
    read_at: datetime
    expires_at: datetime

    def payload(self) -> dict[str, object]:
        return {
            "provider": "apple-weatherkit",
            "conditionCode": self.condition_code,
            "currentCelsius": self.current_celsius,
            "minimumCelsius": self.minimum_celsius,
            "maximumCelsius": self.maximum_celsius,
            "precipitationChance": self.precipitation_chance,
            "attributionUrl": self.attribution_url,
            "providerName": self.provider_name,
            "readAt": self.read_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class WeatherKitClient:
    def __init__(
        self,
        settings: DailyOpeningSettings,
        *,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            timeout=8.0,
            follow_redirects=False,
            trust_env=False,
        )
        self._now = now or (lambda: datetime.now(UTC))

    def fetch(self, *, latitude: float, longitude: float) -> WeatherKitSnapshot:
        if not self._settings.weatherkit_enabled:
            raise DailyOpeningConfigurationError("WeatherKit is not enabled")
        now = self._now().astimezone(UTC)
        response = self._client.get(
            f"{self._settings.weatherkit_base_url}/api/v1/weather/"
            f"{self._settings.weatherkit_language}/{latitude:.3f}/{longitude:.3f}",
            params={
                "dataSets": "currentWeather,forecastDaily",
                "timezone": "Asia/Shanghai",
                "countryCode": "CN",
            },
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._signed_token(now)}",
            },
        )
        if response.status_code != 200:
            raise WeatherKitProviderError(
                f"WeatherKit returned status {response.status_code}"
            )
        try:
            payload = response.json()
            current = _object(payload, "currentWeather")
            daily = _object(payload, "forecastDaily")
            days = daily.get("days")
            if not isinstance(days, list) or not days or not isinstance(days[0], dict):
                raise ValueError("forecastDaily.days is empty")
            today = days[0]
            metadata = _object(current, "metadata")
            attribution_url = _required_text(metadata, "attributionURL")
            if not attribution_url.startswith("https://"):
                raise ValueError("attributionURL must use HTTPS")
            read_at = _timestamp(metadata.get("readTime"), fallback=now)
            expires_at = _timestamp(
                metadata.get("expireTime"), fallback=now + timedelta(minutes=15)
            )
            if expires_at <= now:
                raise ValueError("WeatherKit response is already expired")
            precipitation = float(today.get("precipitationChance", 0.0))
            if not 0 <= precipitation <= 1:
                raise ValueError("precipitationChance is outside [0, 1]")
            return WeatherKitSnapshot(
                condition_code=_required_text(current, "conditionCode"),
                current_celsius=float(current["temperature"]),
                minimum_celsius=float(today["temperatureMin"]),
                maximum_celsius=float(today["temperatureMax"]),
                precipitation_chance=precipitation,
                attribution_url=attribution_url,
                provider_name=str(metadata.get("providerName") or "Apple Weather"),
                read_at=read_at,
                expires_at=expires_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherKitProviderError("WeatherKit response contract is invalid") from exc

    def _signed_token(self, now: datetime) -> str:
        path = self._settings.weatherkit_private_key_path
        if path is None:
            raise DailyOpeningConfigurationError("WeatherKit private key path is missing")
        private_key = path.read_text(encoding="utf-8")
        issued = int(now.timestamp())
        return jwt.encode(
            {
                "iss": self._settings.weatherkit_team_id,
                "iat": issued,
                "exp": issued + 300,
                "sub": self._settings.weatherkit_service_id,
            },
            private_key,
            algorithm="ES256",
            headers={
                "kid": self._settings.weatherkit_key_id,
                "id": (
                    f"{self._settings.weatherkit_team_id}."
                    f"{self._settings.weatherkit_service_id}"
                ),
            },
        )


def _object(value: Any, key: str) -> dict[str, Any]:
    nested = value.get(key) if isinstance(value, dict) else None
    if not isinstance(nested, dict):
        raise ValueError(f"{key} must be an object")
    return nested


def _required_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"{key} must be non-empty text")
    return result


def _timestamp(value: object, *, fallback: datetime) -> datetime:
    if not isinstance(value, str):
        return fallback
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(UTC)
