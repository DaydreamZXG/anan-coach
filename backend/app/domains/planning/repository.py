from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.candidates import (
    EssayPlanningSnapshot,
    ObjectivePlanningSnapshot,
)
from app.domains.planning.errors import PlanningContractError
from app.domains.xingce_learning.contracts import XINGCE_QUESTION_BUNDLE_SIZE
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository


@dataclass(frozen=True)
class ProfileSnapshot:
    profile_version: int
    daily_minutes: int
    study_mode: str


@dataclass(frozen=True)
class PreferenceSnapshot:
    preference_version_id: str
    version_no: int
    timezone: str
    weekday_minutes: int
    weekend_minutes: int
    long_block_minutes: int
    fragment_minutes: int
    regular_rest_weekday: int
    regular_mock_weekday: int
    confirmed: bool
    input_fingerprint: str


@dataclass(frozen=True)
class CapacitySnapshot:
    capacity_version_id: str
    version_no: int
    available_minutes: int
    energy: int
    stress: int
    confidence_level: int
    confirmed: bool
    source_type: str
    input_fingerprint: str


@dataclass(frozen=True)
class GoalAbilitySnapshot:
    goal_version_id: str | None
    position_name: str | None
    department_name: str | None
    target_lower: float | None
    target_recommended: float | None
    target_upper: float | None
    target_evidence_level: str | None
    target_valid_until: datetime | None
    estimate_version_id: str | None
    estimate_status: str | None
    xingce_lower: float | None
    xingce_recommended: float | None
    xingce_upper: float | None
    shenlun_lower: float | None
    shenlun_recommended: float | None
    shenlun_upper: float | None
    total_lower: float | None
    total_recommended: float | None
    total_upper: float | None
    estimate_confidence: str | None
    estimate_valid_until: datetime | None
    evidence_summary: dict[str, Any] | None


@dataclass(frozen=True)
class ActiveStrategyDecision:
    decision_version_id: str
    decision_id: str
    decision_type: str
    scope_type: str
    scope_key: str
    rationale: str
    evidence_refs: tuple[dict[str, Any], ...]
    effective_from: datetime
    effective_until: datetime
    review_at: datetime
    input_fingerprint: str


class PlanningRepository:
    def __init__(
        self,
        *,
        xingce_repository: XingceAttemptRepository | None = None,
    ) -> None:
        self._xingce_repository = xingce_repository or XingceAttemptRepository()

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
                  input_fingerprint, supersedes_capacity_version_id, recorded_at
                ) values (
                  cast(:capacity_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date, :version_no,
                  :available_minutes, 3, 3, 3, false, 'weekly_preference',
                  cast(:preference_version_id as uuid), :input_fingerprint,
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
                  input_fingerprint, supersedes_capacity_version_id, recorded_at
                ) values (
                  cast(:capacity_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date, :version_no,
                  :available_minutes, :energy, :stress, :confidence_level,
                  true, 'user_override', cast(:weekly_preference_version_id as uuid),
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

    def goal_and_ability(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> GoalAbilitySnapshot:
        row = (
            session.execute(
                text(
                    """
                select
                  goal.goal_version_id,
                  position.name as position_name,
                  position.department_full as department_name,
                  goal.target_score_lower,
                  goal.target_score_recommended,
                  goal.target_score_upper,
                  goal.target_score_evidence_level,
                  goal.target_score_valid_until,
                  estimate.estimate_version_id,
                  estimate.status as estimate_status,
                  estimate.xingce_lower,
                  estimate.xingce_recommended,
                  estimate.xingce_upper,
                  estimate.shenlun_lower,
                  estimate.shenlun_recommended,
                  estimate.shenlun_upper,
                  estimate.total_lower,
                  estimate.total_recommended,
                  estimate.total_upper,
                  estimate.confidence as estimate_confidence,
                  estimate.valid_until as estimate_valid_until,
                  estimate.evidence_summary
                from learner.users learner
                left join learner.position_goal_versions goal
                  on goal.user_id = learner.user_id
                 and goal.role = 'primary'
                 and goal.status = 'confirmed'
                left join product.positions position
                  on position.position_id = goal.position_id
                left join lateral (
                  select current_estimate.*
                  from learning.ability_estimate_versions current_estimate
                  where current_estimate.user_id = learner.user_id
                    and not exists (
                      select 1
                      from learning.ability_estimate_versions successor
                      where successor.supersedes_estimate_version_id =
                            current_estimate.estimate_version_id
                    )
                  order by current_estimate.exam_year desc
                  limit 1
                ) estimate on true
                where learner.user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        return GoalAbilitySnapshot(
            goal_version_id=_optional_str(row["goal_version_id"]),
            position_name=row["position_name"],
            department_name=row["department_name"],
            target_lower=_optional_float(row["target_score_lower"]),
            target_recommended=_optional_float(row["target_score_recommended"]),
            target_upper=_optional_float(row["target_score_upper"]),
            target_evidence_level=row["target_score_evidence_level"],
            target_valid_until=row["target_score_valid_until"],
            estimate_version_id=_optional_str(row["estimate_version_id"]),
            estimate_status=row["estimate_status"],
            xingce_lower=_optional_float(row["xingce_lower"]),
            xingce_recommended=_optional_float(row["xingce_recommended"]),
            xingce_upper=_optional_float(row["xingce_upper"]),
            shenlun_lower=_optional_float(row["shenlun_lower"]),
            shenlun_recommended=_optional_float(row["shenlun_recommended"]),
            shenlun_upper=_optional_float(row["shenlun_upper"]),
            total_lower=_optional_float(row["total_lower"]),
            total_recommended=_optional_float(row["total_recommended"]),
            total_upper=_optional_float(row["total_upper"]),
            estimate_confidence=row["estimate_confidence"],
            estimate_valid_until=row["estimate_valid_until"],
            evidence_summary=(
                dict(row["evidence_summary"]) if row["evidence_summary"] is not None else None
            ),
        )

    def active_strategy_decisions(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
    ) -> list[ActiveStrategyDecision]:
        rows = session.execute(
            text(
                """
                select
                  decision.decision_version_id::text,
                  decision.decision_id::text,
                  decision.decision_type,
                  decision.scope_type,
                  decision.scope_key,
                  decision.rationale,
                  decision.evidence_refs,
                  decision.effective_from,
                  decision.effective_until,
                  decision.review_at,
                  decision.input_fingerprint
                from learning.strategy_decision_versions decision
                where decision.user_id = cast(:user_id as uuid)
                  and decision.status = 'active'
                  and decision.effective_from <= :now
                  and decision.effective_until > :now
                  and decision.review_at > :now
                  and not exists (
                    select 1
                    from learning.strategy_decision_versions successor
                    where successor.supersedes_decision_version_id =
                          decision.decision_version_id
                  )
                order by decision.decision_type, decision.scope_type,
                         decision.scope_key, decision.decision_version_id
                """
            ),
            {"user_id": user_id, "now": now},
        ).mappings()
        return [
            ActiveStrategyDecision(
                decision_version_id=str(row["decision_version_id"]),
                decision_id=str(row["decision_id"]),
                decision_type=str(row["decision_type"]),
                scope_type=str(row["scope_type"]),
                scope_key=str(row["scope_key"]),
                rationale=str(row["rationale"]),
                evidence_refs=tuple(dict(item) for item in row["evidence_refs"]),
                effective_from=_as_utc(row["effective_from"]),
                effective_until=_as_utc(row["effective_until"]),
                review_at=_as_utc(row["review_at"]),
                input_fingerprint=str(row["input_fingerprint"]),
            )
            for row in rows
        ]

    def objective_snapshot(
        self,
        session: Session,
        *,
        user_id: str,
        goal: GoalAbilitySnapshot,
        now: datetime,
    ) -> ObjectivePlanningSnapshot:
        weakest = session.execute(
            text(
                """
                select question.module_name
                from learner.practice_answers answer
                join product.questions question
                  on question.question_id = answer.question_id
                where answer.user_id = cast(:user_id as uuid)
                  and answer.answered_at >= now() - interval '60 days'
                  and question.module_name is not null
                group by question.module_name
                having count(*) >= 5
                order by avg(case when answer.correct then 1.0 else 0.0 end),
                         count(*) desc, question.module_name
                limit 1
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        due_reflections = list(
            session.execute(
                text(
                    """
                    select review.review_version_id::text,
                           review.review_id::text,
                           review.session_id::text
                    from learning.xingce_attempt_review_versions review
                    join learning.xingce_task_sessions task_session
                      on task_session.session_id = review.session_id
                     and task_session.user_id = review.user_id
                    where review.user_id = cast(:user_id as uuid)
                      and review.state in (
                        'reflection_due', 'cause_candidate_pending',
                        'cause_decision_due'
                      )
                      and task_session.state = 'submitted'
                      and not exists (
                        select 1
                        from learning.xingce_attempt_review_versions successor
                        where successor.supersedes_review_version_id =
                              review.review_version_id
                      )
                    order by review.created_at, review.review_id
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )
        due_mastery = list(
            session.execute(
                text(
                    """
                    select mastery.mastery_id::text,
                           mastery.mastery_version_id::text,
                           mastery.module_name,
                           mastery.used_question_ids
                    from learning.xingce_mastery_state_versions mastery
                    where mastery.user_id = cast(:user_id as uuid)
                      and mastery.due_at is not null
                      and mastery.due_at <= :now
                      and not exists (
                        select 1
                        from learning.xingce_mastery_state_versions successor
                        where successor.supersedes_mastery_version_id =
                              mastery.mastery_version_id
                      )
                    order by mastery.due_at, mastery.created_at, mastery.mastery_id
                    """
                ),
                {"user_id": user_id, "now": now},
            ).mappings()
        )
        latest_mastery_version_id = session.execute(
            text(
                """
                select mastery.mastery_version_id::text
                from learning.xingce_mastery_state_versions mastery
                where mastery.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1
                    from learning.xingce_mastery_state_versions successor
                    where successor.supersedes_mastery_version_id =
                          mastery.mastery_version_id
                  )
                order by mastery.created_at desc, mastery.mastery_version_id desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        first_reflection = due_reflections[0] if due_reflections else None
        first_mastery = due_mastery[0] if due_mastery else None
        baseline_required = (
            goal.estimate_version_id is None
            or goal.xingce_recommended is None
            or goal.estimate_valid_until is None
            or _as_utc(goal.estimate_valid_until) <= _as_utc(now)
        )
        baseline_question_count = 0
        targeted_question_count = 0
        verification_question_count = 0
        if baseline_required:
            baseline_question_count = len(
                self._xingce_repository.select_frozen_questions(
                    session,
                    module_name=None,
                    limit=XINGCE_QUESTION_BUNDLE_SIZE,
                    user_id=user_id,
                )
            )
        else:
            targeted_question_count = len(
                self._xingce_repository.select_frozen_questions(
                    session,
                    module_name=str(weakest) if weakest else None,
                    limit=XINGCE_QUESTION_BUNDLE_SIZE,
                    user_id=user_id,
                )
            )
        if first_mastery is not None:
            verification_question_count = len(
                self._xingce_repository.select_frozen_questions(
                    session,
                    module_name=(
                        str(first_mastery["module_name"]) if first_mastery["module_name"] else None
                    ),
                    limit=XINGCE_QUESTION_BUNDLE_SIZE,
                    user_id=user_id,
                    excluded_question_ids=[
                        str(question_id) for question_id in first_mastery["used_question_ids"]
                    ],
                )
            )
        source_version_id = (
            str(first_mastery["mastery_version_id"])
            if first_mastery is not None
            else (
                str(first_reflection["review_version_id"])
                if first_reflection is not None
                else (
                    str(latest_mastery_version_id)
                    if latest_mastery_version_id is not None
                    else goal.estimate_version_id
                )
            )
        )
        return ObjectivePlanningSnapshot(
            baseline_required=baseline_required,
            due_reflection_count=len(due_reflections),
            due_verification_count=len(due_mastery),
            weakest_module=str(weakest) if weakest else None,
            source_version_id=source_version_id,
            closure_readiness="available",
            closure_unavailable_reason=None,
            due_reflection_session_id=(
                str(first_reflection["session_id"]) if first_reflection is not None else None
            ),
            due_reflection_review_id=(
                str(first_reflection["review_id"]) if first_reflection is not None else None
            ),
            due_reflection_version_id=(
                str(first_reflection["review_version_id"]) if first_reflection is not None else None
            ),
            due_verification_mastery_id=(
                str(first_mastery["mastery_id"]) if first_mastery is not None else None
            ),
            due_verification_version_id=(
                str(first_mastery["mastery_version_id"]) if first_mastery is not None else None
            ),
            baseline_question_count=baseline_question_count,
            targeted_question_count=targeted_question_count,
            verification_question_count=verification_question_count,
        )

    def essay_snapshot(
        self,
        session: Session,
        *,
        user_id: str,
        goal: GoalAbilitySnapshot,
    ) -> EssayPlanningSnapshot:
        published = (
            session.execute(
                text(
                    """
                select
                  task.task_version_id::text,
                  task.question_type,
                  task.word_limit,
                  task.maximum_score,
                  material.material_bundle_version_id::text,
                  material.version_no as material_version_no,
                  rubric.rubric_version_id::text
                from product.essay_task_versions task
                join product.essay_material_bundle_versions material
                  on material.task_version_id = task.task_version_id
                 and material.completeness = 'complete'
                left join lateral (
                  select item.rubric_version_id
                  from product.essay_rubric_versions item
                  where item.task_version_id = task.task_version_id
                    and item.material_bundle_version_id =
                        material.material_bundle_version_id
                    and item.state = 'published'
                    and not exists (
                      select 1 from product.essay_rubric_versions successor
                      where successor.task_version_id = item.task_version_id
                        and successor.material_bundle_version_id =
                            item.material_bundle_version_id
                        and successor.version_no > item.version_no
                        and successor.state = 'published'
                    )
                  order by item.created_at desc, item.rubric_version_id
                  limit 1
                ) rubric on true
                where task.state = 'published'
                  and task.task_key like 'guokao%'
                  and not exists (
                    select 1 from product.essay_task_versions successor
                    where successor.task_key = task.task_key
                      and successor.version_no > task.version_no
                      and successor.state = 'published'
                  )
                  and not exists (
                    select 1 from product.essay_material_bundle_versions successor
                    where successor.task_version_id = material.task_version_id
                      and successor.version_no > material.version_no
                      and successor.completeness = 'complete'
                  )
                  and exists (
                    select 1
                    from product.essay_material_segments segment
                    where segment.material_bundle_version_id =
                          material.material_bundle_version_id
                  )
                order by (rubric.rubric_version_id is not null) desc,
                         task.version_no desc, material.version_no desc,
                         task.created_at desc, task.task_version_id
                limit 1
                """
                )
            )
            .mappings()
            .one_or_none()
        )
        if published is None:
            return EssayPlanningSnapshot("content_unavailable", None)
        latest = (
            session.execute(
                text(
                    """
                select
                  attempt.attempt_id::text,
                  attempt.task_version_id::text,
                  attempt.material_bundle_version_id::text,
                  attempt.rubric_version_id::text,
                  attempt.submitted_at,
                  task.question_type,
                  task.word_limit,
                  task.maximum_score,
                  answer.answer_version_id::text,
                  review.review_version_id::text,
                  review.status as review_status,
                  review.grounding_contract_version,
                  review.grounding_report,
                  review.findings,
                  review.rewrite_plan,
                  rewrite.rewrite_version_id::text,
                  transfer.verification_version_id::text,
                  transfer.status as transfer_status
                from learning.essay_attempts attempt
                join product.essay_task_versions task
                  on task.task_version_id = attempt.task_version_id
                join product.essay_rubric_versions bound_rubric
                  on bound_rubric.rubric_version_id = attempt.rubric_version_id
                 and bound_rubric.task_version_id = attempt.task_version_id
                 and bound_rubric.material_bundle_version_id =
                     attempt.material_bundle_version_id
                left join lateral (
                  select item.*
                  from learning.essay_answer_versions item
                  where item.user_id = attempt.user_id
                    and item.attempt_id = attempt.attempt_id
                    and item.task_version_id = attempt.task_version_id
                    and not exists (
                      select 1 from learning.essay_answer_versions successor
                      where successor.supersedes_answer_version_id =
                            item.answer_version_id
                    )
                  order by item.version_no desc, item.answer_version_id
                  limit 1
                ) answer on true
                left join lateral (
                  select item.*
                  from learning.essay_review_versions item
                  where item.attempt_id = attempt.attempt_id
                    and item.answer_version_id = answer.answer_version_id
                    and item.execution_fingerprint is not null
                    and not exists (
                      select 1 from learning.essay_review_versions successor
                      where successor.supersedes_review_version_id = item.review_version_id
                    )
                  order by item.generated_at desc, item.review_version_id
                  limit 1
                ) review on true
                left join lateral (
                  select item.*
                  from learning.essay_rewrite_versions item
                  where item.user_id = attempt.user_id
                    and item.base_review_version_id = review.review_version_id
                    and not exists (
                      select 1 from learning.essay_rewrite_versions successor
                      where successor.supersedes_rewrite_version_id = item.rewrite_version_id
                    )
                  order by item.created_at desc, item.rewrite_version_id
                  limit 1
                ) rewrite on true
                left join lateral (
                  select item.*
                  from learning.essay_transfer_verification_versions item
                  where item.user_id = attempt.user_id
                    and item.source_review_version_id = review.review_version_id
                    and not exists (
                      select 1
                      from learning.essay_transfer_verification_versions successor
                      where successor.supersedes_verification_version_id =
                            item.verification_version_id
                    )
                  order by item.generated_at desc, item.verification_version_id
                  limit 1
                ) transfer on true
                where attempt.user_id = cast(:user_id as uuid)
                order by attempt.submitted_at desc, attempt.attempt_id
                limit 1
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        closure_snapshot = self._essay_mastery_snapshot(
            session,
            user_id=user_id,
            latest_attempt=latest,
        )
        if closure_snapshot is not None:
            return closure_snapshot
        artifacts = (
            session.execute(
                text(
                    """
                select distinct on (artifact.artifact_type)
                       artifact.artifact_type,
                       artifact.artifact_version_id::text
                from learning.essay_learning_artifact_versions artifact
                where artifact.user_id = cast(:user_id as uuid)
                  and artifact.task_version_id = cast(:task_version_id as uuid)
                  and artifact.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and (
                    artifact.artifact_type = 'material_annotation'
                    or (
                      artifact.artifact_type in ('point_extraction', 'outline')
                      and artifact.rubric_version_id = cast(:rubric_version_id as uuid)
                    )
                  )
                  and not exists (
                    select 1 from learning.essay_learning_artifact_versions successor
                    where successor.supersedes_artifact_version_id =
                          artifact.artifact_version_id
                  )
                order by artifact.artifact_type, artifact.created_at desc,
                         artifact.artifact_version_id desc
                """
                ),
                {
                    "user_id": user_id,
                    "task_version_id": str(published["task_version_id"]),
                    "material_bundle_version_id": str(published["material_bundle_version_id"]),
                    "rubric_version_id": _optional_str(published["rubric_version_id"]),
                },
            )
            .mappings()
            .all()
        )
        artifact_versions = {
            str(item["artifact_type"]): str(item["artifact_version_id"]) for item in artifacts
        }
        if latest is None:
            material_annotation_id = artifact_versions.get("material_annotation")
            point_extraction_id = artifact_versions.get("point_extraction")
            outline_id = artifact_versions.get("outline")
            rubric_id = _optional_str(published["rubric_version_id"])
            if material_annotation_id is None:
                next_stage = "baseline_due"
                pending_reason = None
            elif rubric_id is None:
                next_stage = "evaluation_pending"
                pending_reason = (
                    "材料地图已完成；当前题目的可审计评分规程尚未发布，"
                    "规划 Agent 暂停找点、提纲与作答，避免伪造评分依据。"
                )
            elif point_extraction_id is None:
                next_stage = "point_extraction_due"
                pending_reason = None
            elif outline_id is None:
                next_stage = "outline_due"
                pending_reason = None
            else:
                next_stage = "drafting_due"
                pending_reason = None
            return EssayPlanningSnapshot(
                next_stage,  # type: ignore[arg-type]
                str(published["task_version_id"]),
                material_bundle_version_id=str(published["material_bundle_version_id"]),
                rubric_version_id=rubric_id,
                question_type=str(published["question_type"]),
                word_limit=int(published["word_limit"]),
                maximum_score=float(published["maximum_score"]),
                material_annotation_version_id=material_annotation_id,
                point_extraction_version_id=point_extraction_id,
                outline_version_id=outline_id,
                pending_reason=pending_reason,
            )
        task_id = str(latest["task_version_id"] or published["task_version_id"])
        material_bundle_version_id = str(latest["material_bundle_version_id"])
        rubric_version_id = str(latest["rubric_version_id"])
        question_type = str(latest["question_type"])
        word_limit = int(latest["word_limit"])
        maximum_score = float(latest["maximum_score"])
        review_acknowledgement_id = None
        if latest["review_version_id"] is not None:
            review_acknowledgement_id = session.execute(
                text(
                    """
                    select artifact.artifact_version_id::text
                    from learning.essay_learning_artifact_versions artifact
                    where artifact.user_id = cast(:user_id as uuid)
                      and artifact.task_version_id = cast(:task_version_id as uuid)
                      and artifact.artifact_type = 'review_acknowledgement'
                      and artifact.review_version_id = cast(:review_version_id as uuid)
                      and not exists (
                        select 1 from learning.essay_learning_artifact_versions successor
                        where successor.supersedes_artifact_version_id =
                              artifact.artifact_version_id
                      )
                    order by artifact.created_at desc, artifact.artifact_version_id
                    limit 1
                    """
                ),
                {
                    "user_id": user_id,
                    "task_version_id": task_id,
                    "review_version_id": str(latest["review_version_id"]),
                },
            ).scalar_one_or_none()
        pending_reason = None
        if latest["answer_version_id"] is None:
            stage = "drafting_due"
        elif latest["review_version_id"] is None:
            stage = "evaluation_pending"
            pending_reason = (
                "申论原稿已提交，证据化 AI 评阅仍在生成；评阅完成前不生成“用户查看评阅”任务。"
            )
        elif not _review_is_learning_eligible(latest):
            stage = "evaluation_pending"
            pending_reason = (
                "本次申论评阅未完成或被证据门禁阻断（未通过 essay-grounding-v2）；"
                "系统不会让 legacy、未验真或不可学习的 finding 进入后续规划，"
                "需等待重新评阅。"
            )
        elif review_acknowledgement_id is None:
            stage = "review_due"
        elif latest["rewrite_version_id"] is None:
            stage = "rewrite_due"
        elif (
            latest["verification_version_id"] is None or latest["transfer_status"] == "inconclusive"
        ):
            fresh_task = (
                session.execute(
                    text(
                        """
                    select
                      task.task_version_id::text,
                      task.question_type,
                      task.word_limit,
                      task.maximum_score,
                      material.material_bundle_version_id::text,
                      material.version_no as material_version_no,
                      rubric.rubric_version_id::text
                    from product.essay_task_versions task
                    join product.essay_material_bundle_versions material
                      on material.task_version_id = task.task_version_id
                     and material.completeness = 'complete'
                    join product.essay_rubric_versions rubric
                      on rubric.task_version_id = task.task_version_id
                     and rubric.material_bundle_version_id =
                         material.material_bundle_version_id
                     and rubric.state = 'published'
                    where task.state = 'published'
                      and task.task_key like 'guokao%'
                      and task.task_version_id <> cast(:source_task_id as uuid)
                      and not exists (
                        select 1 from product.essay_task_versions successor
                        where successor.task_key = task.task_key
                          and successor.version_no > task.version_no
                          and successor.state = 'published'
                      )
                      and not exists (
                        select 1 from product.essay_material_bundle_versions successor
                        where successor.task_version_id = material.task_version_id
                          and successor.version_no > material.version_no
                          and successor.completeness = 'complete'
                      )
                      and not exists (
                        select 1 from product.essay_rubric_versions successor
                        where successor.task_version_id = rubric.task_version_id
                          and successor.material_bundle_version_id =
                              rubric.material_bundle_version_id
                          and successor.version_no > rubric.version_no
                          and successor.state = 'published'
                      )
                      and exists (
                        select 1 from product.essay_material_segments segment
                        where segment.material_bundle_version_id =
                              material.material_bundle_version_id
                      )
                    order by task.version_no desc, material.version_no desc,
                             task.created_at desc, task.task_version_id
                    limit 1
                    """
                    ),
                    {"source_task_id": task_id},
                )
                .mappings()
                .one_or_none()
            )
            if fresh_task is not None:
                stage = "transfer_due"
                task_id = str(fresh_task["task_version_id"])
                material_bundle_version_id = str(fresh_task["material_bundle_version_id"])
                rubric_version_id = str(fresh_task["rubric_version_id"])
                question_type = str(fresh_task["question_type"])
                word_limit = int(fresh_task["word_limit"])
                maximum_score = float(fresh_task["maximum_score"])
            else:
                stage = "rewrite_due"
        elif latest["transfer_status"] == "not_verified":
            stage = "rewrite_due"
        else:
            stage = "practice_due"
        return EssayPlanningSnapshot(
            stage,  # type: ignore[arg-type]
            task_id,
            material_bundle_version_id=material_bundle_version_id,
            rubric_version_id=rubric_version_id,
            question_type=question_type,
            word_limit=word_limit,
            maximum_score=maximum_score,
            attempt_id=str(latest["attempt_id"]),
            answer_version_id=_optional_str(latest["answer_version_id"]),
            review_version_id=_optional_str(latest["review_version_id"]),
            rewrite_version_id=_optional_str(latest["rewrite_version_id"]),
            transfer_verification_version_id=_optional_str(latest["verification_version_id"]),
            review_acknowledgement_version_id=_optional_str(review_acknowledgement_id),
            pending_reason=pending_reason,
            weakest_dimension=(
                _weakest_dimension(latest["findings"], latest["rewrite_plan"])
                if _review_is_learning_eligible(latest)
                else None
            ),
            weakest_dimension_key=(
                _weakest_dimension_key(latest["findings"], latest["rewrite_plan"])
                if _review_is_learning_eligible(latest)
                else None
            ),
        )

    def _essay_mastery_snapshot(
        self,
        session: Session,
        *,
        user_id: str,
        latest_attempt: RowMapping | None,
    ) -> EssayPlanningSnapshot | None:
        heads = list(
            session.execute(
                text(
                    """
                    select mastery.*
                    from learning.essay_mastery_state_versions mastery
                    where mastery.user_id = cast(:user_id as uuid)
                      and mastery.state <> 'withdrawn'
                      and not exists (
                        select 1 from learning.essay_mastery_state_versions successor
                        where successor.supersedes_mastery_version_id =
                              mastery.mastery_version_id
                      )
                    order by mastery.due_at nulls last, mastery.created_at,
                             mastery.finding_decision_artifact_version_id,
                             mastery.finding_id
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )
        if not heads:
            return None
        due_heads = [
            item
            for item in heads
            if item["state"] in {"rewrite_due", "transfer_due"}
            and item["due_at"] is not None
            and _as_utc(item["due_at"]) <= datetime.now(UTC)
        ]
        if not due_heads:
            scheduled = [item for item in heads if item["state"] in {"rewrite_due", "transfer_due"}]
            if scheduled:
                earliest = min(scheduled, key=lambda item: item["due_at"])
                return EssayPlanningSnapshot(
                    "content_unavailable",
                    None,
                    pending_reason=(
                        "申论闭环已有 canonical head，但尚未到复写或迁移验证时间；"
                        "规划不会提前制造重复训练。"
                    ),
                    mastery_state=str(earliest["state"]),  # type: ignore[arg-type]
                    mastery_version_ids=tuple(
                        str(item["mastery_version_id"]) for item in scheduled
                    ),
                    mastery_ids=tuple(str(item["mastery_id"]) for item in scheduled),
                )
            consumed_attempt_ids = {str(item["source_attempt_id"]) for item in heads}
            transfer_version_ids = [
                str(item["transfer_verification_version_id"])
                for item in heads
                if item["transfer_verification_version_id"] is not None
            ]
            for transfer_version_id in transfer_version_ids:
                transfer_attempt_id = session.execute(
                    text(
                        "select transfer_attempt_id::text "
                        "from learning.essay_transfer_verification_versions "
                        "where verification_version_id = cast(:version_id as uuid) "
                        "and user_id = cast(:user_id as uuid)"
                    ),
                    {"version_id": transfer_version_id, "user_id": user_id},
                ).scalar_one_or_none()
                if transfer_attempt_id is not None:
                    consumed_attempt_ids.add(str(transfer_attempt_id))
            if (
                latest_attempt is not None
                and str(latest_attempt["attempt_id"]) not in consumed_attempt_ids
            ):
                return None
            return EssayPlanningSnapshot(
                "content_unavailable",
                None,
                pending_reason=(
                    "当前申论 finding 已通过独立迁移验证并稳定掌握；"
                    "在出现新的真实作答或未解决证据前，不重复生成复写或迁移训练。"
                ),
                mastery_state="stable_mastered",
                mastery_version_ids=tuple(str(item["mastery_version_id"]) for item in heads),
                mastery_ids=tuple(str(item["mastery_id"]) for item in heads),
            )

        decision_id = str(due_heads[0]["finding_decision_artifact_version_id"])
        group = [
            item
            for item in heads
            if str(item["finding_decision_artifact_version_id"]) == decision_id
        ]
        states = {str(item["state"]) for item in group}
        if "stable_mastered" in states or not states.issubset({"rewrite_due", "transfer_due"}):
            return _essay_closure_gap(
                "申论掌握度 current head 状态互相冲突，无法确定唯一可写闭环。"
            )
        identity = {
            (
                str(item["source_task_version_id"]),
                str(item["source_attempt_id"]),
                str(item["source_answer_version_id"]),
                str(item["source_review_version_id"]),
            )
            for item in group
        }
        if len(identity) != 1:
            return _essay_closure_gap("申论掌握度 current head 的来源题目、作答或评阅不一致。")
        rewrite_ids = {
            str(item["rewrite_version_id"])
            for item in group
            if item["rewrite_version_id"] is not None
        }
        if len(rewrite_ids) > 1:
            return _essay_closure_gap("申论掌握度 current head 绑定了不同 source rewrite 版本。")
        context = (
            session.execute(
                text(
                    """
                select
                  mastery.mastery_version_id,
                  source_attempt.attempt_id::text as source_attempt_id,
                  source_attempt.task_version_id::text as source_task_version_id,
                  source_attempt.material_bundle_version_id::text
                    as source_material_bundle_version_id,
                  source_attempt.rubric_version_id::text as source_rubric_version_id,
                  source_task.question_type, source_task.word_limit,
                  source_task.maximum_score, source_task.state as source_task_state,
                  source_material.completeness as source_material_completeness,
                  source_rubric.state as source_rubric_state,
                  source_answer.answer_version_id::text as source_answer_version_id,
                  current_answer.answer_version_id::text as current_answer_version_id,
                  source_review.review_version_id::text as source_review_version_id,
                  source_review.status as source_review_status,
                  source_review.grounding_contract_version,
                  source_review.grounding_report,
                  source_review.execution_fingerprint,
                  source_review.findings,
                  source_review.rewrite_plan,
                  decision.artifact_version_id::text as decision_artifact_version_id,
                  decision.artifact_type as decision_artifact_type,
                  decision.review_version_id::text as decision_review_version_id,
                  decision.task_version_id::text as decision_task_version_id,
                  decision.material_bundle_version_id::text
                    as decision_material_bundle_version_id,
                  decision.rubric_version_id::text as decision_rubric_version_id,
                  rewrite.rewrite_version_id::text,
                  rewrite.writer_contract_version as rewrite_contract_version,
                  rewrite.writer_policy_version as rewrite_policy_version,
                  rewrite.analysis_payload as rewrite_analysis_payload,
                  transfer.verification_version_id::text
                    as transfer_verification_version_id
                from learning.essay_mastery_state_versions mastery
                left join learning.essay_attempts source_attempt
                  on source_attempt.attempt_id = mastery.source_attempt_id
                 and source_attempt.user_id = mastery.user_id
                 and source_attempt.task_version_id = mastery.source_task_version_id
                left join product.essay_task_versions source_task
                  on source_task.task_version_id = source_attempt.task_version_id
                left join product.essay_material_bundle_versions source_material
                  on source_material.material_bundle_version_id =
                     source_attempt.material_bundle_version_id
                 and source_material.task_version_id = source_attempt.task_version_id
                left join product.essay_rubric_versions source_rubric
                  on source_rubric.rubric_version_id = source_attempt.rubric_version_id
                 and source_rubric.task_version_id = source_attempt.task_version_id
                 and source_rubric.material_bundle_version_id =
                     source_attempt.material_bundle_version_id
                left join learning.essay_answer_versions source_answer
                  on source_answer.answer_version_id = mastery.source_answer_version_id
                 and source_answer.attempt_id = mastery.source_attempt_id
                 and source_answer.user_id = mastery.user_id
                 and source_answer.task_version_id = mastery.source_task_version_id
                left join lateral (
                  select answer.*
                  from learning.essay_answer_versions answer
                  where answer.attempt_id = mastery.source_attempt_id
                    and answer.user_id = mastery.user_id
                    and answer.task_version_id = mastery.source_task_version_id
                    and not exists (
                      select 1 from learning.essay_answer_versions successor
                      where successor.supersedes_answer_version_id =
                            answer.answer_version_id
                    )
                  order by answer.version_no desc, answer.created_at desc
                  limit 1
                ) current_answer on true
                left join learning.essay_review_versions source_review
                  on source_review.review_version_id = mastery.source_review_version_id
                 and source_review.attempt_id = mastery.source_attempt_id
                 and source_review.user_id = mastery.user_id
                 and source_review.task_version_id = mastery.source_task_version_id
                 and source_review.answer_version_id = mastery.source_answer_version_id
                left join learning.essay_learning_artifact_versions decision
                  on decision.artifact_version_id =
                     mastery.finding_decision_artifact_version_id
                 and decision.user_id = mastery.user_id
                 and not exists (
                   select 1 from learning.essay_learning_artifact_versions successor
                   where successor.supersedes_artifact_version_id =
                         decision.artifact_version_id
                 )
                left join learning.essay_rewrite_versions rewrite
                  on rewrite.rewrite_version_id = mastery.rewrite_version_id
                 and rewrite.user_id = mastery.user_id
                 and rewrite.base_review_version_id = mastery.source_review_version_id
                 and rewrite.finding_decision_artifact_version_id =
                     mastery.finding_decision_artifact_version_id
                 and not exists (
                   select 1 from learning.essay_rewrite_versions successor
                   where successor.supersedes_rewrite_version_id =
                         rewrite.rewrite_version_id
                 )
                left join learning.essay_transfer_verification_versions transfer
                  on transfer.verification_version_id =
                     mastery.transfer_verification_version_id
                 and transfer.user_id = mastery.user_id
                where mastery.mastery_version_id = cast(:mastery_version_id as uuid)
                  and mastery.user_id = cast(:user_id as uuid)
                """
                ),
                {
                    "mastery_version_id": str(group[0]["mastery_version_id"]),
                    "user_id": user_id,
                },
            )
            .mappings()
            .one()
        )
        gap = _essay_closure_context_gap(context, decision_id=decision_id)
        if gap is not None:
            return _essay_closure_gap(gap)
        expected_rewrite_id = next(iter(rewrite_ids), None)
        if expected_rewrite_id != _optional_str(context["rewrite_version_id"]):
            return _essay_closure_gap("申论掌握度 head 引用的 rewrite 不是当前 owned writer 版本。")
        if expected_rewrite_id is not None and (
            context["rewrite_contract_version"] != "essay-rewrite-writer@v2"
            or context["rewrite_policy_version"] != "essay-mastery@v1"
        ):
            return _essay_closure_gap("申论掌握度 head 引用了非 canonical essay rewrite writer。")
        transfer_ids = {
            str(item["transfer_verification_version_id"])
            for item in group
            if item["transfer_verification_version_id"] is not None
        }
        if len(transfer_ids) > 1 or next(iter(transfer_ids), None) != _optional_str(
            context["transfer_verification_version_id"]
        ):
            return _essay_closure_gap("申论掌握度 head 引用的 transfer verification 不一致。")

        stage = "rewrite_due" if "rewrite_due" in states else "transfer_due"
        rewrite_version_id = _optional_str(context["rewrite_version_id"])
        if stage == "transfer_due":
            if (
                rewrite_version_id is None
                or context["rewrite_contract_version"] != "essay-rewrite-writer@v2"
                or context["rewrite_policy_version"] != "essay-mastery@v1"
                or not isinstance(context["rewrite_analysis_payload"], dict)
                or context["rewrite_analysis_payload"].get("status") != "passed"
            ):
                return _essay_closure_gap(
                    "申论 transfer_due head 缺少当前、已通过的 essay-rewrite-writer@v2 源稿。"
                )
            target = self._essay_fresh_transfer_content(
                session,
                source_task_version_id=str(context["source_task_version_id"]),
                source_material_bundle_version_id=str(context["source_material_bundle_version_id"]),
            )
            if target is None:
                return _essay_closure_gap(
                    "申论迁移验证缺少不同题目和材料的已发布任务，essay-transfer writer 当前不可执行。"
                )
            task_version_id = str(target["task_version_id"])
            material_bundle_version_id = str(target["material_bundle_version_id"])
            rubric_version_id = str(target["rubric_version_id"])
            question_type = str(target["question_type"])
            word_limit = int(target["word_limit"])
            maximum_score = float(target["maximum_score"])
        else:
            task_version_id = str(context["source_task_version_id"])
            material_bundle_version_id = str(context["source_material_bundle_version_id"])
            rubric_version_id = str(context["source_rubric_version_id"])
            question_type = str(context["question_type"])
            word_limit = int(context["word_limit"])
            maximum_score = float(context["maximum_score"])
        ordered = sorted(group, key=lambda item: str(item["finding_id"]))
        return EssayPlanningSnapshot(
            stage,  # type: ignore[arg-type]
            task_version_id,
            material_bundle_version_id=material_bundle_version_id,
            rubric_version_id=rubric_version_id,
            question_type=question_type,
            word_limit=word_limit,
            maximum_score=maximum_score,
            attempt_id=str(context["source_attempt_id"]),
            answer_version_id=str(context["current_answer_version_id"]),
            review_version_id=str(context["source_review_version_id"]),
            rewrite_version_id=rewrite_version_id,
            transfer_verification_version_id=_optional_str(
                context["transfer_verification_version_id"]
            ),
            review_acknowledgement_version_id=decision_id,
            weakest_dimension=_weakest_dimension(
                context["findings"],
                context["rewrite_plan"],
            ),
            weakest_dimension_key=_weakest_dimension_key(
                context["findings"],
                context["rewrite_plan"],
            ),
            mastery_state=stage,
            mastery_version_ids=tuple(str(item["mastery_version_id"]) for item in ordered),
            mastery_ids=tuple(str(item["mastery_id"]) for item in ordered),
            source_task_version_id=str(context["source_task_version_id"]),
            source_material_bundle_version_id=str(context["source_material_bundle_version_id"]),
            source_answer_version_id=str(context["source_answer_version_id"]),
            finding_decision_artifact_version_id=decision_id,
        )

    def _essay_fresh_transfer_content(
        self,
        session: Session,
        *,
        source_task_version_id: str,
        source_material_bundle_version_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select task.task_version_id::text, task.question_type,
                       task.word_limit, task.maximum_score,
                       material.material_bundle_version_id::text,
                       rubric.rubric_version_id::text
                from product.essay_task_versions task
                join product.essay_material_bundle_versions material
                  on material.task_version_id = task.task_version_id
                 and material.completeness = 'complete'
                join product.essay_rubric_versions rubric
                  on rubric.task_version_id = task.task_version_id
                 and rubric.material_bundle_version_id =
                     material.material_bundle_version_id
                 and rubric.state = 'published'
                where task.state = 'published'
                  and task.task_key like 'guokao%'
                  and task.task_version_id <> cast(:source_task_id as uuid)
                  and material.material_bundle_version_id <>
                      cast(:source_material_id as uuid)
                  and not exists (
                    select 1 from product.essay_task_versions successor
                    where successor.task_key = task.task_key
                      and successor.version_no > task.version_no
                      and successor.state = 'published'
                  )
                  and not exists (
                    select 1 from product.essay_material_bundle_versions successor
                    where successor.task_version_id = material.task_version_id
                      and successor.version_no > material.version_no
                      and successor.completeness = 'complete'
                  )
                  and not exists (
                    select 1 from product.essay_rubric_versions successor
                    where successor.task_version_id = rubric.task_version_id
                      and successor.material_bundle_version_id =
                          rubric.material_bundle_version_id
                      and successor.version_no > rubric.version_no
                      and successor.state = 'published'
                  )
                  and exists (
                    select 1 from product.essay_material_segments segment
                    where segment.material_bundle_version_id =
                          material.material_bundle_version_id
                  )
                order by task.created_at desc, task.task_version_id
                limit 1
                """
                ),
                {
                    "source_task_id": source_task_version_id,
                    "source_material_id": source_material_bundle_version_id,
                },
            )
            .mappings()
            .one_or_none()
        )

    def recent_track_minutes(
        self,
        session: Session,
        *,
        user_id: str,
        before_date: date,
    ) -> dict[str, int]:
        rows = (
            session.execute(
                text(
                    """
                select task.track, coalesce(sum(execution.elapsed_minutes), 0) as minutes
                from learning.task_execution_versions execution
                join learning.daily_plan_tasks task
                  on task.task_id = execution.task_id
                 and task.user_id = execution.user_id
                where execution.user_id = cast(:user_id as uuid)
                  and execution.local_date >= :before_date - 7
                  and execution.local_date < :before_date
                  and not exists (
                    select 1 from learning.task_execution_versions successor
                    where successor.supersedes_execution_version_id =
                          execution.execution_version_id
                  )
                group by task.track
                """
                ),
                {"user_id": user_id, "before_date": before_date},
            )
            .mappings()
            .all()
        )
        return {str(row["track"]): int(row["minutes"]) for row in rows}

    def weekly_mock_due(
        self,
        session: Session,
        *,
        user_id: str,
        week_start: date,
    ) -> bool:
        count = session.execute(
            text(
                """
                select count(*)
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and exam_scope = 'guokao'
                  and track = 'xingce'
                  and status in ('submitted', 'reviewed')
                  and submitted_at >= :week_start
                  and submitted_at < :week_start + interval '7 days'
                """
            ),
            {"user_id": user_id, "week_start": week_start},
        ).scalar_one()
        return int(count) == 0

    def current_plan_for_fingerprint(
        self,
        session: Session,
        *,
        user_id: str,
        local_date: date,
        input_fingerprint: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select * from learning.daily_plan_versions
                where user_id = cast(:user_id as uuid)
                  and local_date = :local_date
                  and input_fingerprint = :input_fingerprint
                  and not exists (
                    select 1 from learning.daily_plan_versions successor
                    where successor.supersedes_plan_version_id =
                          daily_plan_versions.plan_version_id
                  )
                order by version_no desc
                limit 1
                """
                ),
                {
                    "user_id": user_id,
                    "local_date": local_date,
                    "input_fingerprint": input_fingerprint,
                },
            )
            .mappings()
            .one_or_none()
        )

    def current_plan(
        self,
        session: Session,
        *,
        user_id: str,
        local_date: date,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select plan.*
                from learning.daily_plan_versions plan
                where plan.user_id = cast(:user_id as uuid)
                  and plan.local_date = :local_date
                  and not exists (
                    select 1 from learning.daily_plan_versions successor
                    where successor.supersedes_plan_version_id = plan.plan_version_id
                  )
                order by plan.version_no desc
                limit 1
                """
                ),
                {"user_id": user_id, "local_date": local_date},
            )
            .mappings()
            .one_or_none()
        )

    def plan_for_capacity(
        self,
        session: Session,
        *,
        user_id: str,
        capacity_version_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select plan.*
                from learning.daily_plan_versions plan
                where plan.user_id = cast(:user_id as uuid)
                  and plan.capacity_version_id = cast(:capacity_version_id as uuid)
                order by plan.version_no desc
                limit 1
                """
                ),
                {"user_id": user_id, "capacity_version_id": capacity_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def capacity_version(
        self,
        session: Session,
        *,
        user_id: str,
        capacity_version_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select * from learning.daily_capacity_versions
                where user_id = cast(:user_id as uuid)
                  and capacity_version_id = cast(:capacity_version_id as uuid)
                """
                ),
                {"user_id": user_id, "capacity_version_id": capacity_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def plan_for_execution(
        self,
        session: Session,
        *,
        user_id: str,
        execution_version_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select plan.*
                from learning.daily_plan_versions plan
                where plan.user_id = cast(:user_id as uuid)
                  and plan.evidence_refs @> cast(:execution_ref as jsonb)
                order by plan.local_date desc, plan.version_no desc
                limit 1
                """
                ),
                {
                    "user_id": user_id,
                    "execution_ref": json.dumps(
                        [{"entityId": execution_version_id}],
                        sort_keys=True,
                    ),
                },
            )
            .mappings()
            .one_or_none()
        )

    def plan_tasks(
        self,
        session: Session,
        *,
        plan_version_id: str,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select
                      task.*,
                      execution.execution_version_id,
                      execution.task_id as execution_task_id,
                      execution.plan_version_id as execution_plan_version_id,
                      execution.local_date as execution_local_date,
                      execution.work_item_key as execution_work_item_key,
                      execution.version_no as execution_version_no,
                      execution.status as execution_status,
                      execution.elapsed_minutes as execution_elapsed_minutes,
                      execution.evidence_refs as execution_evidence_refs,
                      execution.abandonment_reason as execution_abandonment_reason,
                      execution.input_fingerprint as execution_input_fingerprint,
                      execution.supersedes_execution_version_id,
                      execution.supersedes_execution_version_no,
                      execution.recorded_at as execution_recorded_at
                    from learning.daily_plan_tasks task
                    left join lateral (
                      select item.*
                      from learning.task_execution_versions item
                      where item.work_item_key = task.work_item_key
                        and item.user_id = task.user_id
                        and not exists (
                          select 1 from learning.task_execution_versions successor
                          where successor.supersedes_execution_version_id =
                                item.execution_version_id
                        )
                      order by item.version_no desc
                      limit 1
                    ) execution on true
                    where task.plan_version_id = cast(:plan_version_id as uuid)
                    order by task.task_order
                    """
                ),
                {"plan_version_id": plan_version_id},
            )
            .mappings()
            .all()
        )

    def execution_inputs(
        self,
        session: Session,
        *,
        user_id: str,
        local_date: date,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select distinct on (execution.work_item_key)
                      execution.execution_version_id,
                      execution.candidate_key,
                      execution.work_item_key,
                      execution.status,
                      execution.elapsed_minutes,
                      execution.input_fingerprint,
                      execution.recorded_at
                    from learning.task_execution_versions execution
                    where execution.user_id = cast(:user_id as uuid)
                      and execution.local_date = :local_date
                      and not exists (
                        select 1 from learning.task_execution_versions successor
                        where successor.supersedes_execution_version_id =
                              execution.execution_version_id
                      )
                    order by execution.work_item_key,
                             execution.recorded_at desc,
                             execution.execution_version_id desc
                    """
                ),
                {"user_id": user_id, "local_date": local_date},
            )
            .mappings()
            .all()
        )

    def current_task_for_execution(
        self,
        session: Session,
        *,
        user_id: str,
        task_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select task.*,
                       plan.generated_at as plan_generated_at,
                       plan.valid_until as plan_valid_until
                from learning.daily_plan_tasks task
                join learning.daily_plan_versions plan
                  on plan.plan_version_id = task.plan_version_id
                where task.task_id = cast(:task_id as uuid)
                  and task.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1 from learning.daily_plan_versions successor
                    where successor.supersedes_plan_version_id = plan.plan_version_id
                  )
                for update of task
                """
                ),
                {"user_id": user_id, "task_id": task_id},
            )
            .mappings()
            .one_or_none()
        )

    def owned_task_for_execution(
        self,
        session: Session,
        *,
        user_id: str,
        task_id: str,
    ) -> RowMapping | None:
        """Load an immutable owned task when replaying a completed command."""
        return (
            session.execute(
                text(
                    """
                select task.*
                from learning.daily_plan_tasks task
                where task.task_id = cast(:task_id as uuid)
                  and task.user_id = cast(:user_id as uuid)
                for update of task
                """
                ),
                {"user_id": user_id, "task_id": task_id},
            )
            .mappings()
            .one_or_none()
        )

    def current_task_execution(
        self,
        session: Session,
        *,
        user_id: str,
        work_item_key: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select execution.*
                from learning.task_execution_versions execution
                where execution.user_id = cast(:user_id as uuid)
                  and execution.work_item_key = :work_item_key
                  and not exists (
                    select 1 from learning.task_execution_versions successor
                    where successor.supersedes_execution_version_id =
                          execution.execution_version_id
                  )
                for update of execution
                """
                ),
                {"user_id": user_id, "work_item_key": work_item_key},
            )
            .mappings()
            .one_or_none()
        )

    def task_execution_by_command(
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
                select * from learning.task_execution_versions
                where user_id = cast(:user_id as uuid)
                  and command_id = :command_id
                """
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )

    def execution_evidence_exists(
        self,
        session: Session,
        *,
        user_id: str,
        task: RowMapping,
        evidence_type: str,
        owner: str,
        entity_id: str,
        version_id: str | None,
        submitted_evidence: dict[str, str],
    ) -> bool:
        expected_owners = {
            "essay_answer_version": "essay_intelligence",
            "essay_review_version": "essay_intelligence",
            "essay_rewrite_version": "essay_intelligence",
            "essay_transfer_verification": "essay_intelligence",
            "essay_material_annotation": "essay_intelligence",
            "essay_point_extraction": "essay_intelligence",
            "essay_outline": "essay_intelligence",
            "essay_review_acknowledgement": "essay_intelligence",
            "reflection_artifact": "essay_intelligence",
        }
        if expected_owners.get(evidence_type) != owner:
            return False
        try:
            UUID(entity_id)
        except ValueError:
            return False
        if version_id != entity_id:
            return False

        def expected_ref(entity_type: str) -> str | None:
            return next(
                (
                    str(item["entityId"])
                    for item in task["evidence_refs"]
                    if item.get("entityType") == entity_type
                ),
                None,
            )

        expected_task_version = expected_ref("essay_task_version")
        expected_material_version = expected_ref("essay_material_bundle_version")
        expected_rubric_version = expected_ref("essay_rubric_version")
        expected_attempt_id = expected_ref("essay_attempt")
        expected_answer_version = expected_ref("essay_answer_version")
        expected_review_version = expected_ref("essay_review_version")
        expected_rewrite_version = expected_ref("essay_rewrite_version")
        expected_source_task_version = expected_ref("essay_source_task_version")
        expected_source_material_version = expected_ref("essay_source_material_bundle_version")
        expected_source_answer_version = expected_ref("essay_source_answer_version")
        expected_finding_decision = expected_ref("essay_finding_decision")
        expected_mastery_versions = [
            str(item["versionId"])
            for item in task["evidence_refs"]
            if item.get("entityType") == "essay_mastery_state_version" and item.get("versionId")
        ]
        expected_material_annotation = expected_ref("essay_material_annotation")
        expected_point_extraction = expected_ref("essay_point_extraction")
        artifact_types = {
            "essay_material_annotation": "material_annotation",
            "essay_point_extraction": "point_extraction",
            "essay_outline": "outline",
            "essay_review_acknowledgement": "review_acknowledgement",
            "reflection_artifact": "reflection",
        }
        artifact_type = artifact_types.get(evidence_type)
        if artifact_type is not None:
            if expected_task_version is None or expected_material_version is None:
                return False
            rubric_required = artifact_type != "material_annotation"
            if rubric_required and expected_rubric_version is None:
                return False
            review_required = artifact_type == "review_acknowledgement"
            if review_required and (
                expected_review_version is None
                or expected_attempt_id is None
                or expected_answer_version is None
            ):
                return False
            reflection_required = artifact_type == "reflection"
            point_dependency_required = artifact_type == "point_extraction"
            outline_dependency_required = artifact_type == "outline"
            if point_dependency_required and expected_material_annotation is None:
                return False
            if outline_dependency_required and expected_point_extraction is None:
                return False
            submitted_answer_version = submitted_evidence.get("essay_answer_version")
            if reflection_required and (
                task["stage"] != "essay_drafting" or submitted_answer_version is None
            ):
                return False
            return (
                session.execute(
                    text(
                        """
                        select 1
                        from learning.essay_learning_artifact_versions artifact
                        left join learning.essay_review_versions review
                          on review.review_version_id = artifact.review_version_id
                        left join learning.essay_answer_versions answer
                          on answer.answer_version_id = artifact.answer_version_id
                        left join learning.essay_attempts answer_attempt
                          on answer_attempt.attempt_id = answer.attempt_id
                         and answer_attempt.user_id = answer.user_id
                         and answer_attempt.task_version_id = answer.task_version_id
                        where artifact.artifact_version_id = cast(:entity_id as uuid)
                          and artifact.user_id = cast(:user_id as uuid)
                          and artifact.artifact_type = :artifact_type
                          and artifact.task_version_id = cast(:task_version_id as uuid)
                          and artifact.material_bundle_version_id =
                              cast(:material_bundle_version_id as uuid)
                          and (
                            :rubric_required = false
                            or artifact.rubric_version_id = cast(:rubric_version_id as uuid)
                          )
                          and (
                            :review_required = false
                            or (
                              artifact.review_version_id = cast(:review_version_id as uuid)
                              and review.user_id = cast(:user_id as uuid)
                              and review.attempt_id = cast(:attempt_id as uuid)
                              and review.answer_version_id =
                                  cast(:expected_answer_version_id as uuid)
                              and review.task_version_id = cast(:task_version_id as uuid)
                              and review.material_bundle_version_id =
                                  cast(:material_bundle_version_id as uuid)
                              and review.rubric_version_id =
                                  cast(:rubric_version_id as uuid)
                              and review.status = 'completed'
                              and review.grounding_contract_version = 'essay-grounding-v2'
                              and review.grounding_report->>'verified' = 'true'
                              and review.grounding_report->>'eligibleForLearning' = 'true'
                              and review.execution_fingerprint is not null
                              and not exists (
                                select 1 from learning.essay_review_versions successor
                                where successor.supersedes_review_version_id =
                                      review.review_version_id
                              )
                            )
                          )
                          and (
                            :reflection_required = false
                            or (
                              artifact.answer_version_id =
                                  cast(:submitted_answer_version_id as uuid)
                              and answer.user_id = cast(:user_id as uuid)
                              and (
                                cast(:attempt_id as uuid) is null
                                or answer.attempt_id = cast(:attempt_id as uuid)
                              )
                              and answer.task_version_id = cast(:task_version_id as uuid)
                              and answer_attempt.material_bundle_version_id =
                                  cast(:material_bundle_version_id as uuid)
                              and answer_attempt.rubric_version_id =
                                  cast(:rubric_version_id as uuid)
                              and artifact.created_at >= answer.created_at
                              and not exists (
                                select 1 from learning.essay_answer_versions successor
                                where successor.supersedes_answer_version_id =
                                      answer.answer_version_id
                              )
                            )
                          )
                          and (
                            :point_dependency_required = false
                            or artifact.payload->>'sourceMaterialAnnotationVersionId' =
                               :material_annotation_version_id
                          )
                          and (
                            :outline_dependency_required = false
                            or artifact.payload->>'sourcePointExtractionVersionId' =
                               :point_extraction_version_id
                          )
                          and artifact.created_at >= :task_created_at
                          and not exists (
                            select 1
                            from learning.essay_learning_artifact_versions successor
                            where successor.supersedes_artifact_version_id =
                                  artifact.artifact_version_id
                          )
                        """
                    ),
                    {
                        "entity_id": entity_id,
                        "user_id": user_id,
                        "artifact_type": artifact_type,
                        "task_version_id": expected_task_version,
                        "material_bundle_version_id": expected_material_version,
                        "rubric_version_id": expected_rubric_version,
                        "review_version_id": expected_review_version,
                        "attempt_id": expected_attempt_id,
                        "expected_answer_version_id": expected_answer_version,
                        "submitted_answer_version_id": submitted_answer_version,
                        "rubric_required": rubric_required,
                        "review_required": review_required,
                        "reflection_required": reflection_required,
                        "point_dependency_required": point_dependency_required,
                        "outline_dependency_required": outline_dependency_required,
                        "material_annotation_version_id": expected_material_annotation,
                        "point_extraction_version_id": expected_point_extraction,
                        "task_created_at": task["created_at"],
                    },
                ).scalar_one_or_none()
                is not None
            )
        if (
            expected_task_version is None
            or expected_material_version is None
            or expected_rubric_version is None
        ):
            return False
        if evidence_type == "essay_answer_version":
            if task["stage"] != "essay_drafting":
                return False
            statement = """
                select 1
                from learning.essay_answer_versions answer
                join learning.essay_attempts attempt
                  on attempt.attempt_id = answer.attempt_id
                 and attempt.user_id = answer.user_id
                 and attempt.task_version_id = answer.task_version_id
                where answer.answer_version_id = cast(:entity_id as uuid)
                  and answer.user_id = cast(:user_id as uuid)
                  and (
                    cast(:attempt_id as uuid) is null
                    or answer.attempt_id = cast(:attempt_id as uuid)
                  )
                  and answer.task_version_id = cast(:task_version_id as uuid)
                  and attempt.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and attempt.rubric_version_id = cast(:rubric_version_id as uuid)
                  and answer.created_at >= :task_created_at
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id = answer.answer_version_id
                  )
            """
            params = {"attempt_id": expected_attempt_id}
        elif evidence_type == "essay_review_version":
            if expected_attempt_id is None or expected_answer_version is None:
                return False
            statement = """
                select 1 from learning.essay_review_versions review
                where review.review_version_id = cast(:entity_id as uuid)
                  and review.user_id = cast(:user_id as uuid)
                  and review.attempt_id = cast(:attempt_id as uuid)
                  and review.answer_version_id = cast(:answer_version_id as uuid)
                  and review.task_version_id = cast(:task_version_id as uuid)
                  and review.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and review.rubric_version_id = cast(:rubric_version_id as uuid)
                  and review.status = 'completed'
                  and review.grounding_contract_version = 'essay-grounding-v2'
                  and review.grounding_report->>'verified' = 'true'
                  and review.grounding_report->>'eligibleForLearning' = 'true'
                  and review.execution_fingerprint is not null
                  and not exists (
                    select 1 from learning.essay_review_versions successor
                    where successor.supersedes_review_version_id = review.review_version_id
                  )
            """
            params = {
                "attempt_id": expected_attempt_id,
                "answer_version_id": expected_answer_version,
            }
        elif evidence_type == "essay_rewrite_version":
            if (
                expected_attempt_id is None
                or expected_source_answer_version is None
                or expected_review_version is None
                or expected_finding_decision is None
                or not expected_mastery_versions
            ):
                return False
            statement = """
                select 1
                from learning.essay_rewrite_versions rewrite
                join learning.essay_attempts attempt
                  on attempt.attempt_id = rewrite.attempt_id
                 and attempt.user_id = rewrite.user_id
                 and attempt.task_version_id = rewrite.task_version_id
                where rewrite.rewrite_version_id = cast(:entity_id as uuid)
                  and rewrite.user_id = cast(:user_id as uuid)
                  and rewrite.attempt_id = cast(:attempt_id as uuid)
                  and rewrite.task_version_id = cast(:task_version_id as uuid)
                  and rewrite.base_review_version_id = cast(:review_version_id as uuid)
                  and rewrite.base_answer_version_id = cast(:answer_version_id as uuid)
                  and rewrite.finding_decision_artifact_version_id =
                      cast(:finding_decision_id as uuid)
                  and rewrite.writer_contract_version = 'essay-rewrite-writer@v2'
                  and rewrite.writer_policy_version = 'essay-mastery@v1'
                  and attempt.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and attempt.rubric_version_id = cast(:rubric_version_id as uuid)
                  and rewrite.created_at >= :task_created_at
                  and not exists (
                    select 1 from learning.essay_rewrite_versions successor
                    where successor.supersedes_rewrite_version_id =
                          rewrite.rewrite_version_id
                  )
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id =
                          rewrite.rewritten_answer_version_id
                  )
            """
            params = {
                "attempt_id": expected_attempt_id,
                "review_version_id": expected_review_version,
                "answer_version_id": expected_source_answer_version,
                "finding_decision_id": expected_finding_decision,
            }
        elif evidence_type == "essay_transfer_verification":
            if (
                expected_attempt_id is None
                or expected_review_version is None
                or expected_rewrite_version is None
                or expected_source_task_version is None
                or expected_source_material_version is None
                or expected_finding_decision is None
                or not expected_mastery_versions
            ):
                return False
            statement = """
                select 1
                from learning.essay_transfer_verification_versions transfer
                join learning.essay_rewrite_versions source_rewrite
                  on source_rewrite.rewrite_version_id = cast(:rewrite_version_id as uuid)
                 and transfer.source_rewrite_version_id =
                     source_rewrite.rewrite_version_id
                 and source_rewrite.user_id = transfer.user_id
                 and source_rewrite.attempt_id = transfer.source_attempt_id
                 and source_rewrite.base_review_version_id =
                     transfer.source_review_version_id
                join learning.essay_attempts transfer_attempt
                  on transfer_attempt.attempt_id = transfer.transfer_attempt_id
                 and transfer_attempt.user_id = transfer.user_id
                 and transfer_attempt.task_version_id = transfer.transfer_task_version_id
                where transfer.verification_version_id = cast(:entity_id as uuid)
                  and transfer.user_id = cast(:user_id as uuid)
                  and transfer.source_attempt_id = cast(:attempt_id as uuid)
                  and transfer.source_review_version_id = cast(:review_version_id as uuid)
                  and transfer.finding_decision_artifact_version_id =
                      cast(:finding_decision_id as uuid)
                  and transfer.writer_contract_version = 'essay-transfer-writer@v2'
                  and transfer.writer_policy_version = 'essay-mastery@v1'
                  and source_rewrite.task_version_id =
                      cast(:source_task_version_id as uuid)
                  and source_rewrite.writer_contract_version =
                      'essay-rewrite-writer@v2'
                  and exists (
                    select 1 from learning.essay_attempts source_attempt
                    where source_attempt.attempt_id = transfer.source_attempt_id
                      and source_attempt.user_id = transfer.user_id
                      and source_attempt.task_version_id =
                          cast(:source_task_version_id as uuid)
                      and source_attempt.material_bundle_version_id =
                          cast(:source_material_version_id as uuid)
                  )
                  and transfer.transfer_task_version_id = cast(:task_version_id as uuid)
                  and transfer.fresh_material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and transfer_attempt.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and transfer_attempt.rubric_version_id =
                      cast(:rubric_version_id as uuid)
                  and transfer.transfer_task_version_id <>
                      cast(:source_task_version_id as uuid)
                  and transfer.fresh_material_bundle_version_id <>
                      cast(:source_material_version_id as uuid)
                  and transfer.generated_at >= source_rewrite.created_at
                  and not exists (
                    select 1 from learning.essay_rewrite_versions successor
                    where successor.supersedes_rewrite_version_id =
                          source_rewrite.rewrite_version_id
                  )
                  and not exists (
                    select 1
                    from learning.essay_transfer_verification_versions successor
                    where successor.supersedes_verification_version_id =
                          transfer.verification_version_id
                  )
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id =
                          transfer.transfer_answer_version_id
                  )
            """
            params = {
                "attempt_id": expected_attempt_id,
                "review_version_id": expected_review_version,
                "rewrite_version_id": expected_rewrite_version,
                "finding_decision_id": expected_finding_decision,
                "source_task_version_id": expected_source_task_version,
                "source_material_version_id": expected_source_material_version,
            }
        else:
            return False
        canonical_evidence = (
            session.execute(
                text(statement),
                {
                    "entity_id": entity_id,
                    "user_id": user_id,
                    "task_version_id": expected_task_version,
                    "material_bundle_version_id": expected_material_version,
                    "rubric_version_id": expected_rubric_version,
                    "task_created_at": task["created_at"],
                    **params,
                },
            ).scalar_one_or_none()
            is not None
        )
        if not canonical_evidence:
            return False
        if evidence_type not in {
            "essay_rewrite_version",
            "essay_transfer_verification",
        }:
            return True
        link_column = (
            "rewrite_version_id"
            if evidence_type == "essay_rewrite_version"
            else "transfer_verification_version_id"
        )
        for mastery_version_id in expected_mastery_versions:
            linked = session.execute(
                text(
                    f"""
                    select 1
                    from learning.essay_mastery_state_versions source
                    join learning.essay_mastery_state_versions successor
                      on successor.supersedes_mastery_version_id =
                         source.mastery_version_id
                     and successor.mastery_id = source.mastery_id
                     and successor.user_id = source.user_id
                    where source.mastery_version_id = cast(:mastery_version_id as uuid)
                      and source.user_id = cast(:user_id as uuid)
                      and source.finding_decision_artifact_version_id =
                          cast(:finding_decision_id as uuid)
                      and successor.{link_column} = cast(:entity_id as uuid)
                      and successor.policy_version = 'essay-mastery@v1'
                    """
                ),
                {
                    "mastery_version_id": mastery_version_id,
                    "user_id": user_id,
                    "finding_decision_id": expected_finding_decision,
                    "entity_id": entity_id,
                },
            ).scalar_one_or_none()
            if linked is None:
                return False
        return True

    def insert_task_execution(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.task_execution_versions (
                  execution_version_id, command_id, user_id, local_date,
                  task_id, plan_version_id, plan_version_no, candidate_key,
                  work_item_key, version_no, status, elapsed_minutes, evidence_refs,
                  abandonment_reason, input_fingerprint,
                  supersedes_execution_version_id,
                  supersedes_execution_version_no, recorded_at
                ) values (
                  cast(:execution_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date,
                  cast(:task_id as uuid), cast(:plan_version_id as uuid),
                  :plan_version_no, :candidate_key, :work_item_key, :version_no, :status,
                  :elapsed_minutes, cast(:evidence_refs as jsonb),
                  :abandonment_reason, :input_fingerprint,
                  cast(:supersedes_execution_version_id as uuid),
                  :supersedes_execution_version_no, :recorded_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def insert_plan(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.daily_plan_versions (
                  plan_version_id, command_id, user_id, local_date, version_no,
                  capacity_version_id, weekly_preference_version_id, day_mode,
                  headline, rationale, goal_gap, scheduled_minutes,
                  due_reflection_count, due_verification_count, track_explanation,
                  input_versions, evidence_refs, invalidation_conditions,
                  replan_reason_codes, plan_basis, contract_version,
                  algorithm_version, input_fingerprint, supersedes_plan_version_id,
                  generated_at, valid_until
                ) values (
                  cast(:plan_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date, :version_no,
                  cast(:capacity_version_id as uuid),
                  cast(:weekly_preference_version_id as uuid), :day_mode,
                  :headline, :rationale, cast(:goal_gap as jsonb),
                  :scheduled_minutes, :due_reflection_count,
                  :due_verification_count, cast(:track_explanation as jsonb),
                  cast(:input_versions as jsonb), cast(:evidence_refs as jsonb),
                  cast(:invalidation_conditions as jsonb),
                  cast(:replan_reason_codes as jsonb), 'evidence_priority_v2',
                  :contract_version, :algorithm_version, :input_fingerprint,
                  cast(:supersedes_plan_version_id as uuid), :generated_at,
                  :valid_until
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def insert_plan_task(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.daily_plan_tasks (
                  task_id, plan_version_id, user_id, local_date, plan_version_no,
                  candidate_key, work_item_key, task_order, track, stage, kind, title, reason,
                  estimated_minutes, mandatory, start_url, priority_score,
                  goal_gain, ability_gain, verification_urgency, time_fit,
                  load_penalty, track_balance_bonus, evidence_refs,
                  completion_contract, input_fingerprint, created_at
                ) values (
                  cast(:task_id as uuid), cast(:plan_version_id as uuid),
                  cast(:user_id as uuid), :local_date, :plan_version_no,
                  :candidate_key, :work_item_key, :task_order, :track, :stage, :kind, :title, :reason,
                  :estimated_minutes, :mandatory, :start_url, :priority_score,
                  :goal_gain, :ability_gain, :verification_urgency, :time_fit,
                  :load_penalty, :track_balance_bonus,
                  cast(:evidence_refs as jsonb), cast(:completion_contract as jsonb),
                  :input_fingerprint, :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )


def _preference_snapshot(row: RowMapping) -> PreferenceSnapshot:
    return PreferenceSnapshot(
        preference_version_id=str(row["preference_version_id"]),
        version_no=int(row["version_no"]),
        timezone=str(row["timezone"]),
        weekday_minutes=int(row["weekday_minutes"]),
        weekend_minutes=int(row["weekend_minutes"]),
        long_block_minutes=int(row["long_block_minutes"]),
        fragment_minutes=int(row["fragment_minutes"]),
        regular_rest_weekday=int(row["regular_rest_weekday"]),
        regular_mock_weekday=int(row["regular_mock_weekday"]),
        confirmed=bool(row["confirmed"]),
        input_fingerprint=str(row["input_fingerprint"]),
    )


def _capacity_snapshot(row: RowMapping) -> CapacitySnapshot:
    return CapacitySnapshot(
        capacity_version_id=str(row["capacity_version_id"]),
        version_no=int(row["version_no"]),
        available_minutes=int(row["available_minutes"]),
        energy=int(row["energy_level"]),
        stress=int(row["stress_level"]),
        confidence_level=int(row["confidence_level"]),
        confirmed=bool(row["confirmed"]),
        source_type=str(row["source_type"]),
        input_fingerprint=str(row["input_fingerprint"]),
    )


def _review_is_learning_eligible(review: Any) -> bool:
    if review is None or review["review_status"] != "completed":
        return False
    report = review["grounding_report"]
    return (
        review["grounding_contract_version"] == "essay-grounding-v2"
        and isinstance(report, dict)
        and report.get("verified") is True
        and report.get("eligibleForLearning") is True
    )


def _weakest_dimension(findings: Any, rewrite_plan: Any = None) -> str | None:
    labels = {
        "point_finding": "找点与要点完整度",
        "abstraction": "概括抽象度",
        "structure": "结构层次",
        "expression": "语言表达",
        "format": "格式规范",
        "unsupported_claim": "材料依据",
    }
    key = _weakest_dimension_key(findings, rewrite_plan)
    return labels.get(key) if key is not None else None


def _weakest_dimension_key(findings: Any, rewrite_plan: Any = None) -> str | None:
    if not isinstance(findings, list):
        return None
    recognized_types = {
        "point_finding",
        "abstraction",
        "structure",
        "expression",
        "format",
        "unsupported_claim",
    }
    recognized = [
        finding
        for finding in findings
        if isinstance(finding, dict) and finding.get("type") in recognized_types
    ]
    by_id = {
        str(finding["findingId"]): finding
        for finding in recognized
        if isinstance(finding.get("findingId"), str)
    }
    priority_ids = (
        rewrite_plan.get("priorityFindingIds") if isinstance(rewrite_plan, dict) else None
    )
    if isinstance(priority_ids, list):
        for finding_id in priority_ids:
            finding = by_id.get(str(finding_id))
            if finding is not None:
                return str(finding["type"])
    severity_order = {"high": 0, "medium": 1, "low": 2}
    for finding in sorted(
        recognized,
        key=lambda item: severity_order.get(str(item.get("severity")), 3),
    ):
        if isinstance(finding, dict) and finding.get("type") in recognized_types:
            return str(finding["type"])
    return None


def _fingerprint(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _essay_closure_gap(reason: str) -> EssayPlanningSnapshot:
    return EssayPlanningSnapshot(
        "closure_unavailable",
        None,
        pending_reason=reason,
        closure_readiness="unavailable",
        closure_unavailable_reason=reason,
    )


def _essay_closure_context_gap(
    context: RowMapping,
    *,
    decision_id: str,
) -> str | None:
    required = {
        "source attempt": context["source_attempt_id"],
        "source task": context["source_task_version_id"],
        "source material": context["source_material_bundle_version_id"],
        "source rubric": context["source_rubric_version_id"],
        "source answer": context["source_answer_version_id"],
        "current answer": context["current_answer_version_id"],
        "source review": context["source_review_version_id"],
        "finding decision": context["decision_artifact_version_id"],
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return "申论闭环缺少 canonical " + "、".join(missing) + "，不可生成可写任务。"
    if (
        context["source_task_state"] != "published"
        or context["source_material_completeness"] != "complete"
        or context["source_rubric_state"] != "published"
    ):
        return "申论掌握度来源题目、材料或评分规程已不再可消费。"
    grounding = context["grounding_report"]
    if (
        context["source_review_status"] != "completed"
        or context["grounding_contract_version"] != "essay-grounding-v2"
        or not isinstance(grounding, dict)
        or grounding.get("verified") is not True
        or grounding.get("eligibleForLearning") is not True
        or context["execution_fingerprint"] is None
    ):
        return "申论掌握度来源评阅未通过 canonical grounded learning 门禁。"
    if (
        str(context["decision_artifact_version_id"]) != decision_id
        or context["decision_artifact_type"] != "review_acknowledgement"
        or str(context["decision_review_version_id"]) != str(context["source_review_version_id"])
        or str(context["decision_task_version_id"]) != str(context["source_task_version_id"])
        or str(context["decision_material_bundle_version_id"])
        != str(context["source_material_bundle_version_id"])
        or str(context["decision_rubric_version_id"]) != str(context["source_rubric_version_id"])
    ):
        return "申论掌握度 head 与 finding decision 的精确来源不一致。"
    return None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
