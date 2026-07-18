from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.contracts.external_learning.models import RecognitionKind, Sha256Hex, SourceKind


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ArtifactInput(StrictModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    owner_user_id: UUID = Field(alias="ownerUserId")
    state: Literal["available"]
    source_kind: SourceKind = Field(alias="sourceKind")
    object_ref: NonBlank = Field(alias="objectRef")
    mime_type: NonBlank = Field(alias="mimeType")
    content_sha256: Sha256Hex = Field(alias="contentSha256")
    page_count: int | None = Field(default=None, alias="pageCount", ge=1)


class BoundingBox(StrictModel):
    page_no: int = Field(alias="pageNo", ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_page(self) -> "BoundingBox":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("bounding box must remain inside its page")
        return self


class TextSpan(StrictModel):
    span_id: NonBlank = Field(alias="spanId")
    text: Annotated[str, StringConstraints(min_length=1, max_length=100_000)]
    box: BoundingBox
    confidence: float = Field(ge=0, le=1)


class PageSegment(StrictModel):
    segment_id: NonBlank = Field(alias="segmentId")
    segment_kind: Literal[
        "question", "options", "material", "answer_area", "essay_prompt", "unknown"
    ] = Field(alias="segmentKind")
    box: BoundingBox
    text_span_refs: tuple[NonBlank, ...] = Field(default=(), alias="textSpanRefs")
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def unique_span_refs(self) -> "PageSegment":
        if len(self.text_span_refs) != len(set(self.text_span_refs)):
            raise ValueError("segment textSpanRefs must be unique")
        return self


class QuestionUnit(StrictModel):
    """A server-sealed, independently matchable question inside an artifact."""

    question_unit_id: Sha256Hex = Field(alias="questionUnitId")
    ordinal: int = Field(ge=1)
    prompt_segment_refs: tuple[NonBlank, ...] = Field(
        alias="promptSegmentRefs", min_length=1
    )
    option_segment_refs: tuple[NonBlank, ...] = Field(
        default=(), alias="optionSegmentRefs"
    )
    material_segment_refs: tuple[NonBlank, ...] = Field(
        default=(), alias="materialSegmentRefs"
    )

    @model_validator(mode="after")
    def unique_refs(self) -> "QuestionUnit":
        groups = (
            self.prompt_segment_refs,
            self.option_segment_refs,
            self.material_segment_refs,
        )
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("question-unit segment references must be unique")
        flattened = [item for group in groups for item in group]
        if len(flattened) != len(set(flattened)):
            raise ValueError("a segment cannot have two roles in one question unit")
        return self


class OMRMark(StrictModel):
    item_no: int = Field(alias="itemNo", ge=1)
    selected_options: tuple[Annotated[str, StringConstraints(pattern=r"^[A-H]$")], ...] = Field(
        alias="selectedOptions", max_length=8
    )
    box: BoundingBox
    confidence: float = Field(ge=0, le=1)
    ambiguous: bool = False


class OMRExpectedItem(StrictModel):
    item_no: int = Field(alias="itemNo", ge=1)
    option_labels: tuple[
        Annotated[str, StringConstraints(pattern=r"^[A-H]$")], ...
    ] = Field(alias="optionLabels", min_length=2, max_length=8)

    @model_validator(mode="after")
    def unique_option_labels(self) -> "OMRExpectedItem":
        if len(self.option_labels) != len(set(self.option_labels)):
            raise ValueError("OMR expected option labels must be unique")
        return self


class OMRLayout(StrictModel):
    template_version: Literal["anan-omr-a4@v1"] = Field(
        default="anan-omr-a4@v1", alias="templateVersion"
    )
    packet_version_id: UUID = Field(alias="packetVersionId")
    items: tuple[OMRExpectedItem, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def ordered_unique_items(self) -> "OMRLayout":
        item_numbers = [item.item_no for item in self.items]
        if item_numbers != sorted(item_numbers) or len(item_numbers) != len(
            set(item_numbers)
        ):
            raise ValueError("OMR layout item numbers must be ordered and unique")
        return self


class RecognitionCandidatePayload(StrictModel):
    schema_version: Literal["recognition-candidate-payload@v1"] = Field(
        default="recognition-candidate-payload@v1", alias="schemaVersion"
    )
    kind: RecognitionKind
    text_spans: tuple[TextSpan, ...] = Field(default=(), alias="textSpans")
    segments: tuple[PageSegment, ...] = ()
    question_units: tuple[QuestionUnit, ...] = Field(
        default=(), alias="questionUnits"
    )
    omr_marks: tuple[OMRMark, ...] = Field(default=(), alias="omrMarks")
    page_image_sha256: tuple[Sha256Hex, ...] = Field(default=(), alias="pageImageSha256")
    visual_fingerprints: tuple[Sha256Hex, ...] = Field(
        default=(), alias="visualFingerprints"
    )
    processor_request_id_hash: Sha256Hex = Field(alias="processorRequestIdHash")
    processor_output_contract: NonBlank = Field(alias="processorOutputContract")
    request_hash: Sha256Hex = Field(alias="requestHash")
    predecessor_revision_id: UUID | None = Field(
        default=None, alias="predecessorRevisionId"
    )
    correction_reason: str | None = Field(default=None, alias="correctionReason", max_length=500)

    @model_validator(mode="after")
    def kind_shape(self) -> "RecognitionCandidatePayload":
        span_ids = [item.span_id for item in self.text_spans]
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("recognition text span ids must be unique")
        segment_ids = [item.segment_id for item in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("recognition segment ids must be unique")
        known_spans = set(span_ids)
        for segment in self.segments:
            unknown_refs = set(segment.text_span_refs) - known_spans
            if unknown_refs:
                raise ValueError(
                    "segment textSpanRefs must resolve to spans in the same payload"
                )
        item_numbers = [item.item_no for item in self.omr_marks]
        if len(item_numbers) != len(set(item_numbers)):
            raise ValueError("recognition OMR item numbers must be unique")
        if self.kind in {RecognitionKind.PRINTED_OCR, RecognitionKind.HANDWRITING_OCR}:
            if not self.text_spans:
                raise ValueError("OCR candidates require text spans")
        elif self.kind == RecognitionKind.EXAM_SEGMENTATION:
            if not self.segments:
                raise ValueError("segmentation candidates require page segments")
            if not self.question_units:
                raise ValueError("segmentation candidates require question units")
            unit_ordinals = [item.ordinal for item in self.question_units]
            if unit_ordinals != list(range(1, len(unit_ordinals) + 1)):
                raise ValueError("question-unit ordinals must be contiguous and ordered")
            unit_ids = [item.question_unit_id for item in self.question_units]
            if len(unit_ids) != len(set(unit_ids)):
                raise ValueError("question-unit ids must be unique")
            by_id = {item.segment_id: item for item in self.segments}
            prompt_owners: set[str] = set()
            option_owners: set[str] = set()
            for unit in self.question_units:
                for ref in unit.prompt_segment_refs:
                    segment = by_id.get(ref)
                    if segment is None or segment.segment_kind not in {
                        "question",
                        "essay_prompt",
                    }:
                        raise ValueError("question-unit prompt refs must resolve to prompts")
                    if ref in prompt_owners:
                        raise ValueError("prompt segments belong to exactly one question unit")
                    prompt_owners.add(ref)
                for ref in unit.option_segment_refs:
                    segment = by_id.get(ref)
                    if segment is None or segment.segment_kind != "options":
                        raise ValueError("question-unit option refs must resolve to options")
                    if ref in option_owners:
                        raise ValueError("option segments belong to exactly one question unit")
                    option_owners.add(ref)
                for ref in unit.material_segment_refs:
                    segment = by_id.get(ref)
                    if segment is None or segment.segment_kind != "material":
                        raise ValueError("question-unit material refs must resolve to materials")
            expected_prompts = {
                item.segment_id
                for item in self.segments
                if item.segment_kind in {"question", "essay_prompt"}
            }
            if prompt_owners != expected_prompts:
                raise ValueError("every prompt segment must belong to a question unit")
        elif self.kind == RecognitionKind.ANSWER_SHEET_OMR:
            if not self.omr_marks:
                raise ValueError("OMR candidates require marks")
        return self


class ProcessorApproval(StrictModel):
    policy_version: NonBlank = Field(alias="policyVersion")
    route: NonBlank
    provider: NonBlank
    provider_release: NonBlank = Field(alias="providerRelease")
    model_release: NonBlank = Field(alias="modelRelease")
    prompt_version: NonBlank = Field(alias="promptVersion")
    price_version: NonBlank = Field(alias="priceVersion")
    billing_unit: Literal["page", "image", "call"] = Field(alias="billingUnit")
    unit_price_cny: Decimal = Field(alias="unitPriceCny", ge=0)
    output_contract: Literal["recognition-processor-output@v1"] = Field(
        default="recognition-processor-output@v1", alias="outputContract"
    )
    approved_kinds: tuple[RecognitionKind, ...] = Field(alias="approvedKinds", min_length=1)
    approved_mime_types: tuple[NonBlank, ...] = Field(alias="approvedMimeTypes", min_length=1)
    data_region: Literal["mainland_china", "operator_selected_external"] = Field(
        alias="dataRegion"
    )
    dpa_evidence_ref: NonBlank = Field(alias="dpaEvidenceRef")
    security_review_ref: NonBlank = Field(alias="securityReviewRef")
    approval_receipt_ref: NonBlank = Field(alias="approvalReceiptRef")
    transport_security: Literal["tls", "plaintext_http_operator_accepted"] = Field(
        default="tls", alias="transportSecurity"
    )
    provider_data_handling: Literal[
        "contracted_zero_retention", "operator_accepted_external_processing"
    ] = Field(default="contracted_zero_retention", alias="providerDataHandling")
    operator_acceptance_ref: NonBlank | None = Field(
        default=None, alias="operatorAcceptanceRef"
    )
    model_training_use: Literal["prohibited"] = Field(
        default="prohibited", alias="modelTrainingUse"
    )
    provider_retention_seconds: int | None = Field(
        default=0, alias="providerRetentionSeconds", ge=0
    )
    valid_from: AwareDatetime = Field(alias="validFrom")
    valid_until: AwareDatetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def valid_window(self) -> "ProcessorApproval":
        if self.valid_until <= self.valid_from:
            raise ValueError("processor approval window is empty")
        if len(self.approved_kinds) != len(set(self.approved_kinds)):
            raise ValueError("approvedKinds must be unique")
        if len(self.approved_mime_types) != len(set(self.approved_mime_types)):
            raise ValueError("approvedMimeTypes must be unique")
        has_operator_exception = (
            self.data_region == "operator_selected_external"
            or self.transport_security == "plaintext_http_operator_accepted"
            or self.provider_data_handling == "operator_accepted_external_processing"
        )
        if has_operator_exception != (self.operator_acceptance_ref is not None):
            raise ValueError(
                "operator-selected external processing requires one acceptance reference"
            )
        if (
            self.provider_data_handling == "contracted_zero_retention"
            and self.provider_retention_seconds != 0
        ):
            raise ValueError("contracted zero-retention processing must record zero seconds")
        return self

    def permits(self, *, kind: RecognitionKind, mime_type: str, at: datetime) -> bool:
        return (
            self.valid_from <= at < self.valid_until
            and kind in self.approved_kinds
            and mime_type in self.approved_mime_types
        )


class ProcessorRequest(StrictModel):
    contract_version: Literal["recognition-processor-input@v1"] = Field(
        default="recognition-processor-input@v1", alias="contractVersion"
    )
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    owner_scope_hash: Sha256Hex = Field(alias="ownerScopeHash")
    object_ref: NonBlank = Field(alias="objectRef")
    mime_type: NonBlank = Field(alias="mimeType")
    input_sha256: Sha256Hex = Field(alias="inputSha256")
    page_count: int | None = Field(default=None, alias="pageCount", ge=1)
    recognition_kind: RecognitionKind = Field(alias="recognitionKind")
    idempotency_key: NonBlank = Field(alias="idempotencyKey")
    omr_layout: OMRLayout | None = Field(default=None, alias="omrLayout")

    @model_validator(mode="after")
    def context_matches_kind(self) -> "ProcessorRequest":
        if (self.recognition_kind == RecognitionKind.ANSWER_SHEET_OMR) != (
            self.omr_layout is not None
        ):
            raise ValueError("answer-sheet OMR requests require a sealed packet layout")
        return self


class ProcessorResult(StrictModel):
    contract_version: Literal["recognition-processor-output@v1"] = Field(
        default="recognition-processor-output@v1", alias="contractVersion"
    )
    provider_request_id: NonBlank = Field(alias="providerRequestId")
    text_spans: tuple[TextSpan, ...] = Field(default=(), alias="textSpans")
    segments: tuple[PageSegment, ...] = ()
    omr_marks: tuple[OMRMark, ...] = Field(default=(), alias="omrMarks")
    page_image_sha256: tuple[Sha256Hex, ...] = Field(default=(), alias="pageImageSha256")
    visual_fingerprints: tuple[Sha256Hex, ...] = Field(
        default=(), alias="visualFingerprints"
    )
    confidence: float | None = Field(default=None, ge=0, le=1)


class RecognitionEvidenceDecision(StrictModel):
    allowed: bool
    reason: Literal[
        "confirmed_revision",
        "candidate_unconfirmed",
        "revision_rejected",
        "revision_expired",
        "revision_superseded",
    ]
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId")
    may_affect_ability: bool = Field(alias="mayAffectAbility")
    may_affect_cause: bool = Field(alias="mayAffectCause")
    may_affect_plan: bool = Field(alias="mayAffectPlan")
