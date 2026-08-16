from __future__ import annotations

from typing import Any, Protocol
from app.domains.teach_back_provider_shadow.contracts import (
    ProviderCallAuditCandidate,
    TeachBackProviderShadowResult,
)
from app.domains.teach_back_provider_shadow.provider import (
    STUDENT_SCHEMA_VERSION,
    VERIFIER_SCHEMA_VERSION,
    StructuredProviderResult,
    TeachBackShadowProvider,
)
from app.domains.teach_back_shadow.contracts import (
    GroundingVerificationOutput,
    ShadowPreparationInput,
    StudentReproductionOutput,
)
from app.domains.teach_back_shadow.service import (
    prepare_shadow_evaluation,
    validate_grounding_output,
)


class TeachBackShadowPreconditionError(RuntimeError):
    pass


class PriceCard(Protocol):
    def cost(self, usage: Any) -> Any: ...


def shadow_idempotency_key(package_fingerprint: str, phase: str) -> str:
    if phase not in {"student", "verifier"}:
        raise ValueError("unsupported teach-back shadow phase")
    return f"teach-back-shadow:{package_fingerprint}:{phase}:v1"


def run_provider_shadow(
    command: ShadowPreparationInput,
    *,
    provider: TeachBackShadowProvider,
    price_card: PriceCard,
) -> TeachBackProviderShadowResult:
    """Run two isolated synthetic calls and expose no partial business candidate."""

    prepared = prepare_shadow_evaluation(command)
    if prepared.state != "ready":
        raise TeachBackShadowPreconditionError("synthetic explanation is required")
    student_key = shadow_idempotency_key(command.package_fingerprint, "student")
    verifier_key = shadow_idempotency_key(command.package_fingerprint, "verifier")
    student_result = provider.generate_student(
        prepared.student_requests[0],
        idempotency_key=student_key,
    )
    verifier_result = provider.generate_verifier(
        command,
        prepared.verifier_requests[0],
        student_result.output,
        idempotency_key=verifier_key,
    )
    audits = (
        _audit(
            result=student_result,
            idempotency_key=student_key,
            operation="teach_back_student_reproduction",
            provider=provider,
            prompt_version=prepared.student_requests[0].prompt_version,
            schema_version=STUDENT_SCHEMA_VERSION,
            price_card=price_card,
        ),
        _audit(
            result=verifier_result,
            idempotency_key=verifier_key,
            operation="teach_back_grounding_verification",
            provider=provider,
            prompt_version=prepared.verifier_requests[0].prompt_version,
            schema_version=VERIFIER_SCHEMA_VERSION,
            price_card=price_card,
        ),
    )
    grounding = validate_grounding_output(
        prepared.verifier_requests[0], verifier_result.output
    )
    if grounding.state == "undisplayable":
        return TeachBackProviderShadowResult(
            state="undisplayable",
            reasonCodes=grounding.reason_codes,
            studentOutputs=(),
            groundingCandidates=(),
            callAudits=audits,
        )
    return TeachBackProviderShadowResult(
        state="displayable",
        reasonCodes=(),
        studentOutputs=(student_result.output,),
        groundingCandidates=grounding.candidates,
        callAudits=audits,
    )


def _audit(
    *,
    result: StructuredProviderResult[
        StudentReproductionOutput | GroundingVerificationOutput
    ],
    idempotency_key: str,
    operation: str,
    provider: TeachBackShadowProvider,
    prompt_version: str,
    schema_version: str,
    price_card: PriceCard,
) -> ProviderCallAuditCandidate:
    metadata = result.metadata
    cost = price_card.cost(metadata.usage)
    return ProviderCallAuditCandidate(
        idempotencyKey=idempotency_key,
        operation=operation,
        provider=provider.provider_name,
        model=provider.model,
        promptVersion=prompt_version,
        schemaVersion=schema_version,
        inputFingerprint=metadata.input_fingerprint,
        outputFingerprint=metadata.output_fingerprint,
        inputTokens=metadata.usage.input_tokens,
        cachedInputTokens=metadata.usage.cached_input_tokens,
        outputTokens=metadata.usage.output_tokens,
        totalCostCny=f"{cost.total_cost_cny:.8f}",
        latencyMs=metadata.latency_ms,
        requestIdSource=metadata.request_id_source,
        requestIdFingerprint=metadata.request_id_fingerprint,
    )
