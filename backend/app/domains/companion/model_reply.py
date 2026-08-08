from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.ai.governance.retrieval import RetrievalBundle
from app.domains.ai.provider import (
    AICompanionAnswerResult,
    AIProvider,
    AIProviderResponseError,
)


@dataclass(frozen=True, slots=True)
class CompanionModelReply:
    """Invoke one prepared provider request and enforce its grounding boundary."""

    provider: AIProvider

    def answer(
        self,
        *,
        prepared: dict[str, Any],
        idempotency_key: str,
        retrieval_payload: dict[str, Any],
    ) -> AICompanionAnswerResult:
        result = self.provider.answer_prepared_companion_question(
            prepared=prepared,
            idempotency_key=idempotency_key,
        )
        validate_provider_source_refs(
            result.source_refs,
            retrieval_payload,
            result=result,
        )
        return result


def validate_provider_source_refs(
    source_refs: list[str],
    retrieval_payload: dict[str, Any],
    *,
    result: AICompanionAnswerResult,
) -> None:
    bundle = RetrievalBundle.model_validate(retrieval_payload)
    allowed = {item.reference.source_id for item in bundle.sources}
    if set(source_refs) - allowed:
        raise AIProviderResponseError(
            "AI provider cited evidence outside the authorized retrieval bundle",
            code="provider_grounding_invalid",
            usage=result.usage,
            latency_ms=result.latency_ms,
            output_fingerprint=result.output_fingerprint,
            provider_request_id_header=result.provider_request_id_header,
            provider_request_id_fingerprint=result.provider_request_id_fingerprint,
        )
