from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import time
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ai.model_cost import (
    ModelCallLedgerRepository,
    ModelCallRecord,
    ModelPriceCard,
)
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.ai.governance.repository import (
    AIGovernanceRepository,
    generic_evidence_ref,
)
from app.domains.commerce.repository import CommerceRepository
from app.domains.essay_intelligence.grounded_provider import (
    GroundedEssayReviewProvider,
    GroundedReviewMeteredProviderError,
    GroundedReviewProviderConfigurationError,
    GroundedReviewProviderError,
    GroundedReviewProviderResult,
)
from app.domains.essay_intelligence.grounding import (
    GroundedReviewContext,
    review_input_fingerprint,
    review_output_fingerprint,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROMPT_VERSION,
    GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
    REVIEW_CONTRACT_VERSION,
    RUBRIC_CONTRACT_VERSION,
    review_execution_fingerprint,
)
from app.domains.essay_intelligence.review_checkpoint import (
    EssayReviewCheckpointRepository,
)
from app.domains.essay_intelligence.blind_adjudicator import (
    LedgeredEssayBlindAdjudicator,
)
from app.domains.essay_intelligence.multi_model_candidate_runner import (
    LedgeredEssayCandidateRunner,
)
from app.domains.essay_intelligence.multi_model_grading_coordinator import (
    EssayMultiModelGradingCoordinator,
    EssayMultiModelGradingExecution,
    EssayMultiModelGradingExecutionError,
)
from app.domains.essay_intelligence.multi_model_grading_repository import (
    EssayMultiModelGradingRepository,
)
from app.domains.essay_intelligence.multi_model_runtime import (
    EssayMultiModelRuntimePlan,
)
from app.domains.essay_intelligence.review_service import (
    GroundedReviewApplicationResult,
    GroundedReviewApplicationService,
)
from app.domains.essay_intelligence.schemas import (
    EssayProviderReviewPayload,
    EssayReviewAudit,
    EssayReviewReadiness,
    EssayReviewRequest,
    EssayReviewResponse,
)
from app.domains.essay_intelligence.workflow_repository import (
    EssayReviewWorkflowRepository,
)
from app.domains.essay_intelligence.workflow_service import (
    ESSAY_REVIEW_JOB_TYPE,
)
from app.platform.jobs import (
    BackgroundJob,
    BackgroundJobRepository,
    JobContractError,
)
from app.platform.outbox import OutboxRepository


REVIEW_VALIDITY = timedelta(days=30)
STALE_CLAIM_AFTER = timedelta(minutes=10)


@dataclass(frozen=True)
class ReviewExecution:
    fingerprint: str
    attempt_no: int
    supersedes_review_version_id: UUID | None


class _GroundedReviewMeteredCallRejected(GroundedReviewProviderError):
    """Carries a completed billable call through terminal domain rejection."""

    def __init__(
        self,
        *,
        cause: GroundedReviewProviderError,
        record: ModelCallRecord,
        phase: str,
        ordinal: int,
    ) -> None:
        super().__init__(cause.code, str(cause), retryable=False)
        self.record = record
        self.phase = phase
        self.ordinal = ordinal


class GroundedEssayReviewWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: GroundedEssayReviewProvider | None = None,
        price_card: ModelPriceCard | None = None,
        workflow_repository: EssayReviewWorkflowRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
        ledger: ModelCallLedgerRepository | None = None,
        outbox: OutboxRepository | None = None,
        application: GroundedReviewApplicationService | None = None,
        checkpoints: EssayReviewCheckpointRepository | None = None,
        runtime_plan: EssayMultiModelRuntimePlan | None = None,
        grading_repository: EssayMultiModelGradingRepository | None = None,
    ) -> None:
        self._settings = settings
        if provider is not None and runtime_plan is not None:
            raise ValueError("Review worker cannot mix legacy and multi-model runtimes")
        if (
            provider is not None
            and settings.environment.strip().casefold() == "production"
        ):
            raise ValueError("Production review worker forbids single-model execution")
        self._provider = provider
        self._runtime_plan = (
            None
            if provider is not None
            else runtime_plan or EssayMultiModelRuntimePlan.from_settings(settings)
        )
        self._price_card = (
            price_card or ModelPriceCard.from_settings(settings)
            if provider is not None
            else None
        )
        self._workflow = workflow_repository or EssayReviewWorkflowRepository()
        self._jobs = jobs or BackgroundJobRepository()
        self._ledger = ledger or ModelCallLedgerRepository()
        self._outbox = outbox or OutboxRepository()
        self._application = application or GroundedReviewApplicationService()
        self._checkpoints = checkpoints or EssayReviewCheckpointRepository.from_settings(
            settings
        )
        self._grading = grading_repository or EssayMultiModelGradingRepository()

    def recover_stale_claims(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> int:
        recovered_at = now or datetime.now(UTC)
        count = self._jobs.requeue_stale(
            session,
            job_types=(ESSAY_REVIEW_JOB_TYPE,),
            stale_before=recovered_at - STALE_CLAIM_AFTER,
            now=recovered_at,
        )
        self._checkpoints.purge_expired(session, now=recovered_at)
        session.commit()
        return count

    def process_next(self, session: Session) -> BackgroundJob | None:
        job = self._jobs.claim_next(
            session,
            job_types=(ESSAY_REVIEW_JOB_TYPE,),
        )
        if job is None:
            session.rollback()
            return None
        session.commit()
        try:
            return self._process_claim(session, job=job)
        except Exception:
            session.rollback()
            self._fail_claim(
                session,
                job=job,
                code="review_worker_unexpected",
                retryable=True,
            )
            raise

    def _process_claim(self, session: Session, *, job: BackgroundJob) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError("Claimed review job is missing its claim token")
        payload = job.payload
        user_id = str(payload["userId"])
        request = EssayReviewRequest.model_validate_json(
            json.dumps(payload["request"], ensure_ascii=False)
        )
        review_version_id = UUID(str(payload["reviewVersionId"]))
        context = self._workflow.load_context(
            session,
            user_id=user_id,
            attempt_id=request.attempt_id,
            answer_version_id=request.answer_version_id,
        )
        expected_fingerprint = review_input_fingerprint(context, request)
        try:
            execution = self._execution(job=job)
        except (KeyError, TypeError, ValueError):
            return self._fail_claim(
                session,
                job=job,
                code="review_job_execution_contract_invalid",
                retryable=False,
            )
        if payload.get("inputFingerprint") != expected_fingerprint:
            return self._terminal_failure(
                session,
                job=job,
                context=context,
                request=request,
                review_version_id=review_version_id,
                code="review_job_input_fingerprint_mismatch",
                execution=execution,
            )

        existing = self._workflow.review_by_id(
            session,
            user_id=user_id,
            review_version_id=review_version_id,
        )
        if existing is not None:
            self._checkpoints.delete_for_job(session, job_id=job.job_id)
            final = self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={
                    "reviewVersionId": str(review_version_id),
                    "reviewStatus": str(existing["status"]),
                },
            )
            session.commit()
            return final

        if self._runtime_plan is not None:
            return self._process_multi_model_claim(
                session,
                job=job,
                user_id=user_id,
                context=context,
                request=request,
                review_version_id=review_version_id,
                input_fingerprint=expected_fingerprint,
                execution=execution,
            )

        if self._provider is None or self._price_card is None:
            raise RuntimeError("Legacy review worker runtime is incomplete")

        try:
            initial, initial_call = self._candidate(
                session,
                job=job,
                phase="initial",
                ordinal=1,
                input_fingerprint=expected_fingerprint,
                invoke=lambda key: self._provider.generate(
                    context=context,
                    request=request,
                    idempotency_key=key,
                ),
            )
        except (GroundedReviewProviderError, GroundedReviewProviderConfigurationError) as exc:
            return self._provider_failure(
                session,
                job=job,
                context=context,
                request=request,
                review_version_id=review_version_id,
                exc=exc,
                execution=execution,
            )

        initial_application = self._apply_candidate(
            review_version_id=review_version_id,
            context=context,
            request=request,
            candidate=initial,
            execution=execution,
        )
        calls: list[tuple[ModelCallRecord, str, int]] = [
            (initial_call, "initial", 1)
        ]
        final_application = initial_application
        if initial_application.response.status != "completed":
            code = initial_application.response.readiness.blocking_codes[0]
            detail = initial_application.blocking_detail or code
            rejected = (
                json.dumps(
                    initial.payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if initial.payload is not None
                else ""
            )
            repair_input_fingerprint = _fingerprint(
                {
                    "contractVersion": "essay-review-repair-v1",
                    "reviewInputFingerprint": expected_fingerprint,
                    "phase": "repair",
                    "violationCode": code,
                    "violationDetail": detail,
                    "rejectedContentSha256": sha256(
                        rejected.encode("utf-8")
                    ).hexdigest(),
                }
            )
            try:
                repaired, repair_call = self._candidate(
                    session,
                    job=job,
                    phase="repair",
                    ordinal=2,
                    input_fingerprint=repair_input_fingerprint,
                    invoke=lambda key: self._provider.repair(
                        context=context,
                        request=request,
                        rejected_content=rejected,
                        violation_code=code,
                        violation_detail=detail,
                        idempotency_key=key,
                    ),
                )
            except (
                GroundedReviewProviderError,
                GroundedReviewProviderConfigurationError,
            ) as exc:
                return self._provider_failure(
                    session,
                    job=job,
                    context=context,
                    request=request,
                    review_version_id=review_version_id,
                    exc=exc,
                    calls=calls,
                    execution=execution,
                )
            calls.append((repair_call, "repair", 2))
            final_application = self._apply_candidate(
                review_version_id=review_version_id,
                context=context,
                request=request,
                candidate=repaired,
                execution=execution,
            )

        return self._persist_result(
            session,
            job=job,
            user_id=user_id,
            context=context,
            request=request,
            result=final_application,
            calls=calls,
            execution=execution,
        )

    def _process_multi_model_claim(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        user_id: str,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        review_version_id: UUID,
        input_fingerprint: str,
        execution: ReviewExecution,
    ) -> BackgroundJob:
        plan = self._runtime_plan
        if plan is None:
            raise RuntimeError("Formal multi-model grading plan is unavailable")
        runner = LedgeredEssayCandidateRunner(
            session=session,
            settings=self._settings,
            job=job,
            slots=plan.candidate_slots,
            evidence_refs=_job_evidence_refs(job),
            ledger=self._ledger,
            checkpoints=self._checkpoints,
        )
        adjudicator = LedgeredEssayBlindAdjudicator(
            session=session,
            app_settings=self._settings,
            job=job,
            provider=plan.adjudicator_provider,
            price_card=plan.adjudicator_price_card,
            evidence_refs=_job_evidence_refs(job),
            ledger=self._ledger,
            checkpoints=self._checkpoints,
        )
        coordinator = EssayMultiModelGradingCoordinator(
            slot_ids=plan.slot_ids,
            candidate_runner=runner,
            adjudicator=adjudicator,
        )
        try:
            grading = coordinator.execute(
                intended_review_version_id=review_version_id,
                context=context,
                request=request,
                input_fingerprint=input_fingerprint,
            )
        except EssayMultiModelGradingExecutionError as exc:
            return self._multi_model_execution_failure(
                session,
                job=job,
                context=context,
                request=request,
                review_version_id=review_version_id,
                execution=execution,
                failure=exc,
            )

        if grading.resolved.payload is None:
            return self._terminal_failure(
                session,
                job=job,
                context=context,
                request=request,
                review_version_id=review_version_id,
                code="multi_model_insufficient_valid_candidates",
                execution=execution,
                grading_execution=grading,
            )

        selected_payload = grading.resolved.payload
        selected_result = GroundedReviewProviderResult(
            payload=selected_payload.model_dump(by_alias=True, mode="json"),
            raw_content="",
            usage=_zero_usage(),
            latency_ms=0,
            output_fingerprint=review_output_fingerprint(selected_payload),
        )
        applied = self._apply_candidate(
            review_version_id=review_version_id,
            context=context,
            request=request,
            candidate=selected_result,
            execution=execution,
        )
        if applied.response.status != "completed":
            return self._terminal_failure(
                session,
                job=job,
                context=context,
                request=request,
                review_version_id=review_version_id,
                code="multi_model_selected_candidate_rejected",
                execution=execution,
                grading_execution=grading,
            )
        return self._persist_result(
            session,
            job=job,
            user_id=user_id,
            context=context,
            request=request,
            result=applied,
            calls=[],
            execution=execution,
            grading_execution=grading,
        )

    def _multi_model_execution_failure(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        review_version_id: UUID,
        execution: ReviewExecution,
        failure: EssayMultiModelGradingExecutionError,
    ) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError("Review job lost its claim")
        failed = self._jobs.fail(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_code=failure.code,
            error_message=failure.code,
            retryable=failure.retryable,
        )
        if failed.status != "failed":
            session.commit()
            return failed
        return self._terminal_failure(
            session,
            job=failed,
            context=context,
            request=request,
            review_version_id=review_version_id,
            code=failure.code,
            execution=execution,
            already_failed=True,
            grading_error=failure,
        )

    def _execution(self, *, job: BackgroundJob) -> ReviewExecution:
        payload = job.payload
        input_fingerprint = str(payload["inputFingerprint"])
        attempt_no = int(payload["executionAttemptNo"])
        provider = str(payload["executionProvider"])
        model = str(payload["executionModel"])
        identity = self._execution_identity()
        if provider != identity[0] or model != identity[1]:
            raise ValueError("Queued review execution release differs from worker release")
        expected = review_execution_fingerprint(
            input_fingerprint=input_fingerprint,
            execution_attempt_no=attempt_no,
            provider=provider,
            model=model,
        )
        if payload["executionFingerprint"] != expected:
            raise ValueError("Queued review execution fingerprint is invalid")
        expected_review_id = uuid5(
            NAMESPACE_URL,
            f"essay-review-v2:{payload['userId']}:{expected}",
        )
        if UUID(str(payload["reviewVersionId"])) != expected_review_id:
            raise ValueError("Queued review version identity is invalid")
        supersedes = payload.get("supersedesReviewVersionId")
        return ReviewExecution(
            fingerprint=expected,
            attempt_no=attempt_no,
            supersedes_review_version_id=(
                UUID(str(supersedes)) if supersedes is not None else None
            ),
        )

    def _execution_identity(self) -> tuple[str, str]:
        if self._runtime_plan is not None:
            identity = self._runtime_plan.execution_identity
            return identity.provider, identity.model
        if self._provider is None:
            raise RuntimeError("Review execution identity is unavailable")
        return self._provider.provider_name, self._provider.model

    def _candidate(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        phase: str,
        ordinal: int,
        input_fingerprint: str,
        invoke: Callable[[str], GroundedReviewProviderResult],
    ) -> tuple[GroundedReviewProviderResult, ModelCallRecord]:
        if self._provider is None or self._price_card is None:
            raise RuntimeError("Single-model candidate path is test-only")
        saved = self._checkpoints.load(
            session,
            job_id=job.job_id,
            phase=phase,
        )
        if saved is not None:
            record = self._ledger.completed_for_checkpoint(
                session,
                call_id=saved.call_id,
                job_id=job.job_id,
                input_fingerprint=input_fingerprint,
                output_fingerprint=saved.output_fingerprint,
            )
            return (
                GroundedReviewProviderResult(
                    payload=saved.payload,
                    raw_content="",
                    usage=_zero_usage(),
                    latency_ms=0,
                    output_fingerprint=saved.output_fingerprint,
                    provider_request_id_header=(
                        saved.provider_request_id_header
                    ),
                    provider_request_id_fingerprint=(
                        saved.provider_request_id_fingerprint
                    ),
                ),
                record,
            )
        if job.claim_token is None:
            raise JobContractError("Review job lost its claim")
        # Platform retries are transport retries of the same logical provider
        # call. Keep one provider key across claims so a response lost after the
        # provider accepted the request is replayed instead of billed twice.
        call_key = f"essay-review:{job.job_id}:{phase}"
        record = self._ledger.begin(
            session,
            call_key=call_key,
            user_id=str(job.payload["userId"]),
            job_id=job.job_id,
            feature="essay_review",
            operation=f"grounded_review_{phase}",
            provider=self._provider.provider_name,
            model=self._provider.model,
            prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
            schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
            price_card=self._price_card,
            input_fingerprint=input_fingerprint,
            attribution=AIGovernanceRepository().attribution(
                session,
                environment=self._settings.environment,
                scenario_key="essay_grounded_review",
                operation=f"grounded_review_{phase}",
                provider=self._provider.provider_name,
                model=self._provider.model,
                prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
                schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
                evidence_refs=_job_evidence_refs(job),
                retrieval_bundle_id=None,
                reservation_id=UUID(str(job.payload["quotaReservation"]["reservationId"])),
                reservation_source=str(
                    job.payload["quotaReservation"].get("source", "catalog")
                ),
                enforce_evaluated_deployment=False,
            ),
            processor_release_id=(
                self._settings.ai_essay_review_provider_release_id.strip()
            ),
            processor_endpoint_fingerprint=endpoint_fingerprint(
                self._settings.ai_provider_base_url
            ),
            processing_purpose=f"grounded_review_{phase}",
            processing_data_categories=(
                "essay_answer",
                "public_exam_material",
                "grading_rubric",
            ),
        )
        session.commit()
        started = time.monotonic()
        try:
            candidate = invoke(call_key)
        except GroundedReviewMeteredProviderError as exc:
            # The provider completed a billable call. Domain rejection and quota
            # refund must not erase the real model cost or leave the ledger open.
            if (
                record.status == "succeeded"
                and record.output_fingerprint != exc.output_fingerprint
            ):
                violation = GroundedReviewProviderError(
                    "provider_idempotency_violation",
                    "Provider changed output for an already completed idempotent call",
                    retryable=False,
                )
                raise _GroundedReviewMeteredCallRejected(
                    cause=violation,
                    record=record,
                    phase=phase,
                    ordinal=ordinal,
                ) from exc
            if record.status == "running":
                record = self._ledger.succeed(
                    session,
                    call_id=record.call_id,
                    usage=exc.usage,
                    output_fingerprint=exc.output_fingerprint,
                    latency_ms=exc.latency_ms,
                )
            audit_pair_valid = _provider_request_audit_pair_valid(
                exc.provider_request_id_header,
                exc.provider_request_id_fingerprint,
            )
            rejected = (
                exc
                if audit_pair_valid
                else GroundedReviewProviderError(
                    "provider_request_id_contract_invalid",
                    "Provider request ID audit fields must appear together",
                    retryable=False,
                )
            )
            self._checkpoint_call_metadata(
                session,
                job=job,
                phase=phase,
                ordinal=ordinal,
                record=record,
                output_fingerprint=exc.output_fingerprint,
                provider_request_id_header=(
                    exc.provider_request_id_header if audit_pair_valid else None
                ),
                provider_request_id_fingerprint=(
                    exc.provider_request_id_fingerprint if audit_pair_valid else None
                ),
                rejected_code=rejected.code,
            )
            session.commit()
            raise _GroundedReviewMeteredCallRejected(
                cause=rejected,
                record=record,
                phase=phase,
                ordinal=ordinal,
            ) from exc
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            retryable = bool(getattr(exc, "retryable", False))
            final_attempt = job.attempt_count >= job.max_attempts
            if record.status == "running" and (not retryable or final_attempt):
                code = getattr(exc, "code", "provider_configuration_failed")
                self._ledger.fail(
                    session,
                    call_id=record.call_id,
                    error_code=str(code),
                    latency_ms=latency_ms,
                )
            raise
        if (
            record.status == "succeeded"
            and record.output_fingerprint != candidate.output_fingerprint
        ):
            raise GroundedReviewProviderError(
                "provider_idempotency_violation",
                "Provider changed output for an already completed idempotent call",
                retryable=False,
            )
        if record.status == "running":
            record = self._ledger.succeed(
                session,
                call_id=record.call_id,
                usage=candidate.usage,
                output_fingerprint=candidate.output_fingerprint,
                latency_ms=candidate.latency_ms,
            )
        if not _provider_request_audit_pair_valid(
            candidate.provider_request_id_header,
            candidate.provider_request_id_fingerprint,
        ):
            rejected = GroundedReviewProviderError(
                "provider_request_id_contract_invalid",
                "Provider request ID audit fields must appear together",
                retryable=False,
            )
            self._checkpoint_call_metadata(
                session,
                job=job,
                phase=phase,
                ordinal=ordinal,
                record=record,
                output_fingerprint=candidate.output_fingerprint,
                provider_request_id_header=None,
                provider_request_id_fingerprint=None,
                rejected_code=rejected.code,
            )
            session.commit()
            raise _GroundedReviewMeteredCallRejected(
                cause=rejected,
                record=record,
                phase=phase,
                ordinal=ordinal,
            )
        self._checkpoints.save(
            session,
            job_id=job.job_id,
            phase=phase,
            call_id=record.call_id,
            payload=candidate.payload,
            output_fingerprint=candidate.output_fingerprint,
            provider_request_id_header=candidate.provider_request_id_header,
            provider_request_id_fingerprint=(
                candidate.provider_request_id_fingerprint
            ),
        )
        current_job = self._jobs.by_id(session, job_id=job.job_id)
        checkpoint_metadata = dict(current_job.result or {}) if current_job else {}
        phase_metadata = {
            "callId": str(record.call_id),
            "ordinal": ordinal,
            "outputFingerprint": candidate.output_fingerprint,
        }
        if candidate.provider_request_id_fingerprint is not None:
            phase_metadata.update(
                {
                    "providerRequestIdHeader": (
                        candidate.provider_request_id_header
                    ),
                    "providerRequestIdFingerprint": (
                        candidate.provider_request_id_fingerprint
                    ),
                }
            )
        checkpoint_metadata[phase] = phase_metadata
        self._jobs.checkpoint(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result=checkpoint_metadata,
        )
        session.commit()
        return candidate, record

    def _checkpoint_call_metadata(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        phase: str,
        ordinal: int,
        record: ModelCallRecord,
        output_fingerprint: str,
        provider_request_id_header: str | None,
        provider_request_id_fingerprint: str | None,
        rejected_code: str,
    ) -> None:
        if job.claim_token is None:
            raise JobContractError("Review job lost its claim")
        current_job = self._jobs.by_id(session, job_id=job.job_id)
        metadata = dict(current_job.result or {}) if current_job else {}
        phase_metadata = {
            "callId": str(record.call_id),
            "ordinal": ordinal,
            "outputFingerprint": output_fingerprint,
            "rejectedCode": rejected_code,
        }
        if provider_request_id_fingerprint is not None:
            phase_metadata.update(
                {
                    "providerRequestIdHeader": provider_request_id_header,
                    "providerRequestIdFingerprint": provider_request_id_fingerprint,
                }
            )
        metadata[phase] = phase_metadata
        self._jobs.checkpoint(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result=metadata,
        )

    def _apply_candidate(
        self,
        *,
        review_version_id: UUID,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        candidate: GroundedReviewProviderResult,
        execution: ReviewExecution,
    ) -> GroundedReviewApplicationResult:
        provider, model = self._execution_identity()
        payload = candidate.payload or {}
        try:
            parsed = EssayProviderReviewPayload.model_validate_json(
                json.dumps(payload, ensure_ascii=False)
            )
            canonical_output = review_output_fingerprint(parsed)
        except ValidationError:
            canonical_output = candidate.output_fingerprint
        generated_at = datetime.now(UTC)
        audit = EssayReviewAudit(
            contract_version=REVIEW_CONTRACT_VERSION,
            provider_schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
            rubric_version=RUBRIC_CONTRACT_VERSION,
            rubric_source=context.rubric.source_kind,
            rubric_verification=context.rubric.verification_status,
            prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
            provider=provider,
            model=model,
            input_fingerprint=review_input_fingerprint(context, request),
            output_fingerprint=canonical_output,
            generated_at=generated_at,
            valid_until=generated_at + REVIEW_VALIDITY,
        )
        return self._application.apply(
            review_version_id=review_version_id,
            context=context,
            request=request,
            provider_output=payload,
            audit=audit,
            supersedes_review_version_id=execution.supersedes_review_version_id,
        )

    def _persist_result(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        user_id: str,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        result: GroundedReviewApplicationResult,
        calls: list[tuple[ModelCallRecord, str, int]],
        execution: ReviewExecution,
        grading_execution: EssayMultiModelGradingExecution | None = None,
    ) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError("Review job lost its claim")
        now = datetime.now(UTC)
        response = result.response
        canonical_output = (
            response.audit.output_fingerprint if response.audit else None
        )
        provider_request_ids = self._provider_request_audit(
            session,
            job_id=job.job_id,
        )
        provider, model = self._execution_identity()
        self._workflow.insert_review(
            session,
            command_id=f"essay-review-worker:{job.job_id}",
            user_id=user_id,
            response=response,
            contract_version=REVIEW_CONTRACT_VERSION,
            provider_schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
            rubric_contract_version=RUBRIC_CONTRACT_VERSION,
            prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
            provider=provider,
            model=model,
            input_fingerprint=review_input_fingerprint(context, request),
            execution_fingerprint=execution.fingerprint,
            execution_attempt_no=execution.attempt_no,
            output_fingerprint=canonical_output,
            generated_at=response.audit.generated_at if response.audit else now,
            valid_until=response.audit.valid_until if response.audit else now + REVIEW_VALIDITY,
            grounding=result.grounding,
        )
        if grading_execution is not None:
            self._grading.insert_bundle(
                session,
                grading_run_id=grading_execution.grading_run_id,
                intended_review_version_id=response.review_version_id,
                job_id=job.job_id,
                user_id=user_id,
                input_fingerprint=review_input_fingerprint(context, request),
                candidates=grading_execution.candidates,
                resolved=grading_execution.resolved,
                adjudication_audit=grading_execution.adjudication_audit,
            )
            self._grading.bind_review(
                session,
                review_version_id=response.review_version_id,
                grading_run_id=grading_execution.grading_run_id,
            )
        for call, phase, ordinal in calls:
            self._workflow.link_model_call(
                session,
                review_version_id=response.review_version_id,
                call_id=call.call_id,
                phase=phase,
                ordinal=ordinal,
            )
        self._checkpoints.delete_for_job(session, job_id=job.job_id)
        if response.status != "completed":
            self._refund_quota(session, job=job)
        else:
            self._consume_quota(session, job=job)
        self._outbox.append(
            session,
            deduplication_key=f"essay-review-result:{response.review_version_id}",
            aggregate_type="essay_review",
            aggregate_id=str(response.review_version_id),
            event_type=f"essay_review.{response.status}",
            payload={
                "reviewVersionId": str(response.review_version_id),
                "jobId": str(job.job_id),
                "userId": user_id,
                "attemptId": str(response.attempt_id),
                "answerVersionId": str(response.answer_version_id),
                "rubricVersionId": str(response.rubric_version_id),
                "status": response.status,
                "inputFingerprint": review_input_fingerprint(context, request),
                "executionFingerprint": execution.fingerprint,
                "executionAttemptNo": execution.attempt_no,
                "outputFingerprint": canonical_output,
                "providerRequestIds": provider_request_ids,
            },
        )
        final = self._jobs.succeed(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={
                "reviewVersionId": str(response.review_version_id),
                "reviewStatus": response.status,
                "providerRequestIds": provider_request_ids,
            },
        )
        session.commit()
        return final

    def _provider_failure(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        review_version_id: UUID,
        exc: Exception,
        calls: list[tuple[ModelCallRecord, str, int]] | None = None,
        execution: ReviewExecution,
    ) -> BackgroundJob:
        resolved_calls = list(calls or [])
        if isinstance(exc, _GroundedReviewMeteredCallRejected):
            resolved_calls.append((exc.record, exc.phase, exc.ordinal))
        code = getattr(exc, "code", "provider_configuration_failed")
        retryable = bool(getattr(exc, "retryable", False))
        return self._fail_claim(
            session,
            job=job,
            code=str(code),
            retryable=retryable,
            context=context,
            request=request,
            review_version_id=review_version_id,
            calls=resolved_calls,
            execution=execution,
        )

    def _fail_claim(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        code: str,
        retryable: bool,
        context: GroundedReviewContext | None = None,
        request: EssayReviewRequest | None = None,
        review_version_id: UUID | None = None,
        calls: list[tuple[ModelCallRecord, str, int]] | None = None,
        execution: ReviewExecution | None = None,
    ) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError("Review job lost its claim")
        failed = self._jobs.fail(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_code=code,
            error_message=code,
            retryable=retryable,
        )
        if failed.status == "failed" and all(
            (
                context is not None,
                request is not None,
                review_version_id is not None,
                execution is not None,
            )
        ):
            return self._terminal_failure(
                session,
                job=failed,
                context=context,
                request=request,
                review_version_id=review_version_id,
                code=code,
                calls=calls or [],
                already_failed=True,
                execution=execution,
            )
        if failed.status == "failed":
            self._checkpoints.delete_for_job(session, job_id=failed.job_id)
            self._refund_quota(session, job=failed)
        session.commit()
        return failed

    def _terminal_failure(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        review_version_id: UUID,
        code: str,
        execution: ReviewExecution,
        calls: list[tuple[ModelCallRecord, str, int]] | None = None,
        already_failed: bool = False,
        grading_execution: EssayMultiModelGradingExecution | None = None,
        grading_error: EssayMultiModelGradingExecutionError | None = None,
    ) -> BackgroundJob:
        if grading_execution is not None and grading_error is not None:
            raise ValueError("A failed review cannot persist two grading outcomes")
        now = datetime.now(UTC)
        provider_request_ids = self._provider_request_audit(
            session,
            job_id=job.job_id,
        )
        response = EssayReviewResponse(
            review_version_id=review_version_id,
            attempt_id=context.attempt.attempt_id,
            task_version_id=context.task.task_version_id,
            material_bundle_version_id=context.material.material_bundle_version_id,
            answer_version_id=context.answer.answer_version_id,
            rubric_version_id=context.rubric.rubric_version_id,
            status="failed",
            review_scope=request.review_scope,
            thinking_evidence_available=context.answer.thinking is not None,
            readiness=EssayReviewReadiness(
                material_complete=context.material.completeness == "complete",
                prompt_complete=bool(context.task.prompt.strip()),
                answer_complete=bool(context.answer.answer_text.strip()),
                ocr_confirmed=context.answer.source_type in {"typed", "ocr_confirmed"},
                blocking_codes=[code],
            ),
            score=None,
            dimensions=[],
            point_coverage=[],
            claim_evidence=[],
            findings=[],
            rewrite_plan=None,
            audit=None,
            supersedes_review_version_id=execution.supersedes_review_version_id,
        )
        provider, model = self._execution_identity()
        self._workflow.insert_review(
            session,
            command_id=f"essay-review-worker:{job.job_id}",
            user_id=str(job.payload["userId"]),
            response=response,
            contract_version=REVIEW_CONTRACT_VERSION,
            provider_schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
            rubric_contract_version=RUBRIC_CONTRACT_VERSION,
            prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
            provider=provider,
            model=model,
            input_fingerprint=review_input_fingerprint(context, request),
            execution_fingerprint=execution.fingerprint,
            execution_attempt_no=execution.attempt_no,
            output_fingerprint=None,
            generated_at=now,
            valid_until=now + REVIEW_VALIDITY,
            grounding=None,
        )
        if grading_execution is not None:
            self._grading.insert_bundle(
                session,
                grading_run_id=grading_execution.grading_run_id,
                intended_review_version_id=review_version_id,
                job_id=job.job_id,
                user_id=str(job.payload["userId"]),
                input_fingerprint=review_input_fingerprint(context, request),
                candidates=grading_execution.candidates,
                resolved=grading_execution.resolved,
                adjudication_audit=grading_execution.adjudication_audit,
            )
            self._grading.bind_review(
                session,
                review_version_id=review_version_id,
                grading_run_id=grading_execution.grading_run_id,
            )
        elif grading_error is not None:
            self._grading.insert_failure_bundle(
                session,
                grading_run_id=grading_error.grading_run_id,
                intended_review_version_id=review_version_id,
                job_id=job.job_id,
                user_id=str(job.payload["userId"]),
                input_fingerprint=review_input_fingerprint(context, request),
                candidates=grading_error.candidates,
                comparison=grading_error.comparison,
                failure_code=grading_error.code,
                adjudication_audit=grading_error.adjudication_audit,
            )
            self._grading.bind_review(
                session,
                review_version_id=review_version_id,
                grading_run_id=grading_error.grading_run_id,
            )
        for call, phase, ordinal in calls or []:
            self._workflow.link_model_call(
                session,
                review_version_id=review_version_id,
                call_id=call.call_id,
                phase=phase,
                ordinal=ordinal,
            )
        self._checkpoints.delete_for_job(session, job_id=job.job_id)
        self._refund_quota(session, job=job)
        self._outbox.append(
            session,
            deduplication_key=f"essay-review-result:{review_version_id}",
            aggregate_type="essay_review",
            aggregate_id=str(review_version_id),
            event_type="essay_review.failed",
            payload={
                "reviewVersionId": str(review_version_id),
                "jobId": str(job.job_id),
                "userId": str(job.payload["userId"]),
                "status": "failed",
                "blockingCode": code,
                "executionFingerprint": execution.fingerprint,
                "executionAttemptNo": execution.attempt_no,
                "providerRequestIds": provider_request_ids,
            },
        )
        if not already_failed:
            if job.claim_token is None:
                raise JobContractError("Review job lost its claim")
            job = self._jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code=code,
                error_message=code,
                retryable=False,
            )
        session.commit()
        return job

    def _provider_request_audit(
        self,
        session: Session,
        *,
        job_id: UUID,
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        job = self._jobs.by_id(session, job_id=job_id)
        job_result = job.result if job is not None else None
        for phase in (
            "initial",
            "repair",
            "candidate:gemini",
            "candidate:grok",
            "candidate:tokenrhythm",
            "candidate:hunyuan",
            "adjudication",
        ):
            checkpoint = self._checkpoints.load(
                session,
                job_id=job_id,
                phase=phase,
            )
            if checkpoint is not None and checkpoint.provider_request_id_fingerprint:
                result.append(
                    {
                        "phase": phase,
                        "header": str(checkpoint.provider_request_id_header),
                        "fingerprint": checkpoint.provider_request_id_fingerprint,
                    }
                )
                continue
            phase_metadata = (
                job_result.get(phase)
                if isinstance(job_result, dict)
                else None
            )
            if not isinstance(phase_metadata, dict):
                continue
            header = phase_metadata.get("providerRequestIdHeader")
            fingerprint = phase_metadata.get("providerRequestIdFingerprint")
            if (
                not isinstance(header, str)
                or not header
                or not _is_sha256_fingerprint(fingerprint)
            ):
                continue
            result.append(
                {
                    "phase": phase,
                    "header": header,
                    "fingerprint": fingerprint,
                }
            )
        return result

    @staticmethod
    def _refund_quota(session: Session, *, job: BackgroundJob) -> None:
        quota = job.payload["quotaReservation"]
        CommerceRepository(session).refund_feature_usage(
            reservation_key=str(quota["reservationKey"]),
            commit=False,
        )

    @staticmethod
    def _consume_quota(session: Session, *, job: BackgroundJob) -> None:
        quota = job.payload["quotaReservation"]
        CommerceRepository(session).consume_feature_usage(
            reservation_key=str(quota["reservationKey"]),
            commit=False,
        )


def _fingerprint(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _job_evidence_refs(job: BackgroundJob):
    request = job.payload["request"]
    return (
        generic_evidence_ref(
            kind="essay_attempt",
            source_id=str(request["attemptId"]),
            version=str(request["taskVersionId"]),
        ),
        generic_evidence_ref(
            kind="essay_answer",
            source_id=str(request["answerVersionId"]),
            version=str(request["answerVersionId"]),
        ),
        generic_evidence_ref(
            kind="essay_review_execution",
            source_id=str(job.payload["reviewVersionId"]),
            version=str(job.payload["executionFingerprint"]),
        ),
    )


def _provider_request_audit_pair_valid(
    header: object,
    fingerprint: object,
) -> bool:
    if header is None and fingerprint is None:
        return True
    return (
        isinstance(header, str)
        and bool(header.strip())
        and _is_sha256_fingerprint(fingerprint)
    )


def _is_sha256_fingerprint(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _zero_usage():
    from app.domains.ai.model_cost import ModelTokenUsage

    return ModelTokenUsage(input_tokens=0, cached_input_tokens=0, output_tokens=0)
