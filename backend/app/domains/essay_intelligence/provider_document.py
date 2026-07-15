from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.essay_intelligence.grounding import (
    GroundedReviewContext,
    build_answer_segments,
    normalize_grounding_text,
)
from app.domains.essay_intelligence.normalization import NORMALIZATION_CONTRACT
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
    REVIEW_CONTRACT_VERSION,
)


_PROVIDER_MATERIAL_VERSION_ID = UUID("00000000-0000-5000-8000-000000000001")
_PROVIDER_ANSWER_VERSION_ID = UUID("00000000-0000-5000-8000-000000000002")


class _ProviderDocumentModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )


class ProviderTaskDocument(_ProviderDocumentModel):
    version_ref: str = Field(alias="versionRef")
    question_type: str = Field(alias="questionType")
    prompt: str
    word_limit: int = Field(alias="wordLimit")
    maximum_score: float = Field(alias="maximumScore")


class ProviderMaterialSegmentDocument(_ProviderDocumentModel):
    material_ref: str = Field(alias="materialRef")
    segment_ref: str = Field(alias="segmentRef")
    text: str
    normalized_text: str = Field(alias="normalizedText")
    normalization: str


class ProviderMaterialDocument(_ProviderDocumentModel):
    version_ref: str = Field(alias="versionRef")
    segments: list[ProviderMaterialSegmentDocument]


class ProviderRubricCriterionDocument(_ProviderDocumentModel):
    criterion_code: str = Field(alias="criterionCode")
    label: str
    maximum: float
    scoring_rule: str = Field(alias="scoringRule")
    material_evidence_required: bool = Field(alias="materialEvidenceRequired")


class ProviderRubricEvidenceDocument(_ProviderDocumentModel):
    material_version_ref: str = Field(alias="materialVersionRef")
    material_ref: str = Field(alias="materialRef")
    segment_ref: str = Field(alias="segmentRef")
    start_offset: int = Field(alias="startOffset")
    end_offset: int = Field(alias="endOffset")
    quote: str


class ProviderRubricPointDocument(_ProviderDocumentModel):
    rubric_point_id: str = Field(alias="rubricPointId")
    criterion_code: str = Field(alias="criterionCode")
    category: str
    label: str
    maximum_credit: float = Field(alias="maximumCredit")
    material_segment_refs: list[str] = Field(alias="materialSegmentRefs")
    material_evidence_refs: list[ProviderRubricEvidenceDocument] = Field(
        alias="materialEvidenceRefs"
    )


class ProviderRubricDocument(_ProviderDocumentModel):
    version_ref: str = Field(alias="versionRef")
    source_kind: str = Field(alias="sourceKind")
    verification_status: str = Field(alias="verificationStatus")
    total_maximum: float = Field(alias="totalMaximum")
    criteria: list[ProviderRubricCriterionDocument]
    points: list[ProviderRubricPointDocument]


class ProviderThinkingDocument(_ProviderDocumentModel):
    point_finding_text: str | None = Field(default=None, alias="pointFindingText")
    outline_text: str | None = Field(default=None, alias="outlineText")
    user_thought: str | None = Field(default=None, alias="userThought")


class ProviderAnswerSegmentDocument(_ProviderDocumentModel):
    segment_ref: str = Field(alias="segmentRef")
    normalized_text: str = Field(alias="normalizedText")
    normalization: str


class ProviderAnswerDocument(_ProviderDocumentModel):
    version_ref: str = Field(alias="versionRef")
    text: str
    thinking: ProviderThinkingDocument | None
    segments: list[ProviderAnswerSegmentDocument]


class GroundedReviewProviderInputDocument(_ProviderDocumentModel):
    review_contract_version: str = Field(alias="reviewContractVersion")
    provider_schema_version: str = Field(alias="providerSchemaVersion")
    task: ProviderTaskDocument
    material: ProviderMaterialDocument
    rubric: ProviderRubricDocument
    answer: ProviderAnswerDocument


@dataclass(frozen=True)
class ProviderReferenceMap:
    """Maps request-local anonymous references back to immutable domain references."""

    material_version_id: UUID
    answer_version_id: UUID
    material_ids_by_ref: dict[str, str]
    material_segment_ids_by_ref: dict[str, str]
    answer_segment_ids_by_ref: dict[str, str]

    def restore_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._translate(payload, to_provider=False)

    def anonymize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._translate(payload, to_provider=True)

    def anonymize_rejected_content(self, content: str) -> str:
        """Never forward an internal review payload or arbitrary worker detail."""
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        return json.dumps(
            self.anonymize_payload(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _translate(
        self,
        payload: dict[str, Any],
        *,
        to_provider: bool,
    ) -> dict[str, Any]:
        translated = deepcopy(payload)
        material_ids = (
            {value: key for key, value in self.material_ids_by_ref.items()}
            if to_provider
            else self.material_ids_by_ref
        )
        material_segments = (
            {
                value: key
                for key, value in self.material_segment_ids_by_ref.items()
            }
            if to_provider
            else self.material_segment_ids_by_ref
        )
        answer_segments = (
            {value: key for key, value in self.answer_segment_ids_by_ref.items()}
            if to_provider
            else self.answer_segment_ids_by_ref
        )
        material_version_from = (
            str(self.material_version_id)
            if to_provider
            else str(_PROVIDER_MATERIAL_VERSION_ID)
        )
        material_version_to = (
            str(_PROVIDER_MATERIAL_VERSION_ID)
            if to_provider
            else str(self.material_version_id)
        )
        answer_version_from = (
            str(self.answer_version_id)
            if to_provider
            else str(_PROVIDER_ANSWER_VERSION_ID)
        )
        answer_version_to = (
            str(_PROVIDER_ANSWER_VERSION_ID)
            if to_provider
            else str(self.answer_version_id)
        )

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if not isinstance(value, dict):
                return
            if value.get("answerVersionId") == answer_version_from:
                value["answerVersionId"] = answer_version_to
                segment_id = value.get("segmentId")
                if isinstance(segment_id, str) and segment_id in answer_segments:
                    value["segmentId"] = answer_segments[segment_id]
            if value.get("materialBundleVersionId") == material_version_from:
                value["materialBundleVersionId"] = material_version_to
                material_id = value.get("materialId")
                if isinstance(material_id, str) and material_id in material_ids:
                    value["materialId"] = material_ids[material_id]
                segment_id = value.get("segmentId")
                if isinstance(segment_id, str) and segment_id in material_segments:
                    value["segmentId"] = material_segments[segment_id]
            for item in value.values():
                visit(item)

        visit(translated)
        return translated


@dataclass(frozen=True)
class PreparedGroundedReviewProviderInput:
    document: GroundedReviewProviderInputDocument
    references: ProviderReferenceMap


def prepare_grounded_review_provider_input(
    context: GroundedReviewContext,
) -> PreparedGroundedReviewProviderInput:
    material_refs: dict[str, str] = {}
    material_segment_refs: dict[str, str] = {}
    material_documents: list[ProviderMaterialSegmentDocument] = []
    for item in context.material.segments:
        if item.material_id not in material_refs:
            material_refs[item.material_id] = f"source-{len(material_refs) + 1}"
        segment_ref = f"source-segment-{len(material_segment_refs) + 1}"
        material_segment_refs[item.segment_id] = segment_ref
        material_documents.append(
            ProviderMaterialSegmentDocument(
                material_ref=material_refs[item.material_id],
                segment_ref=segment_ref,
                text=item.text,
                normalized_text=normalize_grounding_text(item.text),
                normalization=NORMALIZATION_CONTRACT,
            )
        )

    answer_segments = build_answer_segments(context.answer.answer_text)
    answer_segment_refs = {
        item.segment_id: f"response-segment-{index}"
        for index, item in enumerate(answer_segments, start=1)
    }
    references = ProviderReferenceMap(
        material_version_id=context.material.material_bundle_version_id,
        answer_version_id=context.answer.answer_version_id,
        material_ids_by_ref={value: key for key, value in material_refs.items()},
        material_segment_ids_by_ref={
            value: key for key, value in material_segment_refs.items()
        },
        answer_segment_ids_by_ref={
            value: key for key, value in answer_segment_refs.items()
        },
    )
    rubric_points = [
        ProviderRubricPointDocument(
            rubric_point_id=point.rubric_point_id,
            criterion_code=point.criterion_code,
            category=point.category,
            label=point.label,
            maximum_credit=point.maximum_credit,
            material_segment_refs=[
                material_segment_refs[segment_id]
                for segment_id in point.material_segment_ids
            ],
            material_evidence_refs=[
                ProviderRubricEvidenceDocument(
                    material_version_ref=str(_PROVIDER_MATERIAL_VERSION_ID),
                    material_ref=material_refs[item.material_id],
                    segment_ref=material_segment_refs[item.segment_id],
                    start_offset=item.start_offset,
                    end_offset=item.end_offset,
                    quote=item.quote,
                )
                for item in point.material_evidence_refs
            ],
        )
        for point in context.rubric.points
    ]
    thinking = context.answer.thinking
    document = GroundedReviewProviderInputDocument(
        review_contract_version=REVIEW_CONTRACT_VERSION,
        provider_schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
        task=ProviderTaskDocument(
            version_ref="task-version",
            question_type=context.task.question_type,
            prompt=context.task.prompt,
            word_limit=context.task.word_limit,
            maximum_score=context.task.maximum_score,
        ),
        material=ProviderMaterialDocument(
            version_ref=str(_PROVIDER_MATERIAL_VERSION_ID),
            segments=material_documents,
        ),
        rubric=ProviderRubricDocument(
            version_ref="rubric-version",
            source_kind=context.rubric.source_kind,
            verification_status=context.rubric.verification_status,
            total_maximum=context.rubric.total_maximum,
            criteria=[
                ProviderRubricCriterionDocument(
                    criterion_code=item.criterion_code,
                    label=item.label,
                    maximum=item.maximum,
                    scoring_rule=item.scoring_rule,
                    material_evidence_required=item.material_evidence_required,
                )
                for item in context.rubric.criteria
            ],
            points=rubric_points,
        ),
        answer=ProviderAnswerDocument(
            version_ref=str(_PROVIDER_ANSWER_VERSION_ID),
            text=context.answer.answer_text,
            thinking=(
                ProviderThinkingDocument(
                    point_finding_text=thinking.point_finding_text,
                    outline_text=thinking.outline_text,
                    user_thought=thinking.user_thought,
                )
                if thinking is not None
                else None
            ),
            segments=[
                ProviderAnswerSegmentDocument(
                    segment_ref=answer_segment_refs[item.segment_id],
                    normalized_text=item.normalized_text,
                    normalization=NORMALIZATION_CONTRACT,
                )
                for item in answer_segments
            ],
        ),
    )
    return PreparedGroundedReviewProviderInput(
        document=document,
        references=references,
    )
