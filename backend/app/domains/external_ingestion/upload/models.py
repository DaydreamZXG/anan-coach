from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
import re
from typing import Literal
from uuid import UUID

from app.contracts.external_learning.models import SourceArtifactVersion, SourceKind


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UploadContractError(ValueError):
    pass


class UploadState(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    ASSEMBLING = "assembling"
    UPLOADED = "uploaded"
    SCANNING = "scanning"
    SCAN_FAILED = "scan_failed"
    FINALIZING = "finalizing"
    QUARANTINING = "quarantining"
    REJECTING = "rejecting"
    AVAILABLE = "available"
    DEDUPLICATED = "deduplicated"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"
    FAILED = "failed"


class ScanVerdict(StrEnum):
    CLEAN = "clean"
    MALICIOUS = "malicious"
    UNDETERMINED = "undetermined"


class UploadMode(StrEnum):
    SINGLE_PUT = "single_put"
    MULTIPART = "multipart"


@dataclass(frozen=True)
class ScannerRuntimeApproval:
    provider_route: str
    scanner_policy_version: str
    data_processing_agreement_version: str

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.provider_route,
                self.scanner_policy_version,
                self.data_processing_agreement_version,
            )
        ):
            raise UploadContractError("scanner runtime approval is incomplete")


@dataclass(frozen=True)
class FileTypeRule:
    mime_type: str
    extensions: frozenset[str]
    maximum_bytes: int

    def __post_init__(self) -> None:
        if not self.mime_type or not self.extensions or self.maximum_bytes <= 0:
            raise UploadContractError("file-type rules must be complete")
        if any(not item.startswith(".") for item in self.extensions):
            raise UploadContractError("file extensions must include a leading dot")


@dataclass(frozen=True)
class UploadPolicy:
    policy_version: str
    session_ttl_seconds: int
    rules_by_source: dict[SourceKind, tuple[FileTypeRule, ...]]
    maximum_pdf_pages: int = 300
    object_namespace: str = "private/external-learning"
    multipart_threshold_bytes: int = 8 * 1024 * 1024
    multipart_part_size: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise UploadContractError("upload policy version is required")
        if not 60 <= self.session_ttl_seconds <= 86_400:
            raise UploadContractError("upload session TTL must be between 60s and 24h")
        if self.maximum_pdf_pages <= 0:
            raise UploadContractError("maximum PDF pages must be positive")
        if self.object_namespace.startswith("/") or ".." in self.object_namespace:
            raise UploadContractError("object namespace must be a safe relative prefix")
        missing = set(SourceKind) - set(self.rules_by_source)
        if missing:
            raise UploadContractError(f"missing file rules for: {sorted(missing)}")
        if self.multipart_part_size < 5 * 1024 * 1024:
            raise UploadContractError("multipart part size must be at least 5 MiB")
        if self.multipart_threshold_bytes < self.multipart_part_size:
            raise UploadContractError("multipart threshold cannot be below the part size")

    def validate(self, request: StartUploadRequest) -> str:
        extension = normalized_extension(request.original_filename)
        matches = [
            rule
            for rule in self.rules_by_source[request.source_kind]
            if rule.mime_type == request.mime_type and extension in rule.extensions
        ]
        if not matches:
            raise UploadContractError(
                "declared MIME type, extension and source kind are not an allowed tuple"
            )
        if request.byte_size > matches[0].maximum_bytes:
            raise UploadContractError("file exceeds the configured source limit")
        if request.mime_type == "application/pdf":
            if request.page_count is not None and not (
                1 <= request.page_count <= self.maximum_pdf_pages
            ):
                raise UploadContractError("PDF page count exceeds the configured limit")
        elif request.page_count is not None:
            raise UploadContractError("page count is only accepted for PDF objects")
        return extension

    def plan(self, request: StartUploadRequest) -> UploadPlan:
        extension = self.validate(request)
        multipart = (
            request.mime_type == "application/pdf"
            or request.byte_size > self.multipart_threshold_bytes
        )
        if not multipart:
            if request.part_sha256s:
                raise UploadContractError("single PUT uploads must not carry part hashes")
            return UploadPlan(
                extension=extension,
                mode=UploadMode.SINGLE_PUT,
                part_size=None,
                expected_parts=(),
            )
        part_count = (
            request.byte_size + self.multipart_part_size - 1
        ) // self.multipart_part_size
        if len(request.part_sha256s) != part_count:
            raise UploadContractError(
                "multipart uploads require one SHA-256 value for every part"
            )
        expected_parts = tuple(
            ExpectedUploadPart(
                part_number=index + 1,
                byte_size=min(
                    self.multipart_part_size,
                    request.byte_size - index * self.multipart_part_size,
                ),
                content_sha256=part_hash,
            )
            for index, part_hash in enumerate(request.part_sha256s)
        )
        return UploadPlan(
            extension=extension,
            mode=UploadMode.MULTIPART,
            part_size=self.multipart_part_size,
            expected_parts=expected_parts,
        )


@dataclass(frozen=True)
class StartUploadRequest:
    owner_user_id: UUID
    idempotency_key: str
    source_kind: SourceKind
    original_filename: str
    mime_type: str
    byte_size: int
    content_sha256: str
    page_count: int | None = None
    part_sha256s: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 8 <= len(self.idempotency_key) <= 260:
            raise UploadContractError("idempotency key must contain 8-260 characters")
        if not self.original_filename.strip() or len(self.original_filename) > 255:
            raise UploadContractError("original filename must contain 1-255 characters")
        if (
            PurePath(self.original_filename).name != self.original_filename
            or "/" in self.original_filename
            or "\\" in self.original_filename
            or any(ord(character) < 32 for character in self.original_filename)
        ):
            raise UploadContractError("original filename must not contain a path")
        if (
            len(self.mime_type) > 200
            or re.fullmatch(
                r"[a-z0-9][a-z0-9.+-]{0,126}/[a-z0-9][a-z0-9.+-]{0,126}",
                self.mime_type,
            )
            is None
        ):
            raise UploadContractError("MIME type must contain 1-200 characters")
        if self.byte_size <= 0:
            raise UploadContractError("byte size must be positive")
        if _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise UploadContractError("content SHA-256 must be lowercase hexadecimal")
        if any(_SHA256_RE.fullmatch(value) is None for value in self.part_sha256s):
            raise UploadContractError("part SHA-256 values must be lowercase hexadecimal")


@dataclass(frozen=True)
class ExpectedUploadPart:
    part_number: int
    byte_size: int
    content_sha256: str

    def __post_init__(self) -> None:
        if self.part_number <= 0 or self.byte_size <= 0:
            raise UploadContractError("multipart part identity and size must be positive")
        if _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise UploadContractError("multipart part hash must be SHA-256")


@dataclass(frozen=True)
class UploadPlan:
    extension: str
    mode: UploadMode
    part_size: int | None
    expected_parts: tuple[ExpectedUploadPart, ...]


@dataclass(frozen=True)
class UploadedPart:
    part_number: int
    byte_size: int
    etag: str

    def __post_init__(self) -> None:
        if self.part_number <= 0 or self.byte_size <= 0 or not self.etag.strip():
            raise UploadContractError("uploaded part manifest is incomplete")


@dataclass(frozen=True)
class UploadPartAuthorization:
    object_ref: str
    part_number: int
    byte_size: int
    content_sha256: str
    url: str
    required_headers: dict[str, str]
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.part_number <= 0 or self.byte_size <= 0:
            raise UploadContractError("authorized part identity and size must be positive")
        if _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise UploadContractError("authorized part hash must be SHA-256")
        if (
            not self.object_ref
            or not self.url.startswith("https://")
            or self.expires_at.utcoffset() is None
        ):
            raise UploadContractError("part authorization must be HTTPS and timezone-aware")


@dataclass(frozen=True)
class MultipartUploadHandle:
    object_ref: str
    provider_upload_id: str

    def __post_init__(self) -> None:
        if not self.object_ref or not self.provider_upload_id.strip():
            raise UploadContractError("multipart provider handle is incomplete")


@dataclass(frozen=True)
class UploadAuthorization:
    object_ref: str
    mode: UploadMode
    expires_at: datetime
    parts: tuple[UploadPartAuthorization, ...]
    provider_upload_id: str | None = None
    part_size: int | None = None

    def __post_init__(self) -> None:
        if not self.object_ref or not self.parts:
            raise UploadContractError("private upload authorization is incomplete")
        if self.expires_at.utcoffset() is None:
            raise UploadContractError("upload authorization expiry must be timezone-aware")
        if any(item.expires_at > self.expires_at for item in self.parts):
            raise UploadContractError("part authorization outlives its upload authorization")
        if self.mode == UploadMode.SINGLE_PUT:
            if (
                len(self.parts) != 1
                or self.parts[0].part_number != 1
                or self.provider_upload_id is not None
                or self.part_size is not None
            ):
                raise UploadContractError("single PUT authorization shape is invalid")
        elif (
            self.provider_upload_id is None
            or self.part_size is None
            or tuple(item.part_number for item in self.parts)
            != tuple(range(1, len(self.parts) + 1))
        ):
            raise UploadContractError("multipart authorization shape is invalid")


@dataclass(frozen=True)
class ObjectMetadata:
    object_ref: str
    byte_size: int
    content_sha256: str
    mime_type: str
    access: Literal["private", "public"]
    server_side_encrypted: bool

    def __post_init__(self) -> None:
        if self.byte_size <= 0 or _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise UploadContractError("object metadata lacks verifiable integrity")


@dataclass(frozen=True)
class ScanReceipt:
    scan_id: str
    verdict: ScanVerdict
    detected_mime_type: str
    content_sha256: str
    scanner_policy_version: str
    provider_route: str
    data_processing_agreement_version: str
    definition_version: str
    scanned_at: datetime
    detected_page_count: int | None = None

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.scan_id,
                self.detected_mime_type,
                self.scanner_policy_version,
                self.provider_route,
                self.data_processing_agreement_version,
                self.definition_version,
            )
        ):
            raise UploadContractError("scanner receipts must be attributable and versioned")
        if self.scanned_at.utcoffset() is None:
            raise UploadContractError("scanner receipt time must be timezone-aware")
        if _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise UploadContractError("scanner receipt content hash must be SHA-256")
        if self.detected_mime_type == "application/pdf":
            if self.detected_page_count is None or self.detected_page_count <= 0:
                raise UploadContractError("PDF scanner receipts require a page count")
        elif self.detected_page_count is not None:
            raise UploadContractError("only PDF scanner receipts carry a page count")


@dataclass(frozen=True)
class UploadReservation:
    reservation_id: str
    reservation_key: str


@dataclass(frozen=True)
class UploadSession:
    session_id: UUID
    artifact_id: UUID
    artifact_version_id: UUID
    owner_user_id: UUID
    revision: int
    state: UploadState
    source_kind: SourceKind
    original_filename: str
    extension: str
    mime_type: str
    byte_size: int
    content_sha256: str
    page_count: int | None
    object_ref: str
    reservation_id: str
    upload_policy_version: str
    upload_mode: UploadMode
    provider_upload_id: str | None
    part_size: int | None
    expected_parts: tuple[ExpectedUploadPart, ...]
    uploaded_parts: tuple[UploadedPart, ...]
    created_at: datetime
    expires_at: datetime
    updated_at: datetime
    resolved_artifact_version_id: UUID | None = None
    scan_receipt: ScanReceipt | None = None
    failure_code: str | None = None

    @property
    def recognition_eligible(self) -> bool:
        return self.state in {UploadState.AVAILABLE, UploadState.DEDUPLICATED}

    @property
    def terminal(self) -> bool:
        return self.state in {
            UploadState.AVAILABLE,
            UploadState.DEDUPLICATED,
            UploadState.QUARANTINED,
            UploadState.EXPIRED,
            UploadState.FAILED,
        }

    def transition(self, state: UploadState, *, at: datetime, **changes: object) -> UploadSession:
        allowed = _ALLOWED_TRANSITIONS[self.state]
        if state not in allowed:
            raise UploadContractError(
                f"illegal upload transition {self.state.value}->{state.value}"
            )
        return replace(
            self,
            revision=self.revision + 1,
            state=state,
            updated_at=at,
            **changes,
        )


@dataclass(frozen=True)
class StartUploadResult:
    session: UploadSession
    authorization: UploadAuthorization
    replayed: bool


@dataclass(frozen=True)
class CompleteUploadResult:
    session: UploadSession
    artifact: SourceArtifactVersion | None
    replayed: bool


@dataclass(frozen=True)
class UploadCommandReceipt:
    request_hash: str
    session_id: UUID


@dataclass(frozen=True)
class UploadAuditEvent:
    event_id: UUID
    deduplication_key: str
    session_id: UUID
    owner_user_id: UUID
    event_type: str
    from_state: UploadState | None
    to_state: UploadState
    details: dict[str, object]
    created_at: datetime


_ALLOWED_TRANSITIONS: dict[UploadState, frozenset[UploadState]] = {
    UploadState.AWAITING_UPLOAD: frozenset(
        {
            UploadState.ASSEMBLING,
            UploadState.SCANNING,
            UploadState.REJECTING,
            UploadState.EXPIRED,
        }
    ),
    UploadState.ASSEMBLING: frozenset(
        {UploadState.UPLOADED, UploadState.REJECTING, UploadState.EXPIRED}
    ),
    UploadState.UPLOADED: frozenset(
        {UploadState.SCANNING, UploadState.REJECTING, UploadState.EXPIRED}
    ),
    UploadState.SCANNING: frozenset(
        {UploadState.SCAN_FAILED, UploadState.FINALIZING, UploadState.QUARANTINING}
    ),
    UploadState.SCAN_FAILED: frozenset(
        {
            UploadState.SCANNING,
            UploadState.REJECTING,
            UploadState.EXPIRED,
        }
    ),
    UploadState.FINALIZING: frozenset(
        {UploadState.AVAILABLE, UploadState.DEDUPLICATED}
    ),
    UploadState.QUARANTINING: frozenset({UploadState.QUARANTINED}),
    UploadState.REJECTING: frozenset({UploadState.FAILED}),
    UploadState.AVAILABLE: frozenset(),
    UploadState.DEDUPLICATED: frozenset(),
    UploadState.QUARANTINED: frozenset(),
    UploadState.EXPIRED: frozenset(),
    UploadState.FAILED: frozenset(),
}


def normalized_extension(filename: str) -> str:
    suffix = PurePath(filename).suffix.casefold()
    if not suffix or len(suffix) > 12 or re.fullmatch(r"\.[a-z0-9]+", suffix) is None:
        raise UploadContractError("a safe, explicit file extension is required")
    return suffix


def default_upload_policy() -> UploadPolicy:
    image_rules = (
        FileTypeRule("image/jpeg", frozenset({".jpg", ".jpeg"}), 25 * 1024 * 1024),
        FileTypeRule("image/png", frozenset({".png"}), 25 * 1024 * 1024),
        FileTypeRule("image/webp", frozenset({".webp"}), 25 * 1024 * 1024),
        FileTypeRule("image/heic", frozenset({".heic"}), 25 * 1024 * 1024),
    )
    pdf_rule = FileTypeRule(
        "application/pdf", frozenset({".pdf"}), 100 * 1024 * 1024
    )
    return UploadPolicy(
        policy_version="external-upload-policy@v1",
        session_ttl_seconds=15 * 60,
        rules_by_source={
            SourceKind.CAMERA_IMAGE: image_rules,
            SourceKind.IMAGE_IMPORT: image_rules,
            SourceKind.PDF_UPLOAD: (pdf_rule,),
            SourceKind.ANSWER_SHEET: (*image_rules, pdf_rule),
            SourceKind.NOTES_APP_EXPORT: (pdf_rule,),
        },
    )
