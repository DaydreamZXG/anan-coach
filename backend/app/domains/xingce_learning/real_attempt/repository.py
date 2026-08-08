from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

# Compatibility re-exports keep existing callers/tests stable while the
# selection implementation has one neutral task-runtime owner.
from app.domains.task_runtime.xingce_attempt.question_repository import (
    TargetedQuestionSelection,
    XingceQuestionRepository,
    _bounded_question_candidate_batch_size,
    _stable_question_distribution_key,
    _stable_question_rank,
    _stable_question_selection_cursor,
)

__all__ = [
    "TargetedQuestionSelection",
    "XingceAttemptRepository",
    "_bounded_question_candidate_batch_size",
    "_stable_question_distribution_key",
    "_stable_question_rank",
    "_stable_question_selection_cursor",
]


class XingceAttemptRepository(XingceQuestionRepository):
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
                    select
                      assessment.*,
                      content.snapshot_payload as content_snapshot
                    from learning.xingce_assessments assessment
                    join learning.xingce_task_sessions task_session
                      on task_session.session_id = assessment.session_id
                     and task_session.user_id = assessment.user_id
                    left join lateral jsonb_array_elements(
                      task_session.scoring_bundle -> 'questions'
                    ) with ordinality frozen_question(item, position)
                      on frozen_question.item ->> 'questionId' = assessment.question_id
                    left join product.question_content_versions content
                      on content.question_id = assessment.question_id
                     and content.content_version_id = assessment.content_version_id
                     and content.track = 'xingce'
                    where assessment.user_id = cast(:user_id as uuid)
                      and assessment.session_id = cast(:session_id as uuid)
                    order by frozen_question.position nulls last,
                             assessment.created_at,
                             assessment.question_id
                    """
                ),
                {"user_id": user_id, "session_id": session_id},
            ).mappings()
        )

    def insert_review_version(
        self,
        session: Session,
        *,
        values: dict[str, Any],
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
        self,
        session: Session,
        *,
        values: dict[str, Any],
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
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
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

    def insert_plan_evidence(
        self,
        session: Session,
        *,
        values: dict[str, Any],
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
