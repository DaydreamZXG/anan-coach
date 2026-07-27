from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.task_content.service import (
    TaskContentContractError,
    validate_learner_question,
)


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
                select
                  question.question_id, question.module_name, question.question_type,
                  question.stem, question.stem_html, question.answer,
                  question.material_text, question.material_html,
                  question.shared_context_required, question.visual_required,
                  explanation.preferred_analysis, explanation.analysis_source,
                  explanation.analysis_status,
                  explanation.prefer_ai_analysis,
                  explanation.analysis_needs_review
                from product.questions question
                left join product.question_explanations explanation
                  on explanation.question_id = question.question_id
                where question.domain = 'objective'
                  and question.status in ('active', 'canonical')
                  and nullif(trim(question.answer), '') is not null
                  and (
                    cast(:module_name as text) is null
                    or question.module_name = cast(:module_name as text)
                  )
                  and not (
                    question.question_id = any(cast(:excluded_question_ids as text[]))
                  )
                  and (
                    cast(:user_id as text) is null
                    or (
                      not exists (
                        select 1
                        from learning.xingce_assessments assessment
                        where assessment.user_id = cast(:user_id as uuid)
                          and assessment.question_id = question.question_id
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
                              question.question_id
                      )
                    )
                  )
                  and (
                    not question.visual_required
                    or exists (
                      select 1 from product.question_assets visual
                      where visual.question_id = question.question_id
                        and visual.image_src ~
                          '^https://kg\\.xingguoxian\\.cn(?::443)?/assets/'
                    )
                  )
                  and exists (
                    select 1 from product.question_options option
                    where option.question_id = question.question_id
                  )
                order by
                  question.latest_year desc nulls last,
                  question.occurrence_count desc nulls last,
                  question.question_id
                limit :batch_size
                offset :offset
                """
                    ),
                    {
                        "module_name": module_name,
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
            question_ids = [str(row["question_id"]) for row in rows]
            options_by_question: dict[str, list[dict[str, Any]]] = {}
            for item in session.execute(
                text(
                    """
                    select question_id, label, option_text, option_html
                    from product.question_options
                    where question_id = any(cast(:question_ids as text[]))
                    order by question_id, option_index
                    """
                ),
                {"question_ids": question_ids},
            ).mappings():
                options_by_question.setdefault(str(item["question_id"]), []).append(dict(item))
            assets_by_question: dict[str, list[dict[str, Any]]] = {}
            for item in session.execute(
                text(
                    """
                    select question_id, asset_id, part_kind, part_index,
                           image_ordinal, image_src, object_key,
                           is_representative_asset
                    from product.question_assets
                    where question_id = any(cast(:question_ids as text[]))
                    order by question_id, part_kind nulls last,
                             part_index nulls last, image_ordinal nulls last,
                             asset_id
                    """
                ),
                {"question_ids": question_ids},
            ).mappings():
                assets_by_question.setdefault(str(item["question_id"]), []).append(dict(item))
            for row in rows:
                question = dict(row)
                question_id = str(row["question_id"])
                question["options"] = options_by_question.get(question_id, [])
                question["assets"] = assets_by_question.get(question_id, [])
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
