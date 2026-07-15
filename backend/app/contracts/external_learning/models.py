from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonBlank = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class SourceKind(StrEnum):
    CAMERA_IMAGE = "camera_image"
    IMAGE_IMPORT = "image_import"
    PDF_UPLOAD = "pdf_upload"
    ANSWER_SHEET = "answer_sheet"
    NOTES_APP_EXPORT = "notes_app_export"


class SourceArtifactState(StrEnum):
    UPLOADED = "uploaded"
    AVAILABLE = "available"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"
    DELETED = "deleted"


class RecognitionKind(StrEnum):
    PRINTED_OCR = "printed_ocr"
    HANDWRITING_OCR = "handwriting_ocr"
    EXAM_SEGMENTATION = "exam_segmentation"
    ANSWER_SHEET_OMR = "answer_sheet_omr"


class CandidateState(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class LearningTrack(StrEnum):
    XINGCE = "xingce"
    ESSAY = "essay"


class ContentComponent(StrEnum):
    QUESTION_PROMPT = "question_prompt"
    QUESTION_OPTIONS = "question_options"
    REFERENCE_ANSWER = "reference_answer"
    SOLUTION_EXPLANATION = "solution_explanation"
    MATERIAL_TEXT = "material_text"
    MATERIAL_TABLE = "material_table"
    MATERIAL_IMAGE = "material_image"
    RUBRIC = "rubric"
    USER_SUBMISSION = "user_submission"


class ContentUse(StrEnum):
    CATALOG_DISPLAY = "catalog_display"
    IN_APP_PRACTICE = "in_app_practice"
    PRIVATE_PRINT = "private_print"
    NOTE_EXPORT = "note_export"
    SHARE_FORWARD = "share_forward"
    CROSS_USER_MATCH = "cross_user_match"
    AI_GROUNDING = "ai_grounding"
    MODEL_TRAINING = "model_training"


class DeliveryChannel(StrEnum):
    WECHAT_MINIPROGRAM = "wechat_miniprogram"
    PDF_EXPORT = "pdf_export"
    IMAGE_EXPORT = "image_export"
    NOTE_APP_FILE = "note_app_file"
    QR_ACCESS = "qr_access"
    INTERNAL_RETRIEVAL = "internal_retrieval"
    AI_PROCESSOR = "ai_processor"


class Audience(StrEnum):
    ACCOUNT_OWNER = "account_owner"
    AUTHORIZED_RECIPIENT = "authorized_recipient"
    AUTHENTICATED_USERS = "authenticated_users"
    PUBLIC = "public"


class ContentTransform(StrEnum):
    VERBATIM = "verbatim"
    LAYOUT_REFLOW = "layout_reflow"
    EXCERPT = "excerpt"
    DERIVATIVE_EXPLANATION = "derivative_explanation"
    OCR = "ocr"
    EMBEDDING = "embedding"
    MODEL_TRAINING = "model_training"


class Territory(StrEnum):
    MAINLAND_CHINA = "mainland_china"
    CONTRACT_DEFINED = "contract_defined"


class LicenseState(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REJECTED = "rejected"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"
    ABANDONED = "abandoned"


CONTENT_COMPONENTS = tuple(item.value for item in ContentComponent)
CONTENT_USES = tuple(item.value for item in ContentUse)
DELIVERY_CHANNELS = tuple(item.value for item in DeliveryChannel)
AUDIENCES = tuple(item.value for item in Audience)
CONTENT_TRANSFORMS = tuple(item.value for item in ContentTransform)
TERRITORIES = tuple(item.value for item in Territory)


class SourceArtifactVersion(StrictContract):
    contract_version: Literal["source-artifact@v1"] = Field(
        default="source-artifact@v1", alias="contractVersion"
    )
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    artifact_id: UUID = Field(alias="artifactId")
    owner_user_id: UUID = Field(alias="ownerUserId")
    version_no: int = Field(alias="versionNo", ge=1)
    source_kind: SourceKind = Field(alias="sourceKind")
    state: SourceArtifactState
    object_ref: NonBlank = Field(alias="objectRef")
    mime_type: NonBlank = Field(alias="mimeType")
    content_sha256: Sha256Hex = Field(alias="contentSha256")
    byte_size: int = Field(alias="byteSize", gt=0)
    page_count: int | None = Field(default=None, alias="pageCount", ge=1)
    supersedes_artifact_version_id: UUID | None = Field(
        default=None, alias="supersedesArtifactVersionId"
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_lineage(self) -> SourceArtifactVersion:
        if (self.version_no == 1) != (self.supersedes_artifact_version_id is None):
            raise ValueError("version 1 has no predecessor; later versions require one")
        if self.source_kind == SourceKind.PDF_UPLOAD and self.page_count is None:
            raise ValueError("PDF artifacts require a page count")
        return self


class RecognitionRevision(StrictContract):
    contract_version: Literal["recognition-revision@v1"] = Field(
        default="recognition-revision@v1", alias="contractVersion"
    )
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId")
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    owner_user_id: UUID = Field(alias="ownerUserId")
    revision_no: int = Field(alias="revisionNo", ge=1)
    recognition_kind: RecognitionKind = Field(alias="recognitionKind")
    state: CandidateState
    processor_policy_version: NonBlank = Field(alias="processorPolicyVersion")
    processor_route: NonBlank = Field(alias="processorRoute")
    input_sha256: Sha256Hex = Field(alias="inputSha256")
    output_sha256: Sha256Hex = Field(alias="outputSha256")
    candidate_payload: dict[str, object] = Field(alias="candidatePayload")
    confidence: float | None = Field(default=None, ge=0, le=1)
    supersedes_recognition_revision_id: UUID | None = Field(
        default=None, alias="supersedesRecognitionRevisionId"
    )
    expires_at: AwareDatetime = Field(alias="expiresAt")
    confirmed_at: AwareDatetime | None = Field(default=None, alias="confirmedAt")
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_confirmation(self) -> RecognitionRevision:
        if (self.state == CandidateState.CONFIRMED) != (self.confirmed_at is not None):
            raise ValueError("only confirmed recognition revisions have confirmedAt")
        if (self.revision_no == 1) != (
            self.supersedes_recognition_revision_id is None
        ):
            raise ValueError("recognition revision lineage is incomplete")
        return self


class MatchCandidate(StrictContract):
    content_version_id: NonBlank = Field(alias="contentVersionId")
    match_kind: Literal["exact", "similar"] = Field(alias="matchKind")
    score: float = Field(ge=0, le=1)
    evidence_refs: tuple[NonBlank, ...] = Field(alias="evidenceRefs", min_length=1)


class ContentMatchVersion(StrictContract):
    contract_version: Literal["content-match@v1"] = Field(
        default="content-match@v1", alias="contractVersion"
    )
    content_match_version_id: UUID = Field(alias="contentMatchVersionId")
    content_match_id: UUID = Field(alias="contentMatchId")
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId")
    owner_user_id: UUID = Field(alias="ownerUserId")
    version_no: int = Field(alias="versionNo", ge=1)
    state: Literal["candidate", "confirmed", "rejected", "expired"]
    candidates: tuple[MatchCandidate, ...]
    evaluation_reason: Literal[
        "essay_incomplete",
        "no_eligible_candidate",
        "single_exact",
        "single_similar",
        "score_tie",
    ] = Field(alias="evaluationReason")
    confirmed_content_version_id: str | None = Field(
        default=None, alias="confirmedContentVersionId"
    )
    matching_policy_version: NonBlank = Field(alias="matchingPolicyVersion")
    input_fingerprint: Sha256Hex = Field(alias="inputFingerprint")
    supersedes_content_match_version_id: UUID | None = Field(
        default=None, alias="supersedesContentMatchVersionId"
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_match(self) -> ContentMatchVersion:
        candidate_ids = [item.content_version_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("content match candidates must be unique")
        if self.evaluation_reason in {"essay_incomplete", "no_eligible_candidate"}:
            if self.candidates:
                raise ValueError("non-match evaluation reasons cannot carry candidates")
        elif self.evaluation_reason == "score_tie":
            if len(self.candidates) < 2:
                raise ValueError("score ties require at least two candidates")
        elif len(self.candidates) != 1:
            raise ValueError("single-match evaluation reasons require one candidate")
        if self.evaluation_reason == "single_exact" and self.candidates[0].match_kind != "exact":
            raise ValueError("single_exact requires an exact candidate")
        if self.evaluation_reason == "single_similar" and self.candidates[0].match_kind != "similar":
            raise ValueError("single_similar requires a similar candidate")
        if self.state == "confirmed":
            if self.confirmed_content_version_id not in candidate_ids:
                raise ValueError("confirmed content must be one of the candidates")
        elif self.confirmed_content_version_id is not None:
            raise ValueError("only confirmed matches bind a content version")
        if (self.version_no == 1) != (
            self.supersedes_content_match_version_id is None
        ):
            raise ValueError("content match lineage is incomplete")
        return self


class OfflinePacketItem(StrictContract):
    packet_item_id: UUID = Field(alias="packetItemId")
    content_version_id: NonBlank = Field(alias="contentVersionId")
    track: LearningTrack
    order_no: int = Field(alias="orderNo", ge=1)
    answer_binding_code: NonBlank = Field(alias="answerBindingCode")


class OfflinePacketVersion(StrictContract):
    contract_version: Literal["offline-packet@v1"] = Field(
        default="offline-packet@v1", alias="contractVersion"
    )
    packet_version_id: UUID = Field(alias="packetVersionId")
    packet_id: UUID = Field(alias="packetId")
    owner_user_id: UUID = Field(alias="ownerUserId")
    version_no: int = Field(alias="versionNo", ge=1)
    state: Literal[
        "requested", "generating", "ready", "failed", "revoked", "expired"
    ]
    render_format: Literal["pdf", "note_app_file"] = Field(alias="renderFormat")
    items: tuple[OfflinePacketItem, ...] = Field(min_length=1)
    manifest_sha256: Sha256Hex = Field(alias="manifestSha256")
    license_snapshot_refs: tuple[NonBlank, ...] = Field(
        alias="licenseSnapshotRefs", min_length=1
    )
    artifact_ref: str | None = Field(default=None, alias="artifactRef")
    expires_at: AwareDatetime | None = Field(default=None, alias="expiresAt")
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_packet(self) -> OfflinePacketVersion:
        orders = [item.order_no for item in self.items]
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("packet item order must be contiguous")
        if self.state == "ready" and not self.artifact_ref:
            raise ValueError("ready packets require an artifact")
        return self


class AnswerIngestionVersion(StrictContract):
    contract_version: Literal["answer-ingestion@v1"] = Field(
        default="answer-ingestion@v1", alias="contractVersion"
    )
    ingestion_version_id: UUID = Field(alias="ingestionVersionId")
    ingestion_id: UUID = Field(alias="ingestionId")
    owner_user_id: UUID = Field(alias="ownerUserId")
    version_no: int = Field(alias="versionNo", ge=1)
    packet_version_id: UUID | None = Field(default=None, alias="packetVersionId")
    source_artifact_version_ids: tuple[UUID, ...] = Field(
        default=(), alias="sourceArtifactVersionIds"
    )
    track: LearningTrack
    input_mode: Literal[
        "answer_string", "answer_sheet_omr", "handwriting_ocr", "notes_app_export"
    ] = Field(alias="inputMode")
    state: Literal["candidate", "confirmed", "rejected", "committed", "conflict"]
    candidate_payload: dict[str, object] = Field(alias="candidatePayload")
    target_writer: Literal["xingce_learning", "essay_learning"] = Field(
        alias="targetWriter"
    )
    writer_contract_version: NonBlank = Field(alias="writerContractVersion")
    source_lineage_refs: tuple[NonBlank, ...] = Field(
        alias="sourceLineageRefs", min_length=1
    )
    confirmed_at: AwareDatetime | None = Field(default=None, alias="confirmedAt")
    committed_fact_ref: str | None = Field(default=None, alias="committedFactRef")
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_ingestion(self) -> AnswerIngestionVersion:
        expected_writer = {
            LearningTrack.XINGCE: "xingce_learning",
            LearningTrack.ESSAY: "essay_learning",
        }[self.track]
        if self.target_writer != expected_writer:
            raise ValueError("track must return to its canonical writer")
        if self.input_mode == "answer_string":
            if self.packet_version_id is None:
                raise ValueError("answer strings require a packet version")
        elif not self.source_artifact_version_ids:
            raise ValueError("artifact-based answer ingestion requires source artifacts")
        if self.state in {"confirmed", "committed"} and self.confirmed_at is None:
            raise ValueError("confirmed answer ingestion requires confirmedAt")
        if self.state == "committed" and not self.committed_fact_ref:
            raise ValueError("committed answer ingestion requires the canonical fact ref")
        if self.state not in {"confirmed", "committed"} and self.confirmed_at is not None:
            raise ValueError("unconfirmed candidates cannot carry confirmedAt")
        return self


class LicenseTerm(StrictContract):
    term_kind: Literal["fixed_term", "revocable_open_term"] = Field(alias="termKind")
    effective_at: AwareDatetime = Field(alias="effectiveAt")
    expires_at: AwareDatetime | Literal["not_applicable"] = Field(alias="expiresAt")
    renewal_policy_version_ref: NonBlank = Field(alias="renewalPolicyVersionRef")
    post_termination_new_use: Literal["prohibited"] = Field(
        default="prohibited", alias="postTerminationNewUse"
    )
    post_termination_live_access: Literal["revoke"] = Field(
        default="revoke", alias="postTerminationLiveAccess"
    )
    post_termination_stored_artifacts: Literal[
        "quarantine_then_policy_dispose"
    ] = Field(
        default="quarantine_then_policy_dispose",
        alias="postTerminationStoredArtifacts",
    )
    post_termination_printed_copies: Literal[
        "cannot_recall_notify_and_block_return"
    ] = Field(
        default="cannot_recall_notify_and_block_return",
        alias="postTerminationPrintedCopies",
    )
    post_termination_learning_evidence: Literal[
        "retain_historical_not_current_authority"
    ] = Field(
        default="retain_historical_not_current_authority",
        alias="postTerminationLearningEvidence",
    )

    @model_validator(mode="after")
    def validate_term(self) -> LicenseTerm:
        if self.term_kind == "fixed_term":
            if not isinstance(self.expires_at, datetime):
                raise ValueError("fixed terms require expiresAt")
            if self.expires_at <= self.effective_at:
                raise ValueError("license term must end after it starts")
        elif self.expires_at != "not_applicable":
            raise ValueError("open terms use the not_applicable expiry sentinel")
        return self


class LicenseGrantCell(StrictContract):
    component: ContentComponent
    use: ContentUse
    channel: DeliveryChannel
    audience: Audience
    transform: ContentTransform
    territory: Territory
    decision: Literal["allow"] = "allow"

    def tuple_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.component.value,
            self.use.value,
            self.channel.value,
            self.audience.value,
            self.transform.value,
            self.territory.value,
        )


class LicenseGrantMatrix(StrictContract):
    matrix_version_ref: NonBlank = Field(alias="matrixVersionRef")
    matrix_hash: Sha256Hex = Field(alias="matrixHash")
    components_coverage: tuple[ContentComponent, ...] = Field(
        alias="componentsCoverage"
    )
    uses_coverage: tuple[ContentUse, ...] = Field(alias="usesCoverage")
    channels_coverage: tuple[DeliveryChannel, ...] = Field(alias="channelsCoverage")
    audiences_coverage: tuple[Audience, ...] = Field(alias="audiencesCoverage")
    transforms_coverage: tuple[ContentTransform, ...] = Field(
        alias="transformsCoverage"
    )
    territories_coverage: tuple[Territory, ...] = Field(alias="territoriesCoverage")
    default_decision: Literal["deny"] = Field(default="deny", alias="defaultDecision")
    model_training_decision: Literal["deny"] = Field(
        default="deny", alias="modelTrainingDecision"
    )
    allowed_grant_cells: tuple[LicenseGrantCell, ...] = Field(
        default=(), alias="allowedGrantCells"
    )

    @model_validator(mode="after")
    def validate_closed_matrix(self) -> LicenseGrantMatrix:
        coverage = (
            (self.components_coverage, tuple(ContentComponent)),
            (self.uses_coverage, tuple(ContentUse)),
            (self.channels_coverage, tuple(DeliveryChannel)),
            (self.audiences_coverage, tuple(Audience)),
            (self.transforms_coverage, tuple(ContentTransform)),
            (self.territories_coverage, tuple(Territory)),
        )
        if any(tuple(values) != expected for values, expected in coverage):
            raise ValueError("grant matrix coverage must equal the closed v1 universe")
        keys = [cell.tuple_key() for cell in self.allowed_grant_cells]
        if len(keys) != len(set(keys)):
            raise ValueError("grant cells must be unique")
        for cell in self.allowed_grant_cells:
            if cell.use == ContentUse.MODEL_TRAINING:
                raise ValueError("model training is prohibited")
            if cell.transform == ContentTransform.MODEL_TRAINING:
                raise ValueError("model-training transforms are prohibited")
        if self.matrix_hash != canonical_grant_matrix_hash(
            self.model_dump(by_alias=True, mode="json")
        ):
            raise ValueError("matrixHash does not bind the complete grant matrix")
        return self


class RightsBase(StrictContract):
    source_id: NonBlank = Field(alias="sourceId")
    model_training_use: Literal["prohibited"] = Field(
        default="prohibited", alias="modelTrainingUse"
    )
    term: LicenseTerm
    grant_matrix: LicenseGrantMatrix = Field(alias="grantMatrix")


class AccountPrivateRights(RightsBase):
    distribution_class: Literal["account_private"] = Field(alias="distributionClass")
    rights_basis: Literal["user_private_purpose_limited"] = Field(alias="rightsBasis")
    subject_scope_ref: NonBlank = Field(alias="subjectScopeRef")
    user_authorization_receipt_ref: NonBlank = Field(alias="userAuthorizationReceiptRef")
    user_authorization_receipt_hash: Sha256Hex = Field(alias="userAuthorizationReceiptHash")
    cross_account_use: Literal["prohibited"] = Field(
        default="prohibited", alias="crossAccountUse"
    )
    forwarding: Literal["prohibited"] = "prohibited"

    @model_validator(mode="after")
    def validate_private_grants(self) -> AccountPrivateRights:
        for cell in self.grant_matrix.allowed_grant_cells:
            if cell.audience != Audience.ACCOUNT_OWNER:
                raise ValueError("account-private content is owner-only")
            if cell.use in {ContentUse.SHARE_FORWARD, ContentUse.CROSS_USER_MATCH}:
                raise ValueError("account-private content cannot be shared or cross-matched")
            if cell.channel == DeliveryChannel.QR_ACCESS:
                raise ValueError("account-private content cannot use transferable QR access")
        return self


class TransferableRights(RightsBase):
    distribution_class: Literal["licensed_transferable"] = Field(
        alias="distributionClass"
    )
    rights_basis: Literal["verified_agreement"] = Field(alias="rightsBasis")
    agreement_ref: NonBlank = Field(alias="agreementRef")
    agreement_hash: Sha256Hex = Field(alias="agreementHash")


LicenseRights = AccountPrivateRights | TransferableRights


class ReviewerReceipt(StrictContract):
    reviewer_id: NonBlank = Field(alias="reviewerId")
    decision: Literal["approved", "rejected"]
    reviewed_record_hash: Sha256Hex = Field(alias="reviewedRecordHash")
    evidence_manifest_hash: Sha256Hex = Field(alias="evidenceManifestHash")
    grant_matrix_hash: Sha256Hex = Field(alias="grantMatrixHash")
    reviewed_at: AwareDatetime = Field(alias="reviewedAt")


class ContentLicenseRecord(StrictContract):
    contract_version: Literal["content-license-record@v1"] = Field(
        default="content-license-record@v1", alias="contractVersion"
    )
    content_license_id: UUID = Field(alias="contentLicenseId")
    content_version_id: NonBlank = Field(alias="contentVersionId")
    revision: int = Field(ge=1)
    authorization_epoch: int = Field(alias="authorizationEpoch", ge=0)
    state: LicenseState
    created_by_actor_id: NonBlank = Field(alias="createdByActorId")
    closed_coverage_version_ref: NonBlank = Field(alias="closedCoverageVersionRef")
    closed_coverage_hash: Sha256Hex = Field(alias="closedCoverageHash")
    rights: LicenseRights
    evidence_manifest_ref: NonBlank = Field(alias="evidenceManifestRef")
    evidence_manifest_hash: Sha256Hex = Field(alias="evidenceManifestHash")
    record_canonical_hash: Sha256Hex = Field(alias="recordCanonicalHash")
    reviewer_receipt: ReviewerReceipt | None = Field(
        default=None, alias="reviewerReceipt"
    )
    supersedes_content_license_id: UUID | None = Field(
        default=None, alias="supersedesContentLicenseId"
    )
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")

    @model_validator(mode="after")
    def validate_authority_state(self) -> ContentLicenseRecord:
        never_activated = {
            LicenseState.DRAFT,
            LicenseState.IN_REVIEW,
            LicenseState.REJECTED,
            LicenseState.ABANDONED,
        }
        if self.state in never_activated:
            if self.authorization_epoch != 0:
                raise ValueError("pre-activation records have authorization epoch zero")
            if self.reviewer_receipt is not None and not (
                self.state in {LicenseState.REJECTED, LicenseState.ABANDONED}
                and self.reviewer_receipt.decision == "rejected"
            ):
                raise ValueError(
                    "only rejected or abandoned-after-rejection records carry "
                    "a pre-activation receipt"
                )
        else:
            if self.authorization_epoch < 1:
                raise ValueError("post-activation records require an authorization epoch")
            if self.reviewer_receipt is None:
                raise ValueError("post-activation records require a reviewer receipt")
        return self


class LicenseUseRequest(StrictContract):
    contract_version: Literal["content-license-evaluate@v1"] = Field(
        default="content-license-evaluate@v1", alias="contractVersion"
    )
    content_license_id: UUID = Field(alias="contentLicenseId")
    content_version_id: NonBlank = Field(alias="contentVersionId")
    expected_authorization_epoch: int | None = Field(
        default=None, alias="expectedAuthorizationEpoch", ge=1
    )
    component: ContentComponent
    use: ContentUse
    channel: DeliveryChannel
    audience: Audience
    transform: ContentTransform
    territory: Territory
    requested_at: AwareDatetime = Field(alias="requestedAt")

    def tuple_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.component.value,
            self.use.value,
            self.channel.value,
            self.audience.value,
            self.transform.value,
            self.territory.value,
        )


class LicenseDecision(StrictContract):
    contract_version: Literal["content-license-decision@v1"] = Field(
        default="content-license-decision@v1", alias="contractVersion"
    )
    snapshot_id: UUID = Field(alias="snapshotId")
    allowed: bool
    reason: Literal[
        "explicit_grant",
        "license_unknown",
        "content_version_mismatch",
        "license_not_active",
        "authorization_epoch_stale",
        "term_not_started",
        "term_expired",
        "default_deny",
    ]
    content_license_id: UUID = Field(alias="contentLicenseId")
    content_version_id: NonBlank = Field(alias="contentVersionId")
    license_revision: int | None = Field(alias="licenseRevision", ge=1)
    authorization_epoch: int | None = Field(alias="authorizationEpoch", ge=1)
    grant_matrix_hash: Sha256Hex | None = Field(alias="grantMatrixHash")
    evidence_manifest_hash: Sha256Hex | None = Field(alias="evidenceManifestHash")
    evaluated_at: AwareDatetime = Field(alias="evaluatedAt")


class LicenseSnapshotStatus(StrictContract):
    contract_version: Literal["content-license-snapshot-status@v1"] = Field(
        default="content-license-snapshot-status@v1", alias="contractVersion"
    )
    snapshot_id: UUID = Field(alias="snapshotId")
    valid: bool
    reason: Literal[
        "current",
        "snapshot_denied",
        "license_missing",
        "content_version_mismatch",
        "license_not_active",
        "authorization_epoch_changed",
        "term_not_started",
        "term_expired",
    ]
    bound_authorization_epoch: int | None = Field(
        alias="boundAuthorizationEpoch", ge=1
    )
    current_authorization_epoch: int | None = Field(
        alias="currentAuthorizationEpoch", ge=1
    )
    checked_at: AwareDatetime = Field(alias="checkedAt")


def canonical_record_hash(record: ContentLicenseRecord) -> str:
    payload = record.model_dump(by_alias=True, mode="json")
    payload["recordCanonicalHash"] = ""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def canonical_grant_matrix_hash(payload: dict[str, object]) -> str:
    canonical = dict(payload)
    canonical["matrixHash"] = ""
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def external_learning_contract_catalog() -> dict[str, object]:
    return {
        "schemaVersion": "external-learning-contract-catalog@v1",
        "contractVersions": {
            "sourceArtifact": "source-artifact@v1",
            "recognitionRevision": "recognition-revision@v1",
            "contentMatch": "content-match@v1",
            "offlinePacket": "offline-packet@v1",
            "answerIngestion": "answer-ingestion@v1",
            "contentLicenseRecord": "content-license-record@v1",
            "contentLicenseEvaluate": "content-license-evaluate@v1",
            "contentLicenseDecision": "content-license-decision@v1",
            "contentLicenseSnapshotStatus": "content-license-snapshot-status@v1",
        },
        "sourceKinds": [item.value for item in SourceKind],
        "recognitionKinds": [item.value for item in RecognitionKind],
        "learningTracks": [item.value for item in LearningTrack],
        "contentComponents": list(CONTENT_COMPONENTS),
        "contentUses": list(CONTENT_USES),
        "deliveryChannels": list(DELIVERY_CHANNELS),
        "audiences": list(AUDIENCES),
        "contentTransforms": list(CONTENT_TRANSFORMS),
        "territories": list(TERRITORIES),
        "licenseStates": [item.value for item in LicenseState],
    }
