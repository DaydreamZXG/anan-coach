from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
import json
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.onboarding.v2_schemas import (
    OnboardingProgress,
    WeeklyStudyPreference,
)


REQUIRED_QUALIFICATION_FACT_KEYS = frozenset(
    {
        "education",
        "major",
        "fresh_graduate",
        "political_status",
        "grassroots_years",
    }
)

WEEKDAY_TO_ISO = {
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 7,
}
ISO_TO_WEEKDAY = {value: key for key, value in WEEKDAY_TO_ISO.items()}


class OnboardingProgressError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class OnboardingProgressRepository:
    def ensure_progress(self, session: Session, *, user_id: str) -> None:
        session.execute(
            text(
                """
                insert into learner.onboarding_progress (
                  user_id, progress_version, qualification_ready,
                  qualification_profile_version, study_preference_ready,
                  study_preference_version, result_ready,
                  result_acknowledged, completed, created_at, updated_at
                ) values (
                  cast(:user_id as uuid), 1, false, null, false, null,
                  false, false, false, now(), now()
                )
                on conflict (user_id) do nothing
                """
            ),
            {"user_id": user_id},
        )

    def get_progress(
        self,
        session: Session,
        *,
        user_id: str,
        for_update: bool = False,
    ) -> OnboardingProgress:
        self.ensure_progress(session, user_id=user_id)
        suffix = " for update" if for_update else ""
        row = session.execute(
            text(
                """
                select *
                from learner.onboarding_progress
                where user_id = cast(:user_id as uuid)
                """
                + suffix
            ),
            {"user_id": user_id},
        ).mappings().one()
        return progress_from_row(row)

    def qualification_state(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> tuple[int, set[str]]:
        profile_version = session.execute(
            text(
                """
                select profile_version
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if profile_version is None:
            raise OnboardingProgressError(
                "qualification_profile_missing",
                "尚未建立资格档案。",
            )
        keys = {
            str(value)
            for value in session.execute(
                text(
                    """
                    select distinct on (fact_key) fact_key
                    from learner.qualification_facts
                    where user_id = cast(:user_id as uuid)
                    order by fact_key, profile_version desc, recorded_at desc
                    """
                ),
                {"user_id": user_id},
            ).scalars()
        }
        return int(profile_version), keys

    def current_preference_row(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> Mapping[str, Any] | None:
        return session.execute(
            text(
                """
                select preference.*
                from learning.weekly_study_preference_versions preference
                where preference.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1
                    from learning.weekly_study_preference_versions successor
                    where successor.supersedes_preference_version_id =
                          preference.preference_version_id
                  )
                order by preference.version_no desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()

    def current_preference(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> WeeklyStudyPreference | None:
        row = self.current_preference_row(session, user_id=user_id)
        if row is None or not bool(row["confirmed"]):
            return None
        return preference_from_row(row)

    def append_preference(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        timezone: str,
        effective_from: date,
        weekday_minutes: int,
        weekend_minutes: int,
        long_block_minutes: int,
        fragment_minutes: int,
        fixed_rest_day: str,
        mock_exam_day: str,
        now: datetime,
    ) -> WeeklyStudyPreference:
        current = self.current_preference_row(session, user_id=user_id)
        version_no = int(current["version_no"]) + 1 if current is not None else 1
        preference_id = str(uuid4())
        payload = preference_input_payload(
            timezone=timezone,
            effective_from=effective_from,
            weekday_minutes=weekday_minutes,
            weekend_minutes=weekend_minutes,
            long_block_minutes=long_block_minutes,
            fragment_minutes=fragment_minutes,
            fixed_rest_day=fixed_rest_day,
            mock_exam_day=mock_exam_day,
        )
        row = session.execute(
            text(
                """
                insert into learning.weekly_study_preference_versions (
                  preference_version_id, command_id, user_id, version_no,
                  timezone, weekday_minutes, weekend_minutes, long_block_minutes,
                  fragment_minutes, regular_rest_weekday, regular_mock_weekday,
                  effective_from, source_type, confirmed, input_fingerprint,
                  supersedes_preference_version_id, created_at
                ) values (
                  cast(:preference_id as uuid), :command_id,
                  cast(:user_id as uuid), :version_no,
                  :timezone, :weekday_minutes, :weekend_minutes,
                  :long_block_minutes, :fragment_minutes,
                  :regular_rest_weekday, :regular_mock_weekday,
                  :effective_from, :source_type, true, :input_fingerprint,
                  cast(:supersedes_id as uuid), :created_at
                ) returning *
                """
            ),
            {
                "preference_id": preference_id,
                "command_id": command_id,
                "user_id": user_id,
                "version_no": version_no,
                "timezone": timezone,
                "weekday_minutes": weekday_minutes,
                "weekend_minutes": weekend_minutes,
                "long_block_minutes": long_block_minutes,
                "fragment_minutes": fragment_minutes,
                "regular_rest_weekday": WEEKDAY_TO_ISO[fixed_rest_day],
                "regular_mock_weekday": WEEKDAY_TO_ISO[mock_exam_day],
                "effective_from": effective_from,
                "source_type": "user_revised" if current is not None else "user_confirmed",
                "input_fingerprint": payload_fingerprint(payload),
                "supersedes_id": (
                    str(current["preference_version_id"])
                    if current is not None
                    else None
                ),
                "created_at": now,
            },
        ).mappings().one()

        # learner.profiles retains only the compatibility projection used by old
        # readers. The versioned learning aggregate above remains the schedule owner.
        projected_minutes = weekday_minutes or weekend_minutes
        session.execute(
            text(
                """
                update learner.profiles
                set daily_minutes = :daily_minutes,
                    study_mode = :study_mode,
                    updated_at = :updated_at
                where user_id = cast(:user_id as uuid)
                """
            ),
            {
                "user_id": user_id,
                "daily_minutes": projected_minutes,
                "study_mode": "fulltime" if projected_minutes >= 360 else "parttime",
                "updated_at": now,
            },
        )
        return preference_from_row(row)

    def advance_progress(
        self,
        session: Session,
        *,
        user_id: str,
        qualification_ready: bool,
        qualification_profile_version: int | None,
        study_preference_ready: bool,
        study_preference_version: int | None,
        result_acknowledged: bool,
        now: datetime,
    ) -> OnboardingProgress:
        result_ready = qualification_ready and study_preference_ready
        completed = result_acknowledged
        row = session.execute(
            text(
                """
                update learner.onboarding_progress
                set progress_version = progress_version + 1,
                    qualification_ready = :qualification_ready,
                    qualification_profile_version = :qualification_profile_version,
                    study_preference_ready = :study_preference_ready,
                    study_preference_version = :study_preference_version,
                    result_ready = :result_ready,
                    result_acknowledged = :result_acknowledged,
                    completed = :completed,
                    completed_at = case when :completed then :now else null end,
                    updated_at = :now
                where user_id = cast(:user_id as uuid)
                returning *
                """
            ),
            {
                "user_id": user_id,
                "qualification_ready": qualification_ready,
                "qualification_profile_version": qualification_profile_version,
                "study_preference_ready": study_preference_ready,
                "study_preference_version": study_preference_version,
                "result_ready": result_ready,
                "result_acknowledged": result_acknowledged,
                "completed": completed,
                "now": now,
            },
        ).mappings().one()
        if completed:
            session.execute(
                text(
                    """
                    update learner.profiles
                    set onboarding_complete = true, updated_at = :updated_at
                    where user_id = cast(:user_id as uuid)
                    """
                ),
                {"user_id": user_id, "updated_at": now},
            )
        return progress_from_row(row)

    def get_command(
        self,
        session: Session,
        *,
        command_id: str,
        user_id: str,
        command_type: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = session.execute(
            text(
                """
                select user_id::text as user_id, command_type,
                       request_payload, response_payload
                from learner.onboarding_commands
                where command_id = :command_id
                """
            ),
            {"command_id": command_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        if (
            str(row["user_id"]) != user_id
            or str(row["command_type"]) != command_type
            or row["request_payload"] != request_payload
        ):
            raise OnboardingProgressError(
                "idempotency_key_reused",
                "同一幂等键不能用于不同的建档命令。",
            )
        return dict(row["response_payload"])

    def record_command(
        self,
        session: Session,
        *,
        command_id: str,
        user_id: str,
        command_type: str,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        now: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learner.onboarding_commands (
                  command_id, user_id, command_type, request_payload,
                  response_payload, created_at
                ) values (
                  :command_id, cast(:user_id as uuid), :command_type,
                  cast(:request_payload as jsonb), cast(:response_payload as jsonb),
                  :created_at
                )
                """
            ),
            {
                "command_id": command_id,
                "user_id": user_id,
                "command_type": command_type,
                "request_payload": canonical_json(request_payload),
                "response_payload": canonical_json(response_payload),
                "created_at": now,
            },
        )

    def record_progress_event(
        self,
        session: Session,
        *,
        command_id: str,
        user_id: str,
        event_type: str,
        before_version: int,
        progress: OnboardingProgress,
        now: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learner.onboarding_progress_events (
                  event_id, command_id, user_id, event_type,
                  from_progress_version, to_progress_version,
                  state_snapshot, created_at
                ) values (
                  cast(:event_id as uuid), :command_id, cast(:user_id as uuid),
                  :event_type, :from_progress_version, :to_progress_version,
                  cast(:state_snapshot as jsonb), :created_at
                )
                """
            ),
            {
                "event_id": str(uuid4()),
                "command_id": command_id,
                "user_id": user_id,
                "event_type": event_type,
                "from_progress_version": before_version,
                "to_progress_version": progress.progress_version,
                "state_snapshot": canonical_json(
                    progress.model_dump(by_alias=True, mode="json")
                ),
                "created_at": now,
            },
        )


def progress_from_row(row: Mapping[str, Any]) -> OnboardingProgress:
    if bool(row["completed"]):
        next_step = "workspace"
    elif bool(row["result_ready"]):
        next_step = "result"
    elif bool(row["qualification_ready"]):
        next_step = "study_preference"
    else:
        next_step = "qualification"
    return OnboardingProgress(
        progress_version=int(row["progress_version"]),
        qualification_ready=bool(row["qualification_ready"]),
        qualification_profile_version=(
            int(row["qualification_profile_version"])
            if row["qualification_profile_version"] is not None
            else None
        ),
        study_preference_ready=bool(row["study_preference_ready"]),
        study_preference_version=(
            int(row["study_preference_version"])
            if row["study_preference_version"] is not None
            else None
        ),
        result_ready=bool(row["result_ready"]),
        result_acknowledged=bool(row["result_acknowledged"]),
        next_step=next_step,
        completed=bool(row["completed"]),
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def preference_from_row(row: Mapping[str, Any]) -> WeeklyStudyPreference:
    source_type = str(row["source_type"])
    if source_type not in {"user_confirmed", "user_revised"}:
        raise OnboardingProgressError(
            "study_preference_missing",
            "尚未确认正式学习时间偏好。",
        )
    return WeeklyStudyPreference(
        preference_version_id=str(row["preference_version_id"]),
        preference_version=int(row["version_no"]),
        timezone=str(row["timezone"]),
        effective_from=row["effective_from"],
        weekday_minutes=int(row["weekday_minutes"]),
        weekend_minutes=int(row["weekend_minutes"]),
        long_block_minutes=int(row["long_block_minutes"]),
        fragment_minutes=int(row["fragment_minutes"]),
        fixed_rest_day=ISO_TO_WEEKDAY[int(row["regular_rest_weekday"])],
        mock_exam_day=ISO_TO_WEEKDAY[int(row["regular_mock_weekday"])],
        source_type=source_type,
        created_at=row["created_at"],
        supersedes_preference_version_id=(
            str(row["supersedes_preference_version_id"])
            if row["supersedes_preference_version_id"] is not None
            else None
        ),
    )


def preference_input_payload(
    *,
    timezone: str,
    effective_from: date,
    weekday_minutes: int,
    weekend_minutes: int,
    long_block_minutes: int,
    fragment_minutes: int,
    fixed_rest_day: str,
    mock_exam_day: str,
) -> dict[str, Any]:
    return {
        "timezone": timezone,
        "effectiveFrom": effective_from.isoformat(),
        "weekdayMinutes": weekday_minutes,
        "weekendMinutes": weekend_minutes,
        "longBlockMinutes": long_block_minutes,
        "fragmentMinutes": fragment_minutes,
        "fixedRestDay": fixed_rest_day,
        "mockExamDay": mock_exam_day,
    }


def payload_fingerprint(payload: dict[str, Any]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_now() -> datetime:
    return datetime.now(UTC)
