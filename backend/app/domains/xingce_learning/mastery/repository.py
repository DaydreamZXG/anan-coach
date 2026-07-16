from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class XingceMasteryRepository:
    def queue_counts(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    with current_reviews as (
                      select review.*
                      from learning.xingce_attempt_review_versions review
                      where review.user_id = cast(:user_id as uuid)
                        and not exists (
                          select 1
                          from learning.xingce_attempt_review_versions successor
                          where successor.supersedes_review_version_id = review.review_version_id
                        )
                    ), current_mastery as (
                      select mastery.*,
                             exists (
                               select 1
                               from jsonb_array_elements_text(mastery.used_question_ids) used(question_id)
                               left join product.questions question
                                 on question.question_id = used.question_id
                               where question.question_id is null
                                  or question.status not in ('active', 'canonical')
                             ) as invalidated
                      from learning.xingce_mastery_state_versions mastery
                      where mastery.user_id = cast(:user_id as uuid)
                        and not exists (
                          select 1
                          from learning.xingce_mastery_state_versions successor
                          where successor.supersedes_mastery_version_id = mastery.mastery_version_id
                        )
                    )
                    select
                      (select count(*) from current_reviews where state = 'reflection_due')
                        as pending_reflection_count,
                      (select count(*) from current_mastery
                        where not invalidated and due_at is not null and due_at <= :now)
                        as due_verification_count,
                      (select count(*) from current_mastery
                        where not invalidated and state = 'stable_mastered')
                        as stable_mastery_count,
                      (select count(*) from current_mastery where invalidated)
                        as invalidated_count
                    """
                ),
                {"user_id": user_id, "now": now},
            )
            .mappings()
            .one()
        )

    def queue_page(
        self,
        session: Session,
        *,
        user_id: str,
        bucket: str,
        now: datetime,
        cursor_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> list[RowMapping]:
        if bucket == "pending_reflection":
            return self._reflection_queue_page(
                session,
                user_id=user_id,
                cursor_at=cursor_at,
                cursor_id=cursor_id,
                limit=limit,
            )
        return self._mastery_queue_page(
            session,
            user_id=user_id,
            bucket=bucket,
            now=now,
            cursor_at=cursor_at,
            cursor_id=cursor_id,
            limit=limit,
        )

    def _reflection_queue_page(
        self,
        session: Session,
        *,
        user_id: str,
        cursor_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select review.review_version_id as item_id,
                           review.review_id, review.session_id, review.state,
                           review.created_at,
                           count(assessment.assessment_id)::integer as question_count,
                           coalesce(min(question.module_name), '未分类') as module_name,
                           jsonb_build_array(
                             'task-session:' || task.session_id::text || ':' || task.revision::text,
                             'review-version:' || review.review_version_id::text
                           ) as evidence_refs
                    from learning.xingce_attempt_review_versions review
                    join learning.xingce_task_sessions task
                      on task.session_id = review.session_id and task.user_id = review.user_id
                    join learning.xingce_assessments assessment
                      on assessment.session_id = review.session_id and assessment.user_id = review.user_id
                    left join product.questions question on question.question_id = assessment.question_id
                    where review.user_id = cast(:user_id as uuid)
                      and review.state = 'reflection_due'
                      and not exists (
                        select 1 from learning.xingce_attempt_review_versions successor
                        where successor.supersedes_review_version_id = review.review_version_id
                      )
                      and (
                        cast(:cursor_at as timestamptz) is null
                        or (review.created_at, review.review_version_id) >
                           (cast(:cursor_at as timestamptz), cast(:cursor_id as uuid))
                      )
                    group by review.review_version_id, review.review_id, review.session_id,
                             review.state, review.created_at, task.session_id, task.revision
                    order by review.created_at, review.review_version_id
                    limit :limit
                    """
                ),
                {
                    "user_id": user_id,
                    "cursor_at": cursor_at,
                    "cursor_id": cursor_id,
                    "limit": limit,
                },
            ).mappings()
        )

    def _mastery_queue_page(
        self,
        session: Session,
        *,
        user_id: str,
        bucket: str,
        now: datetime,
        cursor_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> list[RowMapping]:
        bucket_filter = {
            "due_verification": "not current.invalidated and current.due_at is not null and current.due_at <= :now",
            "stable_mastery": "not current.invalidated and current.state = 'stable_mastered'",
            "invalidated": "current.invalidated",
        }[bucket]
        order_column = "current.due_at" if bucket == "due_verification" else "current.created_at"
        direction = "asc" if bucket == "due_verification" else "desc"
        cursor_operator = ">" if direction == "asc" else "<"
        return list(
            session.execute(
                text(
                    f"""
                    with current as (
                      select mastery.*,
                             exists (
                               select 1
                               from jsonb_array_elements_text(mastery.used_question_ids) used(question_id)
                               left join product.questions question
                                 on question.question_id = used.question_id
                               where question.question_id is null
                                  or question.status not in ('active', 'canonical')
                             ) as invalidated
                      from learning.xingce_mastery_state_versions mastery
                      where mastery.user_id = cast(:user_id as uuid)
                        and not exists (
                          select 1 from learning.xingce_mastery_state_versions successor
                          where successor.supersedes_mastery_version_id = mastery.mastery_version_id
                        )
                    )
                    select current.mastery_version_id as item_id,
                           current.mastery_id, current.mastery_version_id,
                           current.state, current.verification_stage,
                           current.module_name, current.due_at, current.created_at,
                           jsonb_array_length(current.used_question_ids)::integer as question_count,
                           current.evidence_refs,
                           plan_task.task_id as plan_task_id
                    from current
                    left join lateral (
                      select task.task_id
                      from learning.daily_plan_tasks task
                      join learning.daily_plan_versions plan
                        on plan.plan_version_id = task.plan_version_id
                       and plan.user_id = task.user_id
                      where task.user_id = current.user_id
                        and task.stage = 'objective_verification'
                        and plan.valid_until > :now
                        and not exists (
                          select 1 from learning.daily_plan_versions successor
                          where successor.supersedes_plan_version_id = plan.plan_version_id
                        )
                        and task.evidence_refs @> jsonb_build_array(jsonb_build_object(
                          'entityType', 'xingce_mastery_state_version',
                          'entityId', current.mastery_id::text,
                          'versionId', current.mastery_version_id::text,
                          'role', 'due_verification'
                        ))
                      order by plan.generated_at desc, task.task_order
                      limit 1
                    ) plan_task on true
                    where {bucket_filter}
                      and (
                        cast(:cursor_at as timestamptz) is null
                        or ({order_column}, current.mastery_version_id) {cursor_operator}
                           (cast(:cursor_at as timestamptz), cast(:cursor_id as uuid))
                      )
                    order by {order_column} {direction}, current.mastery_version_id {direction}
                    limit :limit
                    """
                ),
                {
                    "user_id": user_id,
                    "now": now,
                    "cursor_at": cursor_at,
                    "cursor_id": cursor_id,
                    "limit": limit,
                },
            ).mappings()
        )

    def current_due_version(
        self,
        session: Session,
        *,
        user_id: str,
        mastery_version_id: str,
        now: datetime,
        for_update: bool = False,
    ) -> RowMapping | None:
        suffix = " for update of current" if for_update else ""
        return (
            session.execute(
                text(
                    """
                    select current.*
                    from learning.xingce_mastery_state_versions current
                    where current.user_id = cast(:user_id as uuid)
                      and current.mastery_version_id = cast(:mastery_version_id as uuid)
                      and current.due_at is not null
                      and current.due_at <= :now
                      and not exists (
                        select 1
                        from jsonb_array_elements_text(current.used_question_ids) used(question_id)
                        left join product.questions question
                          on question.question_id = used.question_id
                        where question.question_id is null
                           or question.status not in ('active', 'canonical')
                      )
                      and not exists (
                        select 1
                        from learning.xingce_mastery_state_versions successor
                        where successor.supersedes_mastery_version_id =
                              current.mastery_version_id
                      )
                    """
                    + suffix
                ),
                {
                    "user_id": user_id,
                    "mastery_version_id": mastery_version_id,
                    "now": now,
                },
            )
            .mappings()
            .one_or_none()
        )

    def insert_version(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into learning.xingce_mastery_state_versions (
                      mastery_version_id, mastery_id, command_id, user_id,
                      version_no, state, verification_stage, due_at, rating,
                      module_name, scheduler_card, scheduler_review_log,
                      scheduler_parameters,
                      scheduler_algorithm_version, scheduler_policy_version,
                      used_question_ids, source_review_id,
                      source_review_version_id, source_session_id, evidence_refs,
                      supersedes_mastery_version_id, created_at
                    ) values (
                      cast(:mastery_version_id as uuid), cast(:mastery_id as uuid),
                      :command_id, cast(:user_id as uuid), :version_no, :state,
                      :verification_stage, :due_at, :rating, :module_name,
                      cast(:scheduler_card as jsonb),
                      cast(:scheduler_review_log as jsonb),
                      cast(:scheduler_parameters as jsonb),
                      :scheduler_algorithm_version, :scheduler_policy_version,
                      cast(:used_question_ids as jsonb),
                      cast(:source_review_id as uuid),
                      cast(:source_review_version_id as uuid),
                      cast(:source_session_id as uuid),
                      cast(:evidence_refs as jsonb),
                      cast(:supersedes_mastery_version_id as uuid), :created_at
                    ) returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def verification_evidence(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
        reflection_version_id: str,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select assessment.result_code,
                           coalesce(
                             assessment.result_snapshot->>'confidenceEvidence',
                             answer.payload->>'confidence'
                           ) as confidence,
                           coalesce(
                             assessment.result_snapshot->>'disposition',
                             'answered'
                           ) as disposition,
                           exists (
                             select 1
                             from learning.xingce_reflection_versions reflection,
                                  jsonb_array_elements(
                                    coalesce(
                                      reflection.payload->'itemInterpretations',
                                      '[]'::jsonb
                                    )
                                  ) interpretation
                             where reflection.reflection_version_id =
                                   cast(:reflection_version_id as uuid)
                               and reflection.user_id = assessment.user_id
                               and reflection.session_id = assessment.session_id
                               and interpretation->>'questionId' = assessment.question_id
                               and interpretation->>'interpretation' =
                                   'method_correct_calculation_error'
                           ) as method_correct_calculation_error
                    from learning.xingce_assessments assessment
                    join learning.xingce_answer_versions answer
                      on answer.answer_version_id = assessment.answer_version_id
                     and answer.user_id = assessment.user_id
                     and answer.session_id = assessment.session_id
                     and answer.question_id = assessment.question_id
                    where assessment.user_id = cast(:user_id as uuid)
                      and assessment.session_id = cast(:session_id as uuid)
                    order by assessment.created_at, assessment.question_id
                    """
                ),
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "reflection_version_id": reflection_version_id,
                },
            ).mappings()
        )

    def session_content_is_eligible(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> bool:
        return bool(
            session.execute(
                text(
                    """
                    select not exists (
                      select 1
                      from learning.xingce_assessments assessment
                      left join product.questions question
                        on question.question_id = assessment.question_id
                      where assessment.user_id = cast(:user_id as uuid)
                        and assessment.session_id = cast(:session_id as uuid)
                        and (
                          question.question_id is null
                          or question.status not in ('active', 'canonical')
                        )
                    )
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).scalar_one()
        )


def json_value(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
