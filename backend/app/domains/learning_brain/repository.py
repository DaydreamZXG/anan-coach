from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class LearningBrainRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_user(self, *, user_id: str) -> None:
        self._session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": user_id},
        ).one()

    def current_profile(self, *, user_id: str) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select profile.*
                    from learning.learner_brain_profile_versions profile
                    where profile.user_id = cast(:user_id as uuid)
                      and not exists (
                        select 1
                        from learning.learner_brain_profile_versions successor
                        where successor.supersedes_profile_version_id =
                              profile.profile_version_id
                      )
                    order by profile.version_no desc
                    limit 1
                    """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )

    def profile_by_id(self, *, user_id: str, profile_version_id: str) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select *
                    from learning.learner_brain_profile_versions
                    where user_id = cast(:user_id as uuid)
                      and profile_version_id = cast(:profile_version_id as uuid)
                    """
                ),
                {"user_id": user_id, "profile_version_id": profile_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def input_state(self, *, user_id: str, today: date) -> RowMapping:
        return (
            self._session.execute(
                text(
                    """
                    select
                      (
                        select estimate.estimate_version_id::text
                        from learning.ability_estimate_versions estimate
                        where estimate.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.ability_estimate_versions successor
                            where successor.supersedes_estimate_version_id =
                                  estimate.estimate_version_id
                          )
                        order by estimate.generated_at desc
                        limit 1
                      ) as ability_estimate_version_id,
                      (
                        select count(*)
                        from learning.score_evidence evidence
                        where evidence.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.score_evidence successor
                            where successor.supersedes_evidence_id = evidence.evidence_id
                          )
                      ) as score_evidence_count,
                      (
                        select count(*) from learning.xingce_assessments item
                        where item.user_id = cast(:user_id as uuid)
                      ) as xingce_attempt_count,
                      (
                        select count(*)
                        from learning.xingce_mastery_state_versions item
                        where item.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.xingce_mastery_state_versions successor
                            where successor.supersedes_mastery_version_id =
                                  item.mastery_version_id
                          )
                      ) as xingce_mastery_count,
                      (
                        select count(*)
                        from learning.essay_mastery_state_versions item
                        where item.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.essay_mastery_state_versions successor
                            where successor.supersedes_mastery_version_id =
                                  item.mastery_version_id
                          )
                      ) as essay_mastery_count,
                      (
                        select count(*)
                        from learning.task_execution_versions item
                        where item.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.task_execution_versions successor
                            where successor.supersedes_execution_version_id =
                                  item.execution_version_id
                          )
                      ) as task_execution_count,
                      greatest(
                        (select max(occurred_at) from learning.score_evidence
                          where user_id = cast(:user_id as uuid)),
                        (select max(created_at) from learning.xingce_assessments
                          where user_id = cast(:user_id as uuid)),
                        (select max(created_at) from learning.xingce_mastery_state_versions
                          where user_id = cast(:user_id as uuid)),
                        (select max(created_at) from learning.essay_mastery_state_versions
                          where user_id = cast(:user_id as uuid)),
                        (select max(recorded_at) from learning.task_execution_versions
                          where user_id = cast(:user_id as uuid))
                      ) as latest_evidence_at,
                      (
                        select count(*) filter (
                          where candidate.status = 'awaiting_confirmation'
                            and candidate.expires_at > now()
                        )
                        from learning.inference_candidate_versions candidate
                        where candidate.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.inference_candidate_versions successor
                            where successor.supersedes_candidate_version_id =
                                  candidate.candidate_version_id
                          )
                      ) as candidate_awaiting_count,
                      (
                        select count(*) filter (where candidate.status = 'accepted')
                        from learning.inference_candidate_versions candidate
                        where candidate.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.inference_candidate_versions successor
                            where successor.supersedes_candidate_version_id =
                                  candidate.candidate_version_id
                          )
                      ) as candidate_accepted_count,
                      (
                        select count(*) filter (where candidate.status = 'rejected')
                        from learning.inference_candidate_versions candidate
                        where candidate.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.inference_candidate_versions successor
                            where successor.supersedes_candidate_version_id =
                                  candidate.candidate_version_id
                          )
                      ) as candidate_rejected_count,
                      (
                        select count(*) filter (
                          where candidate.status = 'expired' or candidate.expires_at <= now()
                        )
                        from learning.inference_candidate_versions candidate
                        where candidate.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.inference_candidate_versions successor
                            where successor.supersedes_candidate_version_id =
                                  candidate.candidate_version_id
                          )
                      ) as candidate_expired_count,
                      (
                        select coalesce(string_agg(
                          candidate.candidate_version_id::text || ':' || candidate.status,
                          ',' order by candidate.candidate_id, candidate.candidate_version_id
                        ), '')
                        from learning.inference_candidate_versions candidate
                        where candidate.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.inference_candidate_versions successor
                            where successor.supersedes_candidate_version_id =
                                  candidate.candidate_version_id
                          )
                      ) as candidate_version_fingerprint,
                      (
                        select count(*) filter (
                          where decision.status = 'active'
                            and decision.effective_until > now()
                            and decision.review_at > now()
                        )
                        from learning.strategy_decision_versions decision
                        where decision.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.strategy_decision_versions successor
                            where successor.supersedes_decision_version_id =
                                  decision.decision_version_id
                          )
                      ) as active_strategy_count,
                      (
                        select count(*) filter (
                          where decision.status = 'review_due'
                            or (decision.status = 'active' and decision.review_at <= now())
                        )
                        from learning.strategy_decision_versions decision
                        where decision.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.strategy_decision_versions successor
                            where successor.supersedes_decision_version_id =
                                  decision.decision_version_id
                          )
                      ) as review_due_count,
                      (
                        select coalesce(string_agg(
                          decision.decision_version_id::text || ':' || decision.status,
                          ',' order by decision.decision_id, decision.decision_version_id
                        ), '')
                        from learning.strategy_decision_versions decision
                        where decision.user_id = cast(:user_id as uuid)
                          and not exists (
                            select 1 from learning.strategy_decision_versions successor
                            where successor.supersedes_decision_version_id =
                                  decision.decision_version_id
                          )
                      ) as strategy_decision_fingerprint,
                      (
                        select plan.plan_version_id::text
                        from learning.daily_plan_versions plan
                        where plan.user_id = cast(:user_id as uuid)
                        order by plan.generated_at desc, plan.version_no desc limit 1
                      ) as current_plan_version_id,
                      (
                        select plan.weekly_plan_version_id::text
                        from learning.weekly_plan_versions plan
                        where plan.user_id = cast(:user_id as uuid)
                          and plan.week_start <= :today
                          and plan.week_start + 6 >= :today
                        order by plan.version_no desc limit 1
                      ) as current_weekly_plan_version_id
                    """
                ),
                {"user_id": user_id, "today": today},
            )
            .mappings()
            .one()
        )

    def xingce_modules(self, *, user_id: str) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    with assessment as (
                      select
                        coalesce(nullif(trim(question.module_name), ''), '未分类') as module_name,
                        count(*)::int as attempt_count,
                        count(*) filter (where item.correct)::int as correct_count,
                        avg(
                          case
                            when item.result_snapshot->>'questionElapsedSeconds' ~ '^[0-9]+$'
                            then (item.result_snapshot->>'questionElapsedSeconds')::numeric
                            else null
                          end
                        ) as average_seconds,
                        max(item.created_at) as latest_evidence_at,
                        (array_agg(item.assessment_id::text order by item.created_at desc))[1]
                          as latest_assessment_id
                      from learning.xingce_assessments item
                      join product.questions question on question.question_id = item.question_id
                      where item.user_id = cast(:user_id as uuid)
                      group by coalesce(nullif(trim(question.module_name), ''), '未分类')
                    ), current_mastery as (
                      select item.*
                      from learning.xingce_mastery_state_versions item
                      where item.user_id = cast(:user_id as uuid)
                        and not exists (
                          select 1 from learning.xingce_mastery_state_versions successor
                          where successor.supersedes_mastery_version_id = item.mastery_version_id
                        )
                    ), mastery as (
                      select
                        coalesce(nullif(trim(module_name), ''), '未分类') as module_name,
                        count(*)::int as mastery_count,
                        count(*) filter (where state = 'stable_mastered')::int
                          as stable_mastery_count,
                        count(*) filter (where due_at is not null and due_at <= now())::int
                          as due_count,
                        max(created_at) as latest_mastery_at,
                        (array_agg(mastery_version_id::text order by created_at desc))[1]
                          as latest_mastery_version_id
                      from current_mastery
                      group by coalesce(nullif(trim(module_name), ''), '未分类')
                    )
                    select
                      coalesce(assessment.module_name, mastery.module_name) as module_name,
                      coalesce(assessment.attempt_count, 0)::int as attempt_count,
                      coalesce(assessment.correct_count, 0)::int as correct_count,
                      assessment.average_seconds,
                      coalesce(mastery.mastery_count, 0)::int as mastery_count,
                      coalesce(mastery.stable_mastery_count, 0)::int
                        as stable_mastery_count,
                      coalesce(mastery.due_count, 0)::int as due_count,
                      greatest(assessment.latest_evidence_at, mastery.latest_mastery_at)
                        as latest_evidence_at,
                      assessment.latest_assessment_id,
                      mastery.latest_mastery_version_id
                    from assessment
                    full outer join mastery using (module_name)
                    order by module_name
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )

    def essay_modules(self, *, user_id: str) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    with current_mastery as (
                      select item.*
                      from learning.essay_mastery_state_versions item
                      where item.user_id = cast(:user_id as uuid)
                        and not exists (
                          select 1 from learning.essay_mastery_state_versions successor
                          where successor.supersedes_mastery_version_id = item.mastery_version_id
                        )
                    )
                    select
                      coalesce(finding.item->>'type', 'unclassified') as module_key,
                      count(*)::int as mastery_count,
                      count(*) filter (where mastery.state = 'stable_mastered')::int
                        as stable_mastery_count,
                      count(*) filter (
                        where mastery.due_at is not null and mastery.due_at <= now()
                      )::int as due_count,
                      max(mastery.created_at) as latest_evidence_at,
                      (array_agg(
                        mastery.mastery_version_id::text order by mastery.created_at desc
                      ))[1] as latest_mastery_version_id
                    from current_mastery mastery
                    join learning.essay_review_versions review
                      on review.review_version_id = mastery.source_review_version_id
                    left join lateral jsonb_array_elements(review.findings) finding(item)
                      on finding.item->>'findingId' = mastery.finding_id
                    group by coalesce(finding.item->>'type', 'unclassified')
                    order by module_key
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )

    def error_patterns(self, *, user_id: str) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    with current_confirmed as (
                      select cause.*
                      from learning.xingce_confirmed_cause_versions cause
                      where cause.user_id = cast(:user_id as uuid)
                        and not exists (
                          select 1 from learning.xingce_confirmed_cause_versions successor
                          where successor.supersedes_confirmed_cause_version_id =
                                cause.confirmed_cause_version_id
                        )
                    ), xingce as (
                      select
                        'xingce'::text as track,
                        coalesce(nullif(trim(question.module_name), ''), '未分类')
                          as module_key,
                        cause.cause_code,
                        cause.cause_text as display_name,
                        count(distinct cause.confirmed_cause_version_id)::int
                          as occurrence_count,
                        0::int as counterevidence_count,
                        max(cause.created_at) as latest_observed_at,
                        (array_agg(
                          cause.confirmed_cause_version_id::text
                          order by cause.created_at desc
                        ))[1] as latest_version_id
                      from current_confirmed cause
                      join learning.xingce_assessments assessment
                        on assessment.session_id = cause.session_id
                      join product.questions question
                        on question.question_id = assessment.question_id
                      group by
                        coalesce(nullif(trim(question.module_name), ''), '未分类'),
                        cause.cause_code,
                        cause.cause_text
                    ), current_essay as (
                      select mastery.*
                      from learning.essay_mastery_state_versions mastery
                      where mastery.user_id = cast(:user_id as uuid)
                        and mastery.state <> 'withdrawn'
                        and not exists (
                          select 1 from learning.essay_mastery_state_versions successor
                          where successor.supersedes_mastery_version_id =
                                mastery.mastery_version_id
                        )
                    ), essay as (
                      select
                        'essay'::text as track,
                        coalesce(finding.item->>'type', 'unclassified') as module_key,
                        mastery.finding_id as cause_code,
                        coalesce(finding.item->>'diagnosis', mastery.finding_id)
                          as display_name,
                        1::int as occurrence_count,
                        case when mastery.state = 'stable_mastered' then 1 else 0 end::int
                          as counterevidence_count,
                        mastery.created_at as latest_observed_at,
                        mastery.mastery_version_id::text as latest_version_id
                      from current_essay mastery
                      join learning.essay_review_versions review
                        on review.review_version_id = mastery.source_review_version_id
                      left join lateral jsonb_array_elements(review.findings) finding(item)
                        on finding.item->>'findingId' = mastery.finding_id
                    )
                    select * from xingce
                    union all
                    select * from essay
                    order by latest_observed_at desc, track, module_key, cause_code
                    """
                ),
                {"user_id": user_id},
            ).mappings()
        )

    def insert_profile(self, values: dict[str, Any]) -> RowMapping:
        return (
            self._session.execute(
                text(
                    """
                    insert into learning.learner_brain_profile_versions (
                      profile_version_id, user_id, version_no, readiness,
                      ability_estimate_version_id, ability_snapshot, evidence_summary, candidate_summary,
                      module_abilities, error_patterns, decision_summary, conflicts,
                      source_refs, input_versions, algorithm_version, input_fingerprint,
                      supersedes_profile_version_id, generated_at, valid_until
                    ) values (
                      cast(:profile_version_id as uuid), cast(:user_id as uuid), :version_no,
                      :readiness, cast(:ability_estimate_version_id as uuid),
                      cast(:ability_snapshot as jsonb),
                      cast(:evidence_summary as jsonb), cast(:candidate_summary as jsonb),
                      cast(:module_abilities as jsonb), cast(:error_patterns as jsonb),
                      cast(:decision_summary as jsonb), cast(:conflicts as jsonb),
                      cast(:source_refs as jsonb), cast(:input_versions as jsonb),
                      'learning-brain-profile@v1', :input_fingerprint,
                      cast(:supersedes_profile_version_id as uuid), :generated_at, :valid_until
                    )
                    returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def current_candidate(self, *, user_id: str, candidate_id: str) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select candidate.*
                    from learning.inference_candidate_versions candidate
                    where candidate.user_id = cast(:user_id as uuid)
                      and candidate.candidate_id = cast(:candidate_id as uuid)
                      and not exists (
                        select 1 from learning.inference_candidate_versions successor
                        where successor.supersedes_candidate_version_id =
                              candidate.candidate_version_id
                      )
                    """
                ),
                {"user_id": user_id, "candidate_id": candidate_id},
            )
            .mappings()
            .one_or_none()
        )

    def candidate_by_version(
        self,
        *,
        user_id: str,
        candidate_version_id: str,
    ) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select * from learning.inference_candidate_versions
                    where user_id = cast(:user_id as uuid)
                      and candidate_version_id = cast(:candidate_version_id as uuid)
                    """
                ),
                {"user_id": user_id, "candidate_version_id": candidate_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def candidate_by_command(self, *, user_id: str, command_id: str) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select * from learning.inference_candidate_versions
                    where user_id = cast(:user_id as uuid) and command_id = :command_id
                    """
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )

    def decision_by_command(self, *, user_id: str, command_id: str) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select * from learning.strategy_decision_versions
                    where user_id = cast(:user_id as uuid) and command_id = :command_id
                    """
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )

    def insert_candidate(self, values: dict[str, Any]) -> RowMapping:
        return (
            self._session.execute(
                text(
                    """
                    insert into learning.inference_candidate_versions (
                      candidate_version_id, candidate_id, command_id, user_id, version_no,
                      inference_type, status, producer_type, producer_id, value,
                      evidence_refs, confidence, requires_user_confirmation,
                      confirmation_status, validation_status, model_id, prompt_version,
                      policy_version, valid_from, expires_at, input_fingerprint,
                      supersedes_candidate_version_id, created_at
                    ) values (
                      cast(:candidate_version_id as uuid), cast(:candidate_id as uuid),
                      :command_id, cast(:user_id as uuid), :version_no,
                      'strategy_recommendation', :status, 'rule',
                      'strategy-candidate@v1', cast(:value as jsonb),
                      cast(:evidence_refs as jsonb), :confidence, true,
                      :confirmation_status, 'valid', null, null,
                      'strategy-candidate@v1', :valid_from, :expires_at,
                      :input_fingerprint, cast(:supersedes_candidate_version_id as uuid),
                      :created_at
                    )
                    returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def insert_strategy_decision(self, values: dict[str, Any]) -> RowMapping:
        return (
            self._session.execute(
                text(
                    """
                    insert into learning.strategy_decision_versions (
                      decision_version_id, decision_id, command_id, user_id, version_no,
                      candidate_version_id, decision_type, status, scope_type, scope_key,
                      rationale, benefit_evidence, evidence_refs, effective_from,
                      effective_until, review_at, policy_version, input_fingerprint,
                      supersedes_decision_version_id, created_at
                    ) values (
                      cast(:decision_version_id as uuid), cast(:decision_id as uuid),
                      :command_id, cast(:user_id as uuid), 1,
                      cast(:candidate_version_id as uuid), :decision_type, :status,
                      :scope_type, :scope_key, :rationale,
                      cast(:benefit_evidence as jsonb), cast(:evidence_refs as jsonb),
                      :effective_from, :effective_until, :review_at,
                      'strategy-decision@v1', :input_fingerprint, null, :created_at
                    )
                    returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )


def json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
