from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.contracts.external_learning.models import SourceArtifactVersion, SourceKind
from app.domains.external_ingestion.upload.models import (
    ObjectMetadata,
    ExpectedUploadPart,
    MultipartUploadHandle,
    ScanReceipt,
    UploadAuditEvent,
    UploadCommandReceipt,
    UploadReservation,
    UploadSession,
    UploadedPart,
    UploadPartAuthorization,
)


class UploadRepositoryPort(Protocol):
    def command_guard(
        self, *, owner_user_id: UUID, command_type: str, idempotency_key: str
    ) -> AbstractContextManager[None]: ...

    def session_guard(self, *, session_id: UUID) -> AbstractContextManager[None]: ...

    def content_guard(
        self, *, owner_user_id: UUID, content_sha256: str
    ) -> AbstractContextManager[None]: ...

    def command_receipt(
        self,
        *,
        owner_user_id: UUID,
        command_type: str,
        idempotency_key: str,
    ) -> UploadCommandReceipt | None: ...

    def insert_session_with_command(
        self,
        *,
        session: UploadSession,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
    ) -> None: ...

    def session(self, *, session_id: UUID) -> UploadSession | None: ...

    def save_session(
        self, *, expected_revision: int, session: UploadSession
    ) -> None: ...

    def append_audit(self, event: UploadAuditEvent) -> None: ...

    def insert_artifact(
        self, *, session_id: UUID, artifact: SourceArtifactVersion
    ) -> None: ...

    def artifact_for_session(
        self, *, session_id: UUID
    ) -> SourceArtifactVersion | None: ...

    def available_duplicate(
        self,
        *,
        owner_user_id: UUID,
        source_kind: SourceKind,
        content_sha256: str,
        byte_size: int,
        mime_type: str,
    ) -> SourceArtifactVersion | None: ...

    def stale_session_ids(self, *, before: datetime, limit: int) -> tuple[UUID, ...]: ...

    def recovery_session_ids(
        self, *, before: datetime, limit: int
    ) -> tuple[UUID, ...]: ...


class PrivateObjectStoragePort(Protocol):
    """All mutating operations must replay exactly by operation_key."""

    def authorize_single_put(
        self,
        *,
        object_key: str,
        mime_type: str,
        byte_size: int,
        content_sha256: str,
        expires_at: datetime,
    ) -> UploadPartAuthorization: ...

    def initiate_multipart(
        self,
        *,
        object_key: str,
        mime_type: str,
        content_sha256: str,
        operation_key: str,
    ) -> MultipartUploadHandle: ...

    def authorize_multipart_part(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        part: ExpectedUploadPart,
        expires_at: datetime,
    ) -> UploadPartAuthorization: ...

    def uploaded_parts(
        self, *, object_ref: str, provider_upload_id: str
    ) -> tuple[UploadedPart, ...]: ...

    def complete_multipart(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        parts: tuple[UploadedPart, ...],
        operation_key: str,
    ) -> None: ...

    def abort_multipart(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        operation_key: str,
    ) -> None: ...

    def head_private(self, *, object_ref: str) -> ObjectMetadata: ...

    def delete_private(self, *, object_ref: str, operation_key: str) -> None: ...

    def quarantine_private(
        self, *, object_ref: str, operation_key: str
    ) -> str: ...


class MalwareScannerPort(Protocol):
    """scan must replay the same attributable receipt by operation_key."""

    def scan(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        operation_key: str,
    ) -> ScanReceipt: ...


class PdfPageCounterPort(Protocol):
    """Read an already integrity-checked private PDF and return its real page count."""

    def count_pages(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        maximum_bytes: int,
    ) -> int: ...


class UploadReservationPort(Protocol):
    """Idempotent quota/billing reservation boundary.

    reserve must replay by reservation_key; commit and release must replay by
    reservation_id. The service therefore never double-reserves on retries.
    """

    def reserve(
        self, *, owner_user_id: UUID, reservation_key: str, byte_size: int
    ) -> UploadReservation: ...

    def commit(self, *, reservation_id: str, artifact_version_id: UUID) -> None: ...

    def release(self, *, reservation_id: str, reason: str) -> None: ...
