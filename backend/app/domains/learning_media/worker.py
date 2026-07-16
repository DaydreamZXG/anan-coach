from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.domains.external_ingestion.upload.models import ScanVerdict
from app.domains.external_ingestion.upload.ports import (
    MalwareScannerPort,
    PrivateObjectStoragePort,
)
from app.domains.learning_media.repository import LearningMediaRepository
from app.domains.learning_media.service import (
    RETENTION_CLEANUP_JOB_TYPE,
    SCAN_JOB_TYPE,
)
from app.platform.jobs import BackgroundJob, BackgroundJobRepository, JobContractError
from app.platform.outbox import OutboxRepository


class LearningMediaWorker:
    JOB_TYPES = (SCAN_JOB_TYPE, RETENTION_CLEANUP_JOB_TYPE)

    def __init__(
        self,
        *,
        storage: PrivateObjectStoragePort,
        scanner: MalwareScannerPort,
        repository: LearningMediaRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._storage = storage
        self._scanner = scanner
        self._repository = repository or LearningMediaRepository()
        self._jobs = jobs or BackgroundJobRepository()
        self._outbox = outbox or OutboxRepository()

    def recover_stale_claims(
        self, session: Session, *, now: datetime | None = None
    ) -> int:
        current = _utc(now)
        recovered = self._jobs.requeue_stale(
            session,
            job_types=self.JOB_TYPES,
            stale_before=current - timedelta(minutes=5),
            now=current,
        )
        session.commit()
        return recovered

    def process_next(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> BackgroundJob | None:
        current = _utc(now)
        job = self._jobs.claim_next(
            session,
            job_types=self.JOB_TYPES,
            now=current,
        )
        if job is None:
            session.rollback()
            return None
        session.commit()
        try:
            if job.job_type == SCAN_JOB_TYPE:
                return self._process_scan_claim(session, job=job, now=current)
            if job.job_type == RETENTION_CLEANUP_JOB_TYPE:
                return self._process_retention_claim(session, job=job, now=current)
            raise JobContractError("claimed learning-media job has an unsupported type")
        except Exception as exc:
            session.rollback()
            if job.claim_token is None:
                raise
            failed = self._jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code=(
                    "learning_media_scan_unavailable"
                    if job.job_type == SCAN_JOB_TYPE
                    else "learning_media_retention_cleanup_unavailable"
                ),
                error_message=type(exc).__name__,
                retryable=True,
                now=current,
            )
            session.commit()
            return failed

    def _process_scan_claim(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        now: datetime,
    ) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError("claimed learning-media job has no claim token")
        payload = job.payload
        upload_session_id = str(payload["uploadSessionId"])
        user_id = str(payload["userId"])
        object_ref = str(payload["objectRef"])
        expected_hash = str(payload["contentSha256"])
        expected_mime = str(payload["mimeType"])
        receipt = self._scanner.scan(
            object_ref=object_ref,
            expected_sha256=expected_hash,
            operation_key=_operation_key(job, "scan"),
        )
        valid = (
            receipt.verdict is ScanVerdict.CLEAN
            and receipt.content_sha256 == expected_hash
            and receipt.detected_mime_type == expected_mime
        )
        quarantine_ref = None
        if not valid:
            quarantine_ref = self._storage.quarantine_private(
                object_ref=object_ref,
                operation_key=_operation_key(job, "quarantine"),
            )

        row = self._repository.upload_by_id(
            session, upload_session_id=upload_session_id, for_update=True
        )
        if row is None or str(row["user_id"]) != user_id:
            return self._fail_terminal(
                session, job=job, code="learning_media_scan_source_missing"
            )
        if row["state"] == "available":
            succeeded = self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={"state": "available", "replayed": True},
            )
            session.commit()
            return succeeded
        if row["state"] != "scanning":
            return self._fail_terminal(
                session, job=job, code="learning_media_scan_state_changed"
            )
        receipt_payload = _receipt_payload(receipt)
        if valid:
            self._repository.insert_artifact(
                session,
                values={
                    "artifact_version_id": str(row["artifact_version_id"]),
                    "artifact_id": str(row["artifact_id"]),
                    "user_id": user_id,
                    "purpose": str(row["purpose"]),
                    "object_ref": object_ref,
                    "mime_type": expected_mime,
                    "byte_size": int(row["byte_size"]),
                    "content_sha256": expected_hash,
                    "duration_milliseconds": int(row["duration_milliseconds"]),
                    "scanner_receipt": receipt_payload,
                    "retention_policy_version": str(row["retention_policy_version"]),
                    "source_upload_session_id": upload_session_id,
                    "created_at": now,
                },
            )
            updated = self._repository.transition(
                session,
                upload_session_id=upload_session_id,
                expected_revision=int(row["revision"]),
                expected_state="scanning",
                to_state="available",
                updated_at=now,
                scanner_receipt=receipt_payload,
                completed_at=now,
            )
            if updated is None:
                raise JobContractError("learning-media scan settlement raced")
            self._append_terminal_audit(
                session,
                row=updated,
                event_type="learning-media-available@v1",
                from_state="scanning",
                evidence={"scanId": receipt.scan_id},
                occurred_at=now,
            )
            self._append_available_event(session, row=updated, occurred_at=now)
            result: dict[str, Any] = {
                "state": "available",
                "artifactVersionId": str(row["artifact_version_id"]),
            }
        else:
            updated = self._repository.transition(
                session,
                upload_session_id=upload_session_id,
                expected_revision=int(row["revision"]),
                expected_state="scanning",
                to_state="quarantined",
                updated_at=now,
                scanner_receipt=receipt_payload,
                failure_code="security_scan_rejected",
            )
            if updated is None:
                raise JobContractError("learning-media quarantine settlement raced")
            self._append_terminal_audit(
                session,
                row=updated,
                event_type="learning-media-quarantined@v1",
                from_state="scanning",
                evidence={
                    "scanId": receipt.scan_id,
                    "quarantineRef": quarantine_ref,
                },
                occurred_at=now,
            )
            result = {"state": "quarantined"}
        succeeded = self._jobs.succeed(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result=result,
            completed_at=now,
        )
        session.commit()
        return succeeded

    def _process_retention_claim(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        now: datetime,
    ) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError(
                "claimed learning-media retention job has no claim token"
            )
        payload = job.payload
        upload_session_id = str(payload["uploadSessionId"])
        row = self._repository.upload_by_id(
            session,
            upload_session_id=upload_session_id,
            for_update=True,
        )
        if row is None:
            return self._fail_terminal(
                session,
                job=job,
                code="learning_media_retention_source_missing",
                now=now,
            )
        if not _retention_payload_matches(row, payload):
            return self._fail_terminal(
                session,
                job=job,
                code="learning_media_retention_input_mismatch",
                now=now,
            )
        if row["state"] == "expired":
            succeeded = self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={"state": "expired", "replayed": True},
                completed_at=now,
            )
            session.commit()
            return succeeded
        if row["state"] in {"quarantined", "failed"}:
            succeeded = self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={
                    "state": str(row["state"]),
                    "retentionAction": "security_or_failure_policy_owns_object",
                },
                completed_at=now,
            )
            session.commit()
            return succeeded
        if row["expires_at"] > now:
            raise JobContractError("learning-media retention job was claimed before expiry")
        if row["state"] not in {"awaiting_upload", "scanning", "available"}:
            raise JobContractError("learning-media retention source state is unsupported")

        source_artifact = self._repository.artifact_by_version(
            session,
            artifact_version_id=str(row["artifact_version_id"]),
            for_update=True,
        )
        if row["state"] == "available" and source_artifact is None:
            raise JobContractError("available learning media has no artifact fact")
        if source_artifact is not None:
            if (
                str(source_artifact["user_id"]) != str(row["user_id"])
                or str(source_artifact["artifact_id"]) != str(row["artifact_id"])
                or str(source_artifact["state"]) != "available"
                or self._repository.artifact_successor(
                    session,
                    artifact_version_id=str(source_artifact["artifact_version_id"]),
                )
                is not None
            ):
                raise JobContractError(
                    "learning-media retention artifact lineage is inconsistent"
                )
            if self._repository.artifact_is_bound(
                session,
                artifact_version_id=str(source_artifact["artifact_version_id"]),
            ):
                succeeded = self._jobs.succeed(
                    session,
                    job_id=job.job_id,
                    claim_token=job.claim_token,
                    result={
                        "state": str(row["state"]),
                        "retentionAction": "retained_bound",
                        "artifactVersionId": str(
                            source_artifact["artifact_version_id"]
                        ),
                    },
                    completed_at=now,
                )
                session.commit()
                return succeeded

        delete_operation_key = _operation_key(job, "retention-delete")
        self._storage.delete_private(
            object_ref=str(row["object_ref"]),
            operation_key=delete_operation_key,
        )
        deleted_artifact = None
        if source_artifact is not None:
            deleted_artifact = self._repository.insert_deleted_successor(
                session,
                source=source_artifact,
                artifact_version_id=str(uuid4()),
                created_at=now,
            )
        updated = self._repository.expire_unbound_upload(
            session,
            upload_session_id=upload_session_id,
            expected_revision=int(row["revision"]),
            expected_state=str(row["state"]),
            expired_at=now,
        )
        if updated is None:
            raise JobContractError("learning-media retention settlement raced")
        self._append_terminal_audit(
            session,
            row=updated,
            event_type="learning-media-retention-expired@v1",
            from_state=str(row["state"]),
            evidence={
                "deleteOperationKey": delete_operation_key,
                "sourceArtifactVersionId": str(row["artifact_version_id"]),
                "deletedArtifactVersionId": (
                    str(deleted_artifact["artifact_version_id"])
                    if deleted_artifact is not None
                    else None
                ),
                "retentionPolicyVersion": str(row["retention_policy_version"]),
            },
            occurred_at=now,
        )
        self._append_retention_event(
            session,
            row=updated,
            deleted_artifact=deleted_artifact,
            occurred_at=now,
        )
        succeeded = self._jobs.succeed(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={
                "state": "expired",
                "retentionAction": "deleted_unbound",
                "deletedArtifactVersionId": (
                    str(deleted_artifact["artifact_version_id"])
                    if deleted_artifact is not None
                    else None
                ),
            },
            completed_at=now,
        )
        session.commit()
        return succeeded

    def _fail_terminal(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        code: str,
        now: datetime | None = None,
    ) -> BackgroundJob:
        assert job.claim_token is not None
        failed = self._jobs.fail(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_code=code,
            error_message=code,
            retryable=False,
            now=now,
        )
        session.commit()
        return failed

    def _append_terminal_audit(
        self,
        session: Session,
        *,
        row: Any,
        event_type: str,
        from_state: str,
        evidence: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        self._repository.append_audit(
            session,
            values={
                "event_id": str(uuid4()),
                "upload_session_id": str(row["upload_session_id"]),
                "user_id": str(row["user_id"]),
                "sequence_no": int(row["revision"]),
                "event_type": event_type,
                "from_state": from_state,
                "to_state": str(row["state"]),
                "evidence": evidence,
                "occurred_at": occurred_at,
            },
        )

    def _append_available_event(
        self, session: Session, *, row: Any, occurred_at: datetime
    ) -> None:
        event_id = uuid4()
        artifact_version_id = str(row["artifact_version_id"])
        self._outbox.append(
            session,
            event_id=event_id,
            deduplication_key=f"learning-media:{artifact_version_id}:available",
            aggregate_type="learning_media_artifact",
            aggregate_id=str(row["artifact_id"]),
            event_type="learning-media-available@v1",
            payload={
                "eventId": str(event_id),
                "eventType": "learning-media-available@v1",
                "aggregateId": str(row["artifact_id"]),
                "aggregateRevision": 1,
                "occurredAt": occurred_at.isoformat(),
                "producer": "learning_media",
                "subjectRef": _subject_ref(str(row["user_id"])),
                "trackId": "shared",
                "correlationId": str(row["upload_session_id"]),
                "causationId": str(row["upload_session_id"]),
                "payload": {
                    "artifactVersionId": artifact_version_id,
                    "purpose": str(row["purpose"]),
                    "resourceVersionRefs": [
                        f"learning-media-artifact:{artifact_version_id}"
                    ],
                },
                "schemaVersion": 1,
            },
        )

    def _append_retention_event(
        self,
        session: Session,
        *,
        row: Any,
        deleted_artifact: Any | None,
        occurred_at: datetime,
    ) -> None:
        event_id = uuid4()
        upload_session_id = str(row["upload_session_id"])
        deleted_artifact_version_id = (
            str(deleted_artifact["artifact_version_id"])
            if deleted_artifact is not None
            else None
        )
        resource_version_refs = [
            f"learning-media-upload:{upload_session_id}:{row['revision']}"
        ]
        if deleted_artifact_version_id is not None:
            resource_version_refs.append(
                f"learning-media-artifact:{deleted_artifact_version_id}"
            )
        self._outbox.append(
            session,
            event_id=event_id,
            deduplication_key=(
                f"learning-media:{upload_session_id}:retention-expired"
            ),
            aggregate_type="learning_media_upload",
            aggregate_id=upload_session_id,
            event_type="learning-media-retention-expired@v1",
            payload={
                "eventId": str(event_id),
                "eventType": "learning-media-retention-expired@v1",
                "aggregateId": upload_session_id,
                "aggregateRevision": int(row["revision"]),
                "occurredAt": occurred_at.isoformat(),
                "producer": "learning_media",
                "subjectRef": _subject_ref(str(row["user_id"])),
                "trackId": "shared",
                "correlationId": upload_session_id,
                "causationId": upload_session_id,
                "payload": {
                    "artifactId": str(row["artifact_id"]),
                    "sourceArtifactVersionId": str(row["artifact_version_id"]),
                    "deletedArtifactVersionId": deleted_artifact_version_id,
                    "retentionPolicyVersion": str(
                        row["retention_policy_version"]
                    ),
                    "resourceVersionRefs": resource_version_refs,
                },
                "schemaVersion": 1,
            },
        )


def _receipt_payload(receipt: Any) -> dict[str, Any]:
    return {
        "scanId": receipt.scan_id,
        "verdict": receipt.verdict.value,
        "detectedMimeType": receipt.detected_mime_type,
        "contentSha256": receipt.content_sha256,
        "scannerPolicyVersion": receipt.scanner_policy_version,
        "providerRoute": receipt.provider_route,
        "dataProcessingAgreementVersion": receipt.data_processing_agreement_version,
        "definitionVersion": receipt.definition_version,
        "scannedAt": receipt.scanned_at.isoformat(),
    }


def _operation_key(job: BackgroundJob, phase: str) -> str:
    return sha256(f"{job.job_id}:{phase}".encode("utf-8")).hexdigest()


def _subject_ref(user_id: str) -> str:
    payload = json.dumps(
        {"scope": "event-subject", "subject": user_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _retention_payload_matches(row: Any, payload: dict[str, Any]) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(payload["expiresAt"]))
    except (KeyError, TypeError, ValueError):
        return False
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        return False
    return (
        str(payload.get("uploadSessionId")) == str(row["upload_session_id"])
        and str(payload.get("userId")) == str(row["user_id"])
        and str(payload.get("artifactId")) == str(row["artifact_id"])
        and str(payload.get("artifactVersionId"))
        == str(row["artifact_version_id"])
        and str(payload.get("objectRef")) == str(row["object_ref"])
        and str(payload.get("retentionPolicyVersion"))
        == str(row["retention_policy_version"])
        and expires_at.astimezone(UTC) == row["expires_at"].astimezone(UTC)
    )


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("learning-media worker timestamps require timezone")
    return current.astimezone(UTC)
