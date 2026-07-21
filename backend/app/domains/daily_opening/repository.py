from __future__ import annotations

from datetime import date, datetime, time
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.daily_opening.luckin_mcp import OrderConfirmation
from app.domains.daily_opening.schemas import (
    CoffeeDecisionResult,
    ImpressionCommand,
    ImpressionResult,
    OpeningPreferenceCommand,
    OpeningPreferenceView,
    WeatherLocationCommand,
    WeatherLocationView,
)
from app.domains.daily_opening.weatherkit import WeatherKitSnapshot


class DailyOpeningConflict(RuntimeError):
    """A command conflicts with the immutable current daily-opening state."""


def fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class DailyOpeningRepository:
    def current_preference(
        self, session: Session, *, user_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select preference.*
                from messaging.daily_opening_preference_versions preference
                where preference.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1
                    from messaging.daily_opening_preference_versions successor
                    where successor.supersedes_preference_version_id =
                          preference.preference_version_id
                  )
                order by preference.version_no desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()

    def ensure_default_preference(
        self, session: Session, *, user_id: str, now: datetime
    ) -> RowMapping:
        current = self.current_preference(session, user_id=user_id)
        if current is not None:
            return current
        payload: dict[str, object] = {
            "enabled": True,
            "weatherEnabled": True,
            "coffeePromptEnabled": True,
            "coffeeStart": "07:00:00",
            "coffeeEnd": "17:00:00",
            "timezone": "Asia/Shanghai",
        }
        try:
            with session.begin_nested():
                return session.execute(
                    text(
                    """
                    insert into messaging.daily_opening_preference_versions (
                      preference_version_id, command_id, request_fingerprint,
                      user_id, version_no, enabled, weather_enabled,
                      coffee_prompt_enabled, coffee_start, coffee_end,
                      timezone, source_type, supersedes_preference_version_id,
                      created_at
                    ) values (
                      cast(:preference_id as uuid), :command_id, :fingerprint,
                      cast(:user_id as uuid), 1, true, true, true,
                      time '07:00', time '17:00', 'Asia/Shanghai',
                      'system_default', null, :now
                    ) returning *
                    """
                    ),
                    {
                        "preference_id": str(uuid4()),
                        "command_id": f"daily-opening-default:{user_id}",
                        "fingerprint": fingerprint(payload),
                        "user_id": user_id,
                        "now": now,
                    },
                ).mappings().one()
        except IntegrityError:
            current = self.current_preference(session, user_id=user_id)
            if current is None:
                raise
            return current

    def update_preference(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: OpeningPreferenceCommand,
        now: datetime,
    ) -> OpeningPreferenceView:
        payload = command.model_dump(by_alias=True)
        request_fingerprint = fingerprint(payload)
        replay = self._preference_by_command(
            session, user_id=user_id, command_id=command_id
        )
        if replay is not None:
            if replay["request_fingerprint"] != request_fingerprint:
                raise DailyOpeningConflict("idempotency key was reused with another preference")
            return _preference_view(replay)
        current = self.ensure_default_preference(session, user_id=user_id, now=now)
        row = session.execute(
            text(
                """
                insert into messaging.daily_opening_preference_versions (
                  preference_version_id, command_id, request_fingerprint,
                  user_id, version_no, enabled, weather_enabled,
                  coffee_prompt_enabled, coffee_start, coffee_end,
                  timezone, source_type, supersedes_preference_version_id,
                  created_at
                ) values (
                  cast(:preference_id as uuid), :command_id, :fingerprint,
                  cast(:user_id as uuid), :version_no, :enabled, :weather_enabled,
                  :coffee_enabled, :coffee_start, :coffee_end,
                  'Asia/Shanghai', :source_type,
                  cast(:supersedes as uuid), :now
                ) returning *
                """
            ),
            {
                "preference_id": str(uuid4()),
                "command_id": command_id,
                "fingerprint": request_fingerprint,
                "user_id": user_id,
                "version_no": int(current["version_no"]) + 1,
                "enabled": command.enabled,
                "weather_enabled": command.weather_enabled,
                "coffee_enabled": command.coffee_prompt_enabled,
                "coffee_start": current["coffee_start"],
                "coffee_end": current["coffee_end"],
                "source_type": "user_confirmed" if command.enabled else "user_disabled",
                "supersedes": str(current["preference_version_id"]),
                "now": now,
            },
        ).mappings().one()
        return _preference_view(row)

    def current_location(self, session: Session, *, user_id: str) -> RowMapping | None:
        return session.execute(
            text(
                """
                select location.*
                from messaging.daily_opening_location_versions location
                where location.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1
                    from messaging.daily_opening_location_versions successor
                    where successor.supersedes_location_version_id =
                          location.location_version_id
                  )
                order by location.version_no desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()

    def update_location(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: WeatherLocationCommand,
        now: datetime,
    ) -> WeatherLocationView:
        latitude = float(f"{command.latitude:.3f}")
        longitude = float(f"{command.longitude:.3f}")
        payload: dict[str, object] = {
            "label": command.label.strip(),
            "latitude": latitude,
            "longitude": longitude,
            "sourceType": command.source_type,
        }
        request_fingerprint = fingerprint(payload)
        replay = self._location_by_command(
            session, user_id=user_id, command_id=command_id
        )
        if replay is not None:
            if replay["request_fingerprint"] != request_fingerprint:
                raise DailyOpeningConflict("idempotency key was reused with another location")
            return _location_view(replay)
        current = self.current_location(session, user_id=user_id)
        row = session.execute(
            text(
                """
                insert into messaging.daily_opening_location_versions (
                  location_version_id, command_id, request_fingerprint,
                  user_id, version_no, label, latitude, longitude,
                  source_type, supersedes_location_version_id, created_at
                ) values (
                  cast(:location_id as uuid), :command_id, :fingerprint,
                  cast(:user_id as uuid), :version_no, :label,
                  :latitude, :longitude, 'wechat_choose_location',
                  cast(:supersedes as uuid), :now
                ) returning *
                """
            ),
            {
                "location_id": str(uuid4()),
                "command_id": command_id,
                "fingerprint": request_fingerprint,
                "user_id": user_id,
                "version_no": int(current["version_no"]) + 1 if current else 1,
                "label": command.label.strip(),
                "latitude": latitude,
                "longitude": longitude,
                "supersedes": str(current["location_version_id"]) if current else None,
                "now": now,
            },
        ).mappings().one()
        return _location_view(row)

    def impression(
        self, session: Session, *, user_id: str, local_date: date
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from messaging.daily_opening_impressions
                where user_id = cast(:user_id as uuid) and local_date = :local_date
                """
            ),
            {"user_id": user_id, "local_date": local_date},
        ).mappings().one_or_none()

    def record_impression(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        local_date: date,
        command: ImpressionCommand,
        now: datetime,
    ) -> ImpressionResult:
        existing = self.impression(session, user_id=user_id, local_date=local_date)
        if existing is not None:
            return _impression_result(existing)
        row = session.execute(
            text(
                """
                insert into messaging.daily_opening_impressions (
                  impression_id, command_id, user_id, local_date,
                  preference_version_id, location_version_id, plan_version_id,
                  weather_fingerprint, coffee_state, displayed_at
                ) values (
                  cast(:impression_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date,
                  cast(:preference_id as uuid), cast(:location_id as uuid),
                  cast(:plan_id as uuid), :weather_fingerprint,
                  :coffee_state, :now
                )
                on conflict (user_id, local_date) do nothing
                returning *
                """
            ),
            {
                "impression_id": str(uuid4()),
                "command_id": command_id,
                "user_id": user_id,
                "local_date": local_date,
                "preference_id": str(command.preference_version_id),
                "location_id": (
                    str(command.location_version_id)
                    if command.location_version_id is not None
                    else None
                ),
                "plan_id": str(command.plan_version_id),
                "weather_fingerprint": command.weather_fingerprint,
                "coffee_state": command.coffee_state,
                "now": now,
            },
        ).mappings().one_or_none()
        if row is None:
            row = self.impression(session, user_id=user_id, local_date=local_date)
        if row is None:
            raise DailyOpeningConflict("daily opening impression was not persisted")
        return _impression_result(row)

    def coffee_declined(
        self, session: Session, *, user_id: str, local_date: date
    ) -> bool:
        return bool(
            session.execute(
                text(
                    """
                    select exists (
                      select 1 from messaging.daily_opening_coffee_decisions
                      where user_id = cast(:user_id as uuid)
                        and local_date = :local_date
                        and decision = 'declined_today'
                    )
                    """
                ),
                {"user_id": user_id, "local_date": local_date},
            ).scalar_one()
        )

    def decline_coffee(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        local_date: date,
        now: datetime,
    ) -> CoffeeDecisionResult:
        row = session.execute(
            text(
                """
                insert into messaging.daily_opening_coffee_decisions (
                  decision_id, command_id, user_id, local_date,
                  decision, decided_at
                ) values (
                  cast(:decision_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date,
                  'declined_today', :now
                )
                on conflict (user_id, local_date, decision) do nothing
                returning *
                """
            ),
            {
                "decision_id": str(uuid4()),
                "command_id": command_id,
                "user_id": user_id,
                "local_date": local_date,
                "now": now,
            },
        ).mappings().one_or_none()
        if row is None:
            row = session.execute(
                text(
                    """
                    select * from messaging.daily_opening_coffee_decisions
                    where user_id = cast(:user_id as uuid)
                      and local_date = :local_date
                      and decision = 'declined_today'
                    """
                ),
                {"user_id": user_id, "local_date": local_date},
            ).mappings().one()
        return CoffeeDecisionResult(
            decisionId=row["decision_id"],
            localDate=row["local_date"],
            decision="declined_today",
            decidedAt=row["decided_at"],
        )

    def active_luckin_connection(
        self, session: Session, *, user_id: str, now: datetime
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select connection.*
                from messaging.luckin_connection_versions connection
                where connection.user_id = cast(:user_id as uuid)
                  and connection.state = 'active'
                  and connection.expires_at > :now
                  and not exists (
                    select 1 from messaging.luckin_connection_versions successor
                    where successor.supersedes_connection_version_id =
                          connection.connection_version_id
                  )
                order by connection.created_at desc
                limit 1
                """
            ),
            {"user_id": user_id, "now": now},
        ).mappings().one_or_none()

    def create_order_confirmation(
        self,
        session: Session,
        *,
        user_id: str,
        preview_fingerprint: str,
        confirmed_at: datetime,
        expires_at: datetime,
    ) -> OrderConfirmation:
        row = session.execute(
            text(
                """
                insert into messaging.luckin_order_confirmations (
                  confirmation_id, user_id, preview_fingerprint,
                  confirmed_at, expires_at, used_at
                ) values (
                  cast(:confirmation_id as uuid), cast(:user_id as uuid),
                  :preview_fingerprint, :confirmed_at, :expires_at, null
                ) returning *
                """
            ),
            {
                "confirmation_id": str(uuid4()),
                "user_id": user_id,
                "preview_fingerprint": preview_fingerprint,
                "confirmed_at": confirmed_at,
                "expires_at": expires_at,
            },
        ).mappings().one()
        return _order_confirmation(row)

    def consume_order_confirmation(
        self,
        session: Session,
        *,
        confirmation_id: str,
        user_id: str,
        preview_fingerprint: str,
        now: datetime,
    ) -> OrderConfirmation:
        row = session.execute(
            text(
                """
                update messaging.luckin_order_confirmations
                set used_at = :now
                where confirmation_id = cast(:confirmation_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and preview_fingerprint = :preview_fingerprint
                  and used_at is null
                  and expires_at > :now
                returning *
                """
            ),
            {
                "confirmation_id": confirmation_id,
                "user_id": user_id,
                "preview_fingerprint": preview_fingerprint,
                "now": now,
            },
        ).mappings().one_or_none()
        if row is None:
            raise DailyOpeningConflict(
                "Luckin order confirmation is invalid, expired, or already used"
            )
        return _order_confirmation(row)

    def cached_weather(
        self,
        session: Session,
        *,
        location_key: str,
        language: str,
        now: datetime,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from messaging.weather_cache_entries
                where provider = 'apple-weatherkit'
                  and location_key = :location_key
                  and language = :language
                  and timezone = 'Asia/Shanghai'
                  and expires_at > :now
                """
            ),
            {"location_key": location_key, "language": language, "now": now},
        ).mappings().one_or_none()

    def store_weather(
        self,
        session: Session,
        *,
        location_key: str,
        language: str,
        snapshot: WeatherKitSnapshot,
        now: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into messaging.weather_cache_entries (
                  provider, location_key, language, timezone, summary,
                  summary_fingerprint, read_at, expires_at, fetched_at
                ) values (
                  'apple-weatherkit', :location_key, :language,
                  'Asia/Shanghai', cast(:summary as jsonb), :fingerprint,
                  :read_at, :expires_at, :now
                ) on conflict (provider, location_key, language, timezone)
                do update set
                  summary = excluded.summary,
                  summary_fingerprint = excluded.summary_fingerprint,
                  read_at = excluded.read_at,
                  expires_at = excluded.expires_at,
                  fetched_at = excluded.fetched_at
                """
            ),
            {
                "location_key": location_key,
                "language": language,
                "summary": json.dumps(snapshot.payload(), ensure_ascii=False),
                "fingerprint": snapshot.fingerprint(),
                "read_at": snapshot.read_at,
                "expires_at": snapshot.expires_at,
                "now": now,
            },
        )

    def _preference_by_command(
        self, session: Session, *, user_id: str, command_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from messaging.daily_opening_preference_versions
                where user_id = cast(:user_id as uuid) and command_id = :command_id
                """
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def _location_by_command(
        self, session: Session, *, user_id: str, command_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from messaging.daily_opening_location_versions
                where user_id = cast(:user_id as uuid) and command_id = :command_id
                """
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()


def _preference_view(row: RowMapping) -> OpeningPreferenceView:
    return OpeningPreferenceView(
        preferenceVersionId=row["preference_version_id"],
        versionNo=int(row["version_no"]),
        enabled=bool(row["enabled"]),
        weatherEnabled=bool(row["weather_enabled"]),
        coffeePromptEnabled=bool(row["coffee_prompt_enabled"]),
        coffeeStart=_time(row["coffee_start"]),
        coffeeEnd=_time(row["coffee_end"]),
        timezone="Asia/Shanghai",
    )


def _location_view(row: RowMapping) -> WeatherLocationView:
    return WeatherLocationView(
        locationVersionId=row["location_version_id"],
        versionNo=int(row["version_no"]),
        label=str(row["label"]),
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
    )


def _impression_result(row: RowMapping) -> ImpressionResult:
    return ImpressionResult(
        impressionId=row["impression_id"],
        localDate=row["local_date"],
        displayedAt=row["displayed_at"],
    )


def _order_confirmation(row: RowMapping) -> OrderConfirmation:
    return OrderConfirmation(
        confirmation_id=str(row["confirmation_id"]),
        user_id=str(row["user_id"]),
        preview_fingerprint=str(row["preview_fingerprint"]),
        expires_at=row["expires_at"],
        used_at=row["used_at"],
    )


def _time(value: Any) -> time:
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value))
