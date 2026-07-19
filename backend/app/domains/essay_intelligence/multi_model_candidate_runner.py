from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
        quota = self._job.payload["quotaReservation"]
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
            self._session.commit()
            return self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="invalid",
                error_code=exc.code,
                output_fingerprint=exc.output_fingerprint,
            )
        except (
            GroundedReviewProviderError,
            GroundedReviewProviderConfigurationError,
        ) as exc:
            retryable = bool(getattr(exc, "retryable", False))
            if retryable:
                raise
            if record.status == "running":
                self._ledger.fail(
                    self._session,
                    call_id=record.call_id,
                    error_code=str(getattr(exc, "code", "provider_failed")),
                    latency_ms=0,
                )
            self._session.commit()
            return self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="provider_failed",
                error_code=str(getattr(exc, "code", "provider_failed")),
            )

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
        try:
            payload = EssayProviderReviewPayload.model_validate_json(
                json.dumps(result.payload, ensure_ascii=False)
            )
        except ValidationError:
            return self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="invalid",
                error_code="candidate_contract_invalid",
                output_fingerprint=result.output_fingerprint,
            )
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
            return self._rejected_candidate(
                candidate_id=candidate_id,
                slot=slot,
                input_fingerprint=input_fingerprint,
                model_call_id=record.call_id,
                status="invalid",
                error_code=exc.code,
                output_fingerprint=canonical_output,
            )
        return EssayGradingCandidate(
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
