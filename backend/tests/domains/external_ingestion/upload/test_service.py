from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, RLock
from typing import Iterator
from uuid import UUID, uuid4

import pytest

from app.contracts.external_learning.models import SourceArtifactVersion, SourceKind
from app.domains.external_ingestion.upload.models import (
    ObjectMetadata,
    MultipartUploadHandle,
    ScanReceipt,
    ScanVerdict,
    ScannerRuntimeApproval,
    StartUploadRequest,
    UploadAuditEvent,
    UploadMode,
    UploadPartAuthorization,
    UploadCommandReceipt,
    UploadContractError,
    UploadReservation,
    UploadSession,
    UploadState,
    UploadedPart,
)
from app.domains.external_ingestion.upload.service import (
    UploadIdempotencyConflict,
    UploadIntegrityError,
    UploadOwnershipError,
    UploadRecoveryRequired,
    UploadService,
    UploadUnavailable,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeRepository:
    def __init__(self) -> None:
        self.sessions: dict[UUID, UploadSession] = {}
        self.commands: dict[tuple[UUID, str, str], UploadCommandReceipt] = {}
        self.artifacts: dict[UUID, SourceArtifactVersion] = {}
        self.audits: dict[str, UploadAuditEvent] = {}
        self._locks: defaultdict[str, RLock] = defaultdict(RLock)

    @contextmanager
    def command_guard(
        self, *, owner_user_id: UUID, command_type: str, idempotency_key: str
    ) -> Iterator[None]:
        with self._locks[f"command:{owner_user_id}:{command_type}:{idempotency_key}"]:
            yield

    @contextmanager
    def session_guard(self, *, session_id: UUID) -> Iterator[None]:
        with self._locks[f"session:{session_id}"]:
            yield

    @contextmanager
    def content_guard(
        self, *, owner_user_id: UUID, content_sha256: str
    ) -> Iterator[None]:
        with self._locks[f"content:{owner_user_id}:{content_sha256}"]:
            yield

    def command_receipt(
        self, *, owner_user_id: UUID, command_type: str, idempotency_key: str
    ) -> UploadCommandReceipt | None:
        return self.commands.get((owner_user_id, command_type, idempotency_key))

    def insert_session_with_command(
        self,
        *,
        session: UploadSession,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        self.sessions[session.session_id] = session
        self.commands[(session.owner_user_id, command_type, idempotency_key)] = (
            UploadCommandReceipt(
                request_hash=request_hash, session_id=session.session_id
            )
        )

    def session(self, *, session_id: UUID) -> UploadSession | None:
        return self.sessions.get(session_id)

    def save_session(
        self, *, expected_revision: int, session: UploadSession
    ) -> None:
        current = self.sessions[session.session_id]
        if current.revision != expected_revision:
            raise RuntimeError("CAS failed")
        self.sessions[session.session_id] = session

    def append_audit(self, event: UploadAuditEvent) -> None:
        current = self.audits.get(event.deduplication_key)
        if current is not None and current != event:
            raise RuntimeError("audit identity conflict")
        self.audits[event.deduplication_key] = event

    def insert_artifact(
        self, *, session_id: UUID, artifact: SourceArtifactVersion
    ) -> None:
        current = self.artifacts.get(artifact.artifact_version_id)
        if current is not None and current != artifact:
            raise RuntimeError("artifact identity conflict")
        self.artifacts[artifact.artifact_version_id] = artifact

    def artifact_for_session(
        self, *, session_id: UUID
    ) -> SourceArtifactVersion | None:
        session = self.sessions[session_id]
        return (
            self.artifacts.get(session.resolved_artifact_version_id)
            if session.resolved_artifact_version_id
            else None
        )

    def available_duplicate(
        self,
        *,
        owner_user_id: UUID,
        source_kind: SourceKind,
        content_sha256: str,
        byte_size: int,
        mime_type: str,
    ) -> SourceArtifactVersion | None:
        return next(
            (
                item
                for item in self.artifacts.values()
                if item.owner_user_id == owner_user_id
                and item.source_kind == source_kind
                and item.content_sha256 == content_sha256
                and item.byte_size == byte_size
                and item.mime_type == mime_type
                and item.state.value == "available"
            ),
            None,
        )

    def stale_session_ids(self, *, before: datetime, limit: int) -> tuple[UUID, ...]:
        matches = sorted(
            (
                item
                for item in self.sessions.values()
                if item.expires_at <= before
                and item.state in {UploadState.AWAITING_UPLOAD, UploadState.SCAN_FAILED}
            ),
            key=lambda item: (item.expires_at, item.session_id),
        )
        return tuple(item.session_id for item in matches[:limit])

    def recovery_session_ids(
        self, *, before: datetime, limit: int
    ) -> tuple[UUID, ...]:
        matches = sorted(
            (
                item
                for item in self.sessions.values()
                if item.updated_at < before
                and item.state
                in {
                    UploadState.SCANNING,
                    UploadState.ASSEMBLING,
                    UploadState.UPLOADED,
                    UploadState.REJECTING,
                    UploadState.QUARANTINING,
                    UploadState.FINALIZING,
                }
            ),
            key=lambda item: (item.updated_at, item.session_id),
        )
        return tuple(item.session_id for item in matches[:limit])


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, ObjectMetadata] = {}
        self.authorize_calls = 0
        self.deleted: list[str] = []
        self.quarantined: list[str] = []
        self.fail_delete = False
        self.multipart: dict[str, dict[str, object]] = {}
        self.aborted: list[str] = []
        self.completed: list[str] = []
        self.initiate_calls = 0
        self.fail_complete = False

    def authorize_single_put(
        self,
        *,
        object_key: str,
        mime_type: str,
        byte_size: int,
        content_sha256: str,
        expires_at: datetime,
    ) -> UploadPartAuthorization:
        self.authorize_calls += 1
        return UploadPartAuthorization(
            object_ref=f"tos://private-bucket/{object_key}",
            part_number=1,
            byte_size=byte_size,
            content_sha256=content_sha256,
            url=f"https://tos.example.invalid/{object_key}?signature=redacted",
            required_headers={
                "content-type": mime_type,
                "x-tos-content-sha256": content_sha256,
                "content-length": str(byte_size),
                "x-tos-acl": "private",
                "x-tos-server-side-encryption": "AES256",
            },
            expires_at=expires_at,
        )

    def initiate_multipart(
        self,
        *,
        object_key: str,
        mime_type: str,
        content_sha256: str,
        operation_key: str,
    ) -> MultipartUploadHandle:
        self.initiate_calls += 1
        upload_id = f"multipart-{operation_key}"
        object_ref = f"tos://private-bucket/{object_key}"
        self.multipart.setdefault(
            upload_id,
            {
                "object_ref": object_ref,
                "mime_type": mime_type,
                "content_sha256": content_sha256,
                "multipart_receipt_key": operation_key,
                "parts": (),
            },
        )
        return MultipartUploadHandle(
            object_ref=object_ref, provider_upload_id=upload_id
        )

    def authorize_multipart_part(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        part,
        expires_at: datetime,
    ) -> UploadPartAuthorization:
        self.authorize_calls += 1
        return UploadPartAuthorization(
            object_ref=object_ref,
            part_number=part.part_number,
            byte_size=part.byte_size,
            content_sha256=part.content_sha256,
            url=f"https://tos.example.invalid/part/{part.part_number}?signature=redacted",
            required_headers={
                "content-type": "application/octet-stream",
                "content-length": str(part.byte_size),
                "x-tos-content-sha256": part.content_sha256,
            },
            expires_at=expires_at,
        )

    def uploaded_parts(
        self, *, object_ref: str, provider_upload_id: str
    ) -> tuple[UploadedPart, ...]:
        return self.multipart[provider_upload_id]["parts"]  # type: ignore[return-value]

    def complete_multipart(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        parts: tuple[UploadedPart, ...],
        operation_key: str,
    ) -> None:
        if self.fail_complete:
            raise OSError("multipart completion unavailable")
        record = self.multipart[provider_upload_id]
        assert record["parts"] == parts
        assert record["multipart_receipt_key"] == operation_key
        self.objects[object_ref] = ObjectMetadata(
            object_ref=object_ref,
            byte_size=sum(item.byte_size for item in parts),
            content_sha256=str(record["content_sha256"]),
            mime_type=str(record["mime_type"]),
            access="private",
            server_side_encrypted=True,
        )
        self.completed.append(provider_upload_id)

    def abort_multipart(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        operation_key: str,
    ) -> None:
        self.aborted.append(provider_upload_id)

    def head_private(self, *, object_ref: str) -> ObjectMetadata:
        return self.objects[object_ref]

    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        if self.fail_delete:
            raise OSError("TOS unavailable")
        self.objects.pop(object_ref, None)
        self.deleted.append(object_ref)

    def quarantine_private(self, *, object_ref: str, operation_key: str) -> str:
        metadata = self.objects.pop(object_ref)
        quarantined_ref = f"tos://private-bucket/private/quarantine/{operation_key}"
        self.objects[quarantined_ref] = replace(
            metadata, object_ref=quarantined_ref
        )
        self.quarantined.append(quarantined_ref)
        return quarantined_ref

    def upload(
        self,
        session: UploadSession,
        *,
        content_sha256: str | None = None,
        mime_type: str | None = None,
        byte_size: int | None = None,
        private: bool = True,
        encrypted: bool = True,
    ) -> None:
        if session.upload_mode == UploadMode.MULTIPART:
            assert session.provider_upload_id is not None
            self.multipart[session.provider_upload_id]["parts"] = tuple(
                UploadedPart(
                    part_number=item.part_number,
                    byte_size=item.byte_size,
                    etag=f"etag-{item.part_number}",
                )
                for item in session.expected_parts
            )
            return
        self.objects[session.object_ref] = ObjectMetadata(
            object_ref=session.object_ref,
            byte_size=byte_size or session.byte_size,
            content_sha256=content_sha256 or session.content_sha256,
            mime_type=mime_type or session.mime_type,
            access="private" if private else "public",
            server_side_encrypted=encrypted,
        )


class FakeScanner:
    def __init__(self) -> None:
        self.results: list[ScanReceipt | Exception] = []
        self.calls = 0

    def scan(
        self, *, object_ref: str, expected_sha256: str, operation_key: str
    ) -> ScanReceipt:
        self.calls += 1
        result = self.results.pop(0) if self.results else clean_receipt()
        if isinstance(result, Exception):
            raise result
        return result


class FakeReservations:
    def __init__(self) -> None:
        self.by_key: dict[str, UploadReservation] = {}
        self.reserve_calls = 0
        self.committed: set[str] = set()
        self.released: set[str] = set()

    def reserve(
        self, *, owner_user_id: UUID, reservation_key: str, byte_size: int
    ) -> UploadReservation:
        self.reserve_calls += 1
        return self.by_key.setdefault(
            reservation_key,
            UploadReservation(
                reservation_id=f"reservation-{len(self.by_key) + 1}",
                reservation_key=reservation_key,
            ),
        )

    def commit(self, *, reservation_id: str, artifact_version_id: UUID) -> None:
        if reservation_id in self.released:
            raise RuntimeError("released reservation cannot be committed")
        self.committed.add(reservation_id)

    def release(self, *, reservation_id: str, reason: str) -> None:
        if reservation_id in self.committed:
            raise RuntimeError("committed reservation cannot be released")
        self.released.add(reservation_id)


def clean_receipt(
    *, mime_type: str = "image/jpeg", page_count: int | None = None
) -> ScanReceipt:
    return ScanReceipt(
        scan_id=f"scan-{uuid4()}",
        verdict=ScanVerdict.CLEAN,
        detected_mime_type=mime_type,
        content_sha256="a" * 64,
        scanner_policy_version="malware-scan-policy@v3",
        provider_route="approved-mainland-malware-scanner",
        data_processing_agreement_version="dpa@2026-06",
        definition_version="definitions-2026-07-15-12",
        scanned_at=NOW,
        detected_page_count=page_count,
    )


def request(
    owner: UUID,
    *,
    idempotency_key: str = "upload-key-0001",
    filename: str = "question.jpg",
    mime_type: str = "image/jpeg",
    source_kind: SourceKind = SourceKind.IMAGE_IMPORT,
    content_sha256: str = "a" * 64,
    byte_size: int = 1_024,
    page_count: int | None = None,
    part_sha256s: tuple[str, ...] = (),
) -> StartUploadRequest:
    return StartUploadRequest(
        owner_user_id=owner,
        idempotency_key=idempotency_key,
        source_kind=source_kind,
        original_filename=filename,
        mime_type=mime_type,
        byte_size=byte_size,
        content_sha256=content_sha256,
        page_count=page_count,
        part_sha256s=part_sha256s,
    )


@pytest.fixture()
def harness() -> tuple[
    UploadService,
    FakeRepository,
    FakeStorage,
    FakeScanner,
    FakeReservations,
    MutableClock,
]:
    repository = FakeRepository()
    storage = FakeStorage()
    scanner = FakeScanner()
    reservations = FakeReservations()
    clock = MutableClock()
    return (
        UploadService(
            repository=repository,
            object_storage=storage,
            scanner=scanner,
            scanner_approval=ScannerRuntimeApproval(
                provider_route="approved-mainland-malware-scanner",
                scanner_policy_version="malware-scan-policy@v3",
                data_processing_agreement_version="dpa@2026-06",
            ),
            reservations=reservations,
            clock=clock,
        ),
        repository,
        storage,
        scanner,
        reservations,
        clock,
    )


def test_clean_upload_becomes_the_only_recognition_eligible_artifact(harness) -> None:
    service, repository, storage, scanner, reservations, _ = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)

    result = service.complete(owner_user_id=owner, session_id=started.session.session_id)

    assert result.session.state == UploadState.AVAILABLE
    assert result.artifact is not None
    assert result.artifact.state.value == "available"
    assert service.recognition_artifact(
        owner_user_id=owner, session_id=started.session.session_id
    ) == result.artifact
    assert reservations.committed == {started.session.reservation_id}
    assert scanner.calls == 1
    assert {event.event_type for event in repository.audits.values()} >= {
        "upload_session_started",
        "upload_scan_started",
        "upload_scan_clean",
        "upload_available",
    }


def test_mvp_image_upload_completes_without_malware_scanner() -> None:
    repository = FakeRepository()
    storage = FakeStorage()
    reservations = FakeReservations()
    clock = MutableClock()
    service = UploadService(
        repository=repository,
        object_storage=storage,
        scanner=None,
        scanner_approval=None,
        reservations=reservations,
        clock=clock,
    )
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)

    result = service.complete(
        owner_user_id=owner,
        session_id=started.session.session_id,
    )

    assert result.session.state == UploadState.AVAILABLE
    assert result.artifact is not None
    assert result.session.scan_receipt is not None
    assert result.session.scan_receipt.provider_route == "local-private-object-metadata"
    assert {event.event_type for event in repository.audits.values()} >= {
        "upload_scan_started",
        "upload_metadata_validated",
        "upload_available",
    }


def test_mvp_image_upload_still_rejects_integrity_mismatch_without_scanner() -> None:
    repository = FakeRepository()
    storage = FakeStorage()
    reservations = FakeReservations()
    service = UploadService(
        repository=repository,
        object_storage=storage,
        scanner=None,
        scanner_approval=None,
        reservations=reservations,
    )
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session, content_sha256="b" * 64)

    with pytest.raises(UploadIntegrityError):
        service.complete(
            owner_user_id=owner,
            session_id=started.session.session_id,
        )

    assert repository.sessions[started.session.session_id].state == UploadState.FAILED
    assert started.session.object_ref in storage.deleted


@pytest.mark.parametrize(
    "candidate",
    [
        request(uuid4(), filename="question.exe"),
        request(uuid4(), filename="question.png", mime_type="image/jpeg"),
        request(uuid4(), byte_size=26 * 1024 * 1024),
        request(
            uuid4(),
            filename="paper.pdf",
            mime_type="application/pdf",
            source_kind=SourceKind.PDF_UPLOAD,
            page_count=301,
        ),
    ],
)
def test_policy_rejects_unsafe_type_size_and_pdf_shape(harness, candidate) -> None:
    service, *_ = harness
    with pytest.raises(UploadContractError):
        service.start(candidate)


def test_start_replay_is_stable_and_changed_input_conflicts(harness) -> None:
    service, _, _, _, reservations, _ = harness
    owner = uuid4()
    first = service.start(request(owner))
    replay = service.start(request(owner))

    assert replay.replayed is True
    assert replay.session.session_id == first.session.session_id
    assert reservations.reserve_calls == 1
    with pytest.raises(UploadIdempotencyConflict):
        service.start(request(owner, byte_size=2_048))


def test_integrity_mismatch_deletes_object_releases_reservation_and_stays_blocked(
    harness,
) -> None:
    service, repository, storage, _, reservations, _ = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session, content_sha256="b" * 64)

    with pytest.raises(UploadIntegrityError):
        service.complete(owner_user_id=owner, session_id=started.session.session_id)

    stored = repository.sessions[started.session.session_id]
    assert stored.state == UploadState.FAILED
    assert started.session.object_ref in storage.deleted
    assert started.session.reservation_id in reservations.released
    with pytest.raises(UploadUnavailable):
        service.recognition_artifact(
            owner_user_id=owner, session_id=started.session.session_id
        )


def test_scanner_full_object_hash_mismatch_is_quarantined_and_never_eligible(
    harness,
) -> None:
    service, repository, storage, scanner, reservations, _ = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)
    scanner.results = [replace(clean_receipt(), content_sha256="b" * 64)]

    completed = service.complete(
        owner_user_id=owner, session_id=started.session.session_id
    )

    assert completed.session.state == UploadState.QUARANTINED
    assert completed.session.failure_code == "detected_content_sha256_mismatch"
    assert repository.sessions[started.session.session_id].state == (
        UploadState.QUARANTINED
    )
    assert started.session.reservation_id in reservations.released
    assert len(storage.quarantined) == 1
    with pytest.raises(UploadUnavailable):
        service.recognition_artifact(
            owner_user_id=owner, session_id=started.session.session_id
        )


def test_scanner_unavailable_and_undetermined_are_retriable_but_never_eligible(
    harness,
) -> None:
    service, repository, storage, scanner, _, _ = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)
    scanner.results = [OSError("scanner down"), clean_receipt()]

    failed = service.complete(owner_user_id=owner, session_id=started.session.session_id)
    assert failed.session.state == UploadState.SCAN_FAILED
    assert failed.artifact is None
    assert repository.sessions[started.session.session_id].state == UploadState.SCAN_FAILED
    with pytest.raises(UploadUnavailable):
        service.recognition_artifact(
            owner_user_id=owner, session_id=started.session.session_id
        )

    replayed = service.start(request(owner))
    assert replayed.replayed is True
    assert replayed.session.state == UploadState.SCAN_FAILED

    recovered = service.complete(
        owner_user_id=owner, session_id=started.session.session_id
    )
    assert recovered.session.state == UploadState.AVAILABLE
    assert scanner.calls == 2


def test_undetermined_scan_receipt_remains_blocked_until_a_clean_retry(harness) -> None:
    service, repository, storage, scanner, _, _ = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)
    scanner.results = [
        replace(clean_receipt(), verdict=ScanVerdict.UNDETERMINED),
        clean_receipt(),
    ]

    failed = service.complete(owner_user_id=owner, session_id=started.session.session_id)
    assert failed.session.state == UploadState.SCAN_FAILED
    assert failed.artifact is None
    assert repository.sessions[started.session.session_id].failure_code == (
        "scanner_undetermined"
    )
    assert service.complete(
        owner_user_id=owner, session_id=started.session.session_id
    ).session.state == UploadState.AVAILABLE


def test_malware_is_quarantined_released_and_never_recognition_eligible(harness) -> None:
    service, _, storage, scanner, reservations, _ = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)
    scanner.results = [replace(clean_receipt(), verdict=ScanVerdict.MALICIOUS)]

    result = service.complete(owner_user_id=owner, session_id=started.session.session_id)

    assert result.session.state == UploadState.QUARANTINED
    assert result.artifact is not None
    assert result.artifact.state.value == "quarantined"
    assert result.artifact.object_ref in storage.quarantined
    assert started.session.reservation_id in reservations.released
    with pytest.raises(UploadUnavailable):
        service.recognition_artifact(
            owner_user_id=owner, session_id=started.session.session_id
        )


def test_pdf_page_count_is_scanner_verified_before_artifact_availability(harness) -> None:
    service, _, storage, scanner, reservations, _ = harness
    owner = uuid4()
    started = service.start(
        request(
            owner,
            filename="paper.pdf",
            mime_type="application/pdf",
            source_kind=SourceKind.PDF_UPLOAD,
            page_count=12,
            part_sha256s=("c" * 64,),
        )
    )
    storage.upload(started.session)
    scanner.results = [clean_receipt(mime_type="application/pdf", page_count=11)]

    result = service.complete(owner_user_id=owner, session_id=started.session.session_id)

    assert result.session.state == UploadState.QUARANTINED
    assert result.session.failure_code == "detected_page_count_mismatch"
    assert result.session.page_count == 11
    assert result.artifact is not None
    assert result.artifact.page_count == 11
    assert started.session.reservation_id in reservations.released
    assert started.session.provider_upload_id in storage.completed


def test_pdf_without_client_page_count_uses_scanner_authority_for_artifact(
    harness,
) -> None:
    service, repository, storage, scanner, reservations, _ = harness
    owner = uuid4()
    started = service.start(
        request(
            owner,
            filename="paper.pdf",
            mime_type="application/pdf",
            source_kind=SourceKind.PDF_UPLOAD,
            page_count=None,
            part_sha256s=("c" * 64,),
        )
    )
    assert started.session.page_count is None
    storage.upload(started.session)
    scanner.results = [clean_receipt(mime_type="application/pdf", page_count=27)]

    result = service.complete(
        owner_user_id=owner, session_id=started.session.session_id
    )

    assert result.session.state == UploadState.AVAILABLE
    assert result.session.page_count == 27
    assert repository.sessions[started.session.session_id].page_count == 27
    assert result.artifact is not None
    assert result.artifact.page_count == 27
    assert started.session.reservation_id in reservations.committed


def test_scanner_detected_pdf_over_page_limit_is_quarantined_with_evidence(
    harness,
) -> None:
    service, _, storage, scanner, reservations, _ = harness
    owner = uuid4()
    started = service.start(
        request(
            owner,
            filename="paper.pdf",
            mime_type="application/pdf",
            source_kind=SourceKind.PDF_UPLOAD,
            page_count=None,
            part_sha256s=("c" * 64,),
        )
    )
    storage.upload(started.session)
    scanner.results = [clean_receipt(mime_type="application/pdf", page_count=301)]

    result = service.complete(
        owner_user_id=owner, session_id=started.session.session_id
    )

    assert result.session.state == UploadState.QUARANTINED
    assert result.session.failure_code == "detected_page_count_exceeds_limit"
    assert result.session.page_count == 301
    assert result.artifact is not None
    assert result.artifact.page_count == 301
    assert started.session.reservation_id in reservations.released


def test_malicious_pdf_without_client_page_count_keeps_scanner_page_evidence(
    harness,
) -> None:
    service, _, storage, scanner, _, _ = harness
    owner = uuid4()
    started = service.start(
        request(
            owner,
            filename="paper.pdf",
            mime_type="application/pdf",
            source_kind=SourceKind.PDF_UPLOAD,
            page_count=None,
            part_sha256s=("c" * 64,),
        )
    )
    storage.upload(started.session)
    scanner.results = [
        replace(
            clean_receipt(mime_type="application/pdf", page_count=9),
            verdict=ScanVerdict.MALICIOUS,
        )
    ]

    result = service.complete(
        owner_user_id=owner, session_id=started.session.session_id
    )

    assert result.session.state == UploadState.QUARANTINED
    assert result.session.failure_code == "malware_detected"
    assert result.session.page_count == 9
    assert result.artifact is not None
    assert result.artifact.page_count == 9


def test_multipart_start_replay_reuses_provider_upload_and_refreshes_part_urls(
    harness,
) -> None:
    service, _, storage, _, reservations, _ = harness
    owner = uuid4()
    candidate = request(
        owner,
        filename="paper.pdf",
        mime_type="application/pdf",
        source_kind=SourceKind.PDF_UPLOAD,
        page_count=2,
        part_sha256s=("c" * 64,),
    )
    first = service.start(candidate)
    replay = service.start(candidate)

    assert first.authorization.mode == UploadMode.MULTIPART
    assert replay.session.session_id == first.session.session_id
    assert replay.authorization.provider_upload_id == (
        first.authorization.provider_upload_id
    )
    assert storage.initiate_calls == 1
    assert reservations.reserve_calls == 1


def test_incomplete_multipart_manifest_is_retriable_and_never_scanned(harness) -> None:
    service, repository, _, scanner, _, _ = harness
    owner = uuid4()
    started = service.start(
        request(
            owner,
            filename="paper.pdf",
            mime_type="application/pdf",
            source_kind=SourceKind.PDF_UPLOAD,
            page_count=2,
            part_sha256s=("c" * 64,),
        )
    )

    with pytest.raises(UploadIntegrityError):
        service.complete(owner_user_id=owner, session_id=started.session.session_id)

    assert repository.sessions[started.session.session_id].state == (
        UploadState.AWAITING_UPLOAD
    )
    assert scanner.calls == 0


def test_expired_multipart_is_aborted_and_releases_reservation(harness) -> None:
    service, _, storage, _, reservations, clock = harness
    owner = uuid4()
    started = service.start(
        request(
            owner,
            filename="paper.pdf",
            mime_type="application/pdf",
            source_kind=SourceKind.PDF_UPLOAD,
            page_count=2,
            part_sha256s=("c" * 64,),
        )
    )
    clock.value = started.session.expires_at

    result = service.complete(owner_user_id=owner, session_id=started.session.session_id)

    assert result.session.state == UploadState.EXPIRED
    assert started.session.provider_upload_id in storage.aborted
    assert started.session.reservation_id in reservations.released


def test_multipart_completion_intent_recovers_without_reupload(harness) -> None:
    service, repository, storage, scanner, _, _ = harness
    owner = uuid4()
    started = service.start(
        request(
            owner,
            filename="paper.pdf",
            mime_type="application/pdf",
            source_kind=SourceKind.PDF_UPLOAD,
            page_count=2,
            part_sha256s=("c" * 64,),
        )
    )
    storage.upload(started.session)
    scanner.results = [clean_receipt(mime_type="application/pdf", page_count=2)]
    storage.fail_complete = True

    with pytest.raises(OSError):
        service.complete(owner_user_id=owner, session_id=started.session.session_id)
    assert repository.sessions[started.session.session_id].state == UploadState.ASSEMBLING

    storage.fail_complete = False
    result = service.complete(owner_user_id=owner, session_id=started.session.session_id)
    assert result.session.state == UploadState.AVAILABLE
    assert started.session.provider_upload_id in storage.completed


def test_deduplication_reuses_only_the_same_owners_available_artifact(harness) -> None:
    service, _, storage, _, reservations, _ = harness
    owner = uuid4()
    first = service.start(request(owner, idempotency_key="upload-key-first"))
    storage.upload(first.session)
    first_result = service.complete(
        owner_user_id=owner, session_id=first.session.session_id
    )
    second = service.start(request(owner, idempotency_key="upload-key-second"))
    storage.upload(second.session)
    second_result = service.complete(
        owner_user_id=owner, session_id=second.session.session_id
    )

    assert second_result.session.state == UploadState.DEDUPLICATED
    assert second_result.artifact == first_result.artifact
    assert second.session.object_ref in storage.deleted
    assert second.session.reservation_id in reservations.released

    other_owner = uuid4()
    third = service.start(request(other_owner, idempotency_key="upload-key-third"))
    storage.upload(third.session)
    third_result = service.complete(
        owner_user_id=other_owner, session_id=third.session.session_id
    )
    assert third_result.session.state == UploadState.AVAILABLE
    assert third_result.artifact != first_result.artifact


def test_other_users_cannot_complete_or_read_an_upload(harness) -> None:
    service, _, storage, _, _, _ = harness
    owner = uuid4()
    intruder = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)

    with pytest.raises(UploadOwnershipError):
        service.complete(
            owner_user_id=intruder, session_id=started.session.session_id
        )
    with pytest.raises(UploadOwnershipError):
        service.recognition_artifact(
            owner_user_id=intruder, session_id=started.session.session_id
        )


def test_expired_upload_deletes_staging_object_and_releases_reservation(harness) -> None:
    service, repository, storage, _, reservations, clock = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)
    clock.value = started.session.expires_at

    result = service.complete(owner_user_id=owner, session_id=started.session.session_id)

    assert result.session.state == UploadState.EXPIRED
    assert repository.artifact_for_session(session_id=started.session.session_id) is None
    assert started.session.object_ref in storage.deleted
    assert started.session.reservation_id in reservations.released


def test_cleanup_failure_keeps_session_nonterminal_for_recovery(harness) -> None:
    service, repository, storage, _, reservations, clock = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)
    storage.fail_delete = True
    clock.value = started.session.expires_at

    with pytest.raises(UploadRecoveryRequired):
        service.complete(owner_user_id=owner, session_id=started.session.session_id)

    assert repository.sessions[started.session.session_id].state == (
        UploadState.AWAITING_UPLOAD
    )
    assert started.session.reservation_id not in reservations.released

    storage.fail_delete = False
    recovered = service.complete(
        owner_user_id=owner, session_id=started.session.session_id
    )
    assert recovered.session.state == UploadState.EXPIRED
    assert started.session.reservation_id in reservations.released


def test_integrity_cleanup_intent_survives_storage_failure_and_can_resume(
    harness,
) -> None:
    service, repository, storage, _, reservations, _ = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session, content_sha256="b" * 64)
    storage.fail_delete = True

    with pytest.raises(UploadRecoveryRequired):
        service.complete(owner_user_id=owner, session_id=started.session.session_id)
    assert repository.sessions[started.session.session_id].state == UploadState.REJECTING

    storage.fail_delete = False
    result = service.complete(
        owner_user_id=owner, session_id=started.session.session_id
    )
    assert result.session.state == UploadState.FAILED
    assert started.session.reservation_id in reservations.released


@pytest.mark.parametrize("pending_state", ["scanning", "quarantining", "finalizing"])
def test_recovery_worker_finishes_every_persisted_external_operation_intent(
    harness, pending_state
) -> None:
    service, repository, storage, _, reservations, clock = harness
    owner = uuid4()
    started = service.start(request(owner))
    storage.upload(started.session)
    current = started.session.transition(UploadState.SCANNING, at=NOW)
    expected = UploadState.AVAILABLE
    if pending_state == "quarantining":
        current = current.transition(
            UploadState.QUARANTINING,
            at=NOW,
            scan_receipt=replace(clean_receipt(), verdict=ScanVerdict.MALICIOUS),
            failure_code="malware_detected",
        )
        expected = UploadState.QUARANTINED
    elif pending_state == "finalizing":
        current = current.transition(
            UploadState.FINALIZING,
            at=NOW,
            scan_receipt=clean_receipt(),
        )
    repository.sessions[current.session_id] = current
    clock.value = NOW + timedelta(minutes=2)

    recovered = service.recover_pending(
        before=NOW + timedelta(minutes=1), limit=10
    )

    assert recovered == (current.session_id,)
    assert repository.sessions[current.session_id].state == expected
    if expected == UploadState.AVAILABLE:
        assert current.reservation_id in reservations.committed
    else:
        assert current.reservation_id in reservations.released


def test_concurrent_start_and_completion_are_single_effect(harness) -> None:
    service, repository, storage, scanner, reservations, _ = harness
    owner = uuid4()
    start_barrier = Barrier(2)

    def start_once():
        start_barrier.wait()
        return service.start(request(owner))

    with ThreadPoolExecutor(max_workers=2) as pool:
        starts = list(pool.map(lambda _: start_once(), range(2)))
    assert starts[0].session.session_id == starts[1].session.session_id
    assert reservations.reserve_calls == 1

    session = starts[0].session
    storage.upload(session)
    complete_barrier = Barrier(2)

    def complete_once():
        complete_barrier.wait()
        return service.complete(owner_user_id=owner, session_id=session.session_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        completions = list(pool.map(lambda _: complete_once(), range(2)))
    assert {item.replayed for item in completions} == {False, True}
    assert scanner.calls == 1
    assert len(repository.artifacts) == 1
    assert reservations.committed == {session.reservation_id}


def test_expiry_worker_respects_limit_and_does_not_touch_available_sessions(
    harness,
) -> None:
    service, repository, storage, _, _, clock = harness
    sessions = []
    for index in range(3):
        started = service.start(
            request(uuid4(), idempotency_key=f"upload-worker-{index}")
        )
        storage.upload(started.session)
        sessions.append(started.session)
    available = service.complete(
        owner_user_id=sessions[0].owner_user_id,
        session_id=sessions[0].session_id,
    )
    assert available.session.state == UploadState.AVAILABLE
    clock.value = NOW + timedelta(hours=1)

    expired = service.expire_stale(before=NOW + timedelta(minutes=30), limit=1)

    assert len(expired) == 1
    assert expired[0] != sessions[0].session_id
    assert repository.sessions[sessions[0].session_id].state == UploadState.AVAILABLE


def test_expiry_worker_never_uses_inactivity_as_a_ttl_substitute(harness) -> None:
    service, repository, _, _, _, _ = harness
    started = service.start(request(uuid4()))

    before_actual_expiry = started.session.expires_at - timedelta(seconds=1)
    expired = service.expire_stale(before=before_actual_expiry)

    assert expired == ()
    assert repository.sessions[started.session.session_id].state == (
        UploadState.AWAITING_UPLOAD
    )
