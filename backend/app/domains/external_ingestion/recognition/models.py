from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.contracts.external_learning.models import RecognitionKind, Sha256Hex


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ArtifactInput(StrictModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    owner_user_id: UUID = Field(alias="ownerUserId")
    state: Literal["available"]
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


class OMRMark(StrictModel):
    item_no: int = Field(alias="itemNo", ge=1)
    selected_options: tuple[Annotated[str, StringConstraints(pattern=r"^[A-H]$")], ...] = Field(
        alias="selectedOptions", max_length=8
    )
    box: BoundingBox
    confidence: float = Field(ge=0, le=1)
    ambiguous: bool = False


class RecognitionCandidatePayload(StrictModel):
    schema_version: Literal["recognition-candidate-payload@v1"] = Field(
        default="recognition-candidate-payload@v1", alias="schemaVersion"
    )
    kind: RecognitionKind
    text_spans: tuple[TextSpan, ...] = Field(default=(), alias="textSpans")
    segments: tuple[PageSegment, ...] = ()
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
        elif self.kind == RecognitionKind.ANSWER_SHEET_OMR:
            if not self.omr_marks:
                raise ValueError("OMR candidates require marks")
        return self


class ProcessorApproval(StrictModel):
    policy_version: NonBlank = Field(alias="policyVersion")
    route: NonBlank
    provider: NonBlank
    provider_release: NonBlank = Field(alias="providerRelease")
    output_contract: Literal["recognition-processor-output@v1"] = Field(
        default="recognition-processor-output@v1", alias="outputContract"
    )
    approved_kinds: tuple[RecognitionKind, ...] = Field(alias="approvedKinds", min_length=1)
    approved_mime_types: tuple[NonBlank, ...] = Field(alias="approvedMimeTypes", min_length=1)
    data_region: Literal["mainland_china"] = Field(alias="dataRegion")
    dpa_evidence_ref: NonBlank = Field(alias="dpaEvidenceRef")
    security_review_ref: NonBlank = Field(alias="securityReviewRef")
    approval_receipt_ref: NonBlank = Field(alias="approvalReceiptRef")
    model_training_use: Literal["prohibited"] = Field(
        default="prohibited", alias="modelTrainingUse"
    )
    provider_retention_seconds: Literal[0] = Field(
        default=0, alias="providerRetentionSeconds"
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
