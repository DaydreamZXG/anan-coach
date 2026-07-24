from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.daily_opening.repository import (
    DailyOpeningConflict,
    DailyOpeningRepository,
)
from app.domains.daily_opening.schemas import (
    ImpressionCommand,
    OpeningPreferenceCommand,
    WeatherLocationCommand,
)
from app.domains.daily_opening.service import DailyOpeningService
from app.domains.daily_opening.settings import DailyOpeningSettings
from app.domains.data_rights.repository import DataRightsRepository


NOW = datetime(2026, 7, 21, 0, 30, tzinfo=UTC)
USER_ID = "00000000-0000-0000-0000-000000001047"
PLAN_ID = "00000000-0000-0000-0000-000000001048"
PREFERENCE_ID = UUID("00000000-0000-0000-0000-000000001049")


def _settings() -> DailyOpeningSettings:
    return DailyOpeningSettings(
        weatherkit_enabled=False,
        weatherkit_team_id="",
        weatherkit_key_id="",
        weatherkit_service_id="",
        weatherkit_private_key_path=None,
        weatherkit_base_url="https://weatherkit.apple.com",
        weatherkit_language="zh-Hans",
        luckin_mcp_enabled=False,
        luckin_mcp_endpoint="https://gwmcp.lkcoffee.com/order/user/mcp",
        luckin_token_key_id="",
        luckin_token_encryption_key="",
    )


class FakePlanning:
    def current_plan(self, _session, *, user_id: str, local_date):
        assert user_id == USER_ID
        assert str(local_date) == "2026-07-21"
        return {"plan_version_id": UUID(PLAN_ID), "headline": "完成资料分析复测"}

    def plan_tasks(self, _session, *, plan_version_id: str):
        assert plan_version_id == PLAN_ID
        return [
            {
                "title": "资料分析 · 增长率复测",
                "estimated_minutes": 30,
                "start_url": "/pages/practice/index?task=task-1",
                "execution_status": "ready",
            }
        ]


class FakeRepository:
    def __init__(self) -> None:
        self.seen = False
        self.recorded = None

    def ensure_default_preference(self, _session, *, user_id: str, now: datetime):
        assert user_id == USER_ID and now == NOW
        return {
            "preference_version_id": PREFERENCE_ID,
            "version_no": 1,
            "enabled": True,
            "weather_enabled": False,
            "coffee_prompt_enabled": False,
            "coffee_start": time(7),
            "coffee_end": time(17),
        }

    def current_location(self, _session, *, user_id: str):
        return None

    def impression(self, _session, *, user_id: str, local_date):
        if not self.seen:
            return None
        return {
            "impression_id": UUID("00000000-0000-0000-0000-000000001050"),
            "local_date": local_date,
            "displayed_at": NOW,
        }

    def coffee_declined(self, _session, *, user_id: str, local_date) -> bool:
        return False

    def record_impression(self, _session, **kwargs):
        self.recorded = kwargs
        self.seen = True
        return type(
            "Result",
            (),
            {
                "impression_id": UUID("00000000-0000-0000-0000-000000001050"),
                "local_date": kwargs["local_date"],
                "displayed_at": NOW,
            },
        )()


def test_daily_opening_binds_current_plan_and_displays_only_once() -> None:
    repository = FakeRepository()
    service = DailyOpeningService(
        settings=_settings(),
        repository=repository,  # type: ignore[arg-type]
        planning=FakePlanning(),  # type: ignore[arg-type]
        now=lambda: NOW,
    )
    first = service.view(object(), user_id=USER_ID)  # type: ignore[arg-type]
    assert first.display_state == "ready"
    assert str(first.plan_version_id) == PLAN_ID
    assert first.plan.title == "资料分析 · 增长率复测"
    assert first.plan.estimated_minutes == 30
    assert first.weather.state == "disabled"
    assert first.coffee.state == "disabled"

    result = service.record_impression(
        object(),  # type: ignore[arg-type]
        user_id=USER_ID,
        command_id="daily-opening-impression-command-1",
        command=ImpressionCommand(
            planVersionId=first.plan_version_id,
            preferenceVersionId=first.preference.preference_version_id,
            locationVersionId=None,
            weatherFingerprint=None,
            coffeeState="disabled",
        ),
    )
    assert result.local_date.isoformat() == "2026-07-21"
    assert repository.recorded["command_id"] == "daily-opening-impression-command-1"
    second = service.view(object(), user_id=USER_ID)  # type: ignore[arg-type]
    assert second.display_state == "already_seen"


def test_daily_opening_data_exports_without_secret_and_erases_completely(
    db_session: Session,
) -> None:
    db_session.execute(
        text(
            "insert into learner.users (user_id, display_name, account_status) "
            "values (cast(:user_id as uuid), 'step10-user', 'active')"
        ),
        {"user_id": USER_ID},
    )
    repository = DailyOpeningRepository()
    preference = repository.ensure_default_preference(db_session, user_id=USER_ID, now=NOW)
    repository.update_preference(
        db_session,
        user_id=USER_ID,
        command_id="daily-opening-preference-update-1",
        command=OpeningPreferenceCommand(
            enabled=True,
            weatherEnabled=True,
            coffeePromptEnabled=False,
        ),
        now=NOW,
    )
    repository.update_location(
        db_session,
        user_id=USER_ID,
        command_id="daily-opening-location-update-1",
        command=WeatherLocationCommand(
            label="当前位置附近",
            latitude=28.681932,
            longitude=115.857941,
            sourceType="wechat_fuzzy_location",
        ),
        now=NOW,
    )
    repository.decline_coffee(
        db_session,
        user_id=USER_ID,
        command_id="daily-opening-coffee-decline-1",
        local_date=NOW.date(),
        now=NOW,
    )
    confirmation = repository.create_order_confirmation(
        db_session,
        user_id=USER_ID,
        preview_fingerprint="b" * 64,
        confirmed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    consumed = repository.consume_order_confirmation(
        db_session,
        confirmation_id=confirmation.confirmation_id,
        user_id=USER_ID,
        preview_fingerprint="b" * 64,
        now=NOW + timedelta(seconds=1),
    )
    assert consumed.used_at == NOW + timedelta(seconds=1)
    with pytest.raises(DailyOpeningConflict, match="already used"):
        repository.consume_order_confirmation(
            db_session,
            confirmation_id=confirmation.confirmation_id,
            user_id=USER_ID,
            preview_fingerprint="b" * 64,
            now=NOW + timedelta(seconds=2),
        )
    db_session.execute(
        text(
            """
            insert into messaging.luckin_connection_versions (
              connection_version_id, connection_id, user_id, version_no, state,
              encrypted_bearer_token, encryption_key_id, token_fingerprint,
              expires_at, supersedes_connection_version_id, created_at
            ) values (
              '00000000-0000-0000-0000-000000001051',
              '00000000-0000-0000-0000-000000001052', cast(:user_id as uuid),
              1, 'active', 'ciphertext-not-plaintext', 'key-v1', repeat('a', 64),
              :expires_at, null, :now
            )
            """
        ),
        {"user_id": USER_ID, "expires_at": NOW.replace(year=2027), "now": NOW},
    )
    db_session.commit()

    rights = DataRightsRepository(db_session)
    exported = rights.build_export_payload(user_id=USER_ID, now=NOW)
    assert len(exported["dailyOpeningPreferences"]) == 2
    assert float(exported["dailyOpeningLocations"][0]["latitude"]) == 28.682
    assert exported["dailyOpeningLocations"][0]["source_type"] == "wechat_fuzzy_location"
    assert exported["dailyOpeningCoffeeDecisions"][0]["decision"] == "declined_today"
    assert "ciphertext-not-plaintext" not in str(exported)
    assert str(preference["preference_version_id"]) not in str(exported["dailyOpeningLocations"])

    deletion = rights.create_deletion(
        user_id=USER_ID,
        command_key="daily-opening-account-deletion-1",
        confirmation_phrase="删除我的岸岸账户",
        now=NOW,
    )
    rights.mark_deletion_running(
        deletion_id=deletion.deletion_request_id,
        user_id=USER_ID,
    )
    rights._erase_personal_content(
        deletion_id=deletion.deletion_request_id,
        user_id=USER_ID,
        now=NOW,
    )
    for table in (
        "daily_opening_preference_versions",
        "daily_opening_location_versions",
        "daily_opening_coffee_decisions",
        "luckin_connection_versions",
        "luckin_order_confirmations",
    ):
        count = db_session.execute(
            text(f"select count(*) from messaging.{table} where user_id = cast(:user_id as uuid)"),
            {"user_id": USER_ID},
        ).scalar_one()
        assert count == 0
