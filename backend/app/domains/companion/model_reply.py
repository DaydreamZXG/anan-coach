from __future__ import annotations

from dataclasses import dataclass, replace
import re
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
        return enforce_answering_output_contract(result, retrieval_payload)


_FINAL_OPTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:正确答案|最终答案|答案|正确选项|最终选项)\s*(?:是|为|应为|应该是|：|:)?\s*[A-H](?:\s*项)?",
        r"(?:因此|所以|综上|故|最终)\s*(?:应当|应该|直接|可以)?\s*(?:选择|选)\s*[A-H](?:\s*项)?",
        r"(?:本题|这题)\s*(?:最终|应当|应该|正确地?)?\s*(?:选择|选)\s*[A-H](?:\s*项)?",
        r"(?:正确的|符合题意的)\s*(?:选项)?\s*(?:是|为)\s*[A-H](?:\s*项)?",
        r"[A-H](?:\s*项)?\s*(?:是|为)\s*(?:正确答案|正确选项)",
        r"^\s*(?:我会|应该|应当|直接)?\s*选\s*[A-H](?:\s*项)?[\s。！!]*$",
    )
)

_SAFE_ANSWERING_HINT = (
    "先不直接揭晓最终选项。请先把题干要求的量、材料中的基期与现期"
    "分别标出，再写出你准备使用的关系式；我会根据这一步继续给提示。"
)


def enforce_answering_output_contract(
    result: AICompanionAnswerResult,
    retrieval_payload: dict[str, Any],
) -> AICompanionAnswerResult:
    """Prevent only explicit final-answer declarations during active answering.

    Explanation, comparison, elimination and calculation language remains
    valid.  If the single main model still announces a final option, the
    service returns a deterministic Tutor hint rather than dispatching another
    provider call.
    """

    bundle = RetrievalBundle.model_validate(retrieval_payload)
    answering = any(
        item.trust_scope == "authorized_question"
        and item.reference.anchor == "server-stage:answering"
        for item in bundle.sources
    )
    if not answering or not _declares_final_answer(result.answer):
        return result
    return replace(
        result,
        answer=_SAFE_ANSWERING_HINT,
        confidence="medium",
        limitations=[
            *result.limitations,
            "原回答触及作答中的最终答案，已改为逐步提示。",
        ][:8],
        response_summary="[answering_output_safe_hint] " + _SAFE_ANSWERING_HINT,
    )


def _declares_final_answer(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _FINAL_OPTION_PATTERNS)


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
