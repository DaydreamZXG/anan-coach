from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class EssayClosureRepository:
    def lock_user(self, session: Session, *, user_id: str) -> None:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:user_id, 0))"),
            {"user_id": user_id},
        )

    def answer_by_id(
        self,
        session: Session,
        *,
        user_id: str,
        answer_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from learning.essay_answer_versions "
                "where user_id = cast(:user_id as uuid) "
                "and answer_version_id = cast(:answer_id as uuid)"
            ),
            {"user_id": user_id, "answer_id": answer_version_id},
        ).mappings().one_or_none()

    def rewrite_by_command(
        self, session: Session, *, user_id: str, command_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from learning.essay_rewrite_versions "
                "where user_id = cast(:user_id as uuid) and command_id = :command_id"
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def rewrite_source(
        self,
        session: Session,
        *,
        user_id: str,
        attempt_id: str,
        review_version_id: str,
        answer_version_id: str,
        decision_artifact_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select review.*, attempt.material_bundle_version_id,
                       decision.payload as decision_payload,
                       decision.artifact_version_id as decision_artifact_version_id
                from learning.essay_review_versions review
                join learning.essay_attempts attempt
                  on attempt.attempt_id = review.attempt_id
                 and attempt.user_id = review.user_id
                 and attempt.task_version_id = review.task_version_id
                join learning.essay_learning_artifact_versions decision
                  on decision.artifact_version_id = cast(:decision_id as uuid)
                 and decision.user_id = review.user_id
                 and decision.task_version_id = review.task_version_id
                 and decision.material_bundle_version_id = review.material_bundle_version_id
                 and decision.rubric_version_id = review.rubric_version_id
                 and decision.review_version_id = review.review_version_id
                 and decision.artifact_type = 'review_acknowledgement'
                where review.user_id = cast(:user_id as uuid)
                  and review.attempt_id = cast(:attempt_id as uuid)
                  and review.review_version_id = cast(:review_id as uuid)
                  and review.answer_version_id = cast(:answer_id as uuid)
                  and review.status = 'completed'
                  and review.grounding_contract_version = 'essay-grounding-v2'
                  and review.grounding_report->>'verified' = 'true'
                  and review.grounding_report->>'eligibleForLearning' = 'true'
                  and review.execution_fingerprint is not null
                  and not exists (
                    select 1 from learning.essay_review_versions successor
                    where successor.supersedes_review_version_id = review.review_version_id
                  )
                  and not exists (
                    select 1 from learning.essay_learning_artifact_versions successor
                    where successor.supersedes_artifact_version_id = decision.artifact_version_id
                  )
                """
            ),
            {
                "user_id": user_id,
                "attempt_id": attempt_id,
                "review_id": review_version_id,
                "answer_id": answer_version_id,
                "decision_id": decision_artifact_version_id,
            },
        ).mappings().one_or_none()

    def current_rewrite(
        self,
        session: Session,
        *,
        user_id: str,
        review_version_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        lock = "for update" if for_update else ""
        return session.execute(
            text(
                f"""
                select item.* from learning.essay_rewrite_versions item
                where item.user_id = cast(:user_id as uuid)
                  and item.base_review_version_id = cast(:review_id as uuid)
                  and item.writer_contract_version = 'essay-rewrite-writer@v2'
                  and not exists (
                    select 1 from learning.essay_rewrite_versions successor
                    where successor.supersedes_rewrite_version_id = item.rewrite_version_id
                  )
                order by item.created_at desc
                limit 1
                {lock}
                """
            ),
            {"user_id": user_id, "review_id": review_version_id},
        ).mappings().one_or_none()

    def insert_rewrite(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.essay_rewrite_versions (
                  rewrite_version_id, command_id, attempt_id, user_id, task_version_id,
                  base_review_version_id, base_answer_version_id,
                  rewritten_answer_version_id, analysis_payload, input_fingerprint,
                  output_fingerprint, supersedes_rewrite_version_id,
                  finding_decision_artifact_version_id, writer_contract_version,
                  writer_policy_version, created_at
                ) values (
                  cast(:rewrite_version_id as uuid), :command_id,
                  cast(:attempt_id as uuid), cast(:user_id as uuid),
                  cast(:task_version_id as uuid), cast(:base_review_version_id as uuid),
                  cast(:base_answer_version_id as uuid),
                  cast(:rewritten_answer_version_id as uuid),
                  cast(:analysis_payload as jsonb), :input_fingerprint,
                  :output_fingerprint, cast(:supersedes_rewrite_version_id as uuid),
                  cast(:finding_decision_artifact_version_id as uuid),
                  :writer_contract_version, :writer_policy_version, :created_at
                )
                returning *
                """
            ),
            values,
        ).mappings().one()

    def rewrite_for_transfer(
        self,
        session: Session,
        *,
        user_id: str,
        rewrite_version_id: str,
        decision_artifact_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select rewrite.*, review.material_bundle_version_id,
                       review.rubric_version_id, review.dimensions as source_dimensions,
                       decision.payload as source_decision_payload
                from learning.essay_rewrite_versions rewrite
                join learning.essay_review_versions review
                  on review.review_version_id = rewrite.base_review_version_id
                 and review.user_id = rewrite.user_id
                 and review.attempt_id = rewrite.attempt_id
                 and review.task_version_id = rewrite.task_version_id
                join learning.essay_learning_artifact_versions decision
                  on decision.artifact_version_id = rewrite.finding_decision_artifact_version_id
                 and decision.user_id = rewrite.user_id
                where rewrite.user_id = cast(:user_id as uuid)
                  and rewrite.rewrite_version_id = cast(:rewrite_id as uuid)
                  and rewrite.finding_decision_artifact_version_id = cast(:decision_id as uuid)
                  and rewrite.writer_contract_version = 'essay-rewrite-writer@v2'
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id =
                          rewrite.rewritten_answer_version_id
                  )
                  and not exists (
                    select 1 from learning.essay_rewrite_versions successor
                    where successor.supersedes_rewrite_version_id = rewrite.rewrite_version_id
                  )
                """
            ),
            {
                "user_id": user_id,
                "rewrite_id": rewrite_version_id,
                "decision_id": decision_artifact_version_id,
            },
        ).mappings().one_or_none()

    def transfer_context(
        self,
        session: Session,
        *,
        user_id: str,
        attempt_id: str,
        answer_version_id: str,
        review_version_id: str,
        decision_artifact_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select review.*, attempt.material_bundle_version_id,
                       answer.created_at as transfer_answer_created_at,
                       decision.payload as decision_payload
                from learning.essay_review_versions review
                join learning.essay_attempts attempt
                  on attempt.attempt_id = review.attempt_id
                 and attempt.user_id = review.user_id
                 and attempt.task_version_id = review.task_version_id
                join learning.essay_answer_versions answer
                  on answer.answer_version_id = review.answer_version_id
                 and answer.attempt_id = review.attempt_id
                 and answer.user_id = review.user_id
                 and answer.task_version_id = review.task_version_id
                join learning.essay_learning_artifact_versions decision
                  on decision.artifact_version_id = cast(:decision_id as uuid)
                 and decision.user_id = review.user_id
                 and decision.task_version_id = review.task_version_id
                 and decision.material_bundle_version_id = review.material_bundle_version_id
                 and decision.rubric_version_id = review.rubric_version_id
                 and decision.review_version_id = review.review_version_id
                 and decision.artifact_type = 'review_acknowledgement'
                where review.user_id = cast(:user_id as uuid)
                  and review.attempt_id = cast(:attempt_id as uuid)
                  and review.answer_version_id = cast(:answer_id as uuid)
                  and review.review_version_id = cast(:review_id as uuid)
                  and review.status = 'completed'
                  and review.grounding_contract_version = 'essay-grounding-v2'
                  and review.grounding_report->>'verified' = 'true'
                  and review.grounding_report->>'eligibleForLearning' = 'true'
                  and review.execution_fingerprint is not null
                  and not exists (
                    select 1 from learning.essay_review_versions successor
                    where successor.supersedes_review_version_id = review.review_version_id
                  )
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id = answer.answer_version_id
                  )
                  and not exists (
                    select 1 from learning.essay_learning_artifact_versions successor
                    where successor.supersedes_artifact_version_id = decision.artifact_version_id
                  )
                """
            ),
            {
                "user_id": user_id,
                "attempt_id": attempt_id,
                "answer_id": answer_version_id,
                "review_id": review_version_id,
                "decision_id": decision_artifact_version_id,
            },
        ).mappings().one_or_none()

    def transfer_by_command(
        self, session: Session, *, user_id: str, command_id: str
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from learning.essay_transfer_verification_versions "
                "where user_id = cast(:user_id as uuid) and command_id = :command_id"
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def current_transfer(
        self,
        session: Session,
        *,
        user_id: str,
        source_review_version_id: str,
        transfer_attempt_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        lock = "for update" if for_update else ""
        return session.execute(
            text(
                f"""
                select item.*
                from learning.essay_transfer_verification_versions item
                where item.user_id = cast(:user_id as uuid)
                  and item.source_review_version_id = cast(:source_review_id as uuid)
                  and item.transfer_attempt_id = cast(:transfer_attempt_id as uuid)
                  and item.writer_contract_version = 'essay-transfer-writer@v2'
                  and not exists (
                    select 1 from learning.essay_transfer_verification_versions successor
                    where successor.supersedes_verification_version_id = item.verification_version_id
                  )
                order by item.generated_at desc
                limit 1
                {lock}
                """
            ),
            {
                "user_id": user_id,
                "source_review_id": source_review_version_id,
                "transfer_attempt_id": transfer_attempt_id,
            },
        ).mappings().one_or_none()

    def insert_transfer(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.essay_transfer_verification_versions (
                  verification_version_id, command_id, user_id,
                  source_review_version_id, source_attempt_id, source_task_version_id,
                  transfer_attempt_id, transfer_task_version_id, transfer_answer_version_id,
                  fresh_material_bundle_version_id, status, tested_criterion_codes,
                  evidence_payload, input_fingerprint, output_fingerprint,
                  supersedes_verification_version_id, generated_at, valid_until,
                  source_rewrite_version_id, transfer_review_version_id,
                  finding_decision_artifact_version_id,
                  transfer_finding_decision_artifact_version_id,
                  writer_contract_version, writer_policy_version
                ) values (
                  cast(:verification_version_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:source_review_version_id as uuid),
                  cast(:source_attempt_id as uuid), cast(:source_task_version_id as uuid),
                  cast(:transfer_attempt_id as uuid), cast(:transfer_task_version_id as uuid),
                  cast(:transfer_answer_version_id as uuid),
                  cast(:fresh_material_bundle_version_id as uuid), :status,
                  cast(:tested_criterion_codes as jsonb), cast(:evidence_payload as jsonb),
                  :input_fingerprint, :output_fingerprint,
                  cast(:supersedes_verification_version_id as uuid),
                  :generated_at, :valid_until, cast(:source_rewrite_version_id as uuid),
                  cast(:transfer_review_version_id as uuid),
                  cast(:finding_decision_artifact_version_id as uuid),
                  cast(:transfer_finding_decision_artifact_version_id as uuid),
                  :writer_contract_version, :writer_policy_version
                )
                returning *
                """
            ),
            values,
        ).mappings().one()
