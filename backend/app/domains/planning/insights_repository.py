from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class PlanningInsightsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def preference_by_id(self, *, user_id: str, preference_version_id: str) -> RowMapping:
        return (
            self._session.execute(
                text(
                    """
                    select * from learning.weekly_study_preference_versions
                    where user_id = cast(:user_id as uuid)
                      and preference_version_id = cast(:preference_version_id as uuid)
                    """
                ),
                {"user_id": user_id, "preference_version_id": preference_version_id},
            )
            .mappings()
            .one()
        )

    def current_weekly_plan(self, *, user_id: str, week_start: date) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select plan.*
                    from learning.weekly_plan_versions plan
                    where plan.user_id = cast(:user_id as uuid)
                      and plan.week_start = :week_start
                      and not exists (
                        select 1 from learning.weekly_plan_versions successor
                        where successor.supersedes_weekly_plan_version_id =
                              plan.weekly_plan_version_id
                      )
                    order by plan.version_no desc
                    limit 1
                    """
                ),
                {"user_id": user_id, "week_start": week_start},
            )
            .mappings()
            .one_or_none()
        )

    def daily_settlement_task_facts(
        self,
        *,
        user_id: str,
        local_date: date,
        current_plan_version_id: str,
    ) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    with latest_executions as (
                      select execution.*
                      from learning.task_execution_versions execution
                      where execution.user_id = cast(:user_id as uuid)
                        and execution.local_date = :local_date
                        and not exists (
                          select 1
                          from learning.task_execution_versions successor
                          where successor.supersedes_execution_version_id =
                                execution.execution_version_id
                        )
                    ),
                    executed_tasks as (
                      select
                        task.task_id,
                        task.plan_version_id,
                        task.plan_version_no,
                        task.task_order,
                        task.work_item_key,
                        task.track,
                        task.title,
                        task.estimated_minutes,
                        execution.status,
                        execution.elapsed_minutes,
                        execution.evidence_refs
                      from latest_executions execution
                      join learning.daily_plan_tasks task
                        on task.task_id = execution.task_id
                       and task.plan_version_id = execution.plan_version_id
                       and task.user_id = execution.user_id
                       and task.local_date = execution.local_date
                       and task.plan_version_no = execution.plan_version_no
                       and task.candidate_key = execution.candidate_key
                       and task.work_item_key = execution.work_item_key
                    ),
                    current_tasks_without_execution as (
                      select
                        task.task_id,
                        task.plan_version_id,
                        task.plan_version_no,
                        task.task_order,
                        task.work_item_key,
                        task.track,
                        task.title,
                        task.estimated_minutes,
                        'ready'::text as status,
                        0::integer as elapsed_minutes,
                        '[]'::jsonb as evidence_refs
                      from learning.daily_plan_tasks task
                      where task.user_id = cast(:user_id as uuid)
                        and task.local_date = :local_date
                        and task.plan_version_id =
                            cast(:current_plan_version_id as uuid)
                        and not exists (
                          select 1
                          from latest_executions execution
                          where execution.work_item_key = task.work_item_key
                        )
                    ),
                    settlement_facts as (
                      select * from executed_tasks
                      union all
                      select * from current_tasks_without_execution
                    )
                    select *
                    from settlement_facts
                    order by plan_version_no, task_order, task_id
                    """
                ),
                {
                    "user_id": user_id,
                    "local_date": local_date,
                    "current_plan_version_id": current_plan_version_id,
                },
            ).mappings()
        )

    def daily_plan_summaries(
        self,
        *,
        user_id: str,
        week_start: date,
    ) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    with latest_plan as (
                      select distinct on (plan.local_date) plan.*
                      from learning.daily_plan_versions plan
                      where plan.user_id = cast(:user_id as uuid)
                        and plan.local_date between :week_start and :week_start + 6
                      order by plan.local_date, plan.version_no desc
                    )
                    select
                      plan.local_date, plan.plan_version_id, plan.version_no,
                      plan.day_mode, plan.scheduled_minutes, plan.input_fingerprint,
                      coalesce(task_summary.task_count, 0)::int as task_count,
                      coalesce(task_summary.completed_count, 0)::int as completed_count,
                      coalesce(task_summary.execution_fingerprint, '')
                        as execution_fingerprint
                    from latest_plan plan
                    left join lateral (
                      select
                        count(*)::int as task_count,
                        count(*) filter (where execution.status = 'complete')::int
                          as completed_count,
                        string_agg(
                          coalesce(execution.execution_version_id::text, task.task_id::text),
                          ',' order by task.task_order
                        ) as execution_fingerprint
                      from learning.daily_plan_tasks task
                      left join lateral (
                        select item.*
                        from learning.task_execution_versions item
                        where item.user_id = task.user_id
                          and item.local_date = task.local_date
                          and item.work_item_key = task.work_item_key
                          and not exists (
                            select 1 from learning.task_execution_versions successor
                            where successor.supersedes_execution_version_id =
                                  item.execution_version_id
                          )
                        order by item.version_no desc limit 1
                      ) execution on true
                      where task.plan_version_id = plan.plan_version_id
                    ) task_summary on true
                    order by plan.local_date
                    """
                ),
                {"user_id": user_id, "week_start": week_start},
            ).mappings()
        )

    def active_strategy_versions(self, *, user_id: str) -> list[str]:
        return [
            str(value)
            for value in self._session.execute(
                text(
                    """
                    select decision.decision_version_id
                    from learning.strategy_decision_versions decision
                    where decision.user_id = cast(:user_id as uuid)
                      and decision.status = 'active'
                      and decision.effective_until > now()
                      and decision.review_at > now()
                      and not exists (
                        select 1 from learning.strategy_decision_versions successor
                        where successor.supersedes_decision_version_id =
                              decision.decision_version_id
                      )
                    order by decision.scope_type, decision.scope_key,
                             decision.decision_version_id
                    """
                ),
                {"user_id": user_id},
            ).scalars()
        ]

    def insert_weekly_plan(self, values: dict[str, Any]) -> RowMapping:
        return (
            self._session.execute(
                text(
                    """
                    insert into learning.weekly_plan_versions (
                      weekly_plan_version_id, command_id, user_id, week_start, version_no,
                      weekly_preference_version_id, rhythm, days, evidence_refs,
                      input_versions, replan_reason_codes, algorithm_version,
                      input_fingerprint, supersedes_weekly_plan_version_id,
                      generated_at, valid_until
                    ) values (
                      cast(:weekly_plan_version_id as uuid), :command_id,
                      cast(:user_id as uuid), :week_start, :version_no,
                      cast(:weekly_preference_version_id as uuid), '5+1+1',
                      cast(:days as jsonb), cast(:evidence_refs as jsonb),
                      cast(:input_versions as jsonb), cast(:replan_reason_codes as jsonb),
                      'weekly-rhythm@v1', :input_fingerprint,
                      cast(:supersedes_weekly_plan_version_id as uuid),
                      :generated_at, :valid_until
                    ) returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def plan_history(self, *, user_id: str, limit: int = 50) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    select
                      plan.*,
                      (select count(*) from learning.daily_plan_tasks task
                        where task.plan_version_id = plan.plan_version_id)::int as task_count
                    from learning.daily_plan_versions plan
                    where plan.user_id = cast(:user_id as uuid)
                    order by plan.generated_at desc, plan.version_no desc
                    limit :limit
                    """
                ),
                {"user_id": user_id, "limit": limit},
            ).mappings()
        )


def json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
