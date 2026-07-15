from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    SourceArtifactState,
    SourceArtifactVersion,
    SourceKind,
)
from app.domains.external_ingestion.upload.models import (
    ScanReceipt,
    ScanVerdict,
    UploadAuditEvent,
    UploadMode,
    UploadSession,
    UploadState,
)
from app.domains.external_ingestion.upload.repository import (
    PostgresUploadRepository,
    UploadRepositoryConflict,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def session_record(
    *, owner_user_id, reservation_id: str, state=UploadState.AWAITING_UPLOAD
) -> UploadSession:
    return UploadSession(
        session_id=uuid4(),
        artifact_id=uuid4(),
        artifact_version_id=uuid4(),
        owner_user_id=owner_user_id,
        revision=1,
        state=state,
        source_kind=SourceKind.IMAGE_IMPORT,
        original_filename="question.jpg",
        extension=".jpg",
        mime_type="image/jpeg",
        byte_size=1_024,
        content_sha256="a" * 64,
        page_count=None,
        object_ref=f"tos://bucket/private/external-learning/{owner_user_id}/{uuid4()}.jpg",
        reservation_id=reservation_id,
        upload_policy_version="external-upload-policy@v1",
        upload_mode=UploadMode.SINGLE_PUT,
        provider_upload_id=None,
        part_size=None,
        expected_parts=(),
        uploaded_parts=(),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        updated_at=NOW,
    )


def test_postgres_repository_round_trips_session_command_audit_and_artifact(
    db_session: Session,
) -> None:
    owner = uuid4()
    db_session.execute(
        text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
        {"id": str(owner)},
    )
    repository = PostgresUploadRepository(session=db_session)
    reservation_id = _insert_reservation(db_session, owner_user_id=owner)
    initial = session_record(owner_user_id=owner, reservation_id=reservation_id)
    repository.insert_session_with_command(
        session=initial,
        command_type="source_upload_start",
        idempotency_key="upload-key-0001",
        request_hash="b" * 64,
    )
    audit = UploadAuditEvent(
        event_id=uuid4(),
        deduplication_key="c" * 64,
        session_id=initial.session_id,
        owner_user_id=owner,
        event_type="upload_session_started",
        from_state=None,
        to_state=UploadState.AWAITING_UPLOAD,
        details={"policyVersion": initial.upload_policy_version},
        created_at=NOW,
    )
    repository.append_audit(audit)
    repository.append_audit(audit)

    receipt = ScanReceipt(
        scan_id="scan-1",
        verdict=ScanVerdict.CLEAN,
        detected_mime_type="image/jpeg",
        content_sha256="a" * 64,
        scanner_policy_version="malware-policy@v3",
        provider_route="approved-mainland-malware-scanner",
        data_processing_agreement_version="dpa@2026-06",
        definition_version="definitions-1",
        scanned_at=NOW,
    )
    scanning = initial.transition(UploadState.SCANNING, at=NOW)
    repository.save_session(expected_revision=1, session=scanning)
    finalizing = scanning.transition(
        UploadState.FINALIZING, at=NOW, scan_receipt=receipt
    )
    repository.save_session(expected_revision=2, session=finalizing)
    artifact = SourceArtifactVersion(
        artifactVersionId=initial.artifact_version_id,
        artifactId=initial.artifact_id,
        ownerUserId=owner,
        versionNo=1,
        sourceKind=initial.source_kind,
        state=SourceArtifactState.AVAILABLE,
        objectRef=initial.object_ref,
        mimeType=initial.mime_type,
        contentSha256=initial.content_sha256,
        byteSize=initial.byte_size,
        createdAt=NOW,
    )
    repository.insert_artifact(session_id=initial.session_id, artifact=artifact)
    available = finalizing.transition(
        UploadState.AVAILABLE,
        at=NOW,
        resolved_artifact_version_id=artifact.artifact_version_id,
    )
    repository.save_session(expected_revision=3, session=available)

    command = repository.command_receipt(
        owner_user_id=owner,
        command_type="source_upload_start",
        idempotency_key="upload-key-0001",
    )
    assert command is not None
    assert command.session_id == initial.session_id
    assert repository.session(session_id=initial.session_id) == available
    assert repository.artifact_for_session(session_id=initial.session_id) == artifact
    assert repository.available_duplicate(
        owner_user_id=owner,
        source_kind=initial.source_kind,
        content_sha256=initial.content_sha256,
        byte_size=initial.byte_size,
        mime_type=initial.mime_type,
    ) == artifact
    assert db_session.execute(
        text("select count(*) from learning.upload_audit_events")
    ).scalar_one() == 1

    with pytest.raises(UploadRepositoryConflict):
        repository.save_session(expected_revision=1, session=available)


def test_postgres_repository_recovery_queries_do_not_cross_state_classes(
    db_session: Session,
) -> None:
    owner = uuid4()
    db_session.execute(
        text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
        {"id": str(owner)},
    )
    repository = PostgresUploadRepository(session=db_session)
    awaiting = session_record(
        owner_user_id=owner,
        reservation_id=_insert_reservation(db_session, owner_user_id=owner),
    )
    repository.insert_session_with_command(
        session=awaiting,
        command_type="source_upload_start",
        idempotency_key="upload-key-awaiting",
        request_hash="d" * 64,
    )
    scanning_initial = session_record(
        owner_user_id=owner,
        reservation_id=_insert_reservation(db_session, owner_user_id=owner),
    )
    repository.insert_session_with_command(
        session=scanning_initial,
        command_type="source_upload_start",
        idempotency_key="upload-key-scanning",
        request_hash="e" * 64,
    )
    scanning = scanning_initial.transition(UploadState.SCANNING, at=NOW)
    repository.save_session(expected_revision=1, session=scanning)

    recovery_cutoff = NOW + timedelta(minutes=1)
    assert repository.stale_session_ids(
        before=awaiting.expires_at - timedelta(seconds=1), limit=10
    ) == ()
    assert repository.stale_session_ids(before=awaiting.expires_at, limit=10) == (
        awaiting.session_id,
    )
    assert repository.recovery_session_ids(before=recovery_cutoff, limit=10) == (
        scanning.session_id,
    )


def _insert_reservation(db_session: Session, *, owner_user_id) -> str:
    reservation_id = uuid4()
    db_session.execute(
        text(
            """
            insert into learning.upload_reservations (
              reservation_id, reservation_key, owner_user_id, byte_size,
              policy_version, state, attempt_no, created_at, updated_at
            ) values (
              :reservation_id, :reservation_key, :owner_user_id, 1024,
              'quota-policy@v1', 'reserved', 1, :now, :now
            )
            """
        ),
        {
            "reservation_id": str(reservation_id),
            "reservation_key": uuid4().hex + uuid4().hex,
            "owner_user_id": str(owner_user_id),
            "now": NOW,
        },
    )
    return str(reservation_id)
