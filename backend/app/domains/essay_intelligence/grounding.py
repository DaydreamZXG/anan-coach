from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
from uuid import UUID

from app.domains.essay_intelligence.normalization import (
    NORMALIZATION_CONTRACT,
    normalize_grounding_text,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDING_CONTRACT_VERSION,
    GROUNDED_REVIEW_PROMPT_VERSION,
    GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
    REVIEW_CONTRACT_VERSION,
    RUBRIC_CONTRACT_VERSION,
)
from app.domains.essay_intelligence.schemas import (
    AnswerEvidenceRef,
    EssayAnswerVersion,
    EssayAttempt,
    EssayMaterialBundleVersion,
    EssayProviderReviewPayload,
    EssayReviewAudit,
    EssayReviewRequest,
    EssayRubricVersion,
    EssayTaskVersion,
    MaterialEvidenceRef,
)


class GroundingViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GroundedReviewContext:
    task: EssayTaskVersion
    material: EssayMaterialBundleVersion
    rubric: EssayRubricVersion
    attempt: EssayAttempt
    answer: EssayAnswerVersion


@dataclass(frozen=True)
class GroundingValidationReport:
    contract_version: str
    input_fingerprint: str
    output_fingerprint: str
    answer_segment_ids: tuple[str, ...]
    material_segment_ids: tuple[str, ...]


@dataclass(frozen=True)
class _SourceSegment:
    segment_id: str
    text: str
    material_id: str | None = None

    @property
    def normalized_text(self) -> str:
        return normalize_grounding_text(self.text)


class GroundedReviewValidator:
    def validate(
        self,
        *,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
        output: EssayProviderReviewPayload,
        audit: EssayReviewAudit,
    ) -> GroundingValidationReport:
        self._validate_version_bindings(context=context, request=request)
        material_segments = {
            item.segment_id: _SourceSegment(
                segment_id=item.segment_id,
                material_id=item.material_id,
                text=item.text,
            )
            for item in context.material.segments
        }
        answer_segments = {
            item.segment_id: item
            for item in build_answer_segments(context.answer.answer_text)
        }
        self._validate_rubric(
            context=context,
            output=output,
            material_segments=material_segments,
        )
        self._validate_all_anchors(
            context=context,
            output=output,
            material_segments=material_segments,
            answer_segments=answer_segments,
        )
        input_fingerprint = review_input_fingerprint(context, request)
        output_fingerprint = review_output_fingerprint(output)
        self._validate_audit(
            context=context,
            audit=audit,
            input_fingerprint=input_fingerprint,
            output_fingerprint=output_fingerprint,
        )
        return GroundingValidationReport(
            contract_version=GROUNDING_CONTRACT_VERSION,
            input_fingerprint=input_fingerprint,
            output_fingerprint=output_fingerprint,
            answer_segment_ids=tuple(answer_segments),
            material_segment_ids=tuple(material_segments),
        )

    def _validate_version_bindings(
        self,
        *,
        context: GroundedReviewContext,
        request: EssayReviewRequest,
    ) -> None:
        task = context.task
        material = context.material
        rubric = context.rubric
        attempt = context.attempt
        answer = context.answer
        if task.state != "published":
            raise GroundingViolation(
                "task_not_published",
                "Grounded review requires the pinned published task version",
            )
        if material.completeness != "complete" or not material.segments:
            raise GroundingViolation(
                "material_incomplete",
                "Grounded review requires a complete pinned material bundle",
            )
        if rubric.state != "published":
            raise GroundingViolation(
                "rubric_not_published",
                "Grounded review requires the pinned published rubric version",
            )
        expected = (
            request.attempt_id == attempt.attempt_id,
            request.task_version_id == task.task_version_id == attempt.task_version_id,
            request.answer_version_id == answer.answer_version_id,
            request.material_bundle_version_id
            == material.material_bundle_version_id
            == attempt.material_bundle_version_id,
            request.rubric_version_id
            == rubric.rubric_version_id
            == attempt.rubric_version_id,
            material.task_version_id == task.task_version_id,
            rubric.task_version_id == task.task_version_id,
            rubric.material_bundle_version_id
            == material.material_bundle_version_id,
            answer.attempt_id == attempt.attempt_id,
            answer.task_version_id == task.task_version_id,
            answer.user_id == attempt.user_id,
        )
        if not all(expected):
            raise GroundingViolation(
                "version_binding_invalid",
                "Review request, attempt, answer, task, material, and rubric "
                "versions must match",
            )

    def _validate_rubric(
        self,
        *,
        context: GroundedReviewContext,
        output: EssayProviderReviewPayload,
        material_segments: dict[str, _SourceSegment],
    ) -> None:
        rubric = context.rubric
        if abs(float(context.task.maximum_score) - float(rubric.total_maximum)) > 0.001:
            raise GroundingViolation(
                "rubric_total_mismatch",
                "Rubric total maximum must equal the pinned task maximum score",
            )
        criteria = {item.criterion_code: item for item in rubric.criteria}
        dimensions = {item.criterion_code: item for item in output.dimensions}
        if set(dimensions) != set(criteria):
            raise GroundingViolation(
                "criteria_coverage_incomplete",
                "Review dimensions must cover every rubric criterion exactly once",
            )
        for code, criterion in criteria.items():
            dimension = dimensions[code]
            if (
                dimension.label != criterion.label
                or abs(float(dimension.maximum) - float(criterion.maximum)) > 0.001
            ):
                raise GroundingViolation(
                    "criterion_contract_mismatch",
                    f"Review dimension {code} does not match its rubric criterion",
                )

        points = {item.rubric_point_id: item for item in rubric.points}
        coverage = {item.rubric_point_id: item for item in output.point_coverage}
        if set(coverage) != set(points):
            raise GroundingViolation(
                "point_coverage_incomplete",
                "Review point coverage must cover every rubric point exactly once",
            )
        point_maximum = sum(float(item.maximum_credit) for item in points.values())
        if abs(point_maximum - float(rubric.total_maximum)) > 0.001:
            raise GroundingViolation(
                "rubric_point_total_mismatch",
                "Rubric point maximum credits must conserve the rubric total",
            )
        if abs(float(output.score.maximum) - float(rubric.total_maximum)) > 0.001:
            raise GroundingViolation(
                "score_maximum_mismatch",
                "Review score maximum must equal the pinned rubric total",
            )
        for point_id, item in coverage.items():
            point = points[point_id]
            if item.awarded_range.upper > float(point.maximum_credit) + 0.001:
                raise GroundingViolation(
                    "point_score_out_of_range",
                    f"Review point {point_id} exceeds its maximum credit",
                )
            permitted_segments = set(point.material_segment_ids)
            if not permitted_segments <= set(material_segments):
                raise GroundingViolation(
                    "rubric_material_segment_unknown",
                    f"Rubric point {point_id} cites an unknown material segment",
                )
            if any(
                ref.segment_id not in permitted_segments
                for ref in item.material_evidence_refs
            ):
                raise GroundingViolation(
                    "point_material_scope_invalid",
                    f"Review point {point_id} cites material outside its rubric scope",
                )
        for field in ("lower", "recommended", "upper"):
            point_total = sum(
                float(getattr(item.awarded_range, field))
                for item in output.point_coverage
            )
            score_total = float(getattr(output.score.score_range, field))
            if abs(point_total - score_total) > 0.001:
                raise GroundingViolation(
                    "point_score_conservation_invalid",
                    "Point awarded ranges must sum to the total score range",
                )
        if (
            rubric.verification_status == "unverified"
            and output.score.confidence != "low"
        ):
            raise GroundingViolation(
                "unverified_rubric_overconfidence",
                "An unverified rubric permits only low-confidence scoring",
            )

    def _validate_all_anchors(
        self,
        *,
        context: GroundedReviewContext,
        output: EssayProviderReviewPayload,
        material_segments: dict[str, _SourceSegment],
        answer_segments: dict[str, _SourceSegment],
    ) -> None:
        items = [
            *output.dimensions,
            *output.point_coverage,
            *output.claim_evidence,
            *output.findings,
        ]
        answer_refs = [ref for item in items for ref in item.answer_evidence_refs]
        material_refs = [
            ref for item in items for ref in item.material_evidence_refs
        ]
        for ref in answer_refs:
            self._validate_answer_anchor(
                ref=ref,
                answer_version_id=context.answer.answer_version_id,
                segments=answer_segments,
            )
        for ref in material_refs:
            self._validate_material_anchor(
                ref=ref,
                material_bundle_version_id=context.material.material_bundle_version_id,
                segments=material_segments,
            )

    def _validate_answer_anchor(
        self,
        *,
        ref: AnswerEvidenceRef,
        answer_version_id: UUID,
        segments: dict[str, _SourceSegment],
    ) -> None:
        if ref.answer_version_id != answer_version_id:
            raise GroundingViolation(
                "answer_version_anchor_invalid",
                "Answer quote references a different answer version",
            )
        self._validate_quote_anchor(ref=ref, segments=segments)

    def _validate_material_anchor(
        self,
        *,
        ref: MaterialEvidenceRef,
        material_bundle_version_id: UUID,
        segments: dict[str, _SourceSegment],
    ) -> None:
        if ref.material_bundle_version_id != material_bundle_version_id:
            raise GroundingViolation(
                "material_version_anchor_invalid",
                "Material quote references a different material bundle version",
            )
        source = segments.get(ref.segment_id)
        if source is None or source.material_id != ref.material_id:
            raise GroundingViolation(
                "material_segment_anchor_invalid",
                "Material quote references an unknown material/segment pair",
            )
        self._validate_quote_anchor(ref=ref, segments=segments)

    def _validate_quote_anchor(
        self,
        *,
        ref: AnswerEvidenceRef | MaterialEvidenceRef,
        segments: dict[str, _SourceSegment],
    ) -> None:
        if ref.normalization != NORMALIZATION_CONTRACT:
            raise GroundingViolation(
                "normalization_contract_missing",
                "Every grounded quote must declare nfkc_whitespace_v1",
            )
        if ref.normalized_start is None or ref.normalized_end is None:
            raise GroundingViolation(
                "normalized_offset_missing",
                "Every grounded quote must include normalized character offsets",
            )
        source = segments.get(ref.segment_id)
        if source is None:
            raise GroundingViolation(
                "source_segment_unknown",
                "Grounded quote references an unknown source segment",
            )
        normalized_source = source.normalized_text
        start = ref.normalized_start
        end = ref.normalized_end
        if end > len(normalized_source):
            raise GroundingViolation(
                "normalized_offset_out_of_range",
                "Grounded quote offset exceeds its normalized source segment",
            )
        normalized_quote = normalize_grounding_text(ref.quote)
        anchored_text = normalized_source[start:end]
        if normalized_quote != anchored_text:
            raise GroundingViolation(
                "quote_text_anchor_invalid",
                "Normalized quote does not match the declared source slice",
            )
        expected_hash = sha256(anchored_text.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(ref.quote_sha256, expected_hash):
            raise GroundingViolation(
                "quote_hash_anchor_invalid",
                "Grounded quote hash does not match the normalized source slice",
            )

    def _validate_audit(
        self,
        *,
        context: GroundedReviewContext,
        audit: EssayReviewAudit,
        input_fingerprint: str,
        output_fingerprint: str,
    ) -> None:
        if (
            audit.contract_version != REVIEW_CONTRACT_VERSION
            or audit.provider_schema_version
            != GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION
            or audit.rubric_version != RUBRIC_CONTRACT_VERSION
            or audit.prompt_version != GROUNDED_REVIEW_PROMPT_VERSION
            or audit.rubric_source != context.rubric.source_kind
            or audit.rubric_verification != context.rubric.verification_status
            or audit.input_fingerprint != input_fingerprint
            or audit.output_fingerprint != output_fingerprint
        ):
            raise GroundingViolation(
                "review_audit_binding_invalid",
                "Review audit does not match the fixed input/output versions",
            )


def build_answer_segments(answer_text: str) -> tuple[_SourceSegment, ...]:
    paragraphs = [item.strip() for item in answer_text.splitlines() if item.strip()]
    if not paragraphs:
        raise GroundingViolation(
            "answer_content_empty",
            "Grounded review requires non-empty answer content",
        )
    return tuple(
        _SourceSegment(segment_id=f"answer-{index}", text=text)
        for index, text in enumerate(paragraphs, start=1)
    )


def review_input_fingerprint(
    context: GroundedReviewContext,
    request: EssayReviewRequest,
) -> str:
    return _fingerprint(
        {
            "groundingContractVersion": GROUNDING_CONTRACT_VERSION,
            "request": request.model_dump(by_alias=True, mode="json"),
            "task": {
                "versionId": str(context.task.task_version_id),
                "promptSha256": context.task.prompt_sha256,
            },
            "material": {
                "versionId": str(context.material.material_bundle_version_id),
                "contentSha256": context.material.content_sha256,
            },
            "rubric": {
                "versionId": str(context.rubric.rubric_version_id),
                "contentSha256": context.rubric.content_sha256,
                "sourceKind": context.rubric.source_kind,
                "verificationStatus": context.rubric.verification_status,
            },
            "attemptId": str(context.attempt.attempt_id),
            "answer": {
                "versionId": str(context.answer.answer_version_id),
                "answerSha256": context.answer.answer_sha256,
                "thinkingSha256": context.answer.thinking_sha256,
            },
        }
    )


def review_output_fingerprint(output: EssayProviderReviewPayload) -> str:
    return _fingerprint(output.model_dump(by_alias=True, mode="json"))


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
