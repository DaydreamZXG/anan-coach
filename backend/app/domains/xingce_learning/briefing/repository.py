from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class XingceBriefingRepository:
    def current_task(
        self,
        session: Session,
        *,
        user_id: str,
        plan_task_id: str,
        now: datetime,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select task.*, plan.valid_until
                    from learning.daily_plan_tasks task
                    join learning.daily_plan_versions plan
                      on plan.plan_version_id = task.plan_version_id
                     and plan.user_id = task.user_id
                     and plan.local_date = task.local_date
                     and plan.version_no = task.plan_version_no
                    where task.task_id = cast(:task_id as uuid)
                      and task.user_id = cast(:user_id as uuid)
                      and task.track = 'xingce'
                      and task.stage in (
                        'objective_baseline', 'objective_practice', 'objective_verification'
                      )
                      and plan.valid_until > :now
                      and not exists (
                        select 1 from learning.daily_plan_versions newer
                        where newer.user_id = plan.user_id
                          and newer.local_date = plan.local_date
                          and newer.version_no > plan.version_no
                      )
                      and not exists (
                        select 1 from learning.task_execution_versions execution
                        where execution.user_id = task.user_id
                          and execution.work_item_key = task.work_item_key
                          and execution.status in ('complete', 'abandoned')
                          and not exists (
                            select 1 from learning.task_execution_versions successor
                            where successor.supersedes_execution_version_id =
                                  execution.execution_version_id
                          )
                      )
                    """
                ),
                {"task_id": plan_task_id, "user_id": user_id, "now": now},
            )
            .mappings()
            .one_or_none()
        )
    def mastery(
        self,
        session: Session,
        *,
        user_id: str,
        mastery_version_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select * from learning.xingce_mastery_state_versions
                    where user_id = cast(:user_id as uuid)
                      and mastery_version_id = cast(:mastery_version_id as uuid)
                    """
                ),
                {"user_id": user_id, "mastery_version_id": mastery_version_id},
            )
            .mappings()
            .one_or_none()
        )
