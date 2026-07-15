from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


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

    def select_frozen_questions(
        self,
        session: Session,
        *,
        module_name: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = (
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
                limit :limit
                """
                ),
                {"module_name": module_name, "limit": limit},
            )
            .mappings()
            .all()
        )
        questions: list[dict[str, Any]] = []
        for row in rows:
            question = dict(row)
            question["options"] = [
                dict(item)
                for item in session.execute(
                    text(
                        """
                        select label, option_text, option_html
                        from product.question_options
                        where question_id = :question_id
                        order by option_index
                        """
                    ),
                    {"question_id": row["question_id"]},
                ).mappings()
            ]
            question["assets"] = [
                dict(item)
                for item in session.execute(
                    text(
                        """
                        select asset_id, part_kind, part_index, image_ordinal,
                               image_src, object_key, is_representative_asset
                        from product.question_assets
                        where question_id = :question_id
                        order by part_kind nulls last, part_index nulls last,
                                 image_ordinal nulls last, asset_id
                        """
                    ),
                    {"question_id": row["question_id"]},
                ).mappings()
            ]
            questions.append(question)
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

    def insert_review_version(
        self, session: Session, *, values: dict[str, Any]
    ) -> RowMapping:
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

    def insert_reflection(
        self, session: Session, *, values: dict[str, Any]
    ) -> RowMapping:
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

    def insert_cause_decision(
        self, session: Session, *, values: dict[str, Any]
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.xingce_cause_decision_versions (
                  decision_version_id, command_id, user_id, review_id, session_id,
                  version_no, decision, replacement_cause,
                  supersedes_decision_version_id, created_at
                ) values (
                  cast(:decision_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), :version_no, :decision,
                  :replacement_cause, cast(:supersedes_decision_version_id as uuid),
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
                  input_version_refs, created_at
                ) values (
                  cast(:candidate_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:review_id as uuid),
                  cast(:session_id as uuid), :version_no, 'unavailable',
                  'scenario_not_activated', false,
                  cast(:input_version_refs as jsonb), :created_at
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

    def insert_plan_evidence(
        self, session: Session, *, values: dict[str, Any]
    ) -> RowMapping:
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
