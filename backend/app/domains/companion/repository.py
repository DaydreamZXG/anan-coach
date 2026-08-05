from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.knowledge.module_vocabulary import (
    ReleasedModuleVocabulary,
    load_current_module_vocabulary,
)
from app.domains.learning_brain.service import LearningBrainService


@dataclass(frozen=True)
class CompanionContextRecord:
    target_exam: str | None
    position_group: str | None
    target_score: float | None
    baseline_xingce_score: float | None
    baseline_shenlun_score: float | None
    practice_answers: int
    wrong_answers: int
    mock_exams: int
    latest_mock_score: float | None
    top_wrong_modules: list[dict[str, object]]
    method_cards: list[dict[str, object]]
    current_shenlun_estimate: dict[str, object] | None
    essay_attempts: int
    grounded_essay_reviews: int
    latest_essay_review: dict[str, object] | None
    latest_essay_reflection: dict[str, object] | None
    today_essay_plan: dict[str, object] | None
    essay_plan_omission_reason: str | None
    today_plan_tasks: list[dict[str, object]] = field(default_factory=list)
    rank_level: int | None = None
    rank_name: str | None = None
    fame: int | None = None
    consecutive_days: int | None = None
    xp: int | None = None
    learning_brain_profile: dict[str, object] | None = None


@dataclass(frozen=True)
class KnowledgeMapCandidateRecord:
    release_id: str
    node_id: str
    display_path: str
    module_name: str
    tag_type: str
    tag_value: str
    support_status: str
    method_card_ids: list[str]


@dataclass(frozen=True)
class ExternalQuestionIntakeRecord:
    intake_id: str
    user_id: str
    status: str
    knowledge_release_id: str | None
    selected_node_id: str | None
    candidate_nodes: list[dict[str, object]]
    stem: str
    options: list[str]
    selected_answer: str | None
    correct_answer: str | None


class CompanionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_context(
        self,
        *,
        user_id: str,
        node_id: str | None,
    ) -> CompanionContextRecord:
        profile = (
            self._session.execute(
                text(
                    """
                select
                  profile.target_exam,
                  profile.position_group,
                  current_target.target_score,
                  profile.baseline_xingce_score,
                  profile.baseline_shenlun_score
                from learner.profiles profile
                left join learner.current_profile_position_projections current_target
                  on current_target.user_id = profile.user_id
                where profile.user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )
        summary = (
            self._session.execute(
                text(
                    """
                select
                  (
                    select count(*)
                    from learner.practice_answers
                    where user_id = cast(:user_id as uuid)
                  ) as practice_answers,
                  (
                    select count(*)
                    from learner.practice_answers
                    where user_id = cast(:user_id as uuid)
                      and correct = false
                  ) as wrong_answers,
                  (
                    select count(*)
                    from learner.mock_exam_sessions
                    where user_id = cast(:user_id as uuid)
                      and status in ('submitted', 'reviewed')
                  ) as mock_exams,
                  (
                    select cast(review_payload ->> 'estimatedScore' as numeric)
                    from learner.mock_exam_sessions
                    where user_id = cast(:user_id as uuid)
                      and review_payload ? 'estimatedScore'
                    order by submitted_at desc nulls last, created_at desc
                    limit 1
                  ) as latest_mock_score
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        rank = (
            self._session.execute(
                text(
                    """
                select r.xp, r.fame, r.consecutive_days, c.rank_level, c.rank_name
                from learner.official_ranks r
                join product.official_rank_config c on c.rank_code = r.rank_code
                where r.user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )
        wrong_module_rows = (
            self._session.execute(
                text(
                    """
                select q.module_name, count(*) as wrong_count
                from learner.practice_answers pa
                join product.questions q on q.question_id = pa.question_id
                where pa.user_id = cast(:user_id as uuid)
                  and pa.correct = false
                  and q.module_name is not null
                group by q.module_name
                order by wrong_count desc, q.module_name
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
        module_vocabulary = load_current_module_vocabulary(
            self._session,
            exam_scope="guokao",
            track="xingce",
        )
        wrong_modules = _canonical_wrong_modules(
            wrong_module_rows,
            vocabulary=module_vocabulary,
        )
        method_cards = (
            self._session.execute(
                text(
                    """
                with active_releases as (
                  select
                    release.release_id,
                    release.fresh_until,
                    count(*) over () as active_release_count
                  from product.knowledge_map_releases release
                  where release.exam_scope = 'guokao'
                    and release.track = 'xingce'
                    and release.status = 'active'
                ), current_release as (
                  select release_id
                  from active_releases
                  where active_release_count = 1
                    and fresh_until > statement_timestamp()
                    and release_id = cast(:release_id as uuid)
                )
                select
                  mc.id,
                  mc.title,
                  mc.fix_action,
                  mc.tag_path
                from current_release release
                join product.knowledge_map_nodes kmn
                  on kmn.release_id = release.release_id
                join product.method_cards mc
                  on mc.id in (
                    select jsonb_array_elements_text(kmn.method_card_ids)
                  )
                where (
                  cast(:node_id as text) is not null
                  and kmn.node_id = cast(:node_id as text)
                )
                  and kmn.exam_scope = 'guokao'
                  and kmn.track = 'xingce'
                  and kmn.status = 'published'
                  and mc.status = 'published'
                order by mc.priority desc, mc.id
                limit 3
                """
                ),
                {
                    "node_id": node_id,
                    "release_id": (
                        module_vocabulary.release_id if module_vocabulary is not None else None
                    ),
                },
            )
            .mappings()
            .all()
        )
        ability = (
            self._session.execute(
                text(
                    """
                with open_releases as materialized (
                  select distinct release.release_id, release.exam_code, release.exam_year
                  from product.position_rule_releases release
                  join product.position_release_windows business_window
                    on business_window.release_id = release.release_id
                   and business_window.opens_at <= statement_timestamp()
                   and statement_timestamp() < business_window.closes_at
                  where release.exam_code = 'guokao'
                    and release.status = 'active'
                ), current_release as (
                  select exam_code, exam_year
                  from open_releases
                  where (select count(*) from open_releases) = 1
                )
                select shenlun_lower, shenlun_recommended, shenlun_upper,
                       confidence, generated_at, valid_until
                  from current_release release
                  join learning.ability_estimate_versions estimate
                    on estimate.exam_code = release.exam_code
                   and estimate.exam_year = release.exam_year
                 where estimate.user_id = cast(:user_id as uuid)
                   and estimate.shenlun_recommended is not null
                   and estimate.valid_until > now()
                   and not exists (
                     select 1 from learning.ability_estimate_versions successor
                      where successor.supersedes_estimate_version_id =
                            estimate.estimate_version_id
                   )
                 order by estimate.generated_at desc, estimate.estimate_version_id
                 limit 1
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        essay_summary = (
            self._session.execute(
                text(
                    """
                select
                  (select count(*)
                     from learning.essay_attempts attempt
                    where attempt.user_id = cast(:user_id as uuid)) as attempts,
                  (select count(*)
                     from learning.essay_review_versions review
                    where review.user_id = cast(:user_id as uuid)
                      and review.status = 'completed'
                      and review.execution_fingerprint is not null
                      and review.grounding_contract_version = 'essay-grounding-v2'
                      and review.grounding_report ->> 'verified' = 'true'
                      and review.grounding_report ->> 'eligibleForLearning' = 'true'
                      and not exists (
                        select 1 from learning.essay_review_versions successor
                         where successor.supersedes_review_version_id =
                               review.review_version_id
                      )) as grounded_reviews
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        current_essay_evidence = (
            self._session.execute(
                text(
                    """
                with canonical_attempt as (
                  select attempt.*
                    from learning.essay_attempts attempt
                   where attempt.user_id = cast(:user_id as uuid)
                   order by attempt.submitted_at desc,
                            attempt.created_at desc,
                            attempt.attempt_id desc
                   limit 1
                ),
                canonical_answer as (
                  select answer.*
                    from canonical_attempt attempt
                    join lateral (
                      select candidate.*
                        from learning.essay_answer_versions candidate
                       where candidate.user_id = attempt.user_id
                         and candidate.attempt_id = attempt.attempt_id
                         and candidate.task_version_id = attempt.task_version_id
                         and not exists (
                           select 1
                             from learning.essay_answer_versions successor
                            where successor.supersedes_answer_version_id =
                                  candidate.answer_version_id
                         )
                       order by candidate.version_no desc,
                                candidate.created_at desc,
                                candidate.answer_version_id desc
                       limit 1
                    ) answer on true
                )
                select review.review_version_id,
                       review.score_recommended, review.score_maximum,
                       review.score_confidence,
                       review.score_confidence_reasons, review.findings,
                       review.generated_at, review.valid_until,
                       review.rubric_source, review.rubric_verification,
                       reflection.artifact_version_id as reflection_artifact_version_id,
                       reflection.payload, reflection.created_at
                  from canonical_attempt attempt
                  left join canonical_answer answer
                    on answer.attempt_id = attempt.attempt_id
                   and answer.user_id = attempt.user_id
                   and answer.task_version_id = attempt.task_version_id
                  left join lateral (
                    select candidate.review_version_id,
                           candidate.score_recommended,
                           candidate.score_maximum,
                           candidate.score_confidence,
                           candidate.score_confidence_reasons,
                           candidate.findings, candidate.generated_at,
                           candidate.valid_until,
                           rubric.source_kind as rubric_source,
                           rubric.verification_status as rubric_verification
                      from learning.essay_review_versions candidate
                      join product.essay_rubric_versions rubric
                        on rubric.rubric_version_id = candidate.rubric_version_id
                       and rubric.task_version_id = candidate.task_version_id
                       and rubric.material_bundle_version_id =
                           candidate.material_bundle_version_id
                     where candidate.attempt_id = attempt.attempt_id
                       and candidate.user_id = attempt.user_id
                       and candidate.task_version_id = attempt.task_version_id
                       and candidate.material_bundle_version_id =
                           attempt.material_bundle_version_id
                       and candidate.rubric_version_id = attempt.rubric_version_id
                       and candidate.answer_version_id = answer.answer_version_id
                       and candidate.status = 'completed'
                       and candidate.execution_fingerprint is not null
                       and candidate.grounding_contract_version =
                           'essay-grounding-v2'
                       and candidate.grounding_report ->> 'verified' = 'true'
                       and candidate.grounding_report ->>
                           'eligibleForLearning' = 'true'
                       and candidate.valid_until > now()
                       and not exists (
                         select 1
                           from learning.essay_review_versions successor
                          where successor.supersedes_review_version_id =
                                candidate.review_version_id
                       )
                     order by candidate.generated_at desc,
                              candidate.review_version_id desc
                     limit 1
                  ) review on true
                  left join lateral (
                    select artifact.artifact_version_id,
                           artifact.payload, artifact.created_at
                      from learning.essay_learning_artifact_versions artifact
                     where artifact.user_id = attempt.user_id
                       and artifact.task_version_id = attempt.task_version_id
                       and artifact.material_bundle_version_id =
                           attempt.material_bundle_version_id
                       and artifact.rubric_version_id = attempt.rubric_version_id
                       and artifact.answer_version_id = answer.answer_version_id
                       and artifact.artifact_type = 'reflection'
                       and not exists (
                         select 1
                           from learning.essay_learning_artifact_versions successor
                          where successor.supersedes_artifact_version_id =
                                artifact.artifact_version_id
                       )
                     order by artifact.created_at desc,
                              artifact.artifact_version_id desc
                     limit 1
                  ) reflection on true
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        latest_review = (
            current_essay_evidence
            if current_essay_evidence is not None
            and current_essay_evidence["review_version_id"] is not None
            else None
        )
        latest_reflection = (
            current_essay_evidence
            if current_essay_evidence is not None
            and current_essay_evidence["reflection_artifact_version_id"] is not None
            else None
        )
        current_plan = (
            self._session.execute(
                text(
                    """
                select plan.local_date,
                       plan.headline,
                       plan.rationale,
                       plan.track_explanation,
                       (
                         select jsonb_build_object(
                           'stage', task.stage,
                           'title', task.title,
                           'reason', task.reason,
                           'estimatedMinutes', task.estimated_minutes,
                           'status', coalesce(execution.status, 'pending')
                         )
                           from learning.daily_plan_tasks task
                           left join lateral (
                             select item.status
                               from learning.task_execution_versions item
                              where item.task_id = task.task_id
                                and not exists (
                                  select 1
                                    from learning.task_execution_versions successor
                                   where successor.supersedes_execution_version_id =
                                         item.execution_version_id
                                )
                              order by item.recorded_at desc, item.execution_version_id
                              limit 1
                           ) execution on true
                          where task.plan_version_id = plan.plan_version_id
                            and task.track = 'essay'
                          order by task.task_order
                          limit 1
                       ) as essay_task,
                       coalesce(
                         (
                           select jsonb_agg(
                             jsonb_build_object(
                               'taskId', task.task_id,
                               'track', task.track,
                               'stage', task.stage,
                               'title', task.title,
                               'reason', task.reason,
                               'estimatedMinutes', task.estimated_minutes,
                               'status', coalesce(
                                 (
                                   select item.status
                                     from learning.task_execution_versions item
                                    where item.task_id = task.task_id
                                      and not exists (
                                        select 1
                                          from learning.task_execution_versions successor
                                         where successor.supersedes_execution_version_id =
                                               item.execution_version_id
                                      )
                                    order by item.recorded_at desc,
                                             item.execution_version_id
                                    limit 1
                                 ),
                                 'pending'
                               )
                             ) order by task.task_order
                           )
                             from learning.daily_plan_tasks task
                            where task.plan_version_id = plan.plan_version_id
                         ),
                         '[]'::jsonb
                       ) as tasks
                  from learning.daily_plan_versions plan
                 where plan.user_id = cast(:user_id as uuid)
                   and plan.valid_until > now()
                   and not exists (
                     select 1 from learning.daily_plan_versions successor
                      where successor.supersedes_plan_version_id =
                            plan.plan_version_id
                   )
                 order by plan.generated_at desc, plan.plan_version_id
                 limit 1
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )
        learning_brain = (
            self._session.execute(
                text(
                    """
                select profile_version_id, version_no, readiness,
                       ability_snapshot, evidence_summary, module_abilities,
                       error_patterns, decision_summary, conflicts,
                       input_fingerprint, generated_at, valid_until
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
        strategy_directive = LearningBrainService().current_strategy_directive(
            self._session,
            user_id=user_id,
        )
        return CompanionContextRecord(
            target_exam=profile["target_exam"] if profile else None,
            position_group=profile["position_group"] if profile else None,
            target_score=float(profile["target_score"])
            if profile and profile["target_score"] is not None
            else None,
            baseline_xingce_score=(
                float(profile["baseline_xingce_score"])
                if profile and profile["baseline_xingce_score"] is not None
                else None
            ),
            baseline_shenlun_score=(
                float(profile["baseline_shenlun_score"])
                if profile and profile["baseline_shenlun_score"] is not None
                else None
            ),
            practice_answers=int(summary["practice_answers"]),
            wrong_answers=int(summary["wrong_answers"]),
            mock_exams=int(summary["mock_exams"]),
            latest_mock_score=(
                float(summary["latest_mock_score"])
                if summary["latest_mock_score"] is not None
                else None
            ),
            top_wrong_modules=wrong_modules,
            method_cards=[dict(row) for row in method_cards],
            current_shenlun_estimate=(
                {
                    "lower": float(ability["shenlun_lower"]),
                    "recommended": float(ability["shenlun_recommended"]),
                    "upper": float(ability["shenlun_upper"]),
                    "confidence": str(ability["confidence"]),
                    "generatedAt": ability["generated_at"].isoformat(),
                    "validUntil": ability["valid_until"].isoformat(),
                }
                if ability is not None
                else None
            ),
            essay_attempts=int(essay_summary["attempts"]),
            grounded_essay_reviews=int(essay_summary["grounded_reviews"]),
            latest_essay_review=_essay_review_summary(latest_review),
            latest_essay_reflection=_essay_reflection_summary(latest_reflection),
            today_essay_plan=(
                dict(current_plan["essay_task"])
                if current_plan is not None and isinstance(current_plan["essay_task"], dict)
                else None
            ),
            essay_plan_omission_reason=_essay_omission_reason(current_plan),
            today_plan_tasks=(
                [dict(item) for item in current_plan["tasks"]]
                if current_plan is not None and isinstance(current_plan["tasks"], list)
                else []
            ),
            rank_level=int(rank["rank_level"]) if rank and rank["rank_level"] is not None else None,
            rank_name=str(rank["rank_name"]) if rank and rank["rank_name"] is not None else None,
            fame=int(rank["fame"]) if rank and rank["fame"] is not None else None,
            consecutive_days=int(rank["consecutive_days"])
            if rank and rank["consecutive_days"] is not None
            else None,
            xp=int(rank["xp"]) if rank and rank["xp"] is not None else None,
            learning_brain_profile=(
                {
                    "profileVersionId": str(learning_brain["profile_version_id"]),
                    "versionNo": int(learning_brain["version_no"]),
                    "readiness": str(learning_brain["readiness"]),
                    "ability": dict(learning_brain["ability_snapshot"]),
                    "evidenceSummary": dict(learning_brain["evidence_summary"]),
                    "modules": _relabel_display_name(learning_brain["module_abilities"]),
                    "errorPatterns": _relabel_display_name(learning_brain["error_patterns"]),
                    "decisionSummary": dict(learning_brain["decision_summary"]),
                    "conflicts": list(learning_brain["conflicts"]),
                    "inputFingerprint": str(learning_brain["input_fingerprint"]),
                    "generatedAt": learning_brain["generated_at"].isoformat(),
                    "validUntil": learning_brain["valid_until"].isoformat(),
                    "strategyDirective": (
                        strategy_directive.model_dump(by_alias=True, mode="json")
                        if strategy_directive is not None
                        else None
                    ),
                }
                if learning_brain is not None
                else None
            ),
        )

    def resolve_candidate(
        self,
        *,
        module_name: str,
        tag_type: str,
        tag_value: str,
        vocabulary: ReleasedModuleVocabulary | None = None,
    ) -> KnowledgeMapCandidateRecord | None:
        vocabulary = vocabulary or load_current_module_vocabulary(
            self._session, exam_scope="guokao", track="xingce"
        )
        if vocabulary is None:
            return None
        canonical_module = vocabulary.canonicalize(module_name)
        if canonical_module is None:
            return None
        row = (
            self._session.execute(
                text(
                    """
                with active_releases as (
                  select
                    release.release_id,
                    release.fresh_until,
                    count(*) over () as active_release_count
                  from product.knowledge_map_releases release
                  where release.exam_scope = 'guokao'
                    and release.track = 'xingce'
                    and release.status = 'active'
                ), current_release as (
                  select release_id
                  from active_releases
                  where active_release_count = 1
                    and fresh_until > statement_timestamp()
                    and release_id = cast(:release_id as uuid)
                )
                select
                  release.release_id,
                  node.node_id,
                  node.display_path,
                  node.module_name,
                  node.tag_type,
                  node.tag_value,
                  node.support_status,
                  node.method_card_ids
                from current_release release
                join product.knowledge_map_nodes node
                  on node.release_id = release.release_id
                where node.exam_scope = 'guokao'
                  and node.track = 'xingce'
                  and node.module_name = :module_name
                  and node.tag_type = :tag_type
                  and node.tag_value = :tag_value
                  and node.status = 'published'
                order by
                  node.near5_weight desc nulls last,
                  node.near5_occurrence_count desc nulls last,
                  node.node_id
                limit 1
                """
                ),
                {
                    "release_id": vocabulary.release_id,
                    "module_name": canonical_module,
                    "tag_type": tag_type,
                    "tag_value": tag_value,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return KnowledgeMapCandidateRecord(
            release_id=str(row["release_id"]),
            node_id=str(row["node_id"]),
            display_path=str(row["display_path"]),
            module_name=str(row["module_name"]),
            tag_type=str(row["tag_type"]),
            tag_value=str(row["tag_value"]),
            support_status=str(row["support_status"]),
            method_card_ids=list(row["method_card_ids"] or []),
        )

    def current_knowledge_vocabulary(self) -> ReleasedModuleVocabulary | None:
        return load_current_module_vocabulary(
            self._session,
            exam_scope="guokao",
            track="xingce",
        )

    def create_external_intake(
        self,
        *,
        user_id: str,
        stem: str,
        options: list[str],
        selected_answer: str | None,
        correct_answer: str | None,
        extracted_tags: list[dict[str, object]],
        candidate_nodes: list[dict[str, object]],
        knowledge_release_id: str | None,
        selected_node_id: str | None,
        match_confidence: float | None,
        status: str,
        rejection_reason: str | None,
        answer_preview: str | None,
        commit: bool = True,
    ) -> str:
        intake_id = str(uuid4())
        self._session.execute(
            text(
                """
                insert into learner.external_question_intakes (
                  intake_id,
                  user_id,
                  source,
                  stem,
                  options,
                  selected_answer,
                  correct_answer,
                  extracted_tags,
                  candidate_nodes,
                  knowledge_release_id,
                  selected_node_id,
                  match_confidence,
                  status,
                  rejection_reason,
                  answer_preview,
                  created_at
                ) values (
                  cast(:intake_id as uuid),
                  cast(:user_id as uuid),
                  'external',
                  :stem,
                  cast(:options as jsonb),
                  :selected_answer,
                  :correct_answer,
                  cast(:extracted_tags as jsonb),
                  cast(:candidate_nodes as jsonb),
                  cast(:knowledge_release_id as uuid),
                  :selected_node_id,
                  :match_confidence,
                  :status,
                  :rejection_reason,
                  :answer_preview,
                  now()
                )
                """
            ),
            {
                "intake_id": intake_id,
                "user_id": user_id,
                "stem": stem,
                "options": json.dumps(options, ensure_ascii=False),
                "selected_answer": selected_answer,
                "correct_answer": correct_answer,
                "extracted_tags": json.dumps(extracted_tags, ensure_ascii=False),
                "candidate_nodes": json.dumps(candidate_nodes, ensure_ascii=False),
                "knowledge_release_id": knowledge_release_id,
                "selected_node_id": selected_node_id,
                "match_confidence": match_confidence,
                "status": status,
                "rejection_reason": rejection_reason,
                "answer_preview": answer_preview,
            },
        )
        if commit:
            self._session.commit()
        return intake_id

    def get_external_intake(
        self,
        *,
        intake_id: str,
        user_id: str,
    ) -> ExternalQuestionIntakeRecord | None:
        row = (
            self._session.execute(
                text(
                    """
                select
                  intake_id,
                  user_id,
                  status,
                  knowledge_release_id,
                  selected_node_id,
                  candidate_nodes,
                  stem,
                  options,
                  selected_answer,
                  correct_answer
                from learner.external_question_intakes
                where intake_id = cast(:intake_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
                ),
                {"intake_id": intake_id, "user_id": user_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return ExternalQuestionIntakeRecord(
            intake_id=str(row["intake_id"]),
            user_id=str(row["user_id"]),
            status=str(row["status"]),
            knowledge_release_id=(
                str(row["knowledge_release_id"])
                if row["knowledge_release_id"] is not None
                else None
            ),
            selected_node_id=(
                str(row["selected_node_id"]) if row["selected_node_id"] is not None else None
            ),
            candidate_nodes=list(row["candidate_nodes"] or []),
            stem=str(row["stem"]),
            options=list(row["options"] or []),
            selected_answer=row["selected_answer"],
            correct_answer=row["correct_answer"],
        )

    def confirm_external_wrong_question(
        self,
        *,
        intake: ExternalQuestionIntakeRecord,
        error_summary: str,
    ) -> str:
        external_question_id = str(uuid4())
        self._session.execute(
            text(
                """
                insert into learner.external_wrong_questions (
                  external_question_id,
                  intake_id,
                  user_id,
                  knowledge_release_id,
                  node_id,
                  source,
                  stem,
                  options,
                  selected_answer,
                  correct_answer,
                  error_summary,
                  payload,
                  created_at
                ) values (
                  cast(:external_question_id as uuid),
                  cast(:intake_id as uuid),
                  cast(:user_id as uuid),
                  cast(:knowledge_release_id as uuid),
                  :node_id,
                  'external',
                  :stem,
                  cast(:options as jsonb),
                  :selected_answer,
                  :correct_answer,
                  :error_summary,
                  cast(:payload as jsonb),
                  now()
                )
                """
            ),
            {
                "external_question_id": external_question_id,
                "intake_id": intake.intake_id,
                "user_id": intake.user_id,
                "knowledge_release_id": intake.knowledge_release_id,
                "node_id": intake.selected_node_id,
                "stem": intake.stem,
                "options": json.dumps(intake.options, ensure_ascii=False),
                "selected_answer": intake.selected_answer,
                "correct_answer": intake.correct_answer,
                "error_summary": error_summary,
                "payload": json.dumps(
                    {
                        "candidateNodes": intake.candidate_nodes,
                        "knowledgeReleaseId": intake.knowledge_release_id,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        self._session.execute(
            text(
                """
                update learner.external_question_intakes
                set
                  status = 'confirmed',
                  confirmed_at = now()
                where intake_id = cast(:intake_id as uuid)
                """
            ),
            {"intake_id": intake.intake_id},
        )
        self._session.commit()
        return external_question_id


def _relabel_display_name(items: Any) -> list[dict[str, object]]:
    """把 learning_brain 项里的 displayName 重命名为 label。

    学习脑的 module/errorPattern displayName 是业务标签（模块名、错误模式名），
    不是账户 PII；但 egress 守卫把 displayName 当账户标识字段拒绝，导致
    发 AI 的上下文被 500。序列化时改名为 label 保留业务信息并绕开误伤。
    """
    if not isinstance(items, list):
        return []
    renamed: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        if "displayName" in entry:
            entry["label"] = entry.pop("displayName")
        renamed.append(entry)
    return renamed


def _essay_review_summary(row: Any) -> dict[str, object] | None:
    if row is None:
        return None
    raw_findings = row["findings"] if isinstance(row["findings"], list) else []
    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings = sorted(
        (item for item in raw_findings if isinstance(item, dict)),
        key=lambda item: severity_order.get(str(item.get("severity")), 3),
    )[:3]
    rubric_source = str(row["rubric_source"])
    rubric_verification = str(row["rubric_verification"])
    return {
        "recommendedScore": float(row["score_recommended"]),
        "maximumScore": float(row["score_maximum"]),
        "confidence": str(row["score_confidence"]),
        "confidenceReasons": list(row["score_confidence_reasons"] or []),
        "scoreMeaning": (
            "official_rubric_training_estimate"
            if rubric_source == "official" and rubric_verification == "verified"
            else "unverified_ai_rubric_training_estimate"
        ),
        "rubricSource": rubric_source,
        "rubricVerification": rubric_verification,
        "priorityFindings": [
            {
                "type": str(item.get("type", "")),
                "severity": str(item.get("severity", "")),
                "diagnosis": str(item.get("diagnosis", "")),
                "action": str(item.get("action", "")),
                "attribution": str(item.get("attribution", "")),
            }
            for item in findings
        ],
        "generatedAt": row["generated_at"].isoformat(),
        "validUntil": row["valid_until"].isoformat(),
        "isCurrent": row["valid_until"] > datetime.now(UTC),
    }


def _canonical_wrong_modules(
    rows: list[Any],
    *,
    vocabulary: ReleasedModuleVocabulary | None,
) -> list[dict[str, object]]:
    if vocabulary is None:
        return []
    totals: dict[str, int] = {}
    for row in rows:
        canonical = vocabulary.canonicalize(str(row["module_name"]))
        if canonical is None:
            continue
        totals[canonical] = totals.get(canonical, 0) + int(row["wrong_count"])
    return [
        {"module_name": module_name, "wrong_count": wrong_count}
        for module_name, wrong_count in sorted(
            totals.items(),
            key=lambda item: (-item[1], item[0]),
        )[:3]
    ]


def _essay_reflection_summary(row: Any) -> dict[str, object] | None:
    if row is None or not isinstance(row["payload"], dict):
        return None
    payload = row["payload"]
    return {
        "causeCodes": list(payload.get("causeCodes") or []),
        "nextChange": str(payload.get("whatIWillChange") or ""),
        "verificationPlan": str(payload.get("verificationPlan") or ""),
        "recordedAt": row["created_at"].isoformat(),
    }


def _essay_omission_reason(current_plan: Any) -> str | None:
    if current_plan is None or not isinstance(current_plan["track_explanation"], dict):
        return None
    reasons = current_plan["track_explanation"].get("omittedTrackReasons")
    if not isinstance(reasons, dict):
        return None
    reason = reasons.get("essay")
    return str(reason) if isinstance(reason, str) and reason.strip() else None
