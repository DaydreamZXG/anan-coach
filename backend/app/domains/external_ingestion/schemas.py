from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.external_learning.models import (
    ContentMatchVersion,
    LearningTrack,
    RecognitionRevision,
    SourceArtifactVersion,
    SourceKind,
)
from app.domains.external_ingestion.upload.models import (
    CompleteUploadResult,
    UploadMode,
    StartUploadResult,
    UploadSession,
    UploadState,
)
from app.contracts.external_learning.models import RecognitionKind
from app.domains.external_ingestion.recognition.models import RecognitionCandidatePayload


class IngestionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StartUploadCommand(IngestionModel):
    source_kind: SourceKind = Field(alias="sourceKind")
    original_filename: str = Field(alias="originalFilename", min_length=1, max_length=255)
    mime_type: str = Field(alias="mimeType", min_length=1, max_length=200)
    byte_size: int = Field(alias="byteSize", gt=0)
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")
    page_count: int | None = Field(default=None, alias="pageCount", ge=1)
    part_sha256s: tuple[str, ...] = Field(
        default=(), alias="partSha256s", max_length=10_000
    )


class StartRecognitionCommand(IngestionModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    recognition_kind: RecognitionKind = Field(alias="recognitionKind")
    packet_version_id: UUID | None = Field(default=None, alias="packetVersionId")
    processor_route: str | None = Field(
        default=None, alias="processorRoute", min_length=1, max_length=160
    )

    @model_validator(mode="after")
    def sealed_omr_context(self) -> "StartRecognitionCommand":
        if (self.recognition_kind == RecognitionKind.ANSWER_SHEET_OMR) != (
            self.packet_version_id is not None
        ):
            raise ValueError("answer-sheet OMR requires exactly one packet version")
        return self


class CorrectRecognitionCommand(IngestionModel):
    expected_revision_no: int = Field(alias="expectedRevisionNo", ge=1)
    corrected_payload: RecognitionCandidatePayload = Field(alias="correctedPayload")
    reason: str = Field(min_length=1, max_length=500)


class RecognitionDecisionCommand(IngestionModel):
    expected_revision_no: int = Field(alias="expectedRevisionNo", ge=1)


class ReprocessRecognitionCommand(RecognitionDecisionCommand):
    processor_route: str = Field(alias="processorRoute", min_length=1, max_length=160)
    packet_version_id: UUID | None = Field(default=None, alias="packetVersionId")


class ProcessImportCommand(IngestionModel):
    learning_track: LearningTrack = Field(alias="learningTrack")


class ImportSegmentCorrection(IngestionModel):
    segment_id: str = Field(alias="segmentId", min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=200_000)


class CorrectImportRecognitionCommand(RecognitionDecisionCommand):
    segments: tuple[ImportSegmentCorrection, ...] = Field(min_length=1, max_length=500)


class ConfirmImportRecognitionCommand(RecognitionDecisionCommand):
    learning_track: LearningTrack = Field(alias="learningTrack")


class ConfirmImportMatchCommand(IngestionModel):
    expected_version_no: int = Field(alias="expectedVersionNo", ge=1)
    confirmed_content_version_id: str = Field(
        alias="confirmedContentVersionId", pattern=r"^[0-9a-f]{64}$"
    )


class RequestImportAIAnswerCommand(IngestionModel):
    question_unit_id: str = Field(alias="questionUnitId", pattern=r"^[0-9a-f]{64}$")
    learning_track: LearningTrack = Field(alias="learningTrack")


class ConfirmImportAIAnswerCommand(IngestionModel):
    expected_output_fingerprint: str = Field(
        alias="expectedOutputFingerprint", pattern=r"^[0-9a-f]{64}$"
    )


class ImportRecognitionSegment(IngestionModel):
    segment_id: str = Field(alias="segmentId")
    page_no: int = Field(alias="pageNo", ge=1)
    ordinal: int = Field(ge=1)
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    state: Literal["candidate", "confirmed", "rejected"]
    requires_correction: bool = Field(alias="requiresCorrection")


class ImportReferenceAnswer(IngestionModel):
    source_type: Literal["authorized_reference"] = Field(alias="sourceType")
    match_kind: Literal["exact", "similar"] = Field(alias="matchKind")
    answer_text: str = Field(alias="answerText", min_length=1, max_length=200_000)
    content_version_id: str = Field(
        alias="contentVersionId", pattern=r"^[0-9a-f]{64}$"
    )
    authorization_state: Literal["authorized_current"] = Field(
        alias="authorizationState"
    )
    learning_impact: Literal["reference_only"] = Field(alias="learningImpact")


class ImportAIAnswer(IngestionModel):
    answer_version_id: UUID = Field(alias="answerVersionId")
    state: Literal["requested", "processing", "candidate", "confirmed", "failed"]
    answer_text: str | None = Field(alias="answerText")
    explanation: str | None
    key_points: tuple[str, ...] = Field(alias="keyPoints")
    confidence: Literal["low", "medium", "high"] | None
    limitations: tuple[str, ...]
    output_fingerprint: str | None = Field(
        alias="outputFingerprint", pattern=r"^[0-9a-f]{64}$"
    )
    failure_code: str | None = Field(alias="failureCode")
    source_type: Literal["ai_estimated"] = Field(
        default="ai_estimated", alias="sourceType"
    )
    learning_impact: Literal["reference_only"] = Field(
        default="reference_only", alias="learningImpact"
    )


class ImportQuestionUnit(IngestionModel):
    question_unit_id: str = Field(alias="questionUnitId", pattern=r"^[0-9a-f]{64}$")
    ordinal: int = Field(ge=1)
    segment_ids: tuple[str, ...] = Field(alias="segmentIds", min_length=1)
    match: ContentMatchVersion | None
    reference_answer: ImportReferenceAnswer | None = Field(alias="referenceAnswer")
    ai_answer: ImportAIAnswer | None = Field(alias="aiAnswer")
    state: Literal[
        "recognition_review", "match_review", "unmatched", "ready"
    ]
    partial_reasons: tuple[str, ...] = Field(alias="partialReasons")


class ImportWorkspace(IngestionModel):
    import_id: UUID = Field(alias="importId")
    state: Literal[
        "uploading", "processing", "review", "ready", "failed", "quarantined", "expired"
    ]
    artifact: SourceArtifactVersion | None
    recognition: RecognitionRevision | None
    recognition_segments: tuple[ImportRecognitionSegment, ...] = Field(
        alias="recognitionSegments"
    )
    question_units: tuple[ImportQuestionUnit, ...] = Field(alias="questionUnits")
    partial_reasons: tuple[str, ...] = Field(alias="partialReasons")
    failure_reason: str | None = Field(alias="failureReason")
    processing_label: str | None = Field(alias="processingLabel")
    poll_after_ms: int | None = Field(alias="pollAfterMs", ge=500, le=30_000)
    expires_at: datetime | None = Field(alias="expiresAt")
    diagnostic_eligibility: Literal["blocked_unconfirmed", "eligible_confirmed"] = Field(
        alias="diagnosticEligibility"
    )
    generated_at: datetime = Field(alias="generatedAt")


class UploadSessionProjection(IngestionModel):
    session_id: UUID = Field(alias="sessionId")
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    revision: int
    state: UploadState
    source_kind: SourceKind = Field(alias="sourceKind")
    original_filename: str = Field(alias="originalFilename")
    mime_type: str = Field(alias="mimeType")
    byte_size: int = Field(alias="byteSize")
    content_sha256: str = Field(alias="contentSha256")
    page_count: int | None = Field(alias="pageCount")
    expires_at: datetime = Field(alias="expiresAt")
    recognition_eligible: bool = Field(alias="recognitionEligible")
    failure_code: str | None = Field(alias="failureCode")
    upload_mode: UploadMode = Field(alias="uploadMode")
    part_size: int | None = Field(alias="partSize")
    part_count: int = Field(alias="partCount", ge=0)
    uploaded_part_count: int = Field(alias="uploadedPartCount", ge=0)


class UploadPartAuthorizationProjection(IngestionModel):
    object_ref: str = Field(alias="objectRef")
    part_number: int = Field(alias="partNumber", ge=1)
    byte_size: int = Field(alias="byteSize", gt=0)
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")
    url: str
    required_headers: dict[str, str] = Field(alias="requiredHeaders")
    expires_at: datetime = Field(alias="expiresAt")


class UploadAuthorizationProjection(IngestionModel):
    object_ref: str = Field(alias="objectRef")
    mode: UploadMode
    expires_at: datetime = Field(alias="expiresAt")
    parts: tuple[UploadPartAuthorizationProjection, ...] = Field(min_length=1)
    provider_upload_id: str | None = Field(alias="providerUploadId")
    part_size: int | None = Field(alias="partSize")


class StartUploadProjection(IngestionModel):
    session: UploadSessionProjection
    authorization: UploadAuthorizationProjection
    replayed: bool


class CompleteUploadProjection(IngestionModel):
    session: UploadSessionProjection
    artifact: SourceArtifactVersion | None
    replayed: bool


def upload_session_projection(session: UploadSession) -> UploadSessionProjection:
    return UploadSessionProjection(
        sessionId=session.session_id,
        artifactVersionId=session.artifact_version_id,
        revision=session.revision,
        state=session.state,
        sourceKind=session.source_kind,
        originalFilename=session.original_filename,
        mimeType=session.mime_type,
        byteSize=session.byte_size,
        contentSha256=session.content_sha256,
        pageCount=session.page_count,
        expiresAt=session.expires_at,
        recognitionEligible=session.recognition_eligible,
        failureCode=session.failure_code,
        uploadMode=session.upload_mode,
        partSize=session.part_size,
        partCount=len(session.expected_parts),
        uploadedPartCount=len(session.uploaded_parts),
    )


def start_upload_projection(result: StartUploadResult) -> StartUploadProjection:
    return StartUploadProjection(
        session=upload_session_projection(result.session),
        authorization=UploadAuthorizationProjection(
            objectRef=result.authorization.object_ref,
            mode=result.authorization.mode,
            expiresAt=result.authorization.expires_at,
            parts=tuple(
                UploadPartAuthorizationProjection(
                    objectRef=part.object_ref,
                    partNumber=part.part_number,
                    byteSize=part.byte_size,
                    contentSha256=part.content_sha256,
                    url=part.url,
                    requiredHeaders=part.required_headers,
                    expiresAt=part.expires_at,
                )
                for part in result.authorization.parts
            ),
            providerUploadId=result.authorization.provider_upload_id,
            partSize=result.authorization.part_size,
        ),
        replayed=result.replayed,
    )


def complete_upload_projection(result: CompleteUploadResult) -> CompleteUploadProjection:
    return CompleteUploadProjection(
        session=upload_session_projection(result.session),
        artifact=result.artifact,
        replayed=result.replayed,
    )
