from __future__ import annotations

from datetime import date
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class PlanStorageRepositoryMixin:
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
                  completion_contract, selection_intent, input_fingerprint, created_at
                ) values (
                  cast(:task_id as uuid), cast(:plan_version_id as uuid),
                  cast(:user_id as uuid), :local_date, :plan_version_no,
                  :candidate_key, :work_item_key, :task_order, :track, :stage, :kind, :title, :reason,
                  :estimated_minutes, :mandatory, :start_url, :priority_score,
                  :goal_gain, :ability_gain, :verification_urgency, :time_fit,
                  :load_penalty, :track_balance_bonus,
                  cast(:evidence_refs as jsonb), cast(:completion_contract as jsonb),
                  cast(:selection_intent as jsonb), :input_fingerprint, :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )
