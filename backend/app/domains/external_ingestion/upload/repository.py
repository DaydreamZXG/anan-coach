from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from typing import Iterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    SourceArtifactVersion,
    SourceKind,
)
from app.domains.external_ingestion.upload.models import (
    ScanReceipt,
    ScanVerdict,
    ExpectedUploadPart,
    UploadAuditEvent,
    UploadCommandReceipt,
    UploadSession,
    UploadState,
    UploadMode,
    UploadedPart,
)


class UploadRepositoryConflict(RuntimeError):
    pass


class PostgresUploadRepository:
    """PostgreSQL adapter for durable upload sessions and append-only audit facts.

    It deliberately never commits. The API/worker composition root owns the SQL
    transaction that atomically persists state, audit and source-artifact facts.
    """

    def __init__(self, *, session: Session) -> None:
        self._session = session

    @contextmanager
    def command_guard(
        self, *, owner_user_id: UUID, command_type: str, idempotency_key: str
    ) -> Iterator[None]:
        self._lock(f"upload-command:{owner_user_id}:{command_type}:{idempotency_key}")
        yield

    @contextmanager
    def session_guard(self, *, session_id: UUID) -> Iterator[None]:
        self._lock(f"upload-session:{session_id}")
        yield

    @contextmanager
    def content_guard(
        self, *, owner_user_id: UUID, content_sha256: str
    ) -> Iterator[None]:
        self._lock(f"upload-content:{owner_user_id}:{content_sha256}")
        yield

    def command_receipt(
        self,
        *,
        owner_user_id: UUID,
        command_type: str,
        idempotency_key: str,
    ) -> UploadCommandReceipt | None:
        row = self._session.execute(
            text(
                """
                select request_hash, session_id
                from learning.upload_commands
                where owner_user_id = :owner_user_id
                  and command_type = :command_type
                  and idempotency_key = :idempotency_key
                """
            ),
            {
                "owner_user_id": str(owner_user_id),
                "command_type": command_type,
                "idempotency_key": idempotency_key,
            },
        ).mappings().one_or_none()
        if row is None:
            return None
        return UploadCommandReceipt(
            request_hash=str(row["request_hash"]), session_id=row["session_id"]
        )

    def insert_session_with_command(
        self,
        *,
        session: UploadSession,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into learning.upload_sessions (
                  session_id, artifact_id, artifact_version_id, owner_user_id,
                  revision, state, source_kind, original_filename, extension,
                  mime_type, byte_size, content_sha256, page_count, object_ref,
                  reservation_id, upload_policy_version, created_at, expires_at,
                  updated_at, upload_mode, provider_upload_id, part_size,
                  part_count, expected_parts, uploaded_parts,
                  resolved_artifact_version_id, scan_receipt, failure_code
                ) values (
                  :session_id, :artifact_id, :artifact_version_id, :owner_user_id,
                  :revision, :state, :source_kind, :original_filename, :extension,
                  :mime_type, :byte_size, :content_sha256, :page_count, :object_ref,
                  :reservation_id, :upload_policy_version, :created_at, :expires_at,
                  :updated_at, :upload_mode, :provider_upload_id, :part_size,
                  :part_count, cast(:expected_parts as jsonb),
                  cast(:uploaded_parts as jsonb), null, null, null
                )
                """
            ),
            _session_parameters(session),
        )
        self._session.execute(
            text(
                """
                insert into learning.upload_commands (
                  owner_user_id, command_type, idempotency_key, request_hash,
                  session_id, created_at
                ) values (
                  :owner_user_id, :command_type, :idempotency_key, :request_hash,
                  :session_id, :created_at
                )
                """
            ),
            {
                "owner_user_id": str(session.owner_user_id),
                "command_type": command_type,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "session_id": str(session.session_id),
                "created_at": session.created_at,
            },
        )

    def session(self, *, session_id: UUID) -> UploadSession | None:
        row = self._session.execute(
            text("select * from learning.upload_sessions where session_id = :id"),
            {"id": str(session_id)},
        ).mappings().one_or_none()
        return _stored_session(row) if row is not None else None

    def save_session(
        self, *, expected_revision: int, session: UploadSession
    ) -> None:
        parameters = _session_parameters(session)
        parameters["expected_revision"] = expected_revision
        updated = self._session.execute(
            text(
                """
                update learning.upload_sessions
                set revision = :revision,
                    state = :state,
                    resolved_artifact_version_id = :resolved_artifact_version_id,
                    scan_receipt = cast(:scan_receipt as jsonb),
                    uploaded_parts = cast(:uploaded_parts as jsonb),
                    failure_code = :failure_code,
                    updated_at = :updated_at
                where session_id = :session_id
                  and revision = :expected_revision
                returning revision
                """
            ),
            parameters,
        ).scalar_one_or_none()
        if updated is None:
            raise UploadRepositoryConflict("upload session compare-and-swap failed")

    def append_audit(self, event: UploadAuditEvent) -> None:
        payload = json.dumps(event.details, ensure_ascii=False, sort_keys=True)
        inserted = self._session.execute(
            text(
                """
                insert into learning.upload_audit_events (
                  event_id, deduplication_key, session_id, owner_user_id,
                  event_type, from_state, to_state, details, created_at
                ) values (
                  :event_id, :deduplication_key, :session_id, :owner_user_id,
                  :event_type, :from_state, :to_state, cast(:details as jsonb),
                  :created_at
                )
                on conflict (deduplication_key) do nothing
                returning event_id
                """
            ),
            {
                "event_id": str(event.event_id),
                "deduplication_key": event.deduplication_key,
                "session_id": str(event.session_id),
                "owner_user_id": str(event.owner_user_id),
                "event_type": event.event_type,
                "from_state": event.from_state.value if event.from_state else None,
                "to_state": event.to_state.value,
                "details": payload,
                "created_at": event.created_at,
            },
        ).scalar_one_or_none()
        if inserted is not None:
            return
        current = self._session.execute(
            text(
                """
                select session_id, owner_user_id, event_type, from_state,
                       to_state, details
                from learning.upload_audit_events
                where deduplication_key = :key
                """
            ),
            {"key": event.deduplication_key},
        ).mappings().one()
        expected = (
            event.session_id,
            event.owner_user_id,
            event.event_type,
            event.from_state.value if event.from_state else None,
            event.to_state.value,
            event.details,
        )
        actual = (
            current["session_id"],
            current["owner_user_id"],
            current["event_type"],
            current["from_state"],
            current["to_state"],
            dict(current["details"]),
        )
        if actual != expected:
            raise UploadRepositoryConflict(
                "audit deduplication key refers to a different immutable event"
            )

    def insert_artifact(
        self, *, session_id: UUID, artifact: SourceArtifactVersion
    ) -> None:
        payload = artifact.model_dump(by_alias=True, mode="json")
        self._session.execute(
            text(
                """
                insert into learning.source_artifact_versions (
                  artifact_version_id, artifact_id, owner_user_id, version_no,
                  source_kind, state, object_ref, mime_type, content_sha256,
                  byte_size, page_count, supersedes_artifact_version_id,
                  contract_payload, created_at
                ) values (
                  :artifact_version_id, :artifact_id, :owner_user_id, :version_no,
                  :source_kind, :state, :object_ref, :mime_type, :content_sha256,
                  :byte_size, :page_count, :supersedes_artifact_version_id,
                  cast(:contract_payload as jsonb), :created_at
                )
                on conflict (artifact_version_id) do nothing
                """
            ),
            {
                "artifact_version_id": str(artifact.artifact_version_id),
                "artifact_id": str(artifact.artifact_id),
                "owner_user_id": str(artifact.owner_user_id),
                "version_no": artifact.version_no,
                "source_kind": artifact.source_kind.value,
                "state": artifact.state.value,
                "object_ref": artifact.object_ref,
                "mime_type": artifact.mime_type,
                "content_sha256": artifact.content_sha256,
                "byte_size": artifact.byte_size,
                "page_count": artifact.page_count,
                "supersedes_artifact_version_id": (
                    str(artifact.supersedes_artifact_version_id)
                    if artifact.supersedes_artifact_version_id
                    else None
                ),
                "contract_payload": json.dumps(
                    payload, ensure_ascii=False, sort_keys=True
                ),
                "created_at": artifact.created_at,
            },
        )
        stored = self._session.execute(
            text(
                """
                select contract_payload
                from learning.source_artifact_versions
                where artifact_version_id = :id
                """
            ),
            {"id": str(artifact.artifact_version_id)},
        ).scalar_one()
        if SourceArtifactVersion.model_validate(stored) != artifact:
            raise UploadRepositoryConflict(
                "artifact version identity refers to different immutable content"
            )

    def artifact_for_session(
        self, *, session_id: UUID
    ) -> SourceArtifactVersion | None:
        payload = self._session.execute(
            text(
                """
                select artifact.contract_payload
                from learning.upload_sessions upload
                join learning.source_artifact_versions artifact
                  on artifact.artifact_version_id = upload.resolved_artifact_version_id
                where upload.session_id = :session_id
                """
            ),
            {"session_id": str(session_id)},
        ).scalar_one_or_none()
        return SourceArtifactVersion.model_validate(payload) if payload else None

    def available_duplicate(
        self,
        *,
        owner_user_id: UUID,
        source_kind: SourceKind,
        content_sha256: str,
        byte_size: int,
        mime_type: str,
    ) -> SourceArtifactVersion | None:
        payload = self._session.execute(
            text(
                """
                select contract_payload
                from learning.source_artifact_versions
                where owner_user_id = :owner_user_id
                  and source_kind = :source_kind
                  and content_sha256 = :content_sha256
                  and byte_size = :byte_size
                  and mime_type = :mime_type
                  and state = 'available'
                order by created_at, artifact_version_id
                limit 1
                """
            ),
            {
                "owner_user_id": str(owner_user_id),
                "source_kind": source_kind.value,
                "content_sha256": content_sha256,
                "byte_size": byte_size,
                "mime_type": mime_type,
            },
        ).scalar_one_or_none()
        return SourceArtifactVersion.model_validate(payload) if payload else None

    def stale_session_ids(self, *, before: datetime, limit: int) -> tuple[UUID, ...]:
        rows = self._session.execute(
            text(
                """
                select session_id
                from learning.upload_sessions
                where state in ('awaiting_upload', 'scan_failed')
                  and expires_at <= :before
                order by expires_at, session_id
                limit :limit
                """
            ),
            {"before": before, "limit": limit},
        ).scalars()
        return tuple(rows)

    def recovery_session_ids(
        self, *, before: datetime, limit: int
    ) -> tuple[UUID, ...]:
        rows = self._session.execute(
            text(
                """
                select session_id
                from learning.upload_sessions
                where state in (
                  'assembling', 'uploaded', 'scanning', 'rejecting',
                  'quarantining', 'finalizing'
                )
                  and updated_at < :before
                order by updated_at, session_id
                limit :limit
                """
            ),
            {"before": before, "limit": limit},
        ).scalars()
        return tuple(rows)

    def _lock(self, key: str) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )


def _session_parameters(session: UploadSession) -> dict[str, object]:
    return {
        "session_id": str(session.session_id),
        "artifact_id": str(session.artifact_id),
        "artifact_version_id": str(session.artifact_version_id),
        "owner_user_id": str(session.owner_user_id),
        "revision": session.revision,
        "state": session.state.value,
        "source_kind": session.source_kind.value,
        "original_filename": session.original_filename,
        "extension": session.extension,
        "mime_type": session.mime_type,
        "byte_size": session.byte_size,
        "content_sha256": session.content_sha256,
        "page_count": session.page_count,
        "object_ref": session.object_ref,
        "reservation_id": session.reservation_id,
        "upload_policy_version": session.upload_policy_version,
        "upload_mode": session.upload_mode.value,
        "provider_upload_id": session.provider_upload_id,
        "part_size": session.part_size,
        "part_count": len(session.expected_parts),
        "expected_parts": json.dumps(
            [
                {
                    "partNumber": item.part_number,
                    "byteSize": item.byte_size,
                    "contentSha256": item.content_sha256,
                }
                for item in session.expected_parts
            ],
            sort_keys=True,
        ),
        "uploaded_parts": json.dumps(
            [
                {
                    "partNumber": item.part_number,
                    "byteSize": item.byte_size,
                    "etag": item.etag,
                }
                for item in session.uploaded_parts
            ],
            sort_keys=True,
        ),
        "created_at": session.created_at,
        "expires_at": session.expires_at,
        "updated_at": session.updated_at,
        "resolved_artifact_version_id": (
            str(session.resolved_artifact_version_id)
            if session.resolved_artifact_version_id
            else None
        ),
        "scan_receipt": (
            json.dumps(
                {
                    "scanId": session.scan_receipt.scan_id,
                    "verdict": session.scan_receipt.verdict.value,
                    "detectedMimeType": session.scan_receipt.detected_mime_type,
                    "contentSha256": session.scan_receipt.content_sha256,
                    "scannerPolicyVersion": session.scan_receipt.scanner_policy_version,
                    "providerRoute": session.scan_receipt.provider_route,
                    "dataProcessingAgreementVersion": (
                        session.scan_receipt.data_processing_agreement_version
                    ),
                    "definitionVersion": session.scan_receipt.definition_version,
                    "scannedAt": session.scan_receipt.scanned_at.isoformat(),
                    "detectedPageCount": session.scan_receipt.detected_page_count,
                },
                sort_keys=True,
            )
            if session.scan_receipt
            else None
        ),
        "failure_code": session.failure_code,
    }


def _stored_session(row: object) -> UploadSession:
    payload = row  # RowMapping at runtime; kept local to avoid leaking SQLAlchemy types.
    scan_payload = payload["scan_receipt"]  # type: ignore[index]
    receipt = None
    if scan_payload is not None:
        receipt = ScanReceipt(
            scan_id=str(scan_payload["scanId"]),
            verdict=ScanVerdict(str(scan_payload["verdict"])),
            detected_mime_type=str(scan_payload["detectedMimeType"]),
            content_sha256=str(scan_payload["contentSha256"]),
            scanner_policy_version=str(scan_payload["scannerPolicyVersion"]),
            provider_route=str(scan_payload["providerRoute"]),
            data_processing_agreement_version=str(
                scan_payload["dataProcessingAgreementVersion"]
            ),
            definition_version=str(scan_payload["definitionVersion"]),
            scanned_at=datetime.fromisoformat(str(scan_payload["scannedAt"])),
            detected_page_count=(
                int(scan_payload["detectedPageCount"])
                if scan_payload.get("detectedPageCount") is not None
                else None
            ),
        )
    expected_parts = tuple(
        ExpectedUploadPart(
            part_number=int(item["partNumber"]),
            byte_size=int(item["byteSize"]),
            content_sha256=str(item["contentSha256"]),
        )
        for item in payload["expected_parts"]  # type: ignore[index]
    )
    uploaded_parts = tuple(
        UploadedPart(
            part_number=int(item["partNumber"]),
            byte_size=int(item["byteSize"]),
            etag=str(item["etag"]),
        )
        for item in payload["uploaded_parts"]  # type: ignore[index]
    )
    return UploadSession(
        session_id=payload["session_id"],  # type: ignore[index]
        artifact_id=payload["artifact_id"],  # type: ignore[index]
        artifact_version_id=payload["artifact_version_id"],  # type: ignore[index]
        owner_user_id=payload["owner_user_id"],  # type: ignore[index]
        revision=int(payload["revision"]),  # type: ignore[index]
        state=UploadState(str(payload["state"])),  # type: ignore[index]
        source_kind=SourceKind(str(payload["source_kind"])),  # type: ignore[index]
        original_filename=str(payload["original_filename"]),  # type: ignore[index]
        extension=str(payload["extension"]),  # type: ignore[index]
        mime_type=str(payload["mime_type"]),  # type: ignore[index]
        byte_size=int(payload["byte_size"]),  # type: ignore[index]
        content_sha256=str(payload["content_sha256"]),  # type: ignore[index]
        page_count=(
            int(payload["page_count"])  # type: ignore[index]
            if payload["page_count"] is not None  # type: ignore[index]
            else None
        ),
        object_ref=str(payload["object_ref"]),  # type: ignore[index]
        reservation_id=str(payload["reservation_id"]),  # type: ignore[index]
        upload_policy_version=str(payload["upload_policy_version"]),  # type: ignore[index]
        upload_mode=UploadMode(str(payload["upload_mode"])),  # type: ignore[index]
        provider_upload_id=(
            str(payload["provider_upload_id"])  # type: ignore[index]
            if payload["provider_upload_id"] is not None  # type: ignore[index]
            else None
        ),
        part_size=(
            int(payload["part_size"])  # type: ignore[index]
            if payload["part_size"] is not None  # type: ignore[index]
            else None
        ),
        expected_parts=expected_parts,
        uploaded_parts=uploaded_parts,
        created_at=payload["created_at"],  # type: ignore[index]
        expires_at=payload["expires_at"],  # type: ignore[index]
        updated_at=payload["updated_at"],  # type: ignore[index]
        resolved_artifact_version_id=payload["resolved_artifact_version_id"],  # type: ignore[index]
        scan_receipt=receipt,
        failure_code=(
            str(payload["failure_code"])  # type: ignore[index]
            if payload["failure_code"] is not None  # type: ignore[index]
            else None
        ),
    )
