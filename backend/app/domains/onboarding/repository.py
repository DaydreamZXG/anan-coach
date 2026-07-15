from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class OnboardingProfileRecord:
    target_exam: str | None
    target_region: str | None
    position_group: str | None
    daily_minutes: int | None
    education: str | None
    major: str | None
    fresh_graduate: bool | None
    political_status: str | None
    grassroots_years: int | None
    study_mode: str | None
    target_position_id: str | None
    target_score: float | None
    baseline_source: str | None
    baseline_xingce_score: float | None
    baseline_shenlun_score: float | None
    baseline_weak_modules: list[str]
    baseline_module_snapshot: dict[str, Any] | None
    onboarding_complete: bool


class OnboardingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_profile(self, *, user_id: str) -> OnboardingProfileRecord:
        self.ensure_profile(user_id=user_id)
        row = self._session.execute(
            text(
                """
                select
                  target_exam,
                  target_region,
                  position_group,
                  daily_minutes,
                  education,
                  major,
                  fresh_graduate,
                  political_status,
                  grassroots_years,
                  study_mode,
                  target_position_id,
                  target_score,
                  baseline_source,
                  baseline_xingce_score,
                  baseline_shenlun_score,
                  baseline_weak_modules,
                  baseline_module_snapshot,
                  onboarding_complete
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        return _profile_from_row(row)

    def ensure_profile(self, *, user_id: str) -> None:
        self._session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id,
                  target_exam,
                  created_at,
                  updated_at
                ) values (
                  cast(:user_id as uuid),
                  'guokao',
                  now(),
                  now()
                )
                on conflict (user_id) do nothing
                """
            ),
            {"user_id": user_id},
        )
        self._session.commit()

    def upsert_profile(
        self,
        *,
        user_id: str,
        education: str,
        major: str,
        fresh_graduate: bool,
        political_status: str,
        grassroots_years: int,
        study_mode: str | None,
        daily_minutes: int | None,
        target_position_id: str | None,
        target_score: float | None,
        baseline_source: str | None,
        baseline_xingce_score: float | None,
        baseline_shenlun_score: float | None,
        baseline_weak_modules: list[str],
    ) -> OnboardingProfileRecord:
        row = self._session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id,
                  target_exam,
                  target_region,
                  position_group,
                  education,
                  major,
                  fresh_graduate,
                  political_status,
                  grassroots_years,
                  study_mode,
                  daily_minutes,
                  target_position_id,
                  target_score,
                  baseline_source,
                  baseline_xingce_score,
                  baseline_shenlun_score,
                  baseline_weak_modules,
                  created_at,
                  updated_at
                ) values (
                  cast(:user_id as uuid),
                  'guokao',
                  '全国',
                  '国考岗位',
                  :education,
                  :major,
                  :fresh_graduate,
                  :political_status,
                  :grassroots_years,
                  :study_mode,
                  :daily_minutes,
                  cast(:target_position_id as uuid),
                  cast(:target_score as numeric),
                  :baseline_source,
                  cast(:baseline_xingce_score as numeric),
                  cast(:baseline_shenlun_score as numeric),
                  cast(:baseline_weak_modules as jsonb),
                  now(),
                  now()
                )
                on conflict (user_id) do update set
                  target_exam = 'guokao',
                  target_region = '全国',
                  position_group = '国考岗位',
                  education = excluded.education,
                  major = excluded.major,
                  fresh_graduate = excluded.fresh_graduate,
                  political_status = excluded.political_status,
                  grassroots_years = excluded.grassroots_years,
                  study_mode = excluded.study_mode,
                  daily_minutes = excluded.daily_minutes,
                  target_position_id = excluded.target_position_id,
                  target_score = excluded.target_score,
                  baseline_source = excluded.baseline_source,
                  baseline_xingce_score = excluded.baseline_xingce_score,
                  baseline_shenlun_score = excluded.baseline_shenlun_score,
                  baseline_weak_modules = excluded.baseline_weak_modules,
                  updated_at = now()
                returning *
                """
            ),
            {
                "user_id": user_id,
                "education": education,
                "major": major,
                "fresh_graduate": fresh_graduate,
                "political_status": political_status,
                "grassroots_years": grassroots_years,
                "study_mode": study_mode,
                "daily_minutes": daily_minutes,
                "target_position_id": target_position_id,
                "target_score": target_score,
                "baseline_source": baseline_source,
                "baseline_xingce_score": baseline_xingce_score,
                "baseline_shenlun_score": baseline_shenlun_score,
                "baseline_weak_modules": json.dumps(baseline_weak_modules, ensure_ascii=False),
            },
        ).mappings().one()
        self._session.commit()
        return _profile_from_row(row)

    def complete_onboarding(self, *, user_id: str) -> OnboardingProfileRecord:
        del user_id
        raise OnboardingV1RetiredError(
            "v1 onboarding completion is retired; use the v2 progress commands"
        )

    def record_placement_attempt(
        self,
        *,
        user_id: str,
        task_id: str,
        question_ids: list[str],
        answers: list[dict[str, str]],
        module_snapshot: list[dict[str, object]],
        estimated_xingce_score: float,
    ) -> None:
        self.ensure_profile(user_id=user_id)
        self._session.execute(
            text(
                """
                insert into learner.placement_attempts (
                  user_id,
                  task_id,
                  question_ids,
                  answers,
                  module_snapshot,
                  estimated_xingce_score
                ) values (
                  cast(:user_id as uuid),
                  :task_id,
                  cast(:question_ids as jsonb),
                  cast(:answers as jsonb),
                  cast(:module_snapshot as jsonb),
                  cast(:estimated_xingce_score as numeric)
                )
                """
            ),
            {
                "user_id": user_id,
                "task_id": task_id,
                "question_ids": json.dumps(question_ids, ensure_ascii=False),
                "answers": json.dumps(answers, ensure_ascii=False),
                "module_snapshot": json.dumps(module_snapshot, ensure_ascii=False),
                "estimated_xingce_score": estimated_xingce_score,
            },
        )
        self._session.execute(
            text(
                """
                update learner.profiles
                set
                  baseline_source = 'placement_test',
                  baseline_xingce_score = cast(:estimated_xingce_score as numeric),
                  baseline_module_snapshot = cast(:module_snapshot as jsonb),
                  updated_at = now()
                where user_id = cast(:user_id as uuid)
                """
            ),
            {
                "user_id": user_id,
                "estimated_xingce_score": estimated_xingce_score,
                "module_snapshot": json.dumps(module_snapshot, ensure_ascii=False),
            },
        )
        self._session.commit()


class OnboardingProfileIncompleteError(ValueError):
    pass


class OnboardingV1RetiredError(RuntimeError):
    pass


def _profile_from_row(row: Any) -> OnboardingProfileRecord:
    return OnboardingProfileRecord(
        target_exam=row["target_exam"],
        target_region=row["target_region"],
        position_group=row["position_group"],
        daily_minutes=row["daily_minutes"],
        education=row["education"],
        major=row["major"],
        fresh_graduate=row["fresh_graduate"],
        political_status=row["political_status"],
        grassroots_years=row["grassroots_years"],
        study_mode=row["study_mode"],
        target_position_id=(
            str(row["target_position_id"]) if row["target_position_id"] is not None else None
        ),
        target_score=_optional_float(row["target_score"]),
        baseline_source=row["baseline_source"],
        baseline_xingce_score=_optional_float(row["baseline_xingce_score"]),
        baseline_shenlun_score=_optional_float(row["baseline_shenlun_score"]),
        baseline_weak_modules=list(row["baseline_weak_modules"] or []),
        baseline_module_snapshot=row["baseline_module_snapshot"],
        onboarding_complete=bool(row["onboarding_complete"]),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
