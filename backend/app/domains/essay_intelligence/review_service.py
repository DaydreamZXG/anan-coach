from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.domains.essay_intelligence.grounding import (
    GroundedReviewContext,
    GroundedReviewValidator,
    GroundingValidationReport,
    GroundingViolation,
)
from app.domains.essay_intelligence.schemas import (
    EssayProviderReviewPayload,
    EssayReviewAudit,
    EssayReviewReadiness,
    EssayReviewRequest,
    EssayReviewResponse,
)


@dataclass(frozen=True)
class GroundedReviewApplicationResult:
    response: EssayReviewResponse
    grounding: GroundingValidationReport | None
    blocking_detail: str | None


class StructuredReviewPayloadError(ValueError):
    """The application boundary could not decode a structured review document."""


class GroundedReviewApplicationService:
    """Turns untrusted structured model output into one all-or-nothing review."""

    def __init__(self, *, validator: GroundedReviewValidator | None = None) -> None:
        self._validator = validator or GroundedReviewValidator()

    def apply(
        self,
        *,
        review_version_id: UUID,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        provider_output: EssayProviderReviewPayload | dict[str, Any],
        audit: EssayReviewAudit | dict[str, Any],
        supersedes_review_version_id: UUID | None = None,
    ) -> GroundedReviewApplicationResult:
        try:
            output = _parse_provider_output(provider_output)
        except (ValidationError, StructuredReviewPayloadError) as exc:
            return self._blocked(
                review_version_id=review_version_id,
                context=context,
                request=request,
                code="provider_schema_invalid",
                detail=_safe_error_detail(exc),
                supersedes_review_version_id=supersedes_review_version_id,
            )
        try:
            parsed_audit = _parse_audit(audit)
        except (ValidationError, StructuredReviewPayloadError) as exc:
            return self._blocked(
                review_version_id=review_version_id,
                context=context,
                request=request,
                code="review_audit_schema_invalid",
                detail=_safe_error_detail(exc),
                supersedes_review_version_id=supersedes_review_version_id,
            )
        try:
            grounding = self._validator.validate(
                context=context,
                request=request,
                output=output,
                audit=parsed_audit,
            )
        except GroundingViolation as exc:
            return self._blocked(
                review_version_id=review_version_id,
                context=context,
                request=request,
                code=exc.code,
                detail=str(exc),
                supersedes_review_version_id=supersedes_review_version_id,
            )

        try:
            response = EssayReviewResponse(
                review_version_id=review_version_id,
                attempt_id=context.attempt.attempt_id,
                task_version_id=context.task.task_version_id,
                material_bundle_version_id=(
                    context.material.material_bundle_version_id
                ),
                answer_version_id=context.answer.answer_version_id,
                rubric_version_id=context.rubric.rubric_version_id,
                status="completed",
                review_scope=request.review_scope,
                thinking_evidence_available=context.answer.thinking is not None,
                readiness=_readiness(context=context, blocking_codes=[]),
                score=output.score,
                dimensions=output.dimensions,
                point_coverage=output.point_coverage,
                claim_evidence=output.claim_evidence,
                findings=output.findings,
                rewrite_plan=output.rewrite_plan,
                audit=parsed_audit,
                supersedes_review_version_id=supersedes_review_version_id,
            )
        except ValidationError as exc:
            return self._blocked(
                review_version_id=review_version_id,
                context=context,
                request=request,
                code="review_contract_invalid",
                detail=_safe_error_detail(exc),
                supersedes_review_version_id=supersedes_review_version_id,
            )
        return GroundedReviewApplicationResult(
            response=response,
            grounding=grounding,
            blocking_detail=None,
        )

    def _blocked(
        self,
        *,
        review_version_id: UUID,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        code: str,
        detail: str,
        supersedes_review_version_id: UUID | None,
    ) -> GroundedReviewApplicationResult:
        response = EssayReviewResponse(
            review_version_id=review_version_id,
            attempt_id=context.attempt.attempt_id,
            task_version_id=context.task.task_version_id,
            material_bundle_version_id=context.material.material_bundle_version_id,
            answer_version_id=context.answer.answer_version_id,
            rubric_version_id=context.rubric.rubric_version_id,
            status="blocked",
            review_scope=request.review_scope,
            thinking_evidence_available=context.answer.thinking is not None,
            readiness=_readiness(context=context, blocking_codes=[code]),
            score=None,
            dimensions=[],
            point_coverage=[],
            claim_evidence=[],
            findings=[],
            rewrite_plan=None,
            audit=None,
            supersedes_review_version_id=supersedes_review_version_id,
        )
        return GroundedReviewApplicationResult(
            response=response,
            grounding=None,
            blocking_detail=detail,
        )


def _parse_provider_output(
    value: EssayProviderReviewPayload | dict[str, Any],
) -> EssayProviderReviewPayload:
    if isinstance(value, EssayProviderReviewPayload):
        return value
    try:
        return EssayProviderReviewPayload.model_validate(value)
    except ValidationError:
        return EssayProviderReviewPayload.model_validate_json(_as_json(value))


def _parse_audit(value: EssayReviewAudit | dict[str, Any]) -> EssayReviewAudit:
    if isinstance(value, EssayReviewAudit):
        return value
    try:
        return EssayReviewAudit.model_validate(value)
    except ValidationError:
        return EssayReviewAudit.model_validate_json(_as_json(value))


def _as_json(value: dict[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise StructuredReviewPayloadError(str(exc)) from exc


def _safe_error_detail(
    error: ValidationError | StructuredReviewPayloadError,
) -> str:
    if isinstance(error, ValidationError):
        return json.dumps(
            error.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return str(error)


def _readiness(
    *,
    context: GroundedReviewContext,
    blocking_codes: list[str],
) -> EssayReviewReadiness:
    return EssayReviewReadiness(
        material_complete=(
            context.material.completeness == "complete"
            and bool(context.material.segments)
        ),
        prompt_complete=bool(context.task.prompt.strip()),
        answer_complete=bool(context.answer.answer_text.strip()),
        ocr_confirmed=context.answer.source_type in {"typed", "ocr_confirmed"},
        blocking_codes=blocking_codes,
    )
