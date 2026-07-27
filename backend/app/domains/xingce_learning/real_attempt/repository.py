from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.knowledge.module_vocabulary import load_current_module_vocabulary
from app.domains.knowledge.repository import KnowledgeRepository
from app.domains.task_content.service import (
    TaskContentContractError,
    validate_learner_question,
)


@dataclass(frozen=True)
class TargetedQuestionSelection:
    questions: tuple[dict[str, Any], ...]
    release_id: str
    release_version: str
    source_data_version: str
    vocab_version: str
    fresh_until: datetime
    node_id: str
    node_type: str
    module_name: str


class XingceAttemptRepository:
    def question_dispute_by_idempotency_key(
        self,
        session: Session,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select *
                    from learning.xingce_question_disputes
                    where user_id = cast(:user_id as uuid)
                      and idempotency_key = :idempotency_key
                    """
                ),
                {"user_id": user_id, "idempotency_key": idempotency_key},
            )
            .mappings()
            .one_or_none()
        )

    def insert_question_dispute(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into learning.xingce_question_disputes (
                      dispute_id, user_id, session_kind, session_id, question_id,
                      content_version_id, category, description,
                      evidence_context, idempotency_key, created_at
                    ) values (
                      cast(:dispute_id as uuid), cast(:user_id as uuid),
                      'xingce_task',
                      cast(:session_id as uuid), :question_id,
                      :content_version_id, :category, :description,
                      cast(:evidence_context as jsonb), :idempotency_key, :created_at
                    )
                    returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def session_has_question_dispute(
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
                    select exists (
                      select 1 from learning.xingce_question_disputes
                      where user_id = cast(:user_id as uuid)
                        and session_kind = 'xingce_task'
                        and session_id = cast(:session_id as uuid)
                    )
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).scalar_one()
        )

    def verification_source(
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
                    select mastery_version_id, mastery_id, verification_stage,
                           state, due_at
                    from learning.xingce_mastery_state_versions
                    where user_id = cast(:user_id as uuid)
                      and mastery_version_id = cast(:mastery_version_id as uuid)
                    """
                ),
                {
                    "user_id": user_id,
                    "mastery_version_id": mastery_version_id,
                },
            )
            .mappings()
            .one_or_none()
        )

    def session_content_is_currently_eligible(
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

    def select_frozen_questions(
        self,
        session: Session,
        *,
        module_name: str | None,
        limit: int,
        user_id: str | None = None,
        excluded_question_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        vocabulary = load_current_module_vocabulary(
            session,
            exam_scope="guokao",
            track="xingce",
        )
        if vocabulary is None:
            return []
        canonical_module = (
            vocabulary.canonicalize(module_name)
            if module_name is not None
            else None
        )
        if module_name is not None and canonical_module is None:
            return []
        return self._select_eligible_questions(
            session,
            release_id=vocabulary.release_id,
            node_id=None,
            canonical_modules=(
                (canonical_module,) if canonical_module is not None else None
            ),
            limit=limit,
            user_id=user_id,
            excluded_question_ids=excluded_question_ids,
        )

    def select_targeted_frozen_questions(
        self,
        session: Session,
        *,
        module_name: str | None,
        limit: int,
        user_id: str,
        now: datetime,
    ) -> TargetedQuestionSelection | None:
        normalized_module = module_name.strip() if module_name is not None else None
        if limit <= 0 or normalized_module == "":
            return None

        vocabulary = load_current_module_vocabulary(
            session,
            exam_scope="guokao",
            track="xingce",
            now=now,
        )
        if vocabulary is None:
            return None
        canonical_module = (
            vocabulary.canonicalize(normalized_module)
            if normalized_module is not None
            else None
        )
        if normalized_module is not None and canonical_module is None:
            return None

        repository = KnowledgeRepository(session)
        if canonical_module is None:
            nodes = []
            for module_name_candidate in vocabulary.canonical_names:
                nodes.extend(
                    repository.list_knowledge_map_nodes_for_release(
                        release_id=vocabulary.release_id,
                        node_types=("micro_skill", "knowledge_point"),
                        module_name=module_name_candidate,
                    )
                )
            nodes.sort(
                key=lambda node: (
                    ("micro_skill", "knowledge_point").index(node.node_type),
                    -node.near5_weight,
                    -node.near5_occurrence_count,
                    node.module_name,
                    node.tag_type or "",
                    node.tag_value,
                    node.node_id,
                )
            )
        else:
            nodes = repository.list_knowledge_map_nodes_for_release(
                release_id=vocabulary.release_id,
                node_types=("micro_skill", "knowledge_point"),
                module_name=canonical_module,
            )
        for node in nodes:
            if (
                node.support_status != "text_supported"
                or node.tag_type is None
                or not node.tag_type.strip()
                or not node.tag_value.strip()
                or node.near5_occurrence_count <= 0
            ):
                continue
            questions = self._select_eligible_questions(
                session,
                release_id=node.release_id,
                node_id=node.node_id,
                canonical_modules=(node.module_name,),
                limit=limit,
                user_id=user_id,
                excluded_question_ids=None,
            )
            if len(questions) < limit:
                continue
            return TargetedQuestionSelection(
                questions=tuple(questions),
                release_id=node.release_id,
                release_version=node.release_version,
                source_data_version=node.source_data_version,
                vocab_version=vocabulary.vocab_version,
                fresh_until=node.fresh_until,
                node_id=node.node_id,
                node_type=node.node_type,
                module_name=node.module_name,
            )
        return None

    def _select_eligible_questions(
        self,
        session: Session,
        *,
        release_id: str,
        node_id: str | None,
        canonical_modules: tuple[str, ...] | None,
        limit: int,
        user_id: str | None,
        excluded_question_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        questions: list[dict[str, Any]] = []
        offset = 0
        batch_size = max(24, limit * 4)
        while len(questions) < limit:
            rows = list(
                session.execute(
                    text(
                        """
                with frozen_candidates as (
                  select
                    binding.question_id,
                    min(binding.content_version_id) as content_version_id,
                    min(binding.canonical_module) as canonical_module,
                    bool_and(binding.visual_required) as visual_required,
                    bool_and(
                      binding.shared_context_required
                    ) as shared_context_required,
                    max(binding.latest_year) as latest_year,
                    max(binding.occurrence_count) as occurrence_count
                  from product.knowledge_map_question_bindings binding
                  where binding.release_id = cast(:release_id as uuid)
                    and binding.delivery_eligible
                    and (
                      cast(:node_id as text) is null
                      or binding.node_id = cast(:node_id as text)
                    )
                    and (
                      cast(:canonical_modules as text[]) is null
                      or binding.canonical_module =
                         any(cast(:canonical_modules as text[]))
                    )
                    and not (
                      binding.question_id =
                      any(cast(:excluded_question_ids as text[]))
                    )
                    and (
                      cast(:user_id as text) is null
                      or (
                        not exists (
                          select 1
                          from learning.xingce_assessments assessment
                          where assessment.user_id = cast(:user_id as uuid)
                            and assessment.question_id = binding.question_id
                        )
                        and not exists (
                          select 1
                          from learning.xingce_task_sessions exposed_session
                          cross join lateral jsonb_array_elements(
                            coalesce(
                              exposed_session.safe_bundle -> 'questions',
                              '[]'::jsonb
                            )
                          ) exposed_question
                          where exposed_session.user_id = cast(:user_id as uuid)
                            and exposed_question ->> 'questionId' =
                                binding.question_id
                        )
                      )
                    )
                  group by binding.question_id
                  having count(distinct binding.content_version_id) = 1
                     and count(distinct binding.canonical_module) = 1
                     and count(distinct binding.visual_required) = 1
                     and count(
                       distinct binding.shared_context_required
                     ) = 1
                )
                select
                  candidate.question_id,
                  candidate.content_version_id,
                  candidate.canonical_module,
                  candidate.shared_context_required,
                  candidate.visual_required,
                  candidate.latest_year,
                  candidate.occurrence_count,
                  content.snapshot_payload
                from frozen_candidates candidate
                join product.question_content_versions content
                  on content.question_id = candidate.question_id
                 and content.content_version_id =
                     candidate.content_version_id
                 and content.track = 'xingce'
                order by
                  candidate.latest_year desc nulls last,
                  candidate.occurrence_count desc,
                  candidate.question_id
                limit :batch_size
                offset :offset
                """
                    ),
                    {
                        "release_id": release_id,
                        "node_id": node_id,
                        "canonical_modules": (
                            list(canonical_modules)
                            if canonical_modules is not None
                            else None
                        ),
                        "user_id": user_id,
                        "excluded_question_ids": excluded_question_ids or [],
                        "batch_size": batch_size,
                        "offset": offset,
                    },
                )
                .mappings()
                .all()
            )
            if not rows:
                break
            offset += len(rows)
            for row in rows:
                question = dict(row["snapshot_payload"])
                question_id = str(row["question_id"])
                question["question_id"] = question_id
                question["visual_required"] = bool(row["visual_required"])
                question["shared_context_required"] = bool(
                    row["shared_context_required"]
                )
                question["_content_version_id"] = str(row["content_version_id"])
                try:
                    validate_learner_question(question)
                except TaskContentContractError:
                    continue
                questions.append(question)
                if len(questions) == limit:
                    break
            if len(rows) < batch_size:
                break
        return questions

    def insert_assessment(self, session: Session, *, values: dict[str, Any]) -> None:
        session.execute(
            text(
                """
                insert into learning.xingce_assessments (
                  assessment_id, attempt_record_id, submit_command_id,
                  user_id, session_id, question_id,
                  answer_version_id, content_version_id, scoring_policy_version,
                  assessment_key_hash, result_code, correct, result_snapshot, created_at
                ) values (
                  cast(:assessment_id as uuid), cast(:attempt_record_id as uuid),
                  :submit_command_id,
                  cast(:user_id as uuid), cast(:session_id as uuid), :question_id,
                  cast(:answer_version_id as uuid), :content_version_id,
                  :scoring_policy_version, :assessment_key_hash,
                  :result_code, :correct,
                  cast(:result_snapshot as jsonb), :created_at
                )
                """
            ),
            values,
        )

    def assessments(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select * from learning.xingce_assessments
                    where user_id = cast(:user_id as uuid)
                      and session_id = cast(:session_id as uuid)
                    order by created_at, question_id
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).mappings()
        )

    def insert_review_version(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.xingce_attempt_review_versions (
                  review_version_id, command_id, review_id, user_id, session_id,
                  revision, state, requires_cause_decision, reflection_version_id,
                  cause_candidate_version_id, cause_decision_version_id,
                  supersedes_review_version_id, created_at
                ) values (
                  cast(:review_version_id as uuid), :command_id, cast(:review_id as uuid),
                  cast(:user_id as uuid), cast(:session_id as uuid), :revision,
                  :state, :requires_cause_decision,
                  cast(:reflection_version_id as uuid),
                  cast(:cause_candidate_version_id as uuid),
                  cast(:cause_decision_version_id as uuid),
                  cast(:supersedes_review_version_id as uuid), :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def current_review(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        suffix = " for update of review" if for_update else ""
        return (
            session.execute(
                text(
                    """
                select review.*
                from learning.xingce_attempt_review_versions review
                where review.review_id = cast(:review_id as uuid)
                  and review.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1 from learning.xingce_attempt_review_versions successor
                    where successor.supersedes_review_version_id = review.review_version_id
                  )
                """
                    + suffix
                ),
                {"review_id": review_id, "user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )

    def insert_reflection(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.xingce_reflection_versions (
                  reflection_version_id, command_id, user_id, review_id, session_id,
                  version_no, payload, payload_sha256,
                  supersedes_reflection_version_id, created_at
                ) values (
                  cast(:reflection_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), :version_no, cast(:payload as jsonb),
                  :payload_sha256, cast(:supersedes_reflection_version_id as uuid),
                  :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def reflection(
        self,
        session: Session,
        *,
        reflection_version_id: str | None,
    ) -> RowMapping | None:
        if reflection_version_id is None:
            return None
        return (
            session.execute(
                text(
                    "select * from learning.xingce_reflection_versions "
                    "where reflection_version_id = cast(:id as uuid)"
                ),
                {"id": reflection_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def insert_cause_decision(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.xingce_cause_decision_versions (
                  decision_version_id, command_id, user_id, review_id, session_id,
                  version_no, decision, replacement_cause, candidate_version_id,
                  confirmed_cause_version_id,
                  supersedes_decision_version_id, created_at
                ) values (
                  cast(:decision_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), :version_no, :decision,
                  :replacement_cause, cast(:candidate_version_id as uuid),
                  cast(:confirmed_cause_version_id as uuid),
                  cast(:supersedes_decision_version_id as uuid),
                  :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def insert_cause_candidate(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.xingce_cause_candidate_versions (
                  candidate_version_id, command_id, user_id, review_id, session_id,
                  version_no, state, reason_code, charged,
                  input_version_refs, agent_run_id, payload, output_fingerprint,
                  entitlement_policy_version, supersedes_candidate_version_id,
                  created_at
                ) values (
                  cast(:candidate_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), :version_no, :state,
                  :reason_code, false, cast(:input_version_refs as jsonb),
                  cast(:agent_run_id as uuid), cast(:payload as jsonb),
                  :output_fingerprint, :entitlement_policy_version,
                  cast(:supersedes_candidate_version_id as uuid), :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def cause_candidate(
        self,
        session: Session,
        *,
        candidate_version_id: str | None,
    ) -> RowMapping | None:
        if candidate_version_id is None:
            return None
        return (
            session.execute(
                text(
                    "select * from learning.xingce_cause_candidate_versions "
                    "where candidate_version_id = cast(:id as uuid)"
                ),
                {"id": candidate_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def latest_cause_candidate(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select candidate.*
                    from learning.xingce_cause_candidate_versions candidate
                    where candidate.user_id = cast(:user_id as uuid)
                      and candidate.review_id = cast(:review_id as uuid)
                      and not exists (
                        select 1 from learning.xingce_cause_candidate_versions successor
                        where successor.supersedes_candidate_version_id =
                              candidate.candidate_version_id
                      )
                    order by candidate.version_no desc limit 1
                    """
                ),
                {"user_id": user_id, "review_id": review_id},
            )
            .mappings()
            .one_or_none()
        )

    def cause_decision(
        self,
        session: Session,
        *,
        decision_version_id: str | None,
    ) -> RowMapping | None:
        if decision_version_id is None:
            return None
        return (
            session.execute(
                text(
                    "select * from learning.xingce_cause_decision_versions "
                    "where decision_version_id = cast(:id as uuid)"
                ),
                {"id": decision_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def verification_started_for_review(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
    ) -> bool:
        return bool(
            session.execute(
                text(
                    """
                    select exists (
                      select 1
                      from learning.xingce_mastery_state_versions mastery
                      join learning.xingce_task_sessions verification
                        on verification.source_mastery_id = mastery.mastery_id
                       and verification.user_id = mastery.user_id
                      where mastery.user_id = cast(:user_id as uuid)
                        and mastery.source_review_id = cast(:review_id as uuid)
                        and verification.mode = 'verification'
                    )
                    """
                ),
                {"user_id": user_id, "review_id": review_id},
            ).scalar_one()
        )

    def insert_plan_evidence(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.xingce_plan_evidence_versions (
                  evidence_version_id, command_id, user_id, session_id,
                  work_item_key, version_no, state, assessment_ids,
                  execution_version_id, supersedes_evidence_version_id, created_at
                ) values (
                  cast(:evidence_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:session_id as uuid), :work_item_key,
                  :version_no, :state, cast(:assessment_ids as jsonb),
                  cast(:execution_version_id as uuid),
                  cast(:supersedes_evidence_version_id as uuid), :created_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def current_plan_evidence(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select evidence.*
                from learning.xingce_plan_evidence_versions evidence
                where evidence.user_id = cast(:user_id as uuid)
                  and evidence.session_id = cast(:session_id as uuid)
                  and not exists (
                    select 1 from learning.xingce_plan_evidence_versions successor
                    where successor.supersedes_evidence_version_id =
                          evidence.evidence_version_id
                  )
                """
                ),
                {"user_id": user_id, "session_id": session_id},
            )
            .mappings()
            .one_or_none()
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
