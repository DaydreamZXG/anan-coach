from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.contracts.external_learning.models import RecognitionKind, Sha256Hex, SourceKind
from app.domains.ai.provider_contract import validate_provider_audit_contract
from app.domains.external_ingestion.recognition.errors import ProcessorNotDispatchedError


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ProviderReplayKey = Annotated[
    str,
    StringConstraints(pattern=r"^ocr-v1-[0-9a-f]{64}$"),
]


def provider_replay_key(
    *,
    owner_user_id: UUID,
    operation: str,
    command_id: UUID,
) -> str:
    """Return a stable provider key without exposing client keys or account IDs."""

    normalized_operation = operation.strip().casefold()
    if not normalized_operation or any(
        not (character.isalnum() or character == "_") for character in normalized_operation
    ):
        raise ValueError("recognition provider replay operation is invalid")
    digest = sha256(
        (
            f"recognition-provider-replay@v1:{owner_user_id}:{normalized_operation}:{command_id}"
        ).encode("utf-8")
    ).hexdigest()
    return f"ocr-v1-{digest}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


Hash64Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]


class VisualFeatureHash(StrictModel):
    """Transform-tolerant hashes for one source image."""

    phash: Hash64Hex
    dhash: Hash64Hex
    ahash: Hash64Hex
    edge_dhash: Hash64Hex = Field(alias="edgeDhash")


class NormalizedRegionBox(StrictModel):
    """Normalized page region without a duplicated page identity."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_page(self) -> "NormalizedRegionBox":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("normalized region box must remain inside its page")
        return self


class QuestionRegionVisualEvidence(StrictModel):
    """Immutable visual hashes owned by exactly one recognized question unit."""

    contract_version: Literal["question-region-visual-evidence@v1"] = Field(
        default="question-region-visual-evidence@v1", alias="contractVersion"
    )
    question_unit_id: Sha256Hex = Field(alias="questionUnitId")
    page_no: int = Field(alias="pageNo", ge=1)
    box: NormalizedRegionBox
    source_page_image_sha256: Sha256Hex = Field(alias="sourcePageImageSha256")
    visual_fingerprints: tuple[Sha256Hex, ...] = Field(
        alias="visualFingerprints", min_length=1
    )
    visual_feature_hashes: tuple[VisualFeatureHash, ...] = Field(
        alias="visualFeatureHashes", min_length=1
    )
    derivation: Literal["prompt-vertical-band@v1"] = "prompt-vertical-band@v1"

    @model_validator(mode="after")
    def fingerprints_match_feature_hashes(self) -> "QuestionRegionVisualEvidence":
        if len(self.visual_fingerprints) != len(self.visual_feature_hashes):
            raise ValueError("region fingerprints must map one-to-one to feature hashes")
        from app.domains.external_ingestion.recognition.visual_hash import (
            fingerprint_sha256,
        )

        expected = tuple(
            fingerprint_sha256(
                {
                    "phash": item.phash,
                    "dhash": item.dhash,
                    "ahash": item.ahash,
                    "edge_dhash": item.edge_dhash,
                }
            )
            for item in self.visual_feature_hashes
        )
        if self.visual_fingerprints != expected:
            raise ValueError("region fingerprints must be derived from their feature hashes")
        return self


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
    prompt_segment_refs: tuple[NonBlank, ...] = Field(alias="promptSegmentRefs", min_length=1)
    option_segment_refs: tuple[NonBlank, ...] = Field(default=(), alias="optionSegmentRefs")
    material_segment_refs: tuple[NonBlank, ...] = Field(default=(), alias="materialSegmentRefs")

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
    option_labels: tuple[Annotated[str, StringConstraints(pattern=r"^[A-H]$")], ...] = Field(
        alias="optionLabels", min_length=2, max_length=8
    )

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
        if item_numbers != sorted(item_numbers) or len(item_numbers) != len(set(item_numbers)):
            raise ValueError("OMR layout item numbers must be ordered and unique")
        return self


class RecognitionCandidatePayload(StrictModel):
    schema_version: Literal["recognition-candidate-payload@v1"] = Field(
        default="recognition-candidate-payload@v1", alias="schemaVersion"
    )
    kind: RecognitionKind
    text_spans: tuple[TextSpan, ...] = Field(default=(), alias="textSpans")
    segments: tuple[PageSegment, ...] = ()
    question_units: tuple[QuestionUnit, ...] = Field(default=(), alias="questionUnits")
    omr_marks: tuple[OMRMark, ...] = Field(default=(), alias="omrMarks")
    page_image_sha256: tuple[Sha256Hex, ...] = Field(default=(), alias="pageImageSha256")
    visual_fingerprints: tuple[Sha256Hex, ...] = Field(default=(), alias="visualFingerprints")
    visual_feature_hashes: tuple[VisualFeatureHash, ...] = Field(
        default=(), alias="visualFeatureHashes"
    )
    question_region_visual_evidence: tuple[QuestionRegionVisualEvidence, ...] = Field(
        default=(), alias="questionRegionVisualEvidence"
    )
    processor_request_id_hash: Sha256Hex = Field(alias="processorRequestIdHash")
    processor_output_contract: NonBlank = Field(alias="processorOutputContract")
    request_hash: Sha256Hex = Field(alias="requestHash")
    predecessor_revision_id: UUID | None = Field(default=None, alias="predecessorRevisionId")
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
                raise ValueError("segment textSpanRefs must resolve to spans in the same payload")
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
            region_unit_ids = [
                item.question_unit_id for item in self.question_region_visual_evidence
            ]
            if len(region_unit_ids) != len(set(region_unit_ids)):
                raise ValueError("question-region evidence unit ids must be unique")
            if set(region_unit_ids) - set(unit_ids):
                raise ValueError("question-region evidence must resolve to a question unit")
            if any(
                item.page_no > len(self.page_image_sha256)
                for item in self.question_region_visual_evidence
            ):
                raise ValueError("question-region evidence page must resolve to a rendered page")
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
        if (
            self.kind != RecognitionKind.EXAM_SEGMENTATION
            and self.question_region_visual_evidence
        ):
            raise ValueError("question-region evidence is only valid for exam segmentation")
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
    data_region: Literal["mainland_china", "operator_selected_external"] = Field(alias="dataRegion")
    dpa_evidence_ref: NonBlank = Field(alias="dpaEvidenceRef")
    security_review_ref: NonBlank = Field(alias="securityReviewRef")
    approval_receipt_ref: NonBlank = Field(alias="approvalReceiptRef")
    transport_security: Literal["tls"] = Field(default="tls", alias="transportSecurity")
    provider_data_handling: Literal[
        "contracted_zero_retention",
        "operator_accepted_external_processing",
    ] = Field(default="contracted_zero_retention", alias="providerDataHandling")
    model_training_use: Literal["prohibited", "not_asserted"] = Field(
        default="not_asserted", alias="modelTrainingUse"
    )
    provider_retention_seconds: int | None = Field(
        default=0, alias="providerRetentionSeconds", ge=0
    )
    provider_idempotency_capability: Literal[
        "replayable_queryable",
        "nonreplayable_audited",
    ] = Field(
        default="nonreplayable_audited",
        alias="providerIdempotencyCapability",
    )
    provider_idempotency_scope: Literal["provider_account", "endpoint"] = Field(
        default="endpoint", alias="providerIdempotencyScope"
    )
    provider_idempotency_key_header: Literal["Idempotency-Key"] = Field(
        default="Idempotency-Key", alias="providerIdempotencyKeyHeader"
    )
    provider_request_id_source: Literal[
        "body:providerRequestId",
        "body:id",
        "X-Request-ID",
    ] = Field(default="X-Request-ID", alias="providerRequestIdSource")
    provider_replay_retention_seconds: int = Field(
        default=0,
        alias="providerReplayRetentionSeconds",
        ge=0,
    )
    provider_idempotency_evidence_ref: NonBlank = Field(
        default="mvp-runtime-default@v1",
        alias="providerIdempotencyEvidenceRef",
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
        if (
            self.provider_data_handling == "contracted_zero_retention"
            and self.provider_retention_seconds != 0
        ):
            raise ValueError("contracted zero-retention processing must record zero seconds")
        validate_provider_audit_contract(
            idempotency_capability=self.provider_idempotency_capability,
            idempotency_key_header=self.provider_idempotency_key_header,
            request_id_source=self.provider_request_id_source,
            replay_retention_seconds=self.provider_replay_retention_seconds,
        )
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
    idempotency_key: ProviderReplayKey = Field(alias="idempotencyKey")
    omr_layout: OMRLayout | None = Field(default=None, alias="omrLayout")
    before_provider_dispatch: Callable[[], None] | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )

    @model_validator(mode="after")
    def context_matches_kind(self) -> "ProcessorRequest":
        if (self.recognition_kind == RecognitionKind.ANSWER_SHEET_OMR) != (
            self.omr_layout is not None
        ):
            raise ValueError("answer-sheet OMR requests require a sealed packet layout")
        return self


def enforce_provider_dispatch_fence(request: ProcessorRequest) -> None:
    """Run the durable claim immediately before provider client construction."""

    guard = request.before_provider_dispatch
    if guard is None:
        return
    try:
        guard()
    except ProcessorNotDispatchedError:
        raise
    except Exception as exc:
        raise ProcessorNotDispatchedError(
            "recognition provider dispatch was denied before provider I/O"
        ) from exc


class ProcessorResult(StrictModel):
    contract_version: Literal["recognition-processor-output@v1"] = Field(
        default="recognition-processor-output@v1", alias="contractVersion"
    )
    provider_request_id: NonBlank = Field(alias="providerRequestId")
    text_spans: tuple[TextSpan, ...] = Field(default=(), alias="textSpans")
    segments: tuple[PageSegment, ...] = ()
    omr_marks: tuple[OMRMark, ...] = Field(default=(), alias="omrMarks")
    page_image_sha256: tuple[Sha256Hex, ...] = Field(default=(), alias="pageImageSha256")
    visual_fingerprints: tuple[Sha256Hex, ...] = Field(default=(), alias="visualFingerprints")
    visual_feature_hashes: tuple[VisualFeatureHash, ...] = Field(
        default=(), alias="visualFeatureHashes"
    )
    trusted_rendered_page_images: tuple[bytes, ...] = Field(
        default=(), exclude=True, repr=False
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
