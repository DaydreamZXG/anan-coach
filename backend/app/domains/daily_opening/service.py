from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.domains.daily_opening.repository import (
    DailyOpeningConflict,
    DailyOpeningRepository,
    _location_view,
    _preference_view,
)
from app.domains.daily_opening.schemas import (
    CoffeeDecisionResult,
    DailyOpeningView,
    ImpressionCommand,
    ImpressionResult,
    OpeningCoffeeView,
    OpeningPlanSummary,
    OpeningPreferenceCommand,
    OpeningPreferenceView,
    OpeningWeatherView,
    WeatherLocationCommand,
    WeatherLocationView,
    WeatherSummary,
)
from app.domains.daily_opening.settings import DailyOpeningSettings
from app.domains.daily_opening.weatherkit import (
    WeatherKitClient,
    WeatherKitProviderError,
    WeatherKitSnapshot,
)
from app.domains.planning.repository import PlanningRepository


SHANGHAI = ZoneInfo("Asia/Shanghai")


class DailyOpeningService:
    def __init__(
        self,
        *,
        settings: DailyOpeningSettings | None = None,
        repository: DailyOpeningRepository | None = None,
        planning: PlanningRepository | None = None,
        weatherkit: WeatherKitClient | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings or DailyOpeningSettings.from_env()
        self._repository = repository or DailyOpeningRepository()
        self._planning = planning or PlanningRepository()
        self._weatherkit = weatherkit
        if self._weatherkit is None and self._settings.weatherkit_enabled:
            self._weatherkit = WeatherKitClient(self._settings)
        self._now = now or (lambda: datetime.now(UTC))

    def view(self, session: Session, *, user_id: str) -> DailyOpeningView:
        now = self._now().astimezone(UTC)
        local_now = now.astimezone(SHANGHAI)
        local_date = local_now.date()
        plan = self._planning.current_plan(session, user_id=user_id, local_date=local_date)
        if plan is None:
            raise DailyOpeningConflict("today plan must exist before daily opening")
        tasks = self._planning.plan_tasks(
            session, plan_version_id=str(plan["plan_version_id"])
        )
        if tasks:
            task = next(
                (
                    item
                    for item in tasks
                    if str(item.get("execution_status") or "ready")
                    not in {"complete", "abandoned"}
                ),
                tasks[0],
            )
            plan_summary = OpeningPlanSummary(
                title=str(task["title"]),
                estimatedMinutes=int(task["estimated_minutes"]),
                startUrl=str(task["start_url"]),
            )
        else:
            plan_summary = OpeningPlanSummary(
                title=str(plan["headline"]),
                estimatedMinutes=0,
                startUrl="/pages/today/index",
            )
        preference_row = self._repository.ensure_default_preference(
            session, user_id=user_id, now=now
        )
        preference = _preference_view(preference_row)
        location_row = self._repository.current_location(session, user_id=user_id)
        location = _location_view(location_row) if location_row is not None else None
        weather, weather_fingerprint = self._weather(
            session,
            preference=preference,
            location=location,
            now=now,
        )
        coffee = self._coffee(
            session,
            user_id=user_id,
            preference=preference,
            local_now=local_now,
            now=now,
        )
        already_seen = self._repository.impression(
            session, user_id=user_id, local_date=local_date
        ) is not None
        if already_seen:
            display_state = "already_seen"
        elif not preference.enabled:
            display_state = "disabled"
        elif weather.state == "location_required":
            display_state = "location_required"
        elif weather.state == "provider_unavailable":
            display_state = "weather_unavailable"
        else:
            display_state = "ready"
        greeting = (
            f"早上好，今天先用 {plan_summary.estimated_minutes} 分钟拿下“"
            f"{plan_summary.title}”。"
            if local_now.hour < 12
            else f"回来啦，先完成“{plan_summary.title}”，今天就没有白过。"
        )
        view = DailyOpeningView(
            localDate=local_date,
            displayState=display_state,
            planVersionId=plan["plan_version_id"],
            locationVersionId=(location.location_version_id if location else None),
            weatherFingerprint=weather_fingerprint,
            greeting=greeting,
            plan=plan_summary,
            preference=preference,
            weatherState=weather.state,
            weather=weather,
            coffeeState=coffee.state,
            coffee=coffee,
        )
        return view

    def update_preference(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: OpeningPreferenceCommand,
    ) -> OpeningPreferenceView:
        return self._repository.update_preference(
            session,
            user_id=user_id,
            command_id=command_id,
            command=command,
            now=self._now().astimezone(UTC),
        )

    def preference(self, session: Session, *, user_id: str) -> OpeningPreferenceView:
        row = self._repository.ensure_default_preference(
            session, user_id=user_id, now=self._now().astimezone(UTC)
        )
        return _preference_view(row)

    def update_location(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: WeatherLocationCommand,
    ) -> WeatherLocationView:
        return self._repository.update_location(
            session,
            user_id=user_id,
            command_id=command_id,
            command=command,
            now=self._now().astimezone(UTC),
        )

    def record_impression(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: ImpressionCommand,
    ) -> ImpressionResult:
        current = self.view(session, user_id=user_id)
        if current.display_state == "already_seen":
            existing = self._repository.impression(
                session, user_id=user_id, local_date=current.local_date
            )
            if existing is None:
                raise DailyOpeningConflict("impression projection is inconsistent")
            return ImpressionResult(
                impressionId=existing["impression_id"],
                localDate=existing["local_date"],
                displayedAt=existing["displayed_at"],
            )
        expected = (
            current.plan_version_id,
            current.preference.preference_version_id,
            current.location_version_id,
            current.weather_fingerprint,
            current.coffee_state,
        )
        supplied = (
            command.plan_version_id,
            command.preference_version_id,
            command.location_version_id,
            command.weather_fingerprint,
            command.coffee_state,
        )
        if supplied != expected:
            raise DailyOpeningConflict("daily opening changed before display was recorded")
        return self._repository.record_impression(
            session,
            user_id=user_id,
            command_id=command_id,
            local_date=current.local_date,
            command=command,
            now=self._now().astimezone(UTC),
        )

    def decline_coffee(
        self, session: Session, *, user_id: str, command_id: str
    ) -> CoffeeDecisionResult:
        now = self._now().astimezone(UTC)
        return self._repository.decline_coffee(
            session,
            user_id=user_id,
            command_id=command_id,
            local_date=now.astimezone(SHANGHAI).date(),
            now=now,
        )

    def _weather(
        self,
        session: Session,
        *,
        preference: OpeningPreferenceView,
        location: WeatherLocationView | None,
        now: datetime,
    ) -> tuple[OpeningWeatherView, str | None]:
        if not preference.weather_enabled:
            return OpeningWeatherView(state="disabled", attributionUrl=""), None
        if location is None:
            return OpeningWeatherView(state="location_required", attributionUrl=""), None
        if not self._settings.weatherkit_enabled:
            return OpeningWeatherView(state="provider_unavailable", attributionUrl=""), None
        if self._weatherkit is None:
            return OpeningWeatherView(state="provider_unavailable", attributionUrl=""), None
        location_key = f"{location.latitude:.3f},{location.longitude:.3f}"
        cached = self._repository.cached_weather(
            session,
            location_key=location_key,
            language=self._settings.weatherkit_language,
            now=now,
        )
        if cached is not None:
            payload = dict(cached["summary"])
            snapshot = _snapshot(payload)
            fingerprint = str(cached["summary_fingerprint"])
        else:
            try:
                snapshot = self._weatherkit.fetch(
                    latitude=location.latitude, longitude=location.longitude
                )
            except WeatherKitProviderError:
                return (
                    OpeningWeatherView(state="provider_unavailable", attributionUrl=""),
                    None,
                )
            self._repository.store_weather(
                session,
                location_key=location_key,
                language=self._settings.weatherkit_language,
                snapshot=snapshot,
                now=now,
            )
            fingerprint = snapshot.fingerprint()
        summary = WeatherSummary(
            locationLabel=location.label,
            conditionCode=snapshot.condition_code,
            currentCelsius=snapshot.current_celsius,
            minimumCelsius=snapshot.minimum_celsius,
            maximumCelsius=snapshot.maximum_celsius,
            precipitationChance=snapshot.precipitation_chance,
            attributionUrl=snapshot.attribution_url,
            providerName=snapshot.provider_name,
            readAt=snapshot.read_at,
            expiresAt=snapshot.expires_at,
        )
        return (
            OpeningWeatherView(
                state="ready",
                attributionUrl=snapshot.attribution_url,
                summary=summary,
            ),
            fingerprint,
        )

    def _coffee(
        self,
        session: Session,
        *,
        user_id: str,
        preference: OpeningPreferenceView,
        local_now: datetime,
        now: datetime,
    ) -> OpeningCoffeeView:
        if not preference.coffee_prompt_enabled:
            return OpeningCoffeeView(state="disabled", showPrompt=False)
        if not (
            preference.coffee_start <= local_now.time().replace(tzinfo=None)
            < preference.coffee_end
        ):
            return OpeningCoffeeView(state="outside_window", showPrompt=False)
        if self._repository.coffee_declined(
            session, user_id=user_id, local_date=local_now.date()
        ):
            return OpeningCoffeeView(state="today_declined", showPrompt=False)
        if not self._settings.luckin_mcp_enabled:
            return OpeningCoffeeView(state="provider_unavailable", showPrompt=False)
        if self._repository.active_luckin_connection(
            session, user_id=user_id, now=now
        ) is None:
            return OpeningCoffeeView(state="not_connected", showPrompt=False)
        # Official per-user authorization and exact order schemas are external release
        # evidence, not configuration guesses. Keep the action absent until that release
        # adds its reviewed endpoints and end-to-end confirmation screen.
        return OpeningCoffeeView(state="provider_unavailable", showPrompt=False)


def _snapshot(payload: dict[str, object]) -> WeatherKitSnapshot:
    return WeatherKitSnapshot(
        condition_code=str(payload["conditionCode"]),
        current_celsius=float(payload["currentCelsius"]),
        minimum_celsius=float(payload["minimumCelsius"]),
        maximum_celsius=float(payload["maximumCelsius"]),
        precipitation_chance=float(payload["precipitationChance"]),
        attribution_url=str(payload["attributionUrl"]),
        provider_name=str(payload["providerName"]),
        read_at=datetime.fromisoformat(str(payload["readAt"])),
        expires_at=datetime.fromisoformat(str(payload["expiresAt"])),
    )
