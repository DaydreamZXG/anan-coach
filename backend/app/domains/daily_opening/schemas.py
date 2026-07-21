from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DailyOpeningModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)


class OpeningPreferenceView(DailyOpeningModel):
    preference_version_id: UUID = Field(alias="preferenceVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    enabled: bool
    weather_enabled: bool = Field(alias="weatherEnabled")
    coffee_prompt_enabled: bool = Field(alias="coffeePromptEnabled")
    coffee_start: time = Field(alias="coffeeStart")
    coffee_end: time = Field(alias="coffeeEnd")
    timezone: Literal["Asia/Shanghai"]


class OpeningPreferenceCommand(DailyOpeningModel):
    enabled: bool
    weather_enabled: bool = Field(alias="weatherEnabled")
    coffee_prompt_enabled: bool = Field(alias="coffeePromptEnabled")


class WeatherLocationView(DailyOpeningModel):
    location_version_id: UUID = Field(alias="locationVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    label: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class WeatherLocationCommand(DailyOpeningModel):
    label: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    source_type: Literal["wechat_choose_location"] = Field(
        default="wechat_choose_location", alias="sourceType"
    )


class WeatherSummary(DailyOpeningModel):
    provider: Literal["apple-weatherkit"] = "apple-weatherkit"
    location_label: str = Field(alias="locationLabel", min_length=1, max_length=120)
    condition_code: str = Field(alias="conditionCode", min_length=1, max_length=80)
    current_celsius: float = Field(alias="currentCelsius")
    minimum_celsius: float = Field(alias="minimumCelsius")
    maximum_celsius: float = Field(alias="maximumCelsius")
    precipitation_chance: float = Field(alias="precipitationChance", ge=0, le=1)
    attribution_url: str = Field(alias="attributionUrl", min_length=1, max_length=1000)
    provider_name: str = Field(alias="providerName", min_length=1, max_length=120)
    read_at: datetime = Field(alias="readAt")
    expires_at: datetime = Field(alias="expiresAt")


class OpeningWeatherView(DailyOpeningModel):
    state: Literal["ready", "location_required", "provider_unavailable", "disabled"]
    attribution_url: str = Field(default="", alias="attributionUrl")
    summary: WeatherSummary | None = None


class OpeningCoffeeView(DailyOpeningModel):
    state: Literal[
        "available",
        "today_declined",
        "disabled",
        "outside_window",
        "not_connected",
        "provider_unavailable",
    ]
    show_prompt: bool = Field(alias="showPrompt")
    prompt: str = ""

    @model_validator(mode="after")
    def validate_prompt(self) -> "OpeningCoffeeView":
        if self.show_prompt != (self.state == "available"):
            raise ValueError("only available coffee can display a prompt")
        if self.show_prompt and not self.prompt.strip():
            raise ValueError("available coffee requires prompt copy")
        return self


class OpeningPlanSummary(DailyOpeningModel):
    title: str = Field(min_length=1, max_length=300)
    estimated_minutes: int = Field(alias="estimatedMinutes", ge=0, le=1440)
    start_url: str = Field(alias="startUrl", pattern=r"^/")


class DailyOpeningView(DailyOpeningModel):
    local_date: date = Field(alias="localDate")
    display_state: Literal[
        "ready", "already_seen", "disabled", "location_required", "weather_unavailable"
    ] = Field(alias="displayState")
    plan_version_id: UUID = Field(alias="planVersionId")
    location_version_id: UUID | None = Field(default=None, alias="locationVersionId")
    weather_fingerprint: str | None = Field(
        default=None, alias="weatherFingerprint", pattern=r"^[0-9a-f]{64}$"
    )
    greeting: str = Field(min_length=1, max_length=300)
    plan: OpeningPlanSummary
    preference: OpeningPreferenceView
    weather_state: Literal[
        "ready", "location_required", "provider_unavailable", "disabled"
    ] = Field(alias="weatherState")
    weather: OpeningWeatherView
    coffee_state: Literal[
        "available",
        "today_declined",
        "disabled",
        "outside_window",
        "not_connected",
        "provider_unavailable",
    ] = Field(alias="coffeeState")
    coffee: OpeningCoffeeView


class ImpressionCommand(DailyOpeningModel):
    plan_version_id: UUID = Field(alias="planVersionId")
    preference_version_id: UUID = Field(alias="preferenceVersionId")
    location_version_id: UUID | None = Field(default=None, alias="locationVersionId")
    weather_fingerprint: str | None = Field(
        default=None, alias="weatherFingerprint", pattern=r"^[0-9a-f]{64}$"
    )
    coffee_state: Literal[
        "available",
        "today_declined",
        "disabled",
        "outside_window",
        "not_connected",
        "provider_unavailable",
    ] = Field(alias="coffeeState")


class ImpressionResult(DailyOpeningModel):
    impression_id: UUID = Field(alias="impressionId")
    local_date: date = Field(alias="localDate")
    displayed_at: datetime = Field(alias="displayedAt")


class CoffeeDecisionCommand(DailyOpeningModel):
    decision: Literal["declined_today"]


class CoffeeDecisionResult(DailyOpeningModel):
    decision_id: UUID = Field(alias="decisionId")
    local_date: date = Field(alias="localDate")
    decision: Literal["declined_today"]
    decided_at: datetime = Field(alias="decidedAt")
