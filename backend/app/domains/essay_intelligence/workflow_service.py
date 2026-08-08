from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domains.commerce.repository import (
    CommerceQuotaExceededError,
)
from app.domains.commerce.service import CommerceService
from app.domains.essay_intelligence.grounding import review_input_fingerprint
from app.domains.essay_intelligence.multi_model_runtime import (
    EssayMultiModelRuntimeConfigurationError,
    EssayMultiModelRuntimePlan,
    EssayReviewExecutionIdentity,
)
from app.domains.essay_intelligence.review_contracts import (
    _fingerprint,
    review_execution_fingerprint,
)
from app.domains.essay_intelligence.schemas import EssayReviewRequest
from app.domains.essay_intelligence.workflow_repository import (
    EssayReviewWorkflowRepository,
    EssayWorkflowContractError,
    EssayWorkflowNotFound,
    answer_from_row,
    attempt_from_row,
    review_intent_from_row,
    review_from_row,
)
from app.domains.essay_intelligence.workflow_schemas import (
    EssayAnswerCreateCommand,
    EssayAnswerCreateResult,
    EssayAttemptCreateCommand,
    EssayAttemptCreateResult,
    EssayCurrentReviewEnsureCommand,
    EssayCurrentReviewEnsureResult,
    EssayReviewIntentView,
    EssayReviewJobView,
    EssayReviewQueueCommand,
    EssayReviewQuota,
)
from app.contracts.external_learning.models import CandidateState, RecognitionKind
from app.domains.external_ingestion.recognition.models import (
    RecognitionCandidatePayload,
)
from app.domains.external_ingestion.recognition.repository import (
    SQLRecognitionRepository,
)
from app.platform.jobs import BackgroundJob, BackgroundJobRepository
from app.platform.outbox import OutboxRepository


ESSAY_REVIEW_JOB_TYPE = "essay_grounded_review_v2"
ESSAY_REVIEW_JOB_INPUT_VERSION = "essay-grounded-review-job-v2"


class EssayReviewWorkerUnavailable(RuntimeError):
    """Raised when a public write would enqueue work with no enabled consumer."""


class EssayReviewWorkflowService:
    def __init__(
        self,
        *,
        repository: EssayReviewWorkflowRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
        outbox: OutboxRepository | None = None,
        settings: Settings | None = None,
        execution_identity_override: EssayReviewExecutionIdentity | None = None,
    ) -> None:
        self._repository = repository or EssayReviewWorkflowRepository()
        self._jobs = jobs or BackgroundJobRepository()
        self._outbox = outbox or OutboxRepository()
        self._settings = settings or get_settings()
        if (
            execution_identity_override is not None
            and self._settings.environment.strip().casefold() == "production"
        ):
            raise EssayWorkflowContractError(
                "Production review queues cannot override the formal grading plan"
            )
        self._execution_identity_override = execution_identity_override

    def create_attempt(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayAttemptCreateCommand,
        now: datetime | None = None,
    ) -> EssayAttemptCreateResult:
        submitted_at = now or datetime.now(UTC)
        fingerprint = _fingerprint(
            {
                "contractVersion": "essay-attempt-command-v1",
                "userId": user_id,
                "commandId": command_id,
                "command": command.model_dump(by_alias=True, mode="json"),
            }
        )
        replay = self._repository.attempt_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            _require_fingerprint(replay["input_fingerprint"], fingerprint)
            return EssayAttemptCreateResult(attempt=attempt_from_row(replay))

        self._repository.lock_user(session, user_id=user_id)
        replay = self._repository.attempt_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            _require_fingerprint(replay["input_fingerprint"], fingerprint)
            return EssayAttemptCreateResult(attempt=attempt_from_row(replay))
        source = self._repository.attempt_source(
            session,
            task_version_id=command.task_version_id,
            material_bundle_version_id=command.material_bundle_version_id,
            rubric_version_id=command.rubric_version_id,
        )
        if source is None:
            raise EssayWorkflowContractError(
                "Attempt requires an exact published task, complete material, and "
                "published rubric bound to that material version"
            )
        row = self._repository.insert_attempt(
            session,
            values={
                "attempt_id": str(uuid4()),
                "command_id": command_id,
                "user_id": user_id,
                "task_version_id": str(command.task_version_id),
                "task_key": source["task_key"],
                "material_bundle_version_id": str(command.material_bundle_version_id),
                "rubric_version_id": str(command.rubric_version_id),
                "source_context": command.source_context,
                "timed_status": command.timed_status,
                "elapsed_seconds": command.elapsed_seconds,
                "input_fingerprint": fingerprint,
                "submitted_at": submitted_at,
            },
        )
        attempt = attempt_from_row(row)
        self._outbox.append(
            session,
            deduplication_key=f"essay-attempt:{attempt.attempt_id}",
            aggregate_type="essay_attempt",
            aggregate_id=str(attempt.attempt_id),
            event_type="essay_attempt.created",
            payload={
                "attemptId": str(attempt.attempt_id),
                "userId": user_id,
                "taskVersionId": str(attempt.task_version_id),
                "materialBundleVersionId": str(attempt.material_bundle_version_id),
                "rubricVersionId": str(attempt.rubric_version_id),
                "inputFingerprint": fingerprint,
            },
        )
        return EssayAttemptCreateResult(attempt=attempt)

    def create_answer(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayAnswerCreateCommand,
        now: datetime | None = None,
    ) -> EssayAnswerCreateResult:
        if not command.answer_text.strip():
            raise EssayWorkflowContractError("Essay answer cannot be blank")
        created_at = now or datetime.now(UTC)
        command_payload = command.model_dump(by_alias=True, mode="json")
        fingerprint = _fingerprint(
            {
                "contractVersion": "essay-answer-command-v1",
                "userId": user_id,
                "commandId": command_id,
                "command": command_payload,
            }
        )
        replay = self._repository.answer_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            _require_fingerprint(replay["input_fingerprint"], fingerprint)
            return EssayAnswerCreateResult(answer=answer_from_row(replay))

        self._repository.lock_user(session, user_id=user_id)
        replay = self._repository.answer_by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            _require_fingerprint(replay["input_fingerprint"], fingerprint)
            return EssayAnswerCreateResult(answer=answer_from_row(replay))
        attempt = self._repository.owned_attempt(
            session,
            user_id=user_id,
            attempt_id=command.attempt_id,
        )
        if attempt is None:
            raise EssayWorkflowNotFound("Owned essay attempt does not exist")
        current = self._repository.current_answer(
            session,
            user_id=user_id,
            attempt_id=command.attempt_id,
            for_update=True,
        )
        based_on = command.based_on_answer_version_id
        if current is None and based_on is not None:
            raise EssayWorkflowContractError("Attempt has no answer version to supersede")
        if current is not None and (based_on is None or based_on != current["answer_version_id"]):
            raise EssayWorkflowContractError(
                "Answer changed; reload the current version before saving"
            )
        source_evidence = self._validated_answer_source_evidence(
            session,
            user_id=user_id,
            command=command,
            at=created_at,
        )
        thinking = (
            command.thinking.model_dump(by_alias=True, mode="json") if command.thinking else None
        )
        row = self._repository.insert_answer(
            session,
            values={
                "answer_version_id": str(uuid4()),
                "command_id": command_id,
                "attempt_id": str(command.attempt_id),
                "user_id": user_id,
                "task_version_id": str(attempt["task_version_id"]),
                "version_no": int(current["version_no"]) + 1 if current else 1,
                "source_type": command.source_type,
                "source_artifact_version_id": source_evidence[0],
                "recognition_revision_id": source_evidence[1],
                "answer_text": command.answer_text,
                "answer_sha256": sha256(command.answer_text.encode("utf-8")).hexdigest(),
                "thinking_payload": (
                    json.dumps(thinking, ensure_ascii=False, sort_keys=True)
                    if thinking is not None
                    else None
                ),
                "thinking_sha256": (_fingerprint(thinking) if thinking is not None else None),
                "input_fingerprint": fingerprint,
                "supersedes_answer_version_id": (
                    str(current["answer_version_id"]) if current else None
                ),
                "created_at": created_at,
            },
        )
        answer = answer_from_row(row)
        self._outbox.append(
            session,
            deduplication_key=f"essay-answer:{answer.answer_version_id}",
            aggregate_type="essay_answer",
            aggregate_id=str(answer.answer_version_id),
            event_type="essay_answer.recorded",
            payload={
                "answerVersionId": str(answer.answer_version_id),
                "attemptId": str(answer.attempt_id),
                "userId": user_id,
                "taskVersionId": str(answer.task_version_id),
                "versionNo": answer.version_no,
                "sourceType": answer.source_type,
                "sourceArtifactVersionId": (
                    str(answer.source_artifact_version_id)
                    if answer.source_artifact_version_id is not None
                    else None
                ),
                "recognitionRevisionId": (
                    str(answer.recognition_revision_id)
                    if answer.recognition_revision_id is not None
                    else None
                ),
                "answerSha256": answer.answer_sha256,
                "thinkingSha256": answer.thinking_sha256,
                "inputFingerprint": fingerprint,
            },
        )
        return EssayAnswerCreateResult(answer=answer)

    def _validated_answer_source_evidence(
        self,
        session: Session,
        *,
        user_id: str,
        command: EssayAnswerCreateCommand,
        at: datetime,
    ) -> tuple[str | None, str | None]:
        if command.source_type == "typed":
            return None, None
        assert command.source_artifact_version_id is not None
        assert command.recognition_revision_id is not None
        repository = SQLRecognitionRepository(session)
        revision = repository.revision(recognition_revision_id=command.recognition_revision_id)
        if revision is None or str(revision.owner_user_id) != user_id:
            raise EssayWorkflowContractError(
                "OCR source recognition does not belong to the learner"
            )
        if revision.artifact_version_id != command.source_artifact_version_id:
            raise EssayWorkflowContractError(
                "OCR source artifact does not match the recognition revision"
            )
        if revision.recognition_kind != RecognitionKind.HANDWRITING_OCR:
            raise EssayWorkflowContractError(
                "Essay OCR answers require a handwriting recognition revision"
            )
        head = repository.head(
            artifact_version_id=revision.artifact_version_id,
            owner_user_id=revision.owner_user_id,
            recognition_kind=revision.recognition_kind.value,
        )
        if head is None or head.recognition_revision_id != revision.recognition_revision_id:
            raise EssayWorkflowContractError(
                "OCR source recognition has been superseded; confirm the current revision"
            )
        if revision.state != CandidateState.CONFIRMED or revision.expires_at <= at:
            raise EssayWorkflowContractError(
                "OCR source recognition must be confirmed and unexpired"
            )
        artifact = repository.artifact(
            artifact_version_id=revision.artifact_version_id,
            owner_user_id=revision.owner_user_id,
        )
        if artifact is None:
            raise EssayWorkflowContractError("OCR source artifact is unavailable")
        payload = RecognitionCandidatePayload.model_validate(revision.candidate_payload)
        canonical_text = "\n".join(span.text for span in payload.text_spans)
        if command.answer_text != canonical_text:
            raise EssayWorkflowContractError(
                "Essay answer text must exactly match the confirmed OCR revision"
            )
        return str(revision.artifact_version_id), str(revision.recognition_revision_id)

    def ensure_review_intent(
        self,
        session: Session,
        *,
        user_id: str,
        attempt_id: UUID,
        answer_version_id: UUID,
        review_scope: str = "grounded_full",
        now: datetime | None = None,
    ) -> EssayReviewIntentView:
        """Register the durable review obligation without touching paid quota."""

        self._repository.lock_user(session, user_id=user_id)
        request, input_fingerprint = self._current_review_request(
            session,
            user_id=user_id,
            command=EssayReviewQueueCommand(
                attempt_id=attempt_id,
                answer_version_id=answer_version_id,
                review_scope=review_scope,
            ),
        )
        return self._ensure_review_intent_locked(
            session,
            user_id=user_id,
            request=request,
            input_fingerprint=input_fingerprint,
            now=now,
        )

    def ensure_current_review(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayCurrentReviewEnsureCommand,
        commerce_service: CommerceService,
    ) -> EssayCurrentReviewEnsureResult:
        """Idempotently queue the server-owned current answer on any device."""

        self._require_worker_available()
        self._repository.lock_user(session, user_id=user_id)
        candidate = self._repository.current_review_candidate(
            session,
            user_id=user_id,
            task_version_id=command.task_version_id,
            material_bundle_version_id=command.material_bundle_version_id,
            rubric_version_id=command.rubric_version_id,
        )
        if candidate is None:
            raise EssayWorkflowNotFound(
                "Owned current essay answer does not exist for the pinned content"
            )
        queue_command = EssayReviewQueueCommand(
            attempt_id=candidate["attempt_id"],
            answer_version_id=candidate["answer_version_id"],
            review_scope=command.review_scope,
        )
        try:
            job = self.queue_review(
                session,
                user_id=user_id,
                command_id=command_id,
                command=queue_command,
                commerce_service=commerce_service,
            )
        except CommerceQuotaExceededError:
            intent_row = self._repository.current_review_intent(
                session,
                user_id=user_id,
                answer_version_id=queue_command.answer_version_id,
                review_scope=queue_command.review_scope,
            )
            if intent_row is None or intent_row["status"] != "waiting_quota":
                raise EssayWorkflowContractError(
                    "Quota denial did not persist a review waiting state"
                )
            return EssayCurrentReviewEnsureResult(
                intent=review_intent_from_row(intent_row),
                job=None,
            )

        intent_row = self._repository.current_review_intent(
            session,
            user_id=user_id,
            answer_version_id=queue_command.answer_version_id,
            review_scope=queue_command.review_scope,
        )
        if intent_row is None or intent_row["status"] != "queued":
            raise EssayWorkflowContractError(
                "Grounded review job is missing its durable intent transition"
            )
        return EssayCurrentReviewEnsureResult(
            intent=review_intent_from_row(intent_row),
            job=job,
        )

    def queue_review(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: EssayReviewQueueCommand,
        commerce_service: CommerceService,
    ) -> EssayReviewJobView:
        self._require_worker_available()
        self._repository.lock_user(session, user_id=user_id)
        request_fingerprint = _fingerprint(
            {
                "contractVersion": "essay-review-execution-command-v1",
                "commandKind": "initial",
                "userId": user_id,
                "command": command.model_dump(by_alias=True, mode="json"),
            }
        )
        replay = self._repository.execution_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            _require_fingerprint(replay["request_fingerprint"], request_fingerprint)
            job = self._jobs.by_id(session, job_id=UUID(str(replay["job_id"])))
            if job is None:
                raise EssayWorkflowContractError("Grounded review command references a missing job")
            return self._job_view(session, user_id=user_id, job=job)
        return self._queue_review_locked(
            session,
            user_id=user_id,
            command_id=command_id,
            command_kind="initial",
            request_fingerprint=request_fingerprint,
            command=command,
            commerce_service=commerce_service,
            allow_new_execution=False,
        )

    def regenerate_review_job(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        source_job_id: UUID,
        commerce_service: CommerceService,
    ) -> EssayReviewJobView:
        self._require_worker_available()
        self._repository.lock_user(session, user_id=user_id)
        request_fingerprint = _fingerprint(
            {
                "contractVersion": "essay-review-execution-command-v1",
                "commandKind": "regenerate",
                "userId": user_id,
                "sourceJobId": str(source_job_id),
            }
        )
        replay = self._repository.execution_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if replay is not None:
            _require_fingerprint(replay["request_fingerprint"], request_fingerprint)
            job = self._jobs.by_id(session, job_id=UUID(str(replay["job_id"])))
            if job is None:
                raise EssayWorkflowContractError("Grounded review command references a missing job")
            return self._job_view(session, user_id=user_id, job=job)
        source = self._jobs.by_id(session, job_id=source_job_id)
        if source is None or source.payload.get("userId") != user_id:
            raise EssayWorkflowNotFound("Owned essay review job does not exist")
        if source.status not in {"succeeded", "failed"}:
            raise EssayWorkflowContractError(
                "Only a terminal review job can be explicitly regenerated"
            )
        request = EssayReviewRequest.model_validate_json(
            json.dumps(source.payload["request"], ensure_ascii=False)
        )
        latest = self._repository.latest_review_job(
            session,
            user_id=user_id,
            input_fingerprint=str(source.payload["inputFingerprint"]),
            job_type=ESSAY_REVIEW_JOB_TYPE,
        )
        if latest is None or latest["job_id"] != source_job_id:
            raise EssayWorkflowContractError(
                "Only the latest grounded review execution can be regenerated"
            )
        return self._queue_review_locked(
            session,
            user_id=user_id,
            command_id=command_id,
            command_kind="regenerate",
            request_fingerprint=request_fingerprint,
            command=EssayReviewQueueCommand(
                attempt_id=request.attempt_id,
                answer_version_id=request.answer_version_id,
                review_scope=request.review_scope,
            ),
            commerce_service=commerce_service,
            allow_new_execution=True,
        )

    def _require_worker_available(self) -> None:
        if (
            not self._settings.essay_review_worker_enabled
            and self._execution_identity_override is None
        ):
            raise EssayReviewWorkerUnavailable("Essay review is temporarily unavailable")

    def _current_review_request(
        self,
        session: Session,
        *,
        user_id: str,
        command: EssayReviewQueueCommand,
    ) -> tuple[EssayReviewRequest, str]:
        context = self._repository.load_context(
            session,
            user_id=user_id,
            attempt_id=command.attempt_id,
            answer_version_id=command.answer_version_id,
        )
        current_answer = self._repository.current_answer(
            session,
            user_id=user_id,
            attempt_id=command.attempt_id,
        )
        if (
            current_answer is None
            or current_answer["answer_version_id"] != command.answer_version_id
        ):
            raise EssayWorkflowContractError(
                "Only the current answer version can enter grounded review"
            )
        request = EssayReviewRequest(
            attempt_id=context.attempt.attempt_id,
            task_version_id=context.task.task_version_id,
            answer_version_id=context.answer.answer_version_id,
            material_bundle_version_id=context.material.material_bundle_version_id,
            rubric_version_id=context.rubric.rubric_version_id,
            review_scope=command.review_scope,
        )
        return request, review_input_fingerprint(context, request)

    def _ensure_review_intent_locked(
        self,
        session: Session,
        *,
        user_id: str,
        request: EssayReviewRequest,
        input_fingerprint: str,
        now: datetime | None = None,
    ) -> EssayReviewIntentView:
        intent_fingerprint = _fingerprint(
            {
                "contractVersion": "essay-review-intent-v1",
                "userId": user_id,
                "attemptId": str(request.attempt_id),
                "answerVersionId": str(request.answer_version_id),
                "taskVersionId": str(request.task_version_id),
                "materialBundleVersionId": str(request.material_bundle_version_id),
                "rubricVersionId": str(request.rubric_version_id),
                "reviewScope": request.review_scope,
                "inputFingerprint": input_fingerprint,
            }
        )
        intent_id = uuid5(
            NAMESPACE_URL,
            f"essay-review-intent-v1:{intent_fingerprint}",
        )
        current = self._repository.current_review_intent(
            session,
            user_id=user_id,
            answer_version_id=request.answer_version_id,
            review_scope=request.review_scope,
        )
        if current is not None:
            expected = {
                "intent_id": intent_id,
                "user_id": UUID(user_id),
                "attempt_id": request.attempt_id,
                "answer_version_id": request.answer_version_id,
                "task_version_id": request.task_version_id,
                "material_bundle_version_id": request.material_bundle_version_id,
                "rubric_version_id": request.rubric_version_id,
                "review_scope": request.review_scope,
                "input_fingerprint": input_fingerprint,
                "intent_fingerprint": intent_fingerprint,
            }
            if any(current[key] != value for key, value in expected.items()):
                raise EssayWorkflowContractError(
                    "Current review intent does not match the immutable answer input"
                )
            return review_intent_from_row(current)

        return self._append_review_intent_version(
            session,
            user_id=user_id,
            request=request,
            input_fingerprint=input_fingerprint,
            intent_id=intent_id,
            intent_fingerprint=intent_fingerprint,
            current=None,
            status="pending",
            job=None,
            quota=None,
            retry_after=None,
            transition_key=f"essay-review-intent:{intent_id}:pending",
            now=now,
        )

    def _record_review_intent_quota_waiting(
        self,
        session: Session,
        *,
        user_id: str,
        request: EssayReviewRequest,
        input_fingerprint: str,
        denial: CommerceQuotaExceededError,
    ) -> EssayReviewIntentView:
        if (
            denial.feature != "essay_review"
            or denial.used is None
            or denial.limit is None
            or denial.usage_date is None
            or denial.retry_after is None
            or denial.limit < 1
        ):
            raise EssayWorkflowContractError(
                "Essay review quota denial lacks its authoritative reset window"
            ) from denial
        current = self._repository.current_review_intent(
            session,
            user_id=user_id,
            answer_version_id=request.answer_version_id,
            review_scope=request.review_scope,
        )
        if current is None:
            raise EssayWorkflowContractError(
                "Quota denial cannot precede durable review intent registration"
            )
        if current["status"] == "queued" and current["job_status"] not in {
            "succeeded",
            "failed",
        }:
            raise EssayWorkflowContractError(
                "Only a terminal review execution can enter quota waiting"
            )
        if (
            current["status"] == "waiting_quota"
            and current["quota_usage_date"] == denial.usage_date
            and int(current["quota_used"]) == denial.used
            and int(current["quota_limit"]) == denial.limit
            and current["retry_after"] == denial.retry_after
        ):
            return review_intent_from_row(current)
        quota = EssayReviewQuota(
            feature="essay_review",
            used=denial.used,
            access_mode="entitlement_quota",
            limit=denial.limit,
            usage_date=denial.usage_date,
        )
        return self._append_review_intent_version(
            session,
            user_id=user_id,
            request=request,
            input_fingerprint=input_fingerprint,
            intent_id=UUID(str(current["intent_id"])),
            intent_fingerprint=str(current["intent_fingerprint"]),
            current=current,
            status="waiting_quota",
            job=None,
            quota=quota,
            retry_after=denial.retry_after,
            transition_key=(
                f"essay-review-intent:{current['intent_id']}:quota:"
                f"{current['intent_version_id']}:"
                f"{denial.usage_date.isoformat()}:{denial.used}:{denial.limit}"
            ),
        )

    def _record_review_intent_queued(
        self,
        session: Session,
        *,
        user_id: str,
        request: EssayReviewRequest,
        input_fingerprint: str,
        job: BackgroundJob,
    ) -> EssayReviewIntentView:
        current = self._repository.current_review_intent(
            session,
            user_id=user_id,
            answer_version_id=request.answer_version_id,
            review_scope=request.review_scope,
        )
        if current is None:
            raise EssayWorkflowContractError(
                "Review job cannot precede durable intent registration"
            )
        if current["status"] == "queued":
            if current["job_id"] == job.job_id:
                return review_intent_from_row(current)
            if current["job_status"] not in {"succeeded", "failed"}:
                raise EssayWorkflowContractError(
                    "Review intent already has a non-terminal execution"
                )
        quota = _job_quota(job)
        return self._append_review_intent_version(
            session,
            user_id=user_id,
            request=request,
            input_fingerprint=input_fingerprint,
            intent_id=UUID(str(current["intent_id"])),
            intent_fingerprint=str(current["intent_fingerprint"]),
            current=current,
            status="queued",
            job=job,
            quota=quota,
            retry_after=None,
            transition_key=f"essay-review-intent:{current['intent_id']}:job:{job.job_id}",
        )

    def _append_review_intent_version(
        self,
        session: Session,
        *,
        user_id: str,
        request: EssayReviewRequest,
        input_fingerprint: str,
        intent_id: UUID,
        intent_fingerprint: str,
        current: RowMapping | None,
        status: str,
        job: BackgroundJob | None,
        quota: EssayReviewQuota | None,
        retry_after: datetime | None,
        transition_key: str,
        now: datetime | None = None,
    ) -> EssayReviewIntentView:
        created_at = now or datetime.now(UTC)
        version_no = int(current["version_no"]) + 1 if current is not None else 1
        supersedes = UUID(str(current["intent_version_id"])) if current is not None else None
        state_fingerprint = _fingerprint(
            {
                "contractVersion": "essay-review-intent-state-v1",
                "intentFingerprint": intent_fingerprint,
                "versionNo": version_no,
                "status": status,
                "jobId": str(job.job_id) if job else None,
                "quota": quota.model_dump(by_alias=True, mode="json") if quota else None,
                "retryAfter": retry_after.isoformat() if retry_after else None,
                "supersedesIntentVersionId": str(supersedes) if supersedes else None,
            }
        )
        intent_version_id = uuid5(
            NAMESPACE_URL,
            f"essay-review-intent-state-v1:{intent_id}:{version_no}:{state_fingerprint}",
        )
        self._repository.insert_review_intent_version(
            session,
            values={
                "intent_version_id": str(intent_version_id),
                "intent_id": str(intent_id),
                "command_id": transition_key,
                "user_id": user_id,
                "attempt_id": str(request.attempt_id),
                "answer_version_id": str(request.answer_version_id),
                "task_version_id": str(request.task_version_id),
                "material_bundle_version_id": str(request.material_bundle_version_id),
                "rubric_version_id": str(request.rubric_version_id),
                "review_scope": request.review_scope,
                "version_no": version_no,
                "status": status,
                "input_fingerprint": input_fingerprint,
                "intent_fingerprint": intent_fingerprint,
                "state_fingerprint": state_fingerprint,
                "job_id": str(job.job_id) if job else None,
                "quota_feature": quota.feature if quota else None,
                "quota_usage_date": quota.usage_date if quota else None,
                "quota_used": quota.used if quota else None,
                "quota_access_mode": quota.access_mode if quota else None,
                "quota_limit": quota.limit if quota else None,
                "retry_after": retry_after,
                "supersedes_intent_version_id": (str(supersedes) if supersedes else None),
                "created_at": created_at,
            },
        )
        event_type = {
            "pending": "essay_review.intent_registered",
            "waiting_quota": "essay_review.intent_waiting_quota",
            "queued": "essay_review.intent_queued",
        }[status]
        self._outbox.append(
            session,
            deduplication_key=f"essay-review-intent:{intent_version_id}",
            aggregate_type="essay_review_intent",
            aggregate_id=str(intent_id),
            event_type=event_type,
            payload={
                "intentId": str(intent_id),
                "intentVersionId": str(intent_version_id),
                "versionNo": version_no,
                "status": status,
                "userId": user_id,
                "attemptId": str(request.attempt_id),
                "answerVersionId": str(request.answer_version_id),
                "taskVersionId": str(request.task_version_id),
                "materialBundleVersionId": str(request.material_bundle_version_id),
                "rubricVersionId": str(request.rubric_version_id),
                "reviewScope": request.review_scope,
                "jobId": str(job.job_id) if job else None,
                "inputFingerprint": input_fingerprint,
                "intentFingerprint": intent_fingerprint,
                "stateFingerprint": state_fingerprint,
                "quotaUsageDate": quota.usage_date.isoformat() if quota else None,
                "retryAfter": retry_after.isoformat() if retry_after else None,
                "supersedesIntentVersionId": str(supersedes) if supersedes else None,
            },
        )
        inserted = self._repository.current_review_intent(
            session,
            user_id=user_id,
            answer_version_id=request.answer_version_id,
            review_scope=request.review_scope,
        )
        if inserted is None or inserted["intent_version_id"] != intent_version_id:
            raise EssayWorkflowContractError("Review intent version was not installed as current")
        return review_intent_from_row(inserted)

    def _queue_review_locked(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command_kind: str,
        request_fingerprint: str,
        command: EssayReviewQueueCommand,
        commerce_service: CommerceService,
        allow_new_execution: bool,
    ) -> EssayReviewJobView:
        request, input_fingerprint = self._current_review_request(
            session,
            user_id=user_id,
            command=command,
        )
        self._ensure_review_intent_locked(
            session,
            user_id=user_id,
            request=request,
            input_fingerprint=input_fingerprint,
        )
        identity = self._review_execution_identity()
        provider_identity = identity.provider
        model = identity.model
        previous_review = self._repository.latest_review_for_input(
            session,
            user_id=user_id,
            attempt_id=command.attempt_id,
            input_fingerprint=input_fingerprint,
        )
        latest_job_row = self._repository.latest_review_job(
            session,
            user_id=user_id,
            input_fingerprint=input_fingerprint,
            job_type=ESSAY_REVIEW_JOB_TYPE,
        )
        execution_attempt_no = 1
        if latest_job_row is not None:
            latest_job = self._jobs.by_id(
                session,
                job_id=UUID(str(latest_job_row["job_id"])),
            )
            if latest_job is None:
                raise EssayWorkflowContractError(
                    "Grounded review execution history is inconsistent"
                )
            latest_attempt_no = int(latest_job.payload.get("executionAttemptNo", 0))
            if latest_attempt_no < 1:
                raise EssayWorkflowContractError(
                    "Grounded review execution history lacks an attempt number"
                )
            if latest_job.status in {"queued", "running"}:
                if allow_new_execution:
                    raise EssayWorkflowContractError(
                        "A grounded review execution is already in progress"
                    )
                self._record_execution_command(
                    session,
                    user_id=user_id,
                    command_id=command_id,
                    command_kind=command_kind,
                    request_fingerprint=request_fingerprint,
                    job=latest_job,
                )
                self._record_review_intent_queued(
                    session,
                    user_id=user_id,
                    request=request,
                    input_fingerprint=input_fingerprint,
                    job=latest_job,
                )
                return self._job_view(session, user_id=user_id, job=latest_job)
            if latest_job.status == "succeeded":
                latest_review_id = UUID(str(latest_job.payload["reviewVersionId"]))
                latest_review = self._repository.review_by_id(
                    session,
                    user_id=user_id,
                    review_version_id=latest_review_id,
                )
                if latest_review is None:
                    raise EssayWorkflowContractError(
                        "Succeeded grounded review job has no immutable result"
                    )
                if latest_review["status"] == "completed" and not allow_new_execution:
                    expected_release_fingerprint = review_execution_fingerprint(
                        input_fingerprint=input_fingerprint,
                        execution_attempt_no=latest_attempt_no,
                        provider=provider_identity,
                        model=model,
                    )
                    if (
                        latest_review["valid_until"] <= datetime.now(UTC)
                        or latest_job.payload.get("executionFingerprint")
                        != expected_release_fingerprint
                    ):
                        raise EssayWorkflowContractError(
                            "Completed review is expired or belongs to an older "
                            "execution release; explicitly regenerate it"
                        )
                    self._record_execution_command(
                        session,
                        user_id=user_id,
                        command_id=command_id,
                        command_kind=command_kind,
                        request_fingerprint=request_fingerprint,
                        job=latest_job,
                    )
                    self._record_review_intent_queued(
                        session,
                        user_id=user_id,
                        request=request,
                        input_fingerprint=input_fingerprint,
                        job=latest_job,
                    )
                    return self._job_view(
                        session,
                        user_id=user_id,
                        job=latest_job,
                    )
                if not allow_new_execution:
                    raise EssayWorkflowContractError(
                        "The previous grounded review did not complete; use the "
                        "explicit regenerate command"
                    )
                previous_review = latest_review
            elif latest_job.status != "failed":
                raise EssayWorkflowContractError(
                    f"Grounded review job has unsupported state {latest_job.status}"
                )
            elif not allow_new_execution:
                raise EssayWorkflowContractError(
                    "The previous grounded review failed; use the explicit regenerate command"
                )
            execution_attempt_no = latest_attempt_no + 1

        execution_fingerprint = review_execution_fingerprint(
            input_fingerprint=input_fingerprint,
            execution_attempt_no=execution_attempt_no,
            provider=provider_identity,
            model=model,
        )
        job_key = f"essay-grounded-review:{user_id}:{execution_fingerprint}"
        existing = self._jobs.by_key(session, job_key=job_key)
        if existing is not None:
            self._record_execution_command(
                session,
                user_id=user_id,
                command_id=command_id,
                command_kind=command_kind,
                request_fingerprint=request_fingerprint,
                job=existing,
            )
            self._record_review_intent_queued(
                session,
                user_id=user_id,
                request=request,
                input_fingerprint=input_fingerprint,
                job=existing,
            )
            return self._job_view(session, user_id=user_id, job=existing)

        try:
            reservation = commerce_service.reserve_feature_usage(
                user_id=user_id,
                feature="essay_review",
                reservation_key=(f"essay-review:{user_id}:{execution_fingerprint}"),
                business_key=execution_fingerprint,
                commit=False,
            )
        except CommerceQuotaExceededError as exc:
            self._record_review_intent_quota_waiting(
                session,
                user_id=user_id,
                request=request,
                input_fingerprint=input_fingerprint,
                denial=exc,
            )
            raise
        review_version_id = uuid5(
            NAMESPACE_URL,
            f"essay-review-v2:{user_id}:{execution_fingerprint}",
        )
        payload = {
            "userId": user_id,
            "request": request.model_dump(by_alias=True, mode="json"),
            "reviewVersionId": str(review_version_id),
            "inputFingerprint": input_fingerprint,
            "executionFingerprint": execution_fingerprint,
            "executionAttemptNo": execution_attempt_no,
            "executionProvider": provider_identity,
            "executionModel": model,
            "supersedesReviewVersionId": (
                str(previous_review["review_version_id"]) if previous_review is not None else None
            ),
            "quotaReservation": {
                "reservationId": str(reservation.reservation_id),
                "reservationKey": reservation.reservation_key,
                "businessKey": reservation.business_key,
                "feature": reservation.feature,
                "used": reservation.used,
                "limit": reservation.limit,
                "accessMode": reservation.access_mode,
                "usageDate": reservation.usage_date.isoformat(),
                "source": reservation.source,
            },
        }
        job = self._jobs.enqueue(
            session,
            job_key=job_key,
            job_type=ESSAY_REVIEW_JOB_TYPE,
            input_version=ESSAY_REVIEW_JOB_INPUT_VERSION,
            payload=payload,
            max_attempts=3,
        )
        commerce_service.bind_feature_usage_reservation_job(
            reservation=reservation,
            job_id=job.job_id,
            commit=False,
        )
        self._record_execution_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_kind=command_kind,
            request_fingerprint=request_fingerprint,
            job=job,
        )
        self._outbox.append(
            session,
            deduplication_key=f"essay-review-request:{review_version_id}",
            aggregate_type="essay_review",
            aggregate_id=str(review_version_id),
            event_type="essay_review.requested",
            payload={
                "reviewVersionId": str(review_version_id),
                "jobId": str(job.job_id),
                "userId": user_id,
                "attemptId": str(request.attempt_id),
                "answerVersionId": str(request.answer_version_id),
                "taskVersionId": str(request.task_version_id),
                "materialBundleVersionId": str(request.material_bundle_version_id),
                "rubricVersionId": str(request.rubric_version_id),
                "inputFingerprint": input_fingerprint,
                "executionFingerprint": execution_fingerprint,
                "executionAttemptNo": execution_attempt_no,
                "supersedesReviewVersionId": payload["supersedesReviewVersionId"],
            },
        )
        self._record_review_intent_queued(
            session,
            user_id=user_id,
            request=request,
            input_fingerprint=input_fingerprint,
            job=job,
        )
        return self._job_view(session, user_id=user_id, job=job)

    def _review_execution_identity(self) -> EssayReviewExecutionIdentity:
        if self._execution_identity_override is not None:
            return self._execution_identity_override
        try:
            return EssayMultiModelRuntimePlan.execution_identity_from_settings(self._settings)
        except EssayMultiModelRuntimeConfigurationError as exc:
            raise EssayWorkflowContractError(
                "Formal multi-model essay grading plan is not configured"
            ) from exc

    def _record_execution_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command_kind: str,
        request_fingerprint: str,
        job: BackgroundJob,
    ) -> None:
        self._repository.insert_execution_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_kind=command_kind,
            request_fingerprint=request_fingerprint,
            job_id=job.job_id,
        )

    def get_job(
        self,
        session: Session,
        *,
        user_id: str,
        job_id: UUID,
    ) -> EssayReviewJobView:
        job = self._jobs.by_id(session, job_id=job_id)
        if job is None or job.payload.get("userId") != user_id:
            raise EssayWorkflowNotFound("Owned essay review job does not exist")
        return self._job_view(session, user_id=user_id, job=job)

    def get_review(
        self,
        session: Session,
        *,
        user_id: str,
        review_version_id: UUID,
    ):
        row = self._repository.review_by_id(
            session,
            user_id=user_id,
            review_version_id=review_version_id,
        )
        if row is None:
            raise EssayWorkflowNotFound("Owned essay review does not exist")
        return review_from_row(row)

    def _job_view(
        self,
        session: Session,
        *,
        user_id: str,
        job: BackgroundJob,
    ) -> EssayReviewJobView:
        payload = job.payload
        if payload.get("userId") != user_id:
            raise EssayWorkflowNotFound("Owned essay review job does not exist")
        if job.status not in {"queued", "running", "succeeded", "failed"}:
            raise EssayWorkflowContractError(f"Essay review job has unsupported state {job.status}")
        request = payload["request"]
        review_version_id = UUID(str(payload["reviewVersionId"]))
        review = None
        row = self._repository.review_by_id(
            session,
            user_id=user_id,
            review_version_id=review_version_id,
        )
        if row is not None:
            review = review_from_row(row)
        review_status = review.status if review else None
        return EssayReviewJobView(
            job_id=job.job_id,
            status=job.status,
            attempt_id=UUID(str(request["attemptId"])),
            answer_version_id=UUID(str(request["answerVersionId"])),
            review_version_id=review_version_id,
            review_status=review_status,
            quota=_job_quota(job),
            created_at=job.created_at,
            completed_at=job.completed_at,
            review=review,
        )


def _job_quota(job: BackgroundJob) -> EssayReviewQuota:
    try:
        payload = job.payload["quotaReservation"]
        quota = EssayReviewQuota(
            feature=payload["feature"],
            used=int(payload["used"]),
            access_mode=payload["accessMode"],
            limit=(int(payload["limit"]) if payload["accessMode"] == "entitlement_quota" else None),
            usage_date=date.fromisoformat(str(payload["usageDate"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EssayWorkflowContractError(
            "Grounded review job lacks immutable quota evidence"
        ) from exc
    if quota.feature != "essay_review":
        raise EssayWorkflowContractError("Grounded review job carries a different feature quota")
    return quota


def _require_fingerprint(actual: str, expected: str) -> None:
    if actual != expected:
        raise EssayWorkflowContractError(
            "Idempotency key already belongs to different immutable input"
        )
