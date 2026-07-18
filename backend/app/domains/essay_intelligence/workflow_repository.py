from __future__ import annotations

from datetime import datetime
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.essay_intelligence.grounding import (
    GroundedReviewContext,
    GroundingValidationReport,
)
from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayAttempt,
    EssayMaterialBundleVersion,
    EssayReviewAudit,
    EssayReviewResponse,
    EssayRubricVersion,
    EssayScore,
    EssayScoreRange,
    EssayTaskVersion,
    EssayThinkingSnapshot,
)
from app.domains.essay_intelligence.workflow_schemas import (
    EssayReviewIntentView,
    EssayReviewQuota,
)


class EssayWorkflowContractError(RuntimeError):
    pass


class EssayWorkflowNotFound(LookupError):
    pass


class EssayReviewWorkflowRepository:
    def lock_user(self, session: Session, *, user_id: str) -> None:
        found = session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if found is None:
            raise EssayWorkflowNotFound("Learner does not exist")

    def attempt_source(
        self,
        session: Session,
        *,
        task_version_id: UUID,
        material_bundle_version_id: UUID,
        rubric_version_id: UUID,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select task.task_version_id, task.task_key
                  from product.essay_task_versions task
                  join product.essay_material_bundle_versions material
                    on material.material_bundle_version_id =
                       cast(:material_bundle_version_id as uuid)
                   and material.task_version_id = task.task_version_id
                   and material.completeness = 'complete'
                  join product.essay_rubric_versions rubric
                    on rubric.rubric_version_id = cast(:rubric_version_id as uuid)
                   and rubric.task_version_id = task.task_version_id
                   and rubric.material_bundle_version_id =
                       material.material_bundle_version_id
                   and rubric.state = 'published'
                 where task.task_version_id = cast(:task_version_id as uuid)
                   and task.state = 'published'
                   and exists (
                     select 1 from product.essay_material_segments segment
                      where segment.material_bundle_version_id =
                            material.material_bundle_version_id
                   )
                """
            ),
            {
                "task_version_id": str(task_version_id),
                "material_bundle_version_id": str(material_bundle_version_id),
                "rubric_version_id": str(rubric_version_id),
            },
        ).mappings().one_or_none()

    def attempt_by_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from learning.essay_attempts "
                "where user_id = cast(:user_id as uuid) and command_id = :command_id"
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def insert_attempt(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.essay_attempts (
                  attempt_id, command_id, user_id, task_version_id, task_key,
                  material_bundle_version_id, rubric_version_id, source_context,
                  timed_status, elapsed_seconds, input_fingerprint, submitted_at
                ) values (
                  cast(:attempt_id as uuid), :command_id, cast(:user_id as uuid),
                  cast(:task_version_id as uuid), :task_key,
                  cast(:material_bundle_version_id as uuid),
                  cast(:rubric_version_id as uuid), :source_context, :timed_status,
                  :elapsed_seconds, :input_fingerprint, :submitted_at
                )
                returning *
                """
            ),
            values,
        ).mappings().one()

    def owned_attempt(
        self,
        session: Session,
        *,
        user_id: str,
        attempt_id: UUID,
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from learning.essay_attempts "
                "where user_id = cast(:user_id as uuid) "
                "and attempt_id = cast(:attempt_id as uuid)"
            ),
            {"user_id": user_id, "attempt_id": str(attempt_id)},
        ).mappings().one_or_none()

    def answer_by_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                "select * from learning.essay_answer_versions "
                "where user_id = cast(:user_id as uuid) and command_id = :command_id"
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def current_answer(
        self,
        session: Session,
        *,
        user_id: str,
        attempt_id: UUID,
        for_update: bool = False,
    ) -> RowMapping | None:
        lock = "for update" if for_update else ""
        return session.execute(
            text(
                f"""
                select answer.* from learning.essay_answer_versions answer
                 where answer.user_id = cast(:user_id as uuid)
                   and answer.attempt_id = cast(:attempt_id as uuid)
                   and not exists (
                     select 1 from learning.essay_answer_versions successor
                      where successor.supersedes_answer_version_id =
                            answer.answer_version_id
                   )
                 order by answer.version_no desc
                 limit 1
                 {lock}
                """
            ),
            {"user_id": user_id, "attempt_id": str(attempt_id)},
        ).mappings().one_or_none()

    def current_review_candidate(
        self,
        session: Session,
        *,
        user_id: str,
        task_version_id: UUID,
        material_bundle_version_id: UUID,
        rubric_version_id: UUID,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select attempt.attempt_id, answer.answer_version_id
                  from learning.essay_attempts attempt
                  join lateral (
                    select item.answer_version_id
                      from learning.essay_answer_versions item
                     where item.user_id = attempt.user_id
                       and item.attempt_id = attempt.attempt_id
                       and item.task_version_id = attempt.task_version_id
                       and not exists (
                         select 1
                           from learning.essay_answer_versions successor
                          where successor.supersedes_answer_version_id =
                                item.answer_version_id
                       )
                     order by item.version_no desc
                     limit 1
                  ) answer on true
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
                "task_version_id": str(task_version_id),
                "material_bundle_version_id": str(material_bundle_version_id),
                "rubric_version_id": str(rubric_version_id),
            },
        ).mappings().one_or_none()

    def insert_answer(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.essay_answer_versions (
                  answer_version_id, command_id, attempt_id, user_id,
                  task_version_id, version_no, source_type, answer_text,
                  source_artifact_version_id, recognition_revision_id,
                  answer_sha256, thinking_payload, thinking_sha256,
                  input_fingerprint, supersedes_answer_version_id, created_at
                ) values (
                  cast(:answer_version_id as uuid), :command_id,
                  cast(:attempt_id as uuid), cast(:user_id as uuid),
                  cast(:task_version_id as uuid), :version_no, :source_type,
                  :answer_text,
                  cast(:source_artifact_version_id as uuid),
                  cast(:recognition_revision_id as uuid),
                  :answer_sha256, cast(:thinking_payload as jsonb),
                  :thinking_sha256, :input_fingerprint,
                  cast(:supersedes_answer_version_id as uuid), :created_at
                )
                returning *
                """
            ),
            values,
        ).mappings().one()

    def current_review_intent(
        self,
        session: Session,
        *,
        user_id: str,
        answer_version_id: UUID,
        review_scope: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select intent.*, job.status as job_status
                  from learning.essay_review_intent_versions intent
                  left join platform.background_jobs job
                    on job.job_id = intent.job_id
                 where intent.user_id = cast(:user_id as uuid)
                   and intent.answer_version_id = cast(:answer_version_id as uuid)
                   and intent.review_scope = :review_scope
                   and not exists (
                     select 1
                       from learning.essay_review_intent_versions successor
                      where successor.supersedes_intent_version_id =
                            intent.intent_version_id
                   )
                 order by intent.version_no desc
                 limit 1
                """
            ),
            {
                "user_id": user_id,
                "answer_version_id": str(answer_version_id),
                "review_scope": review_scope,
            },
        ).mappings().one_or_none()

    def insert_review_intent_version(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.essay_review_intent_versions (
                  intent_version_id, intent_id, command_id, user_id,
                  attempt_id, answer_version_id, task_version_id,
                  material_bundle_version_id, rubric_version_id, review_scope,
                  version_no, status, input_fingerprint, intent_fingerprint,
                  state_fingerprint, job_id, quota_feature, quota_usage_date,
                  quota_used, quota_limit, retry_after,
                  supersedes_intent_version_id, created_at
                ) values (
                  cast(:intent_version_id as uuid), cast(:intent_id as uuid),
                  :command_id, cast(:user_id as uuid), cast(:attempt_id as uuid),
                  cast(:answer_version_id as uuid), cast(:task_version_id as uuid),
                  cast(:material_bundle_version_id as uuid),
                  cast(:rubric_version_id as uuid), :review_scope,
                  :version_no, :status, :input_fingerprint, :intent_fingerprint,
                  :state_fingerprint, cast(:job_id as uuid), :quota_feature,
                  :quota_usage_date, :quota_used, :quota_limit, :retry_after,
                  cast(:supersedes_intent_version_id as uuid), :created_at
                )
                returning *
                """
            ),
            values,
        ).mappings().one()

    def load_context(
        self,
        session: Session,
        *,
        user_id: str,
        attempt_id: UUID,
        answer_version_id: UUID,
    ) -> GroundedReviewContext:
        row = session.execute(
            text(
                """
                select
                  attempt.attempt_id, attempt.user_id, attempt.task_version_id,
                  attempt.material_bundle_version_id, attempt.rubric_version_id,
                  attempt.source_context, attempt.timed_status,
                  attempt.elapsed_seconds, attempt.input_fingerprint as attempt_fingerprint,
                  attempt.submitted_at,
                  task.task_key, task.version_no as task_version_no,
                  task.question_id, task.question_type, task.prompt,
                  task.prompt_sha256, task.word_limit, task.maximum_score,
                  task.state as task_state,
                  task.supersedes_task_version_id, task.created_at as task_created_at,
                  material.version_no as material_version_no,
                  material.completeness, material.source_kind as material_source_kind,
                  material.content_sha256 as material_content_sha256,
                  material.supersedes_material_bundle_version_id,
                  material.created_at as material_created_at,
                  rubric.version_no as rubric_version_no,
                  rubric.source_kind as rubric_source_kind,
                  rubric.verification_status, rubric.state as rubric_state,
                  rubric.total_maximum, rubric.criteria, rubric.points,
                  rubric.content_sha256 as rubric_content_sha256,
                  rubric.supersedes_rubric_version_id,
                  rubric.created_at as rubric_created_at,
                  answer.answer_version_id, answer.version_no as answer_version_no,
                  answer.source_type, answer.answer_text, answer.answer_sha256,
                  answer.source_artifact_version_id,
                  answer.recognition_revision_id,
                  answer.thinking_payload, answer.thinking_sha256,
                  answer.input_fingerprint as answer_fingerprint,
                  answer.supersedes_answer_version_id,
                  answer.created_at as answer_created_at
                from learning.essay_attempts attempt
                join product.essay_task_versions task
                  on task.task_version_id = attempt.task_version_id
                join product.essay_material_bundle_versions material
                  on material.material_bundle_version_id =
                     attempt.material_bundle_version_id
                 and material.task_version_id = attempt.task_version_id
                join product.essay_rubric_versions rubric
                  on rubric.rubric_version_id = attempt.rubric_version_id
                 and rubric.task_version_id = attempt.task_version_id
                 and rubric.material_bundle_version_id =
                     attempt.material_bundle_version_id
                join learning.essay_answer_versions answer
                  on answer.answer_version_id = cast(:answer_version_id as uuid)
                 and answer.attempt_id = attempt.attempt_id
                 and answer.user_id = attempt.user_id
                 and answer.task_version_id = attempt.task_version_id
                where attempt.attempt_id = cast(:attempt_id as uuid)
                  and attempt.user_id = cast(:user_id as uuid)
                """
            ),
            {
                "user_id": user_id,
                "attempt_id": str(attempt_id),
                "answer_version_id": str(answer_version_id),
            },
        ).mappings().one_or_none()
        if row is None:
            raise EssayWorkflowNotFound(
                "Owned attempt, answer, and pinned review content do not exist"
            )
        segment_rows = session.execute(
            text(
                """
                select material_id, segment_id, material_order, segment_order,
                       text, text_sha256
                  from product.essay_material_segments
                 where material_bundle_version_id =
                       cast(:material_bundle_version_id as uuid)
                 order by material_order, segment_order, segment_id
                """
            ),
            {"material_bundle_version_id": str(row["material_bundle_version_id"])},
        ).mappings().all()
        return _context(row=row, segments=segment_rows)

    def review_by_id(
        self,
        session: Session,
        *,
        user_id: str,
        review_version_id: UUID,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select review.*, rubric.source_kind as rubric_source_kind,
                       rubric.verification_status as rubric_verification_status
                  from learning.essay_review_versions review
                  join product.essay_rubric_versions rubric
                    on rubric.rubric_version_id = review.rubric_version_id
                 where review.user_id = cast(:user_id as uuid)
                   and review.review_version_id = cast(:review_version_id as uuid)
                   and review.execution_fingerprint is not null
                """
            ),
            {"user_id": user_id, "review_version_id": str(review_version_id)},
        ).mappings().one_or_none()

    def latest_review_job(
        self,
        session: Session,
        *,
        user_id: str,
        input_fingerprint: str,
        job_type: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select job_id, status, payload
                  from platform.background_jobs
                 where job_type = :job_type
                   and payload->>'userId' = :user_id
                   and payload->>'inputFingerprint' = :input_fingerprint
                 order by (payload->>'executionAttemptNo')::integer desc,
                          created_at desc
                 limit 1
                """
            ),
            {
                "job_type": job_type,
                "user_id": user_id,
                "input_fingerprint": input_fingerprint,
            },
        ).mappings().one_or_none()

    def execution_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select command_id, user_id, command_kind,
                       request_fingerprint, job_id, created_at
                  from learning.essay_review_execution_commands
                 where command_id = :command_id
                   and user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id, "command_id": command_id},
        ).mappings().one_or_none()

    def insert_execution_command(
        self,
        session: Session,
        *,
        command_id: str,
        user_id: str,
        command_kind: str,
        request_fingerprint: str,
        job_id: UUID,
    ) -> RowMapping:
        return session.execute(
            text(
                """
                insert into learning.essay_review_execution_commands (
                  command_id, user_id, command_kind,
                  request_fingerprint, job_id
                ) values (
                  :command_id, cast(:user_id as uuid), :command_kind,
                  :request_fingerprint, cast(:job_id as uuid)
                )
                returning *
                """
            ),
            {
                "command_id": command_id,
                "user_id": user_id,
                "command_kind": command_kind,
                "request_fingerprint": request_fingerprint,
                "job_id": str(job_id),
            },
        ).mappings().one()

    def latest_review_for_input(
        self,
        session: Session,
        *,
        user_id: str,
        attempt_id: UUID,
        input_fingerprint: str,
    ) -> RowMapping | None:
        return session.execute(
            text(
                """
                select review.*, rubric.source_kind as rubric_source_kind,
                       rubric.verification_status as rubric_verification_status
                  from learning.essay_review_versions review
                  join product.essay_rubric_versions rubric
                    on rubric.rubric_version_id = review.rubric_version_id
                 where review.user_id = cast(:user_id as uuid)
                   and review.attempt_id = cast(:attempt_id as uuid)
                   and review.input_fingerprint = :input_fingerprint
                   and review.execution_fingerprint is not null
                 order by review.execution_attempt_no desc nulls last
                 limit 1
                """
            ),
            {
                "user_id": user_id,
                "attempt_id": str(attempt_id),
                "input_fingerprint": input_fingerprint,
            },
        ).mappings().one_or_none()

    def insert_review(
        self,
        session: Session,
        *,
        command_id: str,
        user_id: str,
        response: EssayReviewResponse,
        contract_version: str,
        provider_schema_version: str,
        rubric_contract_version: str,
        prompt_version: str,
        provider: str,
        model: str,
        input_fingerprint: str,
        execution_fingerprint: str,
        execution_attempt_no: int,
        output_fingerprint: str | None,
        generated_at: datetime,
        valid_until: datetime,
        grounding: GroundingValidationReport | None,
    ) -> RowMapping:
        score = response.score
        audit = response.audit
        return session.execute(
            text(
                """
                insert into learning.essay_review_versions (
                  review_version_id, command_id, attempt_id, user_id,
                  task_version_id, answer_version_id, material_bundle_version_id,
                  rubric_version_id, status, review_scope,
                  thinking_evidence_available, readiness,
                  score_lower, score_recommended, score_upper, score_maximum,
                  score_confidence, score_confidence_reasons,
                  grounding_contract_version, grounding_report,
                  dimensions, point_coverage, claim_evidence, findings,
                  rewrite_plan, contract_version, provider_schema_version,
                  rubric_contract_version, prompt_version, provider, model,
                  input_fingerprint, execution_fingerprint,
                  execution_attempt_no, output_fingerprint,
                  supersedes_review_version_id, generated_at, valid_until
                ) values (
                  cast(:review_version_id as uuid), :command_id,
                  cast(:attempt_id as uuid), cast(:user_id as uuid),
                  cast(:task_version_id as uuid), cast(:answer_version_id as uuid),
                  cast(:material_bundle_version_id as uuid),
                  cast(:rubric_version_id as uuid), :status, :review_scope,
                  :thinking_evidence_available, cast(:readiness as jsonb),
                  :score_lower, :score_recommended, :score_upper, :score_maximum,
                  :score_confidence, cast(:score_confidence_reasons as jsonb),
                  :grounding_contract_version, cast(:grounding_report as jsonb),
                  cast(:dimensions as jsonb), cast(:point_coverage as jsonb),
                  cast(:claim_evidence as jsonb), cast(:findings as jsonb),
                  cast(:rewrite_plan as jsonb), :contract_version,
                  :provider_schema_version, :rubric_contract_version,
                  :prompt_version, :provider, :model, :input_fingerprint,
                  :execution_fingerprint, :execution_attempt_no,
                  :output_fingerprint, cast(:supersedes_review_version_id as uuid),
                  :generated_at, :valid_until
                )
                returning *
                """
            ),
            {
                "review_version_id": str(response.review_version_id),
                "command_id": command_id,
                "attempt_id": str(response.attempt_id),
                "user_id": user_id,
                "task_version_id": str(response.task_version_id),
                "answer_version_id": str(response.answer_version_id),
                "material_bundle_version_id": str(
                    response.material_bundle_version_id
                ),
                "rubric_version_id": str(response.rubric_version_id),
                "status": response.status,
                "review_scope": response.review_scope,
                "thinking_evidence_available": response.thinking_evidence_available,
                "readiness": _json(response.readiness),
                "score_lower": score.score_range.lower if score else None,
                "score_recommended": score.score_range.recommended if score else None,
                "score_upper": score.score_range.upper if score else None,
                "score_maximum": score.maximum if score else None,
                "score_confidence": score.confidence if score else None,
                "score_confidence_reasons": (
                    json.dumps(score.confidence_reasons, ensure_ascii=False)
                    if score
                    else None
                ),
                "grounding_contract_version": (
                    grounding.contract_version if grounding else None
                ),
                "grounding_report": (
                    json.dumps(
                        {
                            "verified": True,
                            "eligibleForLearning": True,
                            "inputFingerprint": grounding.input_fingerprint,
                            "outputFingerprint": grounding.output_fingerprint,
                            "answerSegmentIds": list(grounding.answer_segment_ids),
                            "materialSegmentIds": list(grounding.material_segment_ids),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if grounding
                    else None
                ),
                "dimensions": _json(response.dimensions),
                "point_coverage": _json(response.point_coverage),
                "claim_evidence": _json(response.claim_evidence),
                "findings": _json(response.findings),
                "rewrite_plan": _json(response.rewrite_plan) if response.rewrite_plan else None,
                "contract_version": audit.contract_version if audit else contract_version,
                "provider_schema_version": (
                    audit.provider_schema_version if audit else provider_schema_version
                ),
                "rubric_contract_version": (
                    audit.rubric_version if audit else rubric_contract_version
                ),
                "prompt_version": audit.prompt_version if audit else prompt_version,
                "provider": audit.provider if audit else provider,
                "model": audit.model if audit else model,
                "input_fingerprint": input_fingerprint,
                "execution_fingerprint": execution_fingerprint,
                "execution_attempt_no": execution_attempt_no,
                "output_fingerprint": output_fingerprint,
                "supersedes_review_version_id": (
                    str(response.supersedes_review_version_id)
                    if response.supersedes_review_version_id
                    else None
                ),
                "generated_at": generated_at,
                "valid_until": valid_until,
            },
        ).mappings().one()

    def link_model_call(
        self,
        session: Session,
        *,
        review_version_id: UUID,
        call_id: UUID,
        phase: str,
        ordinal: int,
    ) -> None:
        session.execute(
            text(
                """
                insert into ai_audit.essay_review_model_calls (
                  review_version_id, call_id, phase, ordinal
                ) values (
                  cast(:review_version_id as uuid), cast(:call_id as uuid),
                  :phase, :ordinal
                )
                on conflict (review_version_id, call_id) do nothing
                """
            ),
            {
                "review_version_id": str(review_version_id),
                "call_id": str(call_id),
                "phase": phase,
                "ordinal": ordinal,
            },
        )


def attempt_from_row(row: RowMapping) -> EssayAttempt:
    return EssayAttempt(
        attempt_id=row["attempt_id"],
        user_id=row["user_id"],
        task_version_id=row["task_version_id"],
        material_bundle_version_id=row["material_bundle_version_id"],
        rubric_version_id=row["rubric_version_id"],
        source_context=row["source_context"],
        timed_status=row["timed_status"],
        elapsed_seconds=row["elapsed_seconds"],
        input_fingerprint=row["input_fingerprint"],
        submitted_at=row["submitted_at"],
    )


def answer_from_row(row: RowMapping) -> EssayAnswerVersion:
    thinking = (
        EssayThinkingSnapshot.model_validate(row["thinking_payload"])
        if row["thinking_payload"] is not None
        else None
    )
    return EssayAnswerVersion(
        answer_version_id=row["answer_version_id"],
        attempt_id=row["attempt_id"],
        user_id=row["user_id"],
        task_version_id=row["task_version_id"],
        version_no=row["version_no"],
        source_type=row["source_type"],
        source_artifact_version_id=row["source_artifact_version_id"],
        recognition_revision_id=row["recognition_revision_id"],
        answer_text=row["answer_text"],
        answer_sha256=row["answer_sha256"],
        thinking=thinking,
        thinking_sha256=row["thinking_sha256"],
        input_fingerprint=row["input_fingerprint"],
        supersedes_answer_version_id=row["supersedes_answer_version_id"],
        created_at=row["created_at"],
    )


def review_intent_from_row(row: RowMapping) -> EssayReviewIntentView:
    quota = None
    if row["quota_feature"] is not None:
        quota = EssayReviewQuota(
            feature=row["quota_feature"],
            used=int(row["quota_used"]),
            limit=int(row["quota_limit"]),
            usage_date=row["quota_usage_date"],
        )
    job_status = row.get("job_status")
    if job_status not in {None, "queued", "running", "succeeded", "failed"}:
        raise EssayWorkflowContractError(
            f"Review intent references unsupported job state {job_status}"
        )
    return EssayReviewIntentView(
        intent_id=row["intent_id"],
        intent_version_id=row["intent_version_id"],
        version_no=int(row["version_no"]),
        status=row["status"],
        attempt_id=row["attempt_id"],
        answer_version_id=row["answer_version_id"],
        task_version_id=row["task_version_id"],
        material_bundle_version_id=row["material_bundle_version_id"],
        rubric_version_id=row["rubric_version_id"],
        review_scope=row["review_scope"],
        input_fingerprint=row["input_fingerprint"],
        intent_fingerprint=row["intent_fingerprint"],
        state_fingerprint=row["state_fingerprint"],
        job_id=row["job_id"],
        job_status=job_status,
        quota=quota,
        retry_after=row["retry_after"],
        supersedes_intent_version_id=row["supersedes_intent_version_id"],
        created_at=row["created_at"],
    )


def review_from_row(row: RowMapping) -> EssayReviewResponse:
    score = None
    audit = None
    if row["status"] == "completed":
        score = EssayScore(
            maximum=float(row["score_maximum"]),
            score_range=EssayScoreRange(
                lower=float(row["score_lower"]),
                recommended=float(row["score_recommended"]),
                upper=float(row["score_upper"]),
            ),
            confidence=row["score_confidence"],
            confidence_reasons=list(row["score_confidence_reasons"]),
        )
        audit = EssayReviewAudit(
            contract_version=row["contract_version"],
            provider_schema_version=row["provider_schema_version"],
            rubric_version=row["rubric_contract_version"],
            rubric_source=row["rubric_source_kind"],
            rubric_verification=row["rubric_verification_status"],
            prompt_version=row["prompt_version"],
            provider=row["provider"],
            model=row["model"],
            input_fingerprint=row["input_fingerprint"],
            output_fingerprint=row["output_fingerprint"],
            generated_at=row["generated_at"],
            valid_until=row["valid_until"],
        )
    payload = {
            "reviewVersionId": row["review_version_id"],
            "attemptId": row["attempt_id"],
            "taskVersionId": row["task_version_id"],
            "materialBundleVersionId": row["material_bundle_version_id"],
            "answerVersionId": row["answer_version_id"],
            "rubricVersionId": row["rubric_version_id"],
            "status": row["status"],
            "reviewScope": row["review_scope"],
            "thinkingEvidenceAvailable": row["thinking_evidence_available"],
            "readiness": row["readiness"],
            "score": score.model_dump(by_alias=True, mode="json") if score else None,
            "dimensions": row["dimensions"],
            "pointCoverage": row["point_coverage"],
            "claimEvidence": row["claim_evidence"],
            "findings": row["findings"],
            "rewritePlan": row["rewrite_plan"],
            "audit": audit.model_dump(by_alias=True, mode="json") if audit else None,
            "supersedesReviewVersionId": row["supersedes_review_version_id"],
        }
    return EssayReviewResponse.model_validate_json(
        json.dumps(payload, ensure_ascii=False, default=str)
    )


def _context(
    *,
    row: RowMapping,
    segments: list[RowMapping],
) -> GroundedReviewContext:
    task = EssayTaskVersion.model_validate(
        {
            "taskVersionId": row["task_version_id"],
            "taskKey": row["task_key"],
            "versionNo": row["task_version_no"],
            "questionId": row["question_id"],
            "questionType": row["question_type"],
            "prompt": row["prompt"],
            "promptSha256": row["prompt_sha256"],
            "wordLimit": row["word_limit"],
            "maximumScore": float(row["maximum_score"]),
            "state": row["task_state"],
            "supersedesTaskVersionId": row["supersedes_task_version_id"],
            "createdAt": row["task_created_at"],
        }
    )
    material = EssayMaterialBundleVersion.model_validate(
        {
            "materialBundleVersionId": row["material_bundle_version_id"],
            "taskVersionId": row["task_version_id"],
            "versionNo": row["material_version_no"],
            "completeness": row["completeness"],
            "sourceKind": row["material_source_kind"],
            "contentSha256": row["material_content_sha256"],
            "segments": [
                {
                    "materialId": item["material_id"],
                    "segmentId": item["segment_id"],
                    "materialOrder": item["material_order"],
                    "segmentOrder": item["segment_order"],
                    "text": item["text"],
                    "textSha256": item["text_sha256"],
                }
                for item in segments
            ],
            "supersedesMaterialBundleVersionId": row[
                "supersedes_material_bundle_version_id"
            ],
            "createdAt": row["material_created_at"],
        }
    )
    rubric = EssayRubricVersion.model_validate_json(
        json.dumps({
            "rubricVersionId": row["rubric_version_id"],
            "taskVersionId": row["task_version_id"],
            "materialBundleVersionId": row["material_bundle_version_id"],
            "versionNo": row["rubric_version_no"],
            "sourceKind": row["rubric_source_kind"],
            "verificationStatus": row["verification_status"],
            "state": row["rubric_state"],
            "totalMaximum": float(row["total_maximum"]),
            "criteria": row["criteria"],
            "points": row["points"],
            "contentSha256": row["rubric_content_sha256"],
            "supersedesRubricVersionId": row["supersedes_rubric_version_id"],
            "createdAt": row["rubric_created_at"],
        }, ensure_ascii=False, default=str)
    )
    attempt = EssayAttempt(
        attempt_id=row["attempt_id"],
        user_id=row["user_id"],
        task_version_id=row["task_version_id"],
        material_bundle_version_id=row["material_bundle_version_id"],
        rubric_version_id=row["rubric_version_id"],
        source_context=row["source_context"],
        timed_status=row["timed_status"],
        elapsed_seconds=row["elapsed_seconds"],
        input_fingerprint=row["attempt_fingerprint"],
        submitted_at=row["submitted_at"],
    )
    answer = EssayAnswerVersion(
        answer_version_id=row["answer_version_id"],
        attempt_id=row["attempt_id"],
        user_id=row["user_id"],
        task_version_id=row["task_version_id"],
        version_no=row["answer_version_no"],
        source_type=row["source_type"],
        source_artifact_version_id=row["source_artifact_version_id"],
        recognition_revision_id=row["recognition_revision_id"],
        answer_text=row["answer_text"],
        answer_sha256=row["answer_sha256"],
        thinking=(
            EssayThinkingSnapshot.model_validate(row["thinking_payload"])
            if row["thinking_payload"] is not None
            else None
        ),
        thinking_sha256=row["thinking_sha256"],
        input_fingerprint=row["answer_fingerprint"],
        supersedes_answer_version_id=row["supersedes_answer_version_id"],
        created_at=row["answer_created_at"],
    )
    return GroundedReviewContext(
        task=task,
        material=material,
        rubric=rubric,
        attempt=attempt,
        answer=answer,
    )


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, mode="json")
    elif isinstance(value, list):
        value = [
            item.model_dump(by_alias=True, mode="json")
            if hasattr(item, "model_dump")
            else item
            for item in value
        ]
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
