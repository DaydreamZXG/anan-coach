from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class EssayArtifactContractError(RuntimeError):
    pass


class EssayArtifactNotFound(RuntimeError):
    pass


class EssayLearningArtifactRepository:
    def lock_user(self, session: Session, *, user_id: str) -> None:
        owner = session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if owner is None:
            raise EssayArtifactNotFound("Learner does not exist")

    def artifact_by_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.essay_learning_artifact_versions
                where user_id = cast(:user_id as uuid) and command_id = :command_id
                """
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def artifact_by_id(
        self,
        session: Session,
        *,
        user_id: str,
        artifact_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.essay_learning_artifact_versions
                where user_id = cast(:user_id as uuid)
                  and artifact_version_id = cast(:artifact_version_id as uuid)
                """
            ),
            {"user_id": user_id, "artifact_version_id": artifact_version_id},
        ).mappings().one_or_none()

    def current_in_scope(
        self,
        session: Session,
        *,
        user_id: str,
        scope_key: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        lock = "for update" if for_update else ""
        return session.execute(
            text(
                f"""
                select artifact.*
                from learning.essay_learning_artifact_versions artifact
                where artifact.user_id = cast(:user_id as uuid)
                  and artifact.scope_key = :scope_key
                  and not exists (
                    select 1 from learning.essay_learning_artifact_versions successor
                    where successor.supersedes_artifact_version_id =
                          artifact.artifact_version_id
                  )
                order by artifact.version_no desc
                limit 1
                {lock}
                """
            ),
            {"user_id": user_id, "scope_key": scope_key},
        ).mappings().one_or_none()

    def current_for_task(
        self,
        session: Session,
        *,
        user_id: str,
        task_version_id: str,
        material_bundle_version_id: str,
        rubric_version_id: str | None,
        review_version_id: str | None,
        answer_version_id: str | None,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select distinct on (artifact.artifact_type) artifact.*
                    from learning.essay_learning_artifact_versions artifact
                    where artifact.user_id = cast(:user_id as uuid)
                      and artifact.task_version_id = cast(:task_version_id as uuid)
                      and artifact.material_bundle_version_id =
                          cast(:material_bundle_version_id as uuid)
                      and (
                        (artifact.artifact_type = 'material_annotation'
                          and artifact.rubric_version_id is null)
                        or (artifact.artifact_type in ('point_extraction', 'outline')
                          and artifact.rubric_version_id = cast(:rubric_version_id as uuid))
                        or (artifact.artifact_type = 'review_acknowledgement'
                          and artifact.rubric_version_id = cast(:rubric_version_id as uuid)
                          and artifact.review_version_id = cast(:review_version_id as uuid))
                        or (artifact.artifact_type = 'reflection'
                          and artifact.rubric_version_id = cast(:rubric_version_id as uuid)
                          and artifact.answer_version_id = cast(:answer_version_id as uuid))
                      )
                      and not exists (
                        select 1 from learning.essay_learning_artifact_versions successor
                        where successor.supersedes_artifact_version_id =
                              artifact.artifact_version_id
                      )
                    order by artifact.artifact_type, artifact.created_at desc,
                             artifact.artifact_version_id desc
                    """
                ),
                {
                    "user_id": user_id,
                    "task_version_id": task_version_id,
                    "material_bundle_version_id": material_bundle_version_id,
                    "rubric_version_id": rubric_version_id,
                    "review_version_id": review_version_id,
                    "answer_version_id": answer_version_id,
                },
            ).mappings().all()
        )

    def consumable_content(
        self,
        session: Session,
        *,
        task_version_id: str,
        material_bundle_version_id: str,
        rubric_version_id: str | None,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select task.task_version_id, task.task_key, task.prompt,
                       task.question_type, task.word_limit, task.maximum_score,
                       material.material_bundle_version_id,
                       rubric.rubric_version_id
                from product.essay_task_versions task
                join product.essay_material_bundle_versions material
                  on material.material_bundle_version_id =
                     cast(:material_bundle_version_id as uuid)
                 and material.task_version_id = task.task_version_id
                 and material.completeness = 'complete'
                left join product.essay_rubric_versions rubric
                  on rubric.rubric_version_id = cast(:rubric_version_id as uuid)
                 and rubric.task_version_id = task.task_version_id
                 and rubric.material_bundle_version_id =
                     material.material_bundle_version_id
                 and rubric.state = 'published'
                where task.task_version_id = cast(:task_version_id as uuid)
                  and task.state = 'published'
                  and (:rubric_version_id is null or rubric.rubric_version_id is not null)
                  and exists (
                    select 1 from product.essay_material_segments segment
                    where segment.material_bundle_version_id =
                          material.material_bundle_version_id
                  )
                """
            ),
            {
                "task_version_id": task_version_id,
                "material_bundle_version_id": material_bundle_version_id,
                "rubric_version_id": rubric_version_id,
            },
        ).mappings().one_or_none()

    def material_segment_ids(
        self,
        session: Session,
        *,
        material_bundle_version_id: str,
    ) -> set[str]:
        return set(
            session.execute(
                text(
                    """
                    select segment_id
                    from product.essay_material_segments
                    where material_bundle_version_id =
                          cast(:material_bundle_version_id as uuid)
                    """
                ),
                {"material_bundle_version_id": material_bundle_version_id},
            ).scalars()
        )

    def current_dependency(
        self,
        session: Session,
        *,
        user_id: str,
        artifact_version_id: str,
        artifact_type: str,
        task_version_id: str,
        material_bundle_version_id: str,
        rubric_version_id: str | None,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select dependency.*
                from learning.essay_learning_artifact_versions dependency
                where dependency.artifact_version_id =
                      cast(:artifact_version_id as uuid)
                  and dependency.user_id = cast(:user_id as uuid)
                  and dependency.artifact_type = :artifact_type
                  and dependency.task_version_id = cast(:task_version_id as uuid)
                  and dependency.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and dependency.rubric_version_id is not distinct from
                      cast(:rubric_version_id as uuid)
                  and not exists (
                    select 1 from learning.essay_learning_artifact_versions successor
                    where successor.supersedes_artifact_version_id =
                          dependency.artifact_version_id
                  )
                """
            ),
            {
                "artifact_version_id": artifact_version_id,
                "user_id": user_id,
                "artifact_type": artifact_type,
                "task_version_id": task_version_id,
                "material_bundle_version_id": material_bundle_version_id,
                "rubric_version_id": rubric_version_id,
            },
        ).mappings().one_or_none()

    def completed_review(
        self,
        session: Session,
        *,
        user_id: str,
        review_version_id: str,
        task_version_id: str,
        material_bundle_version_id: str,
        rubric_version_id: str,
        answer_version_id: str | None = None,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select * from learning.essay_review_versions
                where review_version_id = cast(:review_version_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and task_version_id = cast(:task_version_id as uuid)
                  and material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and rubric_version_id = cast(:rubric_version_id as uuid)
                  and (
                    cast(:answer_version_id as uuid) is null
                    or answer_version_id = cast(:answer_version_id as uuid)
                  )
                  and status = 'completed'
                  and grounding_contract_version = 'essay-grounding-v2'
                  and grounding_report->>'verified' = 'true'
                  and grounding_report->>'eligibleForLearning' = 'true'
                  and execution_fingerprint is not null
                  and not exists (
                    select 1 from learning.essay_review_versions successor
                    where successor.supersedes_review_version_id =
                          essay_review_versions.review_version_id
                  )
                """
            ),
            {
                "user_id": user_id,
                "review_version_id": review_version_id,
                "task_version_id": task_version_id,
                "material_bundle_version_id": material_bundle_version_id,
                "rubric_version_id": rubric_version_id,
                "answer_version_id": answer_version_id,
            },
        ).mappings().one_or_none()

    def owned_answer(
        self,
        session: Session,
        *,
        user_id: str,
        answer_version_id: str,
        task_version_id: str,
        material_bundle_version_id: str,
        rubric_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select answer.*
                from learning.essay_answer_versions answer
                join learning.essay_attempts attempt
                  on attempt.attempt_id = answer.attempt_id
                 and attempt.user_id = answer.user_id
                 and attempt.task_version_id = answer.task_version_id
                where answer.answer_version_id = cast(:answer_version_id as uuid)
                  and answer.user_id = cast(:user_id as uuid)
                  and answer.task_version_id = cast(:task_version_id as uuid)
                  and attempt.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and attempt.rubric_version_id = cast(:rubric_version_id as uuid)
                  and not exists (
                    select 1 from learning.essay_answer_versions successor
                    where successor.supersedes_answer_version_id =
                          answer.answer_version_id
                  )
                """
            ),
            {
                "user_id": user_id,
                "answer_version_id": answer_version_id,
                "task_version_id": task_version_id,
                "material_bundle_version_id": material_bundle_version_id,
                "rubric_version_id": rubric_version_id,
            },
        ).mappings().one_or_none()

    def insert_artifact(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.essay_learning_artifact_versions (
                  artifact_version_id, command_id, user_id, artifact_type,
                  scope_key, task_version_id, material_bundle_version_id,
                  rubric_version_id, review_version_id, answer_version_id,
                  version_no, payload, payload_sha256, input_fingerprint,
                  supersedes_artifact_version_id, created_at
                ) values (
                  cast(:artifact_version_id as uuid), :command_id,
                  cast(:user_id as uuid), :artifact_type, :scope_key,
                  cast(:task_version_id as uuid),
                  cast(:material_bundle_version_id as uuid),
                  cast(:rubric_version_id as uuid), cast(:review_version_id as uuid),
                  cast(:answer_version_id as uuid), :version_no,
                  cast(:payload as jsonb), :payload_sha256, :input_fingerprint,
                  cast(:supersedes_artifact_version_id as uuid), :created_at
                ) returning *
                """
            ),
            {
                **values,
                "payload": json.dumps(
                    values["payload"], ensure_ascii=False, sort_keys=True
                ),
            },
        ).mappings().one()

    def workspace_content(
        self,
        session: Session,
        *,
        task_version_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select task.*,
                       material.material_bundle_version_id,
                       material.source_kind as material_source_kind,
                       material.content_sha256 as material_content_sha256,
                       rubric.rubric_version_id,
                       rubric.source_kind as rubric_source_kind,
                       rubric.verification_status as rubric_verification_status,
                       rubric.total_maximum, rubric.criteria, rubric.points,
                       rubric.content_sha256 as rubric_content_sha256
                from product.essay_task_versions task
                join lateral (
                  select item.*
                  from product.essay_material_bundle_versions item
                  where item.task_version_id = task.task_version_id
                    and item.completeness = 'complete'
                    and not exists (
                      select 1 from product.essay_material_bundle_versions successor
                      where successor.task_version_id = item.task_version_id
                        and successor.version_no > item.version_no
                        and successor.completeness = 'complete'
                    )
                  order by item.version_no desc, item.created_at desc
                  limit 1
                ) material on true
                left join lateral (
                  select item.*
                  from product.essay_rubric_versions item
                  where item.task_version_id = task.task_version_id
                    and item.material_bundle_version_id =
                        material.material_bundle_version_id
                    and item.state = 'published'
                    and not exists (
                      select 1 from product.essay_rubric_versions successor
                      where successor.task_version_id = item.task_version_id
                        and successor.material_bundle_version_id =
                            item.material_bundle_version_id
                        and successor.version_no > item.version_no
                        and successor.state = 'published'
                    )
                  order by item.version_no desc, item.created_at desc
                  limit 1
                ) rubric on true
                where task.task_version_id = cast(:task_version_id as uuid)
                  and task.state = 'published'
                  and not exists (
                    select 1 from product.essay_task_versions successor
                    where successor.task_key = task.task_key
                      and successor.version_no > task.version_no
                      and successor.state = 'published'
                  )
                  and exists (
                    select 1 from product.essay_material_segments segment
                    where segment.material_bundle_version_id =
                          material.material_bundle_version_id
                  )
                """
            ),
            {"task_version_id": task_version_id},
        ).mappings().one_or_none()

    def workspace_segments(
        self,
        session: Session,
        *,
        material_bundle_version_id: str,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select * from product.essay_material_segments
                    where material_bundle_version_id =
                          cast(:material_bundle_version_id as uuid)
                    order by material_order, segment_order, segment_id
                    """
                ),
                {"material_bundle_version_id": material_bundle_version_id},
            ).mappings().all()
        )

    def workspace_progression(
        self,
        session: Session,
        *,
        user_id: str,
        task_version_id: str,
        material_bundle_version_id: str,
        rubric_version_id: str | None,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select attempt.attempt_id,
                       answer.answer_version_id,
                       answer.user_id as answer_user_id,
                       answer.task_version_id as answer_task_version_id,
                       answer.version_no as answer_version_no,
                       answer.source_type as answer_source_type,
                       answer.source_artifact_version_id as answer_source_artifact_version_id,
                       answer.recognition_revision_id as answer_recognition_revision_id,
                       answer.answer_text,
                       answer.answer_sha256,
                       answer.thinking_payload,
                       answer.thinking_sha256,
                       answer.input_fingerprint as answer_input_fingerprint,
                       answer.supersedes_answer_version_id,
                       answer.created_at as answer_created_at,
                       review.review_version_id, review.status as review_status,
                       review_job.job_id as review_job_id,
                       review_intent.intent_id as review_intent_id,
                       review_intent.intent_version_id as review_intent_version_id,
                       review_intent.version_no as review_intent_version_no,
                       review_intent.status as review_intent_status,
                       review_intent.task_version_id as review_intent_task_version_id,
                       review_intent.material_bundle_version_id as review_intent_material_version_id,
                       review_intent.rubric_version_id as review_intent_rubric_version_id,
                       review_intent.review_scope as review_intent_scope,
                       review_intent.input_fingerprint as review_intent_input_fingerprint,
                       review_intent.intent_fingerprint as review_intent_fingerprint,
                       review_intent.state_fingerprint as review_intent_state_fingerprint,
                       review_intent.job_id as review_intent_job_id,
                       review_intent_job.status as review_intent_job_status,
                       review_intent.quota_feature as review_intent_quota_feature,
                       review_intent.quota_usage_date as review_intent_quota_usage_date,
                       review_intent.quota_used as review_intent_quota_used,
                       review_intent.quota_limit as review_intent_quota_limit,
                       review_intent.retry_after as review_intent_retry_after,
                       review_intent.supersedes_intent_version_id as review_intent_supersedes_id,
                       review_intent.created_at as review_intent_created_at,
                       review.findings as review_findings,
                       rewrite.rewrite_version_id,
                       transfer.verification_version_id,
                       transfer.status as transfer_status
                from learning.essay_attempts attempt
                left join lateral (
                  select item.* from learning.essay_answer_versions item
                  where item.attempt_id = attempt.attempt_id
                    and not exists (
                      select 1 from learning.essay_answer_versions successor
                      where successor.supersedes_answer_version_id = item.answer_version_id
                    )
                  order by item.version_no desc limit 1
                ) answer on true
                left join lateral (
                  select item.*
                  from learning.essay_review_intent_versions item
                  where item.user_id = attempt.user_id
                    and item.attempt_id = attempt.attempt_id
                    and item.answer_version_id = answer.answer_version_id
                    and item.review_scope = 'grounded_full'
                    and not exists (
                      select 1
                      from learning.essay_review_intent_versions successor
                      where successor.supersedes_intent_version_id =
                            item.intent_version_id
                    )
                  order by item.version_no desc
                  limit 1
                ) review_intent on true
                left join platform.background_jobs review_intent_job
                  on review_intent_job.job_id = review_intent.job_id
                left join lateral (
                  select item.* from learning.essay_review_versions item
                  where item.attempt_id = attempt.attempt_id
                    and item.answer_version_id = answer.answer_version_id
                    and item.execution_fingerprint is not null
                    and not exists (
                      select 1 from learning.essay_review_versions successor
                      where successor.supersedes_review_version_id = item.review_version_id
                    )
                  order by item.generated_at desc limit 1
                ) review on true
                left join lateral (
                  select job.job_id
                  from platform.background_jobs job
                  where job.job_type = 'essay_grounded_review_v2'
                    and job.payload->>'userId' = :user_id
                    and job.payload->'request'->>'attemptId' =
                        attempt.attempt_id::text
                    and job.payload->'request'->>'answerVersionId' =
                        answer.answer_version_id::text
                  order by job.created_at desc, job.job_id desc
                  limit 1
                ) review_job on true
                left join lateral (
                  select item.* from learning.essay_rewrite_versions item
                  where item.base_review_version_id = review.review_version_id
                    and not exists (
                      select 1 from learning.essay_rewrite_versions successor
                      where successor.supersedes_rewrite_version_id = item.rewrite_version_id
                    )
                  order by item.created_at desc limit 1
                ) rewrite on true
                left join lateral (
                  select item.* from learning.essay_transfer_verification_versions item
                  where item.source_review_version_id = review.review_version_id
                    and not exists (
                      select 1 from learning.essay_transfer_verification_versions successor
                      where successor.supersedes_verification_version_id =
                            item.verification_version_id
                    )
                  order by item.generated_at desc limit 1
                ) transfer on true
                where attempt.user_id = cast(:user_id as uuid)
                  and attempt.task_version_id = cast(:task_version_id as uuid)
                  and attempt.material_bundle_version_id =
                      cast(:material_bundle_version_id as uuid)
                  and attempt.rubric_version_id = cast(:rubric_version_id as uuid)
                order by attempt.submitted_at desc, attempt.attempt_id desc
                limit 1
                """
            ),
            {
                "user_id": user_id,
                "task_version_id": task_version_id,
                "material_bundle_version_id": material_bundle_version_id,
                "rubric_version_id": rubric_version_id,
            },
        ).mappings().one_or_none()
