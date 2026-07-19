from __future__ import annotations

from typing import Any

from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.essay_intelligence.grounded_provider import (
    GroundedReviewProviderError,
    GroundedReviewProviderResult,
    build_grounded_review_messages,
    parse_grounded_review_content,
)
from app.domains.essay_intelligence.grounding import GroundedReviewContext
from app.domains.essay_intelligence.hunyuan_cloud_function import (
    HunyuanCloudFunctionClient,
    HunyuanCloudFunctionError,
    HunyuanCloudFunctionSettings,
)
from app.domains.essay_intelligence.provider_document import (
    PreparedGroundedReviewProviderInput,
    prepare_grounded_review_provider_input,
)
from app.domains.essay_intelligence.schemas import EssayReviewRequest


class HunyuanGroundedEssayReviewProvider:
    """Grounded candidate adapter whose model and credentials remain in CloudBase."""

    def __init__(
        self,
        settings: HunyuanCloudFunctionSettings | None = None,
        *,
        client: HunyuanCloudFunctionClient | None = None,
    ) -> None:
        resolved = settings or HunyuanCloudFunctionSettings.from_environment()
        self._settings = resolved
        self._client = client or HunyuanCloudFunctionClient(resolved)

    @property
    def provider_name(self) -> str:
        return f"cloudbase-hunyuan@{self._settings.release_id.strip()}"

    @property
    def provider_release_id(self) -> str:
        return self._settings.release_id.strip()

    @property
    def model(self) -> str:
        return self._settings.model.strip()

    @property
    def endpoint(self) -> str:
        return self._client.endpoint

    def generate(
        self,
        *,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        idempotency_key: str | None = None,
    ) -> GroundedReviewProviderResult:
        del request
        return self._call(
            prepared_input=prepare_grounded_review_provider_input(context),
            correction=None,
            idempotency_key=idempotency_key,
        )

    def repair(
        self,
        *,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        rejected_content: str,
        violation_code: str,
        violation_detail: str,
        idempotency_key: str | None = None,
    ) -> GroundedReviewProviderResult:
        del request, violation_detail
        prepared = prepare_grounded_review_provider_input(context)
        return self._call(
            prepared_input=prepared,
            correction={
                "violationCode": violation_code,
                "rejectedOutput": prepared.references.anonymize_rejected_content(
                    rejected_content
                ),
                "instruction": "重新生成完整 JSON；所有证据仍须引用固定匿名段落。",
            },
            idempotency_key=idempotency_key,
        )

    def _call(
        self,
        *,
        prepared_input: PreparedGroundedReviewProviderInput,
        correction: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> GroundedReviewProviderResult:
        messages, _, _ = build_grounded_review_messages(
            prepared_input,
            correction=correction,
        )
        if idempotency_key is None:
            raise GroundedReviewProviderError(
                "idempotency_key_required",
                "Hunyuan formal grading requires a stable request identity",
                retryable=False,
            )
        try:
            result = self._client.score_candidate(
                request_id=idempotency_key,
                messages=messages,  # type: ignore[arg-type]
            )
        except HunyuanCloudFunctionError as exc:
            raise GroundedReviewProviderError(
                exc.code,
                str(exc),
                retryable=exc.retryable,
            ) from exc
        payload = parse_grounded_review_content(result.text, prepared_input)
        return GroundedReviewProviderResult(
            payload=payload,
            raw_content=result.text,
            usage=ModelTokenUsage(
                input_tokens=result.usage.prompt_tokens,
                cached_input_tokens=0,
                output_tokens=result.usage.completion_tokens,
            ),
            latency_ms=result.latency_ms,
            output_fingerprint=result.output_fingerprint,
            provider_request_id_header=None,
            provider_request_id_fingerprint=None,
        )
