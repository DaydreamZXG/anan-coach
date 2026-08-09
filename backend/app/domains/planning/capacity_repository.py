from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.errors import PlanningContractError
from app.domains.planning.repository_support import (
    CapacitySnapshot,
    PreferenceSnapshot,
    ProfileSnapshot,
    _capacity_snapshot,
    _fingerprint,
    _preference_snapshot,
)


class CapacityRepositoryMixin:
    def lock_user(self, session: Session, *, user_id: str) -> None:
        owner = session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if owner is None:
            raise PlanningContractError("Learner does not exist")

    def profile(self, session: Session, *, user_id: str) -> ProfileSnapshot:
        row = (
            session.execute(
                text(
                    """
                select profile_version, daily_minutes, study_mode
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["daily_minutes"] is None or row["study_mode"] is None:
            raise PlanningContractError("Daily planning requires an onboarding study schedule")
        return ProfileSnapshot(
            profile_version=int(row["profile_version"]),
            daily_minutes=int(row["daily_minutes"]),
            study_mode=str(row["study_mode"]),
        )
    def current_or_create_preference(
        self,
        session: Session,
        *,
        user_id: str,
        profile: ProfileSnapshot,
        local_date: date,
        now: datetime,
    ) -> PreferenceSnapshot:
        current = (
            session.execute(
                text(
                    """
                select preference.*
                from learning.weekly_study_preference_versions preference
                where preference.user_id = cast(:user_id as uuid)
                  and preference.effective_from <= :local_date
                  and not exists (
                    select 1
                    from learning.weekly_study_preference_versions successor
                    where successor.supersedes_preference_version_id =
                          preference.preference_version_id
                      and successor.effective_from <= :local_date
                  )
                order by preference.effective_from desc, preference.version_no desc
                limit 1
                """
                ),
                {"user_id": user_id, "local_date": local_date},
            )
            .mappings()
            .one_or_none()
        )
        if current is not None and (
            bool(current["confirmed"])
            or (
                int(current["weekday_minutes"]) == profile.daily_minutes
                and int(current["weekend_minutes"]) == profile.daily_minutes
            )
        ):
            return _preference_snapshot(current)

        payload = {
            "profileVersion": profile.profile_version,
            "weekdayMinutes": profile.daily_minutes,
            "weekendMinutes": profile.daily_minutes,
            "regularRestWeekday": 7,
            "regularMockWeekday": 6,
            "timezone": "Asia/Shanghai",
        }
        fingerprint = _fingerprint(payload)
        existing = (
            session.execute(
                text(
                    """
                select * from learning.weekly_study_preference_versions
                where user_id = cast(:user_id as uuid)
                  and effective_from <= :local_date
                  and input_fingerprint = :input_fingerprint
                  and not exists (
                    select 1
                    from learning.weekly_study_preference_versions successor
                    where successor.supersedes_preference_version_id =
                          weekly_study_preference_versions.preference_version_id
                      and successor.effective_from <= :local_date
                  )
                order by effective_from desc, version_no desc
                limit 1
                """
                ),
                {
                    "user_id": user_id,
                    "local_date": local_date,
                    "input_fingerprint": fingerprint,
                },
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            return _preference_snapshot(existing)
        preference_id = str(uuid4())
        version_no = int(current["version_no"]) + 1 if current is not None else 1
        row = (
            session.execute(
                text(
                    """
                insert into learning.weekly_study_preference_versions (
                  preference_version_id, command_id, user_id, version_no,
                  timezone, weekday_minutes, weekend_minutes, long_block_minutes,
                  fragment_minutes, regular_rest_weekday, regular_mock_weekday,
                  effective_from, source_type, confirmed, input_fingerprint,
                  supersedes_preference_version_id, created_at
                ) values (
                  cast(:preference_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :version_no, 'Asia/Shanghai',
                  :daily_minutes, :daily_minutes, :long_block_minutes,
                  :fragment_minutes, 7, 6, :effective_from, 'system_default', false,
                  :input_fingerprint, cast(:supersedes_id as uuid), :created_at
                ) returning *
                """
                ),
                {
                    "preference_version_id": preference_id,
                    "command_id": (
                        f"planning-system-default:{user_id}:v{version_no}:{fingerprint}"
                    ),
                    "user_id": user_id,
                    "version_no": version_no,
                    "daily_minutes": profile.daily_minutes,
                    "long_block_minutes": min(120, profile.daily_minutes),
                    "fragment_minutes": min(25, profile.daily_minutes),
                    "effective_from": local_date,
                    "input_fingerprint": fingerprint,
                    "supersedes_id": (
                        str(current["preference_version_id"]) if current is not None else None
                    ),
                    "created_at": now,
                },
            )
            .mappings()
            .one()
        )
        return _preference_snapshot(row)

    def current_or_create_capacity(
        self,
        session: Session,
        *,
        user_id: str,
        local_date: date,
        preference: PreferenceSnapshot,
        now: datetime,
    ) -> CapacitySnapshot:
        current = (
            session.execute(
                text(
                    """
                select capacity.*
                from learning.daily_capacity_versions capacity
                where capacity.user_id = cast(:user_id as uuid)
                  and capacity.local_date = :local_date
                  and not exists (
                    select 1
                    from learning.daily_capacity_versions successor
                    where successor.supersedes_capacity_version_id =
                          capacity.capacity_version_id
                  )
                order by capacity.version_no desc
                limit 1
                """
                ),
                {"user_id": user_id, "local_date": local_date},
            )
            .mappings()
            .one_or_none()
        )
        if current is not None and (
            bool(current["confirmed"])
            or str(current["weekly_preference_version_id"]) == preference.preference_version_id
        ):
            return _capacity_snapshot(current)
        available_minutes = (
            preference.weekend_minutes
            if local_date.isoweekday() >= 6
            else preference.weekday_minutes
        )
        payload = {
            "localDate": local_date.isoformat(),
            "preferenceVersionId": preference.preference_version_id,
            "availableMinutes": available_minutes,
            "energy": 3,
            "stress": 3,
            "confidence": 3,
        }
        fingerprint = _fingerprint(payload)
        existing = (
            session.execute(
                text(
                    """
                select * from learning.daily_capacity_versions
                where user_id = cast(:user_id as uuid)
                  and local_date = :local_date
                  and input_fingerprint = :input_fingerprint
                  and not exists (
                    select 1
                    from learning.daily_capacity_versions successor
                    where successor.supersedes_capacity_version_id =
                          daily_capacity_versions.capacity_version_id
                  )
                """
                ),
                {
                    "user_id": user_id,
                    "local_date": local_date,
                    "input_fingerprint": fingerprint,
                },
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            return _capacity_snapshot(existing)
        version_no = int(current["version_no"]) + 1 if current is not None else 1
        row = (
            session.execute(
                text(
                    """
                insert into learning.daily_capacity_versions (
                  capacity_version_id, command_id, user_id, local_date, version_no,
                  available_minutes, energy_level, stress_level, confidence_level,
                  confirmed, source_type, weekly_preference_version_id,
                  preferred_xingce_module, xingce_only,
                  input_fingerprint, supersedes_capacity_version_id, recorded_at
                ) values (
                  cast(:capacity_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date, :version_no,
                  :available_minutes, 3, 3, 3, false, 'weekly_preference',
                  cast(:preference_version_id as uuid), null, false, :input_fingerprint,
                  cast(:supersedes_id as uuid), :recorded_at
                ) returning *
                """
                ),
                {
                    "capacity_version_id": str(uuid4()),
                    "command_id": (
                        f"planning-capacity:{user_id}:{local_date}:v{version_no}:{fingerprint}"
                    ),
                    "user_id": user_id,
                    "local_date": local_date,
                    "version_no": version_no,
                    "available_minutes": available_minutes,
                    "preference_version_id": preference.preference_version_id,
                    "input_fingerprint": fingerprint,
                    "supersedes_id": (
                        str(current["capacity_version_id"]) if current is not None else None
                    ),
                    "recorded_at": now,
                },
            )
            .mappings()
            .one()
        )
        return _capacity_snapshot(row)

    def current_capacity(
        self,
        session: Session,
        *,
        user_id: str,
        local_date: date,
        for_update: bool = False,
    ) -> RowMapping | None:
        lock_clause = "for update of capacity" if for_update else ""
        return (
            session.execute(
                text(
                    f"""
                select capacity.*
                from learning.daily_capacity_versions capacity
                where capacity.user_id = cast(:user_id as uuid)
                  and capacity.local_date = :local_date
                  and not exists (
                    select 1
                    from learning.daily_capacity_versions successor
                    where successor.supersedes_capacity_version_id =
                          capacity.capacity_version_id
                  )
                {lock_clause}
                """
                ),
                {"user_id": user_id, "local_date": local_date},
            )
            .mappings()
            .one_or_none()
        )

    def capacity_by_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select * from learning.daily_capacity_versions
                where user_id = cast(:user_id as uuid)
                  and command_id = :command_id
                """
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )

    def insert_user_capacity(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.daily_capacity_versions (
                  capacity_version_id, command_id, user_id, local_date, version_no,
                  available_minutes, energy_level, stress_level, confidence_level,
                  confirmed, source_type, weekly_preference_version_id,
                  preferred_xingce_module, xingce_only,
                  input_fingerprint, supersedes_capacity_version_id, recorded_at
                ) values (
                  cast(:capacity_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date, :version_no,
                  :available_minutes, :energy, :stress, :confidence_level,
                  true, 'user_override', cast(:weekly_preference_version_id as uuid),
                  :preferred_xingce_module, :xingce_only,
                  :input_fingerprint,
                  cast(:supersedes_capacity_version_id as uuid), :recorded_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )
