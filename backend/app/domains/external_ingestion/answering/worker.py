from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.config import Settings
from app.domains.ai.governance.repository import AIGovernanceRepository, generic_evidence_ref
from app.domains.ai.model_cost import ModelCallLedgerRepository, ModelPriceCard, ModelTokenUsage
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.external_ingestion.answering.provider import (
    ExternalAIAnswerProvider,
    ExternalAIAnswerProviderError,
)
from app.domains.external_ingestion.answering.repository import ExternalAIAnswerRepository
from app.domains.external_ingestion.answering.service import ExternalAIAnswerService
from app.domains.external_ingestion.matching.models import MatchQuery
from app.platform.jobs import BackgroundJob, BackgroundJobRepository


class ExternalAIAnswerWorker:
    JOB_TYPES = (ExternalAIAnswerService.JOB_TYPE,)

    def __init__(
        self,
        *,
        settings: Settings,
        provider: ExternalAIAnswerProvider | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or ExternalAIAnswerProvider(settings)
        self._jobs = BackgroundJobRepository()
        self._ledger = ModelCallLedgerRepository()

    def process_next(self, session: Session) -> BackgroundJob | None:
        job = self._jobs.claim_next(session, job_types=self.JOB_TYPES)
        if job is None:
            return None
        session.commit()
        try:
            self._process(session, job=job)
        except ExternalAIAnswerProviderError as exc:
            session.rollback()
            self._fail(session, job=job, error=exc)
        except Exception:
            session.rollback()
            self._fail(
                session,
                job=job,
                error=ExternalAIAnswerProviderError(
                    "external_answer_internal_error", retryable=True
                ),
            )
        return self._jobs.by_id(session, job_id=job.job_id)

    def _process(self, session: Session, *, job: BackgroundJob) -> None:
        assert job.claim_token is not None
        answer_id = UUID(str(job.payload["answerId"]))
        repository = ExternalAIAnswerRepository(session)
        repository.lock(answer_id=answer_id)
        source = repository.head(answer_id=answer_id, for_update=True)
        if source is None:
            raise RuntimeError("external AI answer aggregate disappeared")
        if source.state in {"candidate", "confirmed"}:
            self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={"answerVersionId": str(source.answer_version_id)},
            )
            session.commit()
            return
        if source.state == "requested":
            processing = source.model_copy(
                update={
                    "answer_version_id": uuid5(NAMESPACE_URL, f"external-answer-processing:{answer_id}"),
                    "version_no": source.version_no + 1,
                    "state": "processing",
                    "supersedes_answer_version_id": source.answer_version_id,
                    "created_at": datetime.now(UTC),
                }
            )
            repository.insert(processing)
            source = processing
            session.commit()
        query = MatchQuery.model_validate(job.payload["query"])
        evidence_refs = tuple(
            generic_evidence_ref(
                kind="external_question_segment",
                source_id=f"recognition:{job.payload['recognitionRevisionId']}:{ref}",
                version=str(job.payload["inputFingerprint"]),
            )
            for ref in job.payload["sourceSegmentRefs"]
        )
        attribution = AIGovernanceRepository().attribution(
            session,
            environment=self._settings.environment,
            scenario_key="external_question_answer",
            operation="external_question_answer_generate",
            provider=self._provider.provider_name,
            model=self._provider.model,
            prompt_version=self._provider.prompt_version,
            schema_version=self._provider.schema_version,
            evidence_refs=evidence_refs,
            retrieval_bundle_id=None,
            reservation_id=UUID(str(job.payload["reservationId"])),
            reservation_source=str(job.payload["reservationSource"]),
        )
        call = self._ledger.begin(
            session,
            call_key=f"external-answer:{answer_id}",
            user_id=str(job.payload["ownerUserId"]),
            job_id=job.job_id,
            feature="photo_explain",
            operation="external_question_answer_generate",
            provider=self._provider.provider_name,
            model=self._provider.model,
            prompt_version=self._provider.prompt_version,
            schema_version=self._provider.schema_version,
            price_card=ModelPriceCard.from_settings(self._settings),
            input_fingerprint=str(job.payload["inputFingerprint"]),
            attribution=attribution,
            processor_release_id=(
                self._settings.ai_companion_provider_release_id.strip()
            ),
            processor_endpoint_fingerprint=endpoint_fingerprint(
                self._settings.ai_provider_base_url
            ),
            processing_purpose="external_question_answer_generate",
            processing_data_categories=("recognized_question",),
        )
        session.commit()
        result = self._provider.generate(
            query=query,
            source_segment_refs=tuple(job.payload["sourceSegmentRefs"]),
            idempotency_key=f"external-answer:{answer_id}",
        )
        repository.lock(answer_id=answer_id)
        current = repository.head(answer_id=answer_id, for_update=True)
        if current is None or current.state != "processing":
            raise RuntimeError("external AI answer head changed during provider call")
        if call.status == "running":
            self._ledger.succeed(
                session,
                call_id=call.call_id,
                usage=ModelTokenUsage(
                    input_tokens=result.usage_input_tokens,
                    cached_input_tokens=result.usage_cached_input_tokens,
                    output_tokens=result.usage_output_tokens,
                ),
                output_fingerprint=result.output_fingerprint,
                latency_ms=result.latency_ms,
            )
        elif call.status != "succeeded" or call.output_fingerprint != result.output_fingerprint:
            raise RuntimeError(
                "external AI answer replay conflicts with the completed model call"
            )
        reservation = CommerceRepository(session).feature_usage_reservation_by_key(
            reservation_key=str(job.payload["reservationKey"])
        )
        if reservation is None:
            raise RuntimeError("external AI answer reservation disappeared")
        self._commerce(session).consume_feature_usage(reservation=reservation, commit=False)
        candidate = current.model_copy(
            update={
                "answer_version_id": uuid5(NAMESPACE_URL, f"external-answer-candidate:{answer_id}:{result.output_fingerprint}"),
                "version_no": current.version_no + 1,
                "state": "candidate",
                "answer_payload": result.output,
                "output_fingerprint": result.output_fingerprint,
                "model_call_id": call.call_id,
                "provider_request_id_fingerprint": result.provider_request_id_fingerprint,
                "supersedes_answer_version_id": current.answer_version_id,
                "created_at": datetime.now(UTC),
            }
        )
        repository.insert(candidate)
        self._jobs.succeed(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={
                "answerVersionId": str(candidate.answer_version_id),
                "outputFingerprint": candidate.output_fingerprint,
                "quotaState": "consumed",
            },
        )
        session.commit()

    def _fail(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        error: ExternalAIAnswerProviderError,
    ) -> None:
        assert job.claim_token is not None
        failed_job = self._jobs.fail(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_code=error.code,
            error_message=error.code,
            retryable=error.retryable,
        )
        if failed_job.status == "failed":
            call_id = session.execute(
                text(
                    "select call_id from ai_audit.model_call_ledger "
                    "where call_key = :call_key and status = 'running'"
                ),
                {"call_key": f"external-answer:{job.payload['answerId']}"},
            ).scalar_one_or_none()
            if call_id is not None:
                self._ledger.fail(
                    session,
                    call_id=UUID(str(call_id)),
                    error_code=error.code,
                    latency_ms=0,
                )
            repository = ExternalAIAnswerRepository(session)
            answer_id = UUID(str(job.payload["answerId"]))
            repository.lock(answer_id=answer_id)
            current = repository.head(answer_id=answer_id, for_update=True)
            if current is not None and current.state not in {"candidate", "confirmed", "failed"}:
                failed = current.model_copy(
                    update={
                        "answer_version_id": uuid5(NAMESPACE_URL, f"external-answer-failed:{answer_id}:{error.code}"),
                        "version_no": current.version_no + 1,
                        "state": "failed",
                        "failure_code": error.code,
                        "supersedes_answer_version_id": current.answer_version_id,
                        "created_at": datetime.now(UTC),
                    }
                )
                repository.insert(failed)
                reservation = CommerceRepository(session).feature_usage_reservation_by_key(
                    reservation_key=str(job.payload["reservationKey"])
                )
                if reservation is not None and reservation.status == "reserved":
                    self._commerce(session).refund_feature_usage(
                        reservation=reservation, commit=False
                    )
        session.commit()

    def _commerce(self, session: Session) -> CommerceService:
        return CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=AppStoreSignedTransactionVerifier(self._settings),
            commercial_mode=self._settings.commercial_mode,
            usage_access_mode=self._settings.usage_access_mode,
        )
