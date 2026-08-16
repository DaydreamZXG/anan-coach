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
                      (
                        select count(*)
                        from learning.external_answer_facts fact
                        where fact.owner_user_id = cast(:user_id as uuid)
                          and fact.review_state = 'user_confirmed'
                      ) as external_answer_count,
                      (
                        select fact.fact_id::text
                        from learning.external_answer_facts fact
                        where fact.owner_user_id = cast(:user_id as uuid)
                          and fact.review_state = 'user_confirmed'
                        order by fact.created_at desc, fact.fact_id desc
                        limit 1
                      ) as latest_external_answer_fact_id,
                      (
                        select fact.track
                        from learning.external_answer_facts fact
                        where fact.owner_user_id = cast(:user_id as uuid)
                          and fact.review_state = 'user_confirmed'
                        order by fact.created_at desc, fact.fact_id desc
                        limit 1
                      ) as latest_external_answer_track,
                      (
                        select max(fact.created_at)
                        from learning.external_answer_facts fact
                        where fact.owner_user_id = cast(:user_id as uuid)
                          and fact.review_state = 'user_confirmed'
                      ) as latest_external_answer_at,
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
                          where user_id = cast(:user_id as uuid)),
                        (select max(created_at) from learning.external_answer_facts
                          where owner_user_id = cast(:user_id as uuid)
                            and review_state = 'user_confirmed')
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
                            when jsonb_typeof(
                              item.result_snapshot #>
                                '{timingEvidence,elapsedSeconds}'
                            ) = 'number'
                              and item.result_snapshot #>>
                                '{timingEvidence,elapsedSeconds}' ~ '^[0-9]+$'
                            then case
                              when (
                                item.result_snapshot #>>
                                  '{timingEvidence,elapsedSeconds}'
                              )::numeric between 0 and 86400
                              then (
                                item.result_snapshot #>>
                                  '{timingEvidence,elapsedSeconds}'
                              )::numeric
                              else null
                            end
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
                    ), external_answer as (
                      select
                        coalesce(nullif(trim(question.module_name), ''), '未分类') as module_name,
                        count(*)::int as external_answer_count,
                        max(fact.created_at) as latest_external_at,
                        (array_agg(
                          fact.fact_id::text order by fact.created_at desc, fact.fact_id desc
                        ))[1] as latest_external_fact_id
                      from learning.external_answer_facts fact
                      join product.question_content_versions content
                        on content.content_version_id = fact.content_version_id
                      join product.questions question
                        on question.question_id = content.question_id
                      where fact.owner_user_id = cast(:user_id as uuid)
                        and fact.track = 'xingce'
                        and fact.review_state = 'user_confirmed'
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
                      coalesce(
                        assessment.module_name,
                        mastery.module_name,
                        external_answer.module_name
                      ) as module_name,
                      coalesce(assessment.attempt_count, 0)::int as attempt_count,
                      coalesce(assessment.correct_count, 0)::int as correct_count,
                      assessment.average_seconds,
                      coalesce(external_answer.external_answer_count, 0)::int
                        as external_answer_count,
                      coalesce(mastery.mastery_count, 0)::int as mastery_count,
                      coalesce(mastery.stable_mastery_count, 0)::int
                        as stable_mastery_count,
                      coalesce(mastery.due_count, 0)::int as due_count,
                      greatest(
                        assessment.latest_evidence_at,
                        mastery.latest_mastery_at,
                        external_answer.latest_external_at
                      )
                        as latest_evidence_at,
                      assessment.latest_assessment_id,
                      mastery.latest_mastery_version_id,
                      external_answer.latest_external_fact_id
                    from assessment
                    full outer join mastery using (module_name)
                    full outer join external_answer using (module_name)
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
                      -- 判头 v2（2026-07-27 审计 §三 AI 消费侧）：
                      -- 1) supersedes 链判头：改判（confirm/replace 复议）落新确认
                      --    版本并引用旧链头，被取代的旧错因不再计入；
                      -- 2) 决定链兜底：收回（reject/defer 复议）不产生新确认版本，
                      --    无法靠链条表达，按「所属决定已被后继决定取代」排除。
                      --    该条件同时覆盖历史上尚未建立 supersedes 链的数据。
                      select cause.*
                      from learning.xingce_confirmed_cause_versions cause
                      where cause.user_id = cast(:user_id as uuid)
                        and not exists (
                          select 1 from learning.xingce_confirmed_cause_versions successor
                          where successor.supersedes_confirmed_cause_version_id =
                                cause.confirmed_cause_version_id
                        )
                        and not exists (
                          select 1
                          from learning.xingce_cause_decision_versions revoked_by
                          where revoked_by.supersedes_decision_version_id =
                                cause.decision_version_id
                        )
                    ), cause_mastery as (
                      -- 一个确认错因只沿自身 review 创建的 mastery 链取当前头。
                      -- 后续复测版本的 source_review_id 会变成复测 review，不能
                      -- 直接拿当前 mastery.source_review_id 与原错因做等值连接。
                      select
                        cause.*,
                        current_mastery.mastery_version_id as current_mastery_version_id,
                        current_mastery.state as current_mastery_state,
                        current_mastery.created_at as current_mastery_at
                      from current_confirmed cause
                      left join lateral (
                        select initial_mastery.mastery_id
                        from learning.xingce_mastery_state_versions initial_mastery
                        where initial_mastery.user_id = cause.user_id
                          and initial_mastery.source_review_id = cause.review_id
                        order by initial_mastery.version_no, initial_mastery.created_at
                        limit 1
                      ) initial_mastery on true
                      left join lateral (
                        select mastery.*
                        from learning.xingce_mastery_state_versions mastery
                        where mastery.user_id = cause.user_id
                          and mastery.mastery_id = initial_mastery.mastery_id
                          and not exists (
                            select 1
                            from learning.xingce_mastery_state_versions successor
                            where successor.supersedes_mastery_version_id =
                                  mastery.mastery_version_id
                          )
                        order by mastery.version_no desc
                        limit 1
                      ) current_mastery on true
                    ), question_candidate_entries as (
                      -- 保留所有逐题状态，供回退分支区分“旧候选没有逐题合同”
                      -- 和“新候选明确声明非 candidate 的证据边界”。后者
                      -- 绝不能因为没有 candidate 状态而退回整组错因摊派。
                      select
                        cause.confirmed_cause_version_id,
                        cause.created_at,
                        cause.current_mastery_version_id,
                        cause.current_mastery_state,
                        cause.current_mastery_at,
                        question_item.item->>'questionId' as question_id,
                        question_item.item->>'status' as status,
                        question_item.item->>'causeCode' as cause_code
                      from cause_mastery cause
                      join learning.xingce_cause_candidate_versions candidate
                        on candidate.candidate_version_id = cause.candidate_version_id
                      cross join lateral jsonb_array_elements(
                        coalesce(candidate.payload->'questionCandidates', '[]'::jsonb)
                      ) question_item(item)
                      where cause.source_kind = 'ai_confirmed'
                    ), question_candidates as (
                      -- 逐题候选来自 AI 确认候选的 append-only payload；
                      -- 只有 status=candidate 才进入错因聚合，其他状态只作为
                      -- 显式证据边界，不推断、不回退。
                      select *
                      from question_candidate_entries
                      where status = 'candidate'
                        and coalesce(cause_code, '') <> ''
                    ), xingce_question as (
                      -- 优先：逐题 causeCode 按题归模块（证据不足的题没有行，不硬给）。
                      select
                        'xingce'::text as track,
                        coalesce(nullif(trim(question.module_name), ''), '未分类')
                          as module_key,
                        question_candidate.cause_code,
                        null::text as display_name,
                        count(*)::int as occurrence_count,
                        count(*) filter (
                          where question_candidate.current_mastery_state =
                                'stable_mastered'
                        )::int as counterevidence_count,
                        max(question_candidate.created_at) as latest_observed_at,
                        (array_agg(
                          question_candidate.confirmed_cause_version_id::text
                          order by question_candidate.created_at desc
                        ))[1] as latest_version_id,
                        ((array_agg(
                          question_candidate.current_mastery_version_id::text
                          order by question_candidate.current_mastery_at desc
                        ) filter (
                          where question_candidate.current_mastery_state =
                                'stable_mastered'
                        )))[1] as latest_counterevidence_version_id,
                        max(question_candidate.current_mastery_at) filter (
                          where question_candidate.current_mastery_state =
                                'stable_mastered'
                        ) as latest_counterevidence_at
                      from question_candidates question_candidate
                      join product.questions question
                        on question.question_id = question_candidate.question_id
                      where not exists (
                        select 1
                        from learning.xingce_question_disputes dispute
                        where dispute.user_id = cast(:user_id as uuid)
                          and dispute.question_id = question_candidate.question_id
                          and not exists (
                            select 1
                            from product.question_quality_heads head
                            where head.question_id = dispute.question_id
                              and head.content_version_id = dispute.content_version_id
                              and head.decision = 'confirmed_valid'
                          )
                      )
                      group by
                        coalesce(nullif(trim(question.module_name), ''), '未分类'),
                        question_candidate.cause_code
                    ), xingce as (
                      -- 回退：该确认错因没有可用逐题候选时，保持既有 session 摊派。
                      -- 用户改写若带结构化 replacement_code，则按该码归桶
                      -- （display_name 置空，展示回落到分类中文名）；
                      -- 未选码的改写维持 user_replacement 单桶原行为。
                      select
                        'xingce'::text as track,
                        coalesce(nullif(trim(question.module_name), ''), '未分类')
                          as module_key,
                        coalesce(cause.replacement_code, cause.cause_code)
                          as cause_code,
                        case
                          when cause.replacement_code is not null then null
                          else cause.cause_text
                        end as display_name,
                        count(distinct cause.confirmed_cause_version_id)::int
                          as occurrence_count,
                        count(distinct cause.confirmed_cause_version_id) filter (
                          where cause.current_mastery_state = 'stable_mastered'
                        )::int as counterevidence_count,
                        max(cause.created_at) as latest_observed_at,
                        (array_agg(
                          cause.confirmed_cause_version_id::text
                          order by cause.created_at desc
                        ))[1] as latest_version_id,
                        ((array_agg(
                          cause.current_mastery_version_id::text
                          order by cause.current_mastery_at desc
                        ) filter (
                          where cause.current_mastery_state = 'stable_mastered'
                        )))[1] as latest_counterevidence_version_id,
                        max(cause.current_mastery_at) filter (
                          where cause.current_mastery_state = 'stable_mastered'
                        ) as latest_counterevidence_at
                      from cause_mastery cause
                      join learning.xingce_assessments assessment
                        on assessment.session_id = cause.session_id
                      join product.questions question
                        on question.question_id = assessment.question_id
                      where (
                        cause.source_kind <> 'ai_confirmed'
                        or not exists (
                          select 1 from question_candidate_entries question_entry
                          where question_entry.confirmed_cause_version_id =
                                cause.confirmed_cause_version_id
                        )
                      )
                      and not exists (
                        select 1
                        from learning.xingce_question_disputes dispute
                        where dispute.user_id = cast(:user_id as uuid)
                          and dispute.question_id = assessment.question_id
                          and not exists (
                            select 1
                            from product.question_quality_heads head
                            where head.question_id = dispute.question_id
                              and head.content_version_id = dispute.content_version_id
                              and head.decision = 'confirmed_valid'
                          )
                      )
                      group by
                        coalesce(nullif(trim(question.module_name), ''), '未分类'),
                        coalesce(cause.replacement_code, cause.cause_code),
                        case
                          when cause.replacement_code is not null then null
                          else cause.cause_text
                        end
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
                        mastery.mastery_version_id::text as latest_version_id,
                        case when mastery.state = 'stable_mastered'
                          then mastery.mastery_version_id::text else null end
                          as latest_counterevidence_version_id,
                        case when mastery.state = 'stable_mastered'
                          then mastery.created_at else null end
                          as latest_counterevidence_at
                      from current_essay mastery
                      join learning.essay_review_versions review
                        on review.review_version_id = mastery.source_review_version_id
                      left join lateral jsonb_array_elements(review.findings) finding(item)
                        on finding.item->>'findingId' = mastery.finding_id
                    )
                    select * from xingce_question
                    union all
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
                      :inference_type, :status, :producer_type,
                      :producer_id, cast(:value as jsonb),
                      cast(:evidence_refs as jsonb), :confidence, true,
                      :confirmation_status, 'valid', :model_id, :prompt_version,
                      :policy_version, :valid_from, :expires_at,
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

    def weekly_rhythm(
        self,
        *,
        user_id: str,
        today: date,
    ) -> dict[str, object]:
        row = (
            self._session.execute(
                text(
                    """
                    select
                      preference.timezone,
                      preference.weekday_minutes,
                      preference.weekend_minutes,
                      preference.regular_rest_weekday,
                      preference.regular_mock_weekday,
                      exists (
                        select 1 from learning.weekly_plan_versions plan
                        where plan.user_id = cast(:user_id as uuid)
                          and plan.week_start <= :today
                          and plan.week_start + 6 >= :today
                      ) as has_weekly_plan
                    from learning.weekly_study_preference_versions preference
                    where preference.user_id = cast(:user_id as uuid)
                      and preference.effective_from <= :today
                      and not exists (
                        select 1 from learning.weekly_study_preference_versions successor
                        where successor.supersedes_preference_version_id =
                              preference.preference_version_id
                          and successor.effective_from <= :today
                      )
                    order by preference.effective_from desc, preference.version_no desc
                    limit 1
                    """
                ),
                {"user_id": user_id, "today": today},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return {}
        return {
            "timezone": str(row["timezone"]),
            "weekdayMinutes": int(row["weekday_minutes"]),
            "weekendMinutes": int(row["weekend_minutes"]),
            "restWeekday": int(row["regular_rest_weekday"]),
            "mockWeekday": int(row["regular_mock_weekday"]),
            "hasWeeklyPlan": bool(row["has_weekly_plan"]),
        }

    def agent_suggestion_for_date(
        self,
        *,
        user_id: str,
        local_date: date,
    ) -> RowMapping | None:
        return (
            self._session.execute(
                text(
                    """
                    select *
                    from learning.learning_brain_agent_suggestions
                    where user_id = cast(:user_id as uuid)
                      and local_date = :local_date
                    limit 1
                    """
                ),
                {"user_id": user_id, "local_date": local_date},
            )
            .mappings()
            .one_or_none()
        )

    def insert_agent_suggestion(self, values: dict[str, Any]) -> RowMapping:
        return (
            self._session.execute(
                text(
                    """
                    insert into learning.learning_brain_agent_suggestions (
                      suggestion_id, command_id, user_id, local_date, trigger_kind,
                      profile_version_id, candidate_version_id, input_fingerprint,
                      created_at
                    ) values (
                      cast(:suggestion_id as uuid), :command_id,
                      cast(:user_id as uuid), :local_date, :trigger_kind,
                      cast(:profile_version_id as uuid),
                      cast(:candidate_version_id as uuid),
                      :input_fingerprint, :created_at
                    )
                    returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def current_strategy_directive(
        self,
        *,
        user_id: str,
        now: datetime,
    ) -> dict[str, object]:
        """读当前 active 策略决定 + 待确认策略候选（纯只读，供岸岸引用）。

        语义对齐 ``input_state`` 的 active_strategy_count / candidate 计数：
        active 决定需在有效窗口内且未被后继决定取代；待确认候选取最新的
        ``strategy_directive``（中控大脑产物），未过期且未被后继候选取代。
        """
        active_row = (
            self._session.execute(
                text(
                    """
                    select decision.decision_type, decision.scope_type,
                           decision.scope_key, decision.rationale,
                           decision.effective_until, decision.review_at,
                           decision.created_at
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
                    order by decision.created_at desc, decision.decision_version_id desc
                    limit 1
                    """
                ),
                {"user_id": user_id, "now": now},
            )
            .mappings()
            .one_or_none()
        )
        pending_row = (
            self._session.execute(
                text(
                    """
                    select candidate.value, candidate.expires_at,
                           candidate.created_at
                    from learning.inference_candidate_versions candidate
                    where candidate.user_id = cast(:user_id as uuid)
                      and candidate.status = 'awaiting_confirmation'
                      and candidate.inference_type = 'strategy_directive'
                      and candidate.expires_at > :now
                      and not exists (
                        select 1
                        from learning.inference_candidate_versions successor
                        where successor.supersedes_candidate_version_id =
                              candidate.candidate_version_id
                      )
                    order by candidate.created_at desc, candidate.version_no desc
                    limit 1
                    """
                ),
                {"user_id": user_id, "now": now},
            )
            .mappings()
            .one_or_none()
        )
        return {
            "activeStrategy": active_row,
            "pendingCandidate": pending_row,
        }

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
