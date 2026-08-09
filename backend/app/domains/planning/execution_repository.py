from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class ExecutionRepositoryMixin:
    def recent_track_minutes(
        self,
        session: Session,
        *,
        user_id: str,
        before_date: date,
    ) -> dict[str, int]:
        rows = (
            session.execute(
                text(
                    """
                select task.track, coalesce(sum(execution.elapsed_minutes), 0) as minutes
                from learning.task_execution_versions execution
                join learning.daily_plan_tasks task
                  on task.task_id = execution.task_id
                 and task.user_id = execution.user_id
                where execution.user_id = cast(:user_id as uuid)
                  and execution.local_date >= :before_date - 7
                  and execution.local_date < :before_date
                  and not exists (
                    select 1 from learning.task_execution_versions successor
                    where successor.supersedes_execution_version_id =
                          execution.execution_version_id
                  )
                group by task.track
                """
                ),
                {"user_id": user_id, "before_date": before_date},
            )
            .mappings()
            .all()
        )
        return {str(row["track"]): int(row["minutes"]) for row in rows}


    def execution_inputs(
        self,
        session: Session,
        *,
        user_id: str,
        local_date: date,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select distinct on (execution.work_item_key)
                      execution.execution_version_id,
                      execution.candidate_key,
                      execution.work_item_key,
                      execution.status,
                      execution.elapsed_minutes,
                      task.estimated_minutes,
                      execution.input_fingerprint,
                      execution.recorded_at
                    from learning.task_execution_versions execution
                    join learning.daily_plan_tasks task
                      on task.task_id = execution.task_id
                     and task.plan_version_id = execution.plan_version_id
                     and task.user_id = execution.user_id
                    where execution.user_id = cast(:user_id as uuid)
                      and execution.local_date = :local_date
                      and not exists (
                        select 1 from learning.task_execution_versions successor
                        where successor.supersedes_execution_version_id =
                              execution.execution_version_id
                      )
                    order by execution.work_item_key,
                             execution.recorded_at desc,
                             execution.execution_version_id desc
                    """
                ),
                {"user_id": user_id, "local_date": local_date},
            )
            .mappings()
            .all()
        )
    def current_task_for_execution(
        self,
        session: Session,
        *,
        user_id: str,
        task_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select task.*,
                       plan.generated_at as plan_generated_at,
                       plan.valid_until as plan_valid_until
                from learning.daily_plan_tasks task
                join learning.daily_plan_versions plan
                  on plan.plan_version_id = task.plan_version_id
                where task.task_id = cast(:task_id as uuid)
                  and task.user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1 from learning.daily_plan_versions successor
                    where successor.supersedes_plan_version_id = plan.plan_version_id
                  )
                for update of task
                """
                ),
                {"user_id": user_id, "task_id": task_id},
            )
            .mappings()
            .one_or_none()
        )

    def owned_task_for_execution(
        self,
        session: Session,
        *,
        user_id: str,
        task_id: str,
    ) -> RowMapping | None:
        """Load an immutable owned task when replaying a completed command."""
        return (
            session.execute(
                text(
                    """
                select task.*
                from learning.daily_plan_tasks task
                where task.task_id = cast(:task_id as uuid)
                  and task.user_id = cast(:user_id as uuid)
                for update of task
                """
                ),
                {"user_id": user_id, "task_id": task_id},
            )
            .mappings()
            .one_or_none()
        )

    def current_task_execution(
        self,
        session: Session,
        *,
        user_id: str,
        work_item_key: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select execution.*
                from learning.task_execution_versions execution
                where execution.user_id = cast(:user_id as uuid)
                  and execution.work_item_key = :work_item_key
                  and not exists (
                    select 1 from learning.task_execution_versions successor
                    where successor.supersedes_execution_version_id =
                          execution.execution_version_id
                  )
                for update of execution
                """
                ),
                {"user_id": user_id, "work_item_key": work_item_key},
            )
            .mappings()
            .one_or_none()
        )

    def task_execution_by_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                select * from learning.task_execution_versions
                where user_id = cast(:user_id as uuid)
                  and command_id = :command_id
                """
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )

    def execution_evidence_exists(
        self,
        session: Session,
        *,
        user_id: str,
        task: RowMapping,
        evidence_type: str,
        owner: str,
        entity_id: str,
        version_id: str | None,
        submitted_evidence: dict[str, str],
    ) -> bool:
        expected_owners = {
            "essay_answer_version": "essay_intelligence",
            "essay_review_version": "essay_intelligence",
            "essay_rewrite_version": "essay_intelligence",
            "essay_transfer_verification": "essay_intelligence",
            "essay_material_annotation": "essay_intelligence",
            "essay_point_extraction": "essay_intelligence",
            "essay_outline": "essay_intelligence",
            "essay_review_acknowledgement": "essay_intelligence",
            "reflection_artifact": "essay_intelligence",
        }
        if expected_owners.get(evidence_type) != owner:
            return False
        try:
            UUID(entity_id)
        except ValueError:
            return False
        if version_id != entity_id:
            return False

        def expected_ref(entity_type: str) -> str | None:
            return next(
                (
                    str(item["entityId"])
                    for item in task["evidence_refs"]
                    if item.get("entityType") == entity_type
                ),
                None,
            )

        expected_task_version = expected_ref("essay_task_version")
        expected_material_version = expected_ref("essay_material_bundle_version")
        expected_rubric_version = expected_ref("essay_rubric_version")
        expected_attempt_id = expected_ref("essay_attempt")
        expected_answer_version = expected_ref("essay_answer_version")
        expected_review_version = expected_ref("essay_review_version")
        expected_rewrite_version = expected_ref("essay_rewrite_version")
        expected_source_task_version = expected_ref("essay_source_task_version")
        expected_source_material_version = expected_ref("essay_source_material_bundle_version")
        expected_source_answer_version = expected_ref("essay_source_answer_version")
        expected_finding_decision = expected_ref("essay_finding_decision")
        expected_mastery_versions = [
            str(item["versionId"])
            for item in task["evidence_refs"]
            if item.get("entityType") == "essay_mastery_state_version" and item.get("versionId")
        ]
        expected_material_annotation = expected_ref("essay_material_annotation")
        expected_point_extraction = expected_ref("essay_point_extraction")
        artifact_types = {
            "essay_material_annotation": "material_annotation",
            "essay_point_extraction": "point_extraction",
            "essay_outline": "outline",
            "essay_review_acknowledgement": "review_acknowledgement",
            "reflection_artifact": "reflection",
        }
        artifact_type = artifact_types.get(evidence_type)
        if artifact_type is not None:
            if expected_task_version is None or expected_material_version is None:
                return False
            rubric_required = artifact_type != "material_annotation"
            if rubric_required and expected_rubric_version is None:
                return False
            review_required = artifact_type == "review_acknowledgement"
            if review_required and (
                expected_review_version is None
                or expected_attempt_id is None
                or expected_answer_version is None
            ):
                return False
            reflection_required = artifact_type == "reflection"
            point_dependency_required = artifact_type == "point_extraction"
            outline_dependency_required = artifact_type == "outline"
            if point_dependency_required and expected_material_annotation is None:
                return False
            if outline_dependency_required and expected_point_extraction is None:
                return False
            submitted_answer_version = submitted_evidence.get("essay_answer_version")
            if reflection_required and (
                task["stage"] != "essay_drafting" or submitted_answer_version is None
            ):
                return False
            return (
                session.execute(
                    text(
                        """
                        select 1
                        from learning.essay_learning_artifact_versions artifact
                        left join learning.essay_review_versions review
                          on review.review_version_id = artifact.review_version_id
                        left join learning.essay_answer_versions answer
                          on answer.answer_version_id = artifact.answer_version_id
                        left join learning.essay_attempts answer_attempt
                          on answer_attempt.attempt_id = answer.attempt_id
                         and answer_attempt.user_id = answer.user_id
                         and answer_attempt.task_version_id = answer.task_version_id
                        where artifact.artifact_version_id = cast(:entity_id as uuid)
                          and artifact.user_id = cast(:user_id as uuid)
                          and artifact.artifact_type = :artifact_type
                          and artifact.task_version_id = cast(:task_version_id as uuid)
                          and artifact.material_bundle_version_id =
                              cast(:material_bundle_version_id as uuid)
                          and (
                            :rubric_required = false
                            or artifact.rubric_version_id = cast(:rubric_version_id as uuid)
                          )
                          and (
                            :review_required = false
                            or (
                              artifact.review_version_id = cast(:review_version_id as uuid)
                              and review.user_id = cast(:user_id as uuid)
                              and review.attempt_id = cast(:attempt_id as uuid)
                              and review.answer_version_id =
                                  cast(:expected_answer_version_id as uuid)
                              and review.task_version_id = cast(:task_version_id as uuid)
                              and review.material_bundle_version_id =
                                  cast(:material_bundle_version_id as uuid)
                              and review.rubric_version_id =
                                  cast(:rubric_version_id as uuid)
                              and review.status = 'completed'
                              and review.grounding_contract_version = 'essay-grounding-v2'
                              and review.grounding_report->>'verified' = 'true'
                              and review.grounding_report->>'eligibleForLearning' = 'true'
                              and review.execution_fingerprint is not null
                              and not exists (
                                select 1 from learning.essay_review_versions successor
                                where successor.supersedes_review_version_id =
                                      review.review_version_id
                              )
                            )
                          )
                          and (
                            :reflection_required = false
                            or (
                              artifact.answer_version_id =
                                  cast(:submitted_answer_version_id as uuid)
                              and answer.user_id = cast(:user_id as uuid)
                              and (
                                cast(:attempt_id as uuid) is null
                                or answer.attempt_id = cast(:attempt_id as uuid)
                              )
                              and answer.task_version_id = cast(:task_version_id as uuid)
                              and answer_attempt.material_bundle_version_id =
                                  cast(:material_bundle_version_id as uuid)
                              and answer_attempt.rubric_version_id =
                                  cast(:rubric_version_id as uuid)
                              and artifact.created_at >= answer.created_at
                              and not exists (
                                select 1 from learning.essay_answer_versions successor
                                where successor.supersedes_answer_version_id =
                                      answer.answer_version_id
                              )
                            )
                          )
                          and (
                            :point_dependency_required = false
                            or artifact.payload->>'sourceMaterialAnnotationVersionId' =
                               :material_annotation_version_id
                          )
                          and (
                            :outline_dependency_required = false
                            or artifact.payload->>'sourcePointExtractionVersionId' =
                               :point_extraction_version_id
                          )
                          and artifact.created_at >= :task_created_at
                          and not exists (
                            select 1
                            from learning.essay_learning_artifact_versions successor
                            where successor.supersedes_artifact_version_id =
                                  artifact.artifact_version_id
                          )
                        """
                    ),
                    {
                        "entity_id": entity_id,
                        "user_id": user_id,
                        "artifact_type": artifact_type,
                        "task_version_id": expected_task_version,
                        "material_bundle_version_id": expected_material_version,
                        "rubric_version_id": expected_rubric_version,
                        "review_version_id": expected_review_version,
                        "attempt_id": expected_attempt_id,
                        "expected_answer_version_id": expected_answer_version,
                        "submitted_answer_version_id": submitted_answer_version,
                        "rubric_required": rubric_required,
                        "review_required": review_required,
                        "reflection_required": reflection_required,
                        "point_dependency_required": point_dependency_required,
                        "outline_dependency_required": outline_dependency_required,
                        "material_annotation_version_id": expected_material_annotation,
                        "point_extraction_version_id": expected_point_extraction,
                        "task_created_at": task["created_at"],
                    },
                ).scalar_one_or_none()
                is not None
            )
        if (
            expected_task_version is None
            or expected_material_version is None
            or expected_rubric_version is None
        ):
            return False
        if evidence_type == "essay_answer_version":
            if task["stage"] != "essay_drafting":
                return False
            statement = """
                select 1
                from learning.essay_answer_versions answer
                join learning.essay_attempts attempt
                  on attempt.attempt_id = answer.attempt_id
                 and attempt.user_id = answer.user_id
                 and attempt.task_version_id = answer.task_version_id
                where answer.answer_version_id = cast(:entity_id as uuid)
                  and answer.user_id = cast(:user_id as uuid)
                  and (
                    cast(:attempt_id as uuid) is null
                    or answer.attempt_id = cast(:attempt_id as uuid)
                  )
                  and answer.task_version_id = cast(:task_version_id as uuid)
                  and attempt.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and attempt.rubric_version_id = cast(:rubric_version_id as uuid)
                  and answer.created_at >= :task_created_at
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id = answer.answer_version_id
                  )
            """
            params = {"attempt_id": expected_attempt_id}
        elif evidence_type == "essay_review_version":
            if expected_attempt_id is None or expected_answer_version is None:
                return False
            statement = """
                select 1 from learning.essay_review_versions review
                where review.review_version_id = cast(:entity_id as uuid)
                  and review.user_id = cast(:user_id as uuid)
                  and review.attempt_id = cast(:attempt_id as uuid)
                  and review.answer_version_id = cast(:answer_version_id as uuid)
                  and review.task_version_id = cast(:task_version_id as uuid)
                  and review.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and review.rubric_version_id = cast(:rubric_version_id as uuid)
                  and review.status = 'completed'
                  and review.grounding_contract_version = 'essay-grounding-v2'
                  and review.grounding_report->>'verified' = 'true'
                  and review.grounding_report->>'eligibleForLearning' = 'true'
                  and review.execution_fingerprint is not null
                  and not exists (
                    select 1 from learning.essay_review_versions successor
                    where successor.supersedes_review_version_id = review.review_version_id
                  )
            """
            params = {
                "attempt_id": expected_attempt_id,
                "answer_version_id": expected_answer_version,
            }
        elif evidence_type == "essay_rewrite_version":
            if (
                expected_attempt_id is None
                or expected_source_answer_version is None
                or expected_review_version is None
                or expected_finding_decision is None
                or not expected_mastery_versions
            ):
                return False
            statement = """
                select 1
                from learning.essay_rewrite_versions rewrite
                join learning.essay_attempts attempt
                  on attempt.attempt_id = rewrite.attempt_id
                 and attempt.user_id = rewrite.user_id
                 and attempt.task_version_id = rewrite.task_version_id
                where rewrite.rewrite_version_id = cast(:entity_id as uuid)
                  and rewrite.user_id = cast(:user_id as uuid)
                  and rewrite.attempt_id = cast(:attempt_id as uuid)
                  and rewrite.task_version_id = cast(:task_version_id as uuid)
                  and rewrite.base_review_version_id = cast(:review_version_id as uuid)
                  and rewrite.base_answer_version_id = cast(:answer_version_id as uuid)
                  and rewrite.finding_decision_artifact_version_id =
                      cast(:finding_decision_id as uuid)
                  and rewrite.writer_contract_version = 'essay-rewrite-writer@v2'
                  and rewrite.writer_policy_version = 'essay-mastery@v1'
                  and attempt.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and attempt.rubric_version_id = cast(:rubric_version_id as uuid)
                  and rewrite.created_at >= :task_created_at
                  and not exists (
                    select 1 from learning.essay_rewrite_versions successor
                    where successor.supersedes_rewrite_version_id =
                          rewrite.rewrite_version_id
                  )
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id =
                          rewrite.rewritten_answer_version_id
                  )
            """
            params = {
                "attempt_id": expected_attempt_id,
                "review_version_id": expected_review_version,
                "answer_version_id": expected_source_answer_version,
                "finding_decision_id": expected_finding_decision,
            }
        elif evidence_type == "essay_transfer_verification":
            if (
                expected_attempt_id is None
                or expected_review_version is None
                or expected_rewrite_version is None
                or expected_source_task_version is None
                or expected_source_material_version is None
                or expected_finding_decision is None
                or not expected_mastery_versions
            ):
                return False
            statement = """
                select 1
                from learning.essay_transfer_verification_versions transfer
                join learning.essay_rewrite_versions source_rewrite
                  on source_rewrite.rewrite_version_id = cast(:rewrite_version_id as uuid)
                 and transfer.source_rewrite_version_id =
                     source_rewrite.rewrite_version_id
                 and source_rewrite.user_id = transfer.user_id
                 and source_rewrite.attempt_id = transfer.source_attempt_id
                 and source_rewrite.base_review_version_id =
                     transfer.source_review_version_id
                join learning.essay_attempts transfer_attempt
                  on transfer_attempt.attempt_id = transfer.transfer_attempt_id
                 and transfer_attempt.user_id = transfer.user_id
                 and transfer_attempt.task_version_id = transfer.transfer_task_version_id
                where transfer.verification_version_id = cast(:entity_id as uuid)
                  and transfer.user_id = cast(:user_id as uuid)
                  and transfer.source_attempt_id = cast(:attempt_id as uuid)
                  and transfer.source_review_version_id = cast(:review_version_id as uuid)
                  and transfer.finding_decision_artifact_version_id =
                      cast(:finding_decision_id as uuid)
                  and transfer.writer_contract_version = 'essay-transfer-writer@v2'
                  and transfer.writer_policy_version = 'essay-mastery@v1'
                  and source_rewrite.task_version_id =
                      cast(:source_task_version_id as uuid)
                  and source_rewrite.writer_contract_version =
                      'essay-rewrite-writer@v2'
                  and exists (
                    select 1 from learning.essay_attempts source_attempt
                    where source_attempt.attempt_id = transfer.source_attempt_id
                      and source_attempt.user_id = transfer.user_id
                      and source_attempt.task_version_id =
                          cast(:source_task_version_id as uuid)
                      and source_attempt.material_bundle_version_id =
                          cast(:source_material_version_id as uuid)
                  )
                  and transfer.transfer_task_version_id = cast(:task_version_id as uuid)
                  and transfer.fresh_material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and transfer_attempt.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and transfer_attempt.rubric_version_id =
                      cast(:rubric_version_id as uuid)
                  and transfer.transfer_task_version_id <>
                      cast(:source_task_version_id as uuid)
                  and transfer.fresh_material_bundle_version_id <>
                      cast(:source_material_version_id as uuid)
                  and transfer.generated_at >= source_rewrite.created_at
                  and not exists (
                    select 1 from learning.essay_rewrite_versions successor
                    where successor.supersedes_rewrite_version_id =
                          source_rewrite.rewrite_version_id
                  )
                  and not exists (
                    select 1
                    from learning.essay_transfer_verification_versions successor
                    where successor.supersedes_verification_version_id =
                          transfer.verification_version_id
                  )
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id =
                          transfer.transfer_answer_version_id
                  )
            """
            params = {
                "attempt_id": expected_attempt_id,
                "review_version_id": expected_review_version,
                "rewrite_version_id": expected_rewrite_version,
                "finding_decision_id": expected_finding_decision,
                "source_task_version_id": expected_source_task_version,
                "source_material_version_id": expected_source_material_version,
            }
        else:
            return False
        canonical_evidence = (
            session.execute(
                text(statement),
                {
                    "entity_id": entity_id,
                    "user_id": user_id,
                    "task_version_id": expected_task_version,
                    "material_bundle_version_id": expected_material_version,
                    "rubric_version_id": expected_rubric_version,
                    "task_created_at": task["created_at"],
                    **params,
                },
            ).scalar_one_or_none()
            is not None
        )
        if not canonical_evidence:
            return False
        if evidence_type not in {
            "essay_rewrite_version",
            "essay_transfer_verification",
        }:
            return True
        link_column = (
            "rewrite_version_id"
            if evidence_type == "essay_rewrite_version"
            else "transfer_verification_version_id"
        )
        for mastery_version_id in expected_mastery_versions:
            linked = session.execute(
                text(
                    f"""
                    select 1
                    from learning.essay_mastery_state_versions source
                    join learning.essay_mastery_state_versions successor
                      on successor.supersedes_mastery_version_id =
                         source.mastery_version_id
                     and successor.mastery_id = source.mastery_id
                     and successor.user_id = source.user_id
                    where source.mastery_version_id = cast(:mastery_version_id as uuid)
                      and source.user_id = cast(:user_id as uuid)
                      and source.finding_decision_artifact_version_id =
                          cast(:finding_decision_id as uuid)
                      and successor.{link_column} = cast(:entity_id as uuid)
                      and successor.policy_version = 'essay-mastery@v1'
                    """
                ),
                {
                    "mastery_version_id": mastery_version_id,
                    "user_id": user_id,
                    "finding_decision_id": expected_finding_decision,
                    "entity_id": entity_id,
                },
            ).scalar_one_or_none()
            if linked is None:
                return False
        return True

    def insert_task_execution(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                insert into learning.task_execution_versions (
                  execution_version_id, command_id, user_id, local_date,
                  task_id, plan_version_id, plan_version_no, candidate_key,
                  work_item_key, version_no, status, elapsed_minutes, evidence_refs,
                  abandonment_reason, input_fingerprint,
                  supersedes_execution_version_id,
                  supersedes_execution_version_no, recorded_at
                ) values (
                  cast(:execution_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date,
                  cast(:task_id as uuid), cast(:plan_version_id as uuid),
                  :plan_version_no, :candidate_key, :work_item_key, :version_no, :status,
                  :elapsed_minutes, cast(:evidence_refs as jsonb),
                  :abandonment_reason, :input_fingerprint,
                  cast(:supersedes_execution_version_id as uuid),
                  :supersedes_execution_version_no, :recorded_at
                ) returning *
                """
                ),
                values,
            )
            .mappings()
            .one()
        )
