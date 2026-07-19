from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.ai.governance.contracts import EvidenceReference
from app.domains.ai.governance.repository import AIGovernanceRepository
from app.domains.ai.model_cost import (
    ModelCallLedgerRepository,
    ModelCallRecord,
    ModelPriceCard,
)
from app.domains.essay_intelligence.grounded_provider import (
    GroundedReviewMeteredProviderError,
    GroundedReviewProviderConfigurationError,
    GroundedReviewProviderError,
    GroundedReviewProviderResult,
)
from app.domains.essay_intelligence.grounding import (
    GroundedReviewContext,
    GroundedReviewValidator,
    GroundingViolation,
    review_output_fingerprint,
)
from app.domains.essay_intelligence.multi_model_grading import (
    GRADING_PLAN_VERSION,
    EssayGradingCandidate,
)
from app.domains.essay_intelligence.schemas import (
    EssayProviderReviewPayload,
    EssayReviewAudit,
    EssayReviewRequest,
)
from app.domains.essay_intelligence.review_contracts import (
    REVIEW_CONTRACT_VERSION,
    RUBRIC_CONTRACT_VERSION,
)
from app.domains.essay_intelligence.review_checkpoint import (
    EssayReviewCheckpointRepository,
)
from app.platform.jobs import BackgroundJob


class GroundedCandidateProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def provider_release_id(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def endpoint(self) -> str: ...

    def generate(
        self,
        *,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        idempotency_key: str | None = None,
    ) -> GroundedReviewProviderResult: ...


@dataclass(frozen=True)
class EssayCandidateSlot:
    slot_id: str
    provider: GroundedCandidateProvider
    price_card: ModelPriceCard
    prompt_version: str
    schema_version: str


class LedgeredEssayCandidateRunner:
    """Execute independent candidates with a durable cost and processor receipt."""

    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        job: BackgroundJob,
        slots: tuple[EssayCandidateSlot, ...],
        evidence_refs: tuple[EvidenceReference, ...],
        ledger: ModelCallLedgerRepository | None = None,
        validator: GroundedReviewValidator | None = None,
        checkpoints: EssayReviewCheckpointRepository | None = None,
    ) -> None:
        if len(slots) not in {2, 3}:
            raise ValueError("formal essay grading requires two or three slots")
        if len({slot.slot_id for slot in slots}) != len(slots):
            raise ValueError("formal essay grading slot IDs must be unique")
        releases = {
            (
                slot.provider.provider_release_id.casefold(),
                slot.provider.model.casefold(),
            )
            for slot in slots
        }
        if len(releases) != len(slots):
            raise ValueError("formal essay grading releases must be independent")
        self._session = session
        self._settings = settings
        self._job = job
        self._slots = {slot.slot_id: slot for slot in slots}
        self._evidence_refs = evidence_refs
        self._ledger = ledger or ModelCallLedgerRepository()
        self._validator = validator or GroundedReviewValidator()
        self._checkpoints = checkpoints or EssayReviewCheckpointRepository.from_settings(
            settings
        )

    def run_candidate(
        self,
        *,
        grading_run_id: UUID,
        slot_id: str,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        input_fingerprint: str,
    ) -> EssayGradingCandidate:
        slot = self._slots.get(slot_id)
        if slot is None:
            raise ValueError("candidate runner received an unknown grading slot")
        candidate_id = uuid5(
            NAMESPACE_URL,
            f"{GRADING_PLAN_VERSION}:{grading_run_id}:{slot_id}",
        )
        call_key = f"essay-grading:{grading_run_id}:{slot_id}"
        checkpointed = self._load_checkpoint(
            slot=slot,
            candidate_id=candidate_id,
            input_fingerprint=input_fingerprint,
        )
        if checkpointed is not None:
            return checkpointed
        quota = self._job.payload["quotaReservation"]
        existing_before_dispatch = self._ledger.get_existing(
            self._session,
            call_key=call_key,
            user_id=str(self._job.payload["userId"]),
            job_id=self._job.job_id,
            feature="essay_review",
            operation=f"essay_score_candidate_{slot_id}",
            input_fingerprint=input_fingerprint,
        )
        record = self._ledger.begin(
            self._session,
            call_key=call_key,
            user_id=str(self._job.payload["userId"]),
            job_id=self._job.job_id,
            feature="essay_review",
            operation=f"essay_score_candidate_{slot_id}",
            provider=slot.provider.provider_name,
            model=slot.provider.model,
            prompt_version=slot.prompt_version,
            schema_version=slot.schema_version,
            price_card=slot.price_card,
            input_fingerprint=input_fingerprint,
            attribution=AIGovernanceRepository().attribution(
                self._session,
                environment=self._settings.environment,
                scenario_key="essay_grounded_review",
                operation=f"essay_score_candidate_{slot_id}",
                provider=slot.provider.provider_name,
                model=slot.provider.model,
                prompt_version=slot.prompt_version,
                schema_version=slot.schema_version,
                evidence_refs=self._evidence_refs,
                retrieval_bundle_id=None,
                reservation_id=UUID(str(quota["reservationId"])),
                reservation_source=str(quota.get("source", "catalog")),
            ),
            processor_release_id=slot.provider.provider_release_id,
            processor_endpoint_fingerprint=endpoint_fingerprint(
                slot.provider.endpoint
            ),
            processing_purpose="essay_score_candidate",
            processing_data_categories=(
                "essay_answer",
                "public_exam_material",
                "grading_rubric",
            ),
        )
        self._session.commit()
        if existing_before_dispatch is not None and record.status == "running":
            record = self._ledger.fail(
                self._session,
                call_id=record.call_id,
                error_code="candidate_outcome_unknown_after_interruption",
                latency_ms=0,
            )
        if existing_before_dispatch is not None and record.status == "succeeded":
            candidate = self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="invalid",
                error_code="candidate_checkpoint_missing_after_completed_call",
                output_fingerprint=record.output_fingerprint,
            )
            self._save_checkpoint(candidate=candidate, record=record)
            return candidate
        if record.status == "failed":
            candidate = self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="provider_failed",
                error_code=record.error_code or "provider_failed",
            )
            self._save_checkpoint(candidate=candidate, record=record)
            return candidate
        try:
            result = slot.provider.generate(
                context=context,
                request=request,
                idempotency_key=call_key,
            )
        except GroundedReviewMeteredProviderError as exc:
            if (
                record.status == "succeeded"
                and record.output_fingerprint != exc.output_fingerprint
            ):
                raise GroundedReviewProviderError(
                    "provider_idempotency_violation",
                    "Candidate provider changed a rejected immutable response",
                    retryable=False,
                ) from exc
            if record.status == "running":
                record = self._ledger.succeed(
                    self._session,
                    call_id=record.call_id,
                    usage=exc.usage,
                    output_fingerprint=exc.output_fingerprint,
                    latency_ms=exc.latency_ms,
                )
            audit_pair_valid = _provider_request_audit_pair_valid(
                exc.provider_request_id_header,
                exc.provider_request_id_fingerprint,
            )
            candidate = self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="invalid",
                error_code=(
                    exc.code
                    if audit_pair_valid
                    else "provider_request_id_contract_invalid"
                ),
                output_fingerprint=exc.output_fingerprint,
            )
            self._save_checkpoint(
                candidate=candidate,
                record=record,
                provider_request_id_header=(
                    exc.provider_request_id_header if audit_pair_valid else None
                ),
                provider_request_id_fingerprint=(
                    exc.provider_request_id_fingerprint if audit_pair_valid else None
                ),
            )
            return candidate
        except (
            GroundedReviewProviderError,
            GroundedReviewProviderConfigurationError,
        ) as exc:
            if record.status == "succeeded":
                raise GroundedReviewProviderError(
                    "provider_idempotency_violation",
                    "Candidate provider replaced a completed immutable response with an error",
                    retryable=False,
                ) from exc
            retryable = bool(getattr(exc, "retryable", False))
            if retryable and self._job.attempt_count < self._job.max_attempts:
                raise
            if record.status == "running":
                record = self._ledger.fail(
                    self._session,
                    call_id=record.call_id,
                    error_code=str(getattr(exc, "code", "provider_failed")),
                    latency_ms=0,
                )
            candidate = self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="provider_failed",
                error_code=str(getattr(exc, "code", "provider_failed")),
            )
            self._save_checkpoint(candidate=candidate, record=record)
            return candidate

        if (
            record.status == "succeeded"
            and record.output_fingerprint != result.output_fingerprint
        ):
            raise GroundedReviewProviderError(
                "provider_idempotency_violation",
                "Candidate provider changed output for an immutable call key",
                retryable=False,
            )
        if record.status == "running":
            record = self._ledger.succeed(
                self._session,
                call_id=record.call_id,
                usage=result.usage,
                output_fingerprint=result.output_fingerprint,
                latency_ms=result.latency_ms,
            )
        self._session.commit()
        audit_pair_valid = _provider_request_audit_pair_valid(
            result.provider_request_id_header,
            result.provider_request_id_fingerprint,
        )
        if not audit_pair_valid:
            candidate = self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="invalid",
                error_code="provider_request_id_contract_invalid",
                output_fingerprint=result.output_fingerprint,
            )
            self._save_checkpoint(candidate=candidate, record=record)
            return candidate
        try:
            payload = EssayProviderReviewPayload.model_validate_json(
                json.dumps(result.payload, ensure_ascii=False)
            )
        except ValidationError:
            candidate = self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="invalid",
                error_code="candidate_contract_invalid",
                output_fingerprint=result.output_fingerprint,
            )
            self._save_checkpoint(
                candidate=candidate,
                record=record,
                provider_request_id_header=result.provider_request_id_header,
                provider_request_id_fingerprint=(
                    result.provider_request_id_fingerprint
                ),
            )
            return candidate
        canonical_output = review_output_fingerprint(payload)
        generated_at = datetime.now(UTC)
        try:
            self._validator.validate(
                context=context,
                request=request,
                output=payload,
                audit=EssayReviewAudit(
                    contractVersion=REVIEW_CONTRACT_VERSION,
                    providerSchemaVersion=slot.schema_version,
                    rubricVersion=RUBRIC_CONTRACT_VERSION,
                    rubricSource=context.rubric.source_kind,
                    rubricVerification=context.rubric.verification_status,
                    promptVersion=slot.prompt_version,
                    provider=slot.provider.provider_name,
                    model=slot.provider.model,
                    inputFingerprint=input_fingerprint,
                    outputFingerprint=canonical_output,
                    generatedAt=generated_at,
                    validUntil=generated_at + timedelta(days=30),
                ),
            )
        except GroundingViolation as exc:
            candidate = self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="invalid",
                error_code=exc.code,
                output_fingerprint=canonical_output,
            )
            self._save_checkpoint(
                candidate=candidate,
                record=record,
                provider_request_id_header=result.provider_request_id_header,
                provider_request_id_fingerprint=(
                    result.provider_request_id_fingerprint
                ),
            )
            return candidate
        candidate = EssayGradingCandidate(
            candidateId=candidate_id,
            slotId=slot_id,
            providerReleaseId=slot.provider.provider_release_id,
            model=slot.provider.model,
            promptVersion=slot.prompt_version,
            schemaVersion=slot.schema_version,
            inputFingerprint=input_fingerprint,
            outputFingerprint=canonical_output,
            status="valid",
            payload=payload,
            modelCallId=record.call_id,
            errorCode=None,
        )
        self._save_checkpoint(
            candidate=candidate,
            record=record,
            provider_request_id_header=result.provider_request_id_header,
            provider_request_id_fingerprint=result.provider_request_id_fingerprint,
        )
        return candidate

    def _load_checkpoint(
        self,
        *,
        slot: EssayCandidateSlot,
        candidate_id: UUID,
        input_fingerprint: str,
    ) -> EssayGradingCandidate | None:
        saved = self._checkpoints.load(
            self._session,
            job_id=self._job.job_id,
            phase=f"candidate:{slot.slot_id}",
        )
        if saved is None or saved.payload is None:
            return None
        candidate = EssayGradingCandidate.model_validate_json(
            json.dumps(saved.payload, ensure_ascii=False)
        )
        immutable = (
            candidate.candidate_id == candidate_id,
            candidate.slot_id == slot.slot_id,
            candidate.provider_release_id == slot.provider.provider_release_id,
            candidate.model == slot.provider.model,
            candidate.input_fingerprint == input_fingerprint,
            candidate.model_call_id == saved.call_id,
        )
        if not all(immutable):
            raise ValueError("Candidate checkpoint belongs to another grading contract")
        record = self._ledger.terminal_for_checkpoint(
            self._session,
            call_id=saved.call_id,
            job_id=self._job.job_id,
            input_fingerprint=input_fingerprint,
        )
        if candidate.status == "provider_failed":
            if record.status != "failed" or saved.output_fingerprint != _candidate_hash(
                candidate
            ):
                raise ValueError("Failed candidate checkpoint ledger contract is invalid")
        elif (
            record.status != "succeeded"
            or record.output_fingerprint != saved.output_fingerprint
        ):
            raise ValueError("Candidate checkpoint ledger contract is invalid")
        return candidate

    def _save_checkpoint(
        self,
        *,
        candidate: EssayGradingCandidate,
        record: ModelCallRecord,
        provider_request_id_header: str | None = None,
        provider_request_id_fingerprint: str | None = None,
    ) -> None:
        output_fingerprint = (
            record.output_fingerprint
            if record.status == "succeeded" and record.output_fingerprint is not None
            else _candidate_hash(candidate)
        )
        self._checkpoints.save(
            self._session,
            job_id=self._job.job_id,
            phase=f"candidate:{candidate.slot_id}",
            call_id=record.call_id,
            payload=candidate.model_dump(by_alias=True, mode="json"),
            output_fingerprint=output_fingerprint,
            provider_request_id_header=provider_request_id_header,
            provider_request_id_fingerprint=provider_request_id_fingerprint,
        )
        self._session.commit()

    @staticmethod
    def _rejected_candidate(
        *,
        candidate_id: UUID,
        slot: EssayCandidateSlot,
        input_fingerprint: str,
        model_call_id: UUID,
        status: str,
        error_code: str,
        output_fingerprint: str | None = None,
    ) -> EssayGradingCandidate:
        return EssayGradingCandidate(
            candidateId=candidate_id,
            slotId=slot.slot_id,
            providerReleaseId=slot.provider.provider_release_id,
            model=slot.provider.model,
            promptVersion=slot.prompt_version,
            schemaVersion=slot.schema_version,
            inputFingerprint=input_fingerprint,
            outputFingerprint=output_fingerprint,
            status=status,
            payload=None,
            modelCallId=model_call_id,
            errorCode=error_code,
        )


def _candidate_hash(candidate: EssayGradingCandidate) -> str:
    return sha256(
        json.dumps(
            candidate.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _provider_request_audit_pair_valid(
    header: object,
    fingerprint: object,
) -> bool:
    if header is None and fingerprint is None:
        return True
    return (
        isinstance(header, str)
        and bool(header.strip())
        and isinstance(fingerprint, str)
        and len(fingerprint) == 64
        and all(character in "0123456789abcdef" for character in fingerprint)
    )
