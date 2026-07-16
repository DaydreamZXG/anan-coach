from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import PurePath
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.external_ingestion.upload.models import ObjectMetadata
from app.domains.external_ingestion.upload.ports import PrivateObjectStoragePort
from app.domains.learning_media.repository import LearningMediaRepository
from app.domains.learning_media.schemas import (
    LearningMediaArtifact,
    LearningMediaAuthorization,
    LearningMediaUploadProjection,
    LearningMediaUploadSession,
    StartLearningMediaUploadCommand,
)
from app.platform.jobs import BackgroundJobRepository


UPLOAD_POLICY_VERSION = "reflection-voice-upload@v1"
RETENTION_POLICY_VERSION = "learner-media-retention@v1"
AUTHORIZATION_TTL = timedelta(minutes=15)
UNBOUND_RETENTION = timedelta(hours=24)
SCAN_JOB_TYPE = "learning_media_scan_v1"
RETENTION_CLEANUP_JOB_TYPE = "learning_media_retention_cleanup_v1"


class LearningMediaError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LearningMediaService:
    def __init__(
        self,
        *,
        storage: PrivateObjectStoragePort,
        repository: LearningMediaRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
    ) -> None:
        self._storage = storage
        self._repository = repository or LearningMediaRepository()
        self._jobs = jobs or BackgroundJobRepository()

    def start(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: StartLearningMediaUploadCommand,
        now: datetime | None = None,
    ) -> LearningMediaUploadProjection:
        started_at = _utc(now)
        fingerprint = _fingerprint("upload_start", command)
        self._repository.lock_user(session, user_id=user_id)
        existing = self._repository.command(
            session, user_id=user_id, command_id=command_id
        )
        if existing is not None:
            _require_replay(existing, "upload_start", fingerprint)
            row = self._required_upload(
                session,
                user_id=user_id,
                upload_session_id=str(existing["target_id"]),
            )
            return self._projection(session, row=row, replayed=True)

        upload_session_id = str(uuid4())
        artifact_id = str(uuid4())
        artifact_version_id = str(uuid4())
        extension = PurePath(command.original_filename).suffix.casefold()
        object_key = (
            f"private/learning-media/{user_id}/{artifact_id}/"
            f"{artifact_version_id}{extension}"
        )
        authorization_expires_at = started_at + AUTHORIZATION_TTL
        authorization = self._storage.authorize_single_put(
            object_key=object_key,
            mime_type=command.mime_type,
            byte_size=command.byte_size,
            content_sha256=command.content_sha256,
            expires_at=authorization_expires_at,
        )
        row = self._repository.insert_upload(
            session,
            values={
                "upload_session_id": upload_session_id,
                "artifact_id": artifact_id,
                "artifact_version_id": artifact_version_id,
                "user_id": user_id,
                "purpose": command.purpose,
                "original_filename": command.original_filename,
                "mime_type": command.mime_type,
                "byte_size": command.byte_size,
                "content_sha256": command.content_sha256,
                "duration_milliseconds": command.duration_milliseconds,
                "object_ref": authorization.object_ref,
                "upload_policy_version": UPLOAD_POLICY_VERSION,
                "retention_policy_version": RETENTION_POLICY_VERSION,
                "authorization_expires_at": authorization_expires_at,
                "created_at": started_at,
                "expires_at": started_at + UNBOUND_RETENTION,
            },
        )
        self._jobs.enqueue(
            session,
            job_key=f"learning-media-retention:{upload_session_id}",
            job_type=RETENTION_CLEANUP_JOB_TYPE,
            input_version="learning-media-retention-input@v1",
            payload={
                "uploadSessionId": upload_session_id,
                "userId": user_id,
                "artifactId": artifact_id,
                "artifactVersionId": artifact_version_id,
                "objectRef": authorization.object_ref,
                "expiresAt": row["expires_at"].isoformat(),
                "retentionPolicyVersion": RETENTION_POLICY_VERSION,
            },
            max_attempts=8,
            run_after=row["expires_at"],
        )
        self._repository.insert_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_type="upload_start",
            target_id=upload_session_id,
            request_fingerprint=fingerprint,
            created_at=started_at,
        )
        self._repository.append_audit(
            session,
            values={
                "event_id": str(uuid4()),
                "upload_session_id": upload_session_id,
                "user_id": user_id,
                "sequence_no": 1,
                "event_type": "learning-media-upload-authorized@v1",
                "from_state": None,
                "to_state": "awaiting_upload",
                "evidence": {
                    "artifactVersionId": artifact_version_id,
                    "contentSha256": command.content_sha256,
                    "uploadPolicyVersion": UPLOAD_POLICY_VERSION,
                },
                "occurred_at": started_at,
            },
        )
        return LearningMediaUploadProjection(
            session=_session_projection(row),
            authorization=_authorization_projection(authorization),
            artifact=None,
            replayed=False,
        )

    def complete(
        self,
        session: Session,
        *,
        user_id: str,
        upload_session_id: str,
        command_id: str,
        now: datetime | None = None,
    ) -> LearningMediaUploadProjection:
        completed_at = _utc(now)
        fingerprint = _hash(
            {"commandType": "upload_complete", "uploadSessionId": upload_session_id}
        )
        self._repository.lock_user(session, user_id=user_id)
        existing = self._repository.command(
            session, user_id=user_id, command_id=command_id
        )
        if existing is not None:
            _require_replay(existing, "upload_complete", fingerprint)
            if str(existing["target_id"]) != upload_session_id:
                raise _conflict("upload completion command targets another session")
            row = self._required_upload(
                session,
                user_id=user_id,
                upload_session_id=upload_session_id,
            )
            return self._projection(session, row=row, replayed=True)

        row = self._required_upload(
            session,
            user_id=user_id,
            upload_session_id=upload_session_id,
            for_update=True,
        )
        if row["state"] not in {"awaiting_upload", "scanning", "available"}:
            raise _conflict("learning media upload is already terminal")
        if row["state"] == "awaiting_upload":
            metadata = self._storage.head_private(object_ref=str(row["object_ref"]))
            _verify_object(row, metadata)
            transitioned = self._repository.transition(
                session,
                upload_session_id=upload_session_id,
                expected_revision=int(row["revision"]),
                expected_state="awaiting_upload",
                to_state="scanning",
                updated_at=completed_at,
            )
            if transitioned is None:
                raise _conflict("learning media upload revision changed")
            row = transitioned
            self._jobs.enqueue(
                session,
                job_key=(
                    f"learning-media-scan:{upload_session_id}:{row['content_sha256']}"
                ),
                job_type=SCAN_JOB_TYPE,
                input_version="learning-media-scan-input@v1",
                payload={
                    "uploadSessionId": upload_session_id,
                    "userId": user_id,
                    "artifactVersionId": str(row["artifact_version_id"]),
                    "objectRef": str(row["object_ref"]),
                    "contentSha256": str(row["content_sha256"]),
                    "mimeType": str(row["mime_type"]),
                },
                max_attempts=3,
                run_after=completed_at,
            )
            self._repository.append_audit(
                session,
                values={
                    "event_id": str(uuid4()),
                    "upload_session_id": upload_session_id,
                    "user_id": user_id,
                    "sequence_no": int(row["revision"]),
                    "event_type": "learning-media-scan-queued@v1",
                    "from_state": "awaiting_upload",
                    "to_state": "scanning",
                    "evidence": {"contentSha256": str(row["content_sha256"])},
                    "occurred_at": completed_at,
                },
            )
        self._repository.insert_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_type="upload_complete",
            target_id=upload_session_id,
            request_fingerprint=fingerprint,
            created_at=completed_at,
        )
        return self._projection(session, row=row, replayed=False)

    def get(
        self,
        session: Session,
        *,
        user_id: str,
        upload_session_id: str,
    ) -> LearningMediaUploadProjection:
        row = self._required_upload(
            session,
            user_id=user_id,
            upload_session_id=upload_session_id,
        )
        return self._projection(session, row=row, replayed=False)

    def _required_upload(
        self,
        session: Session,
        *,
        user_id: str,
        upload_session_id: str,
        for_update: bool = False,
    ) -> RowMapping:
        row = self._repository.upload(
            session,
            user_id=user_id,
            upload_session_id=upload_session_id,
            for_update=for_update,
        )
        if row is None:
            raise LearningMediaError(
                "learning_media_not_found", "录音上传不存在。", status_code=404
            )
        return row

    def _projection(
        self, session: Session, *, row: RowMapping, replayed: bool
    ) -> LearningMediaUploadProjection:
        authorization = None
        if row["state"] == "awaiting_upload":
            authorization = _authorization_projection(
                self._storage.authorize_single_put(
                    object_key=_object_key(str(row["object_ref"])),
                    mime_type=str(row["mime_type"]),
                    byte_size=int(row["byte_size"]),
                    content_sha256=str(row["content_sha256"]),
                    expires_at=row["authorization_expires_at"],
                )
            )
        artifact_row = self._repository.artifact_for_upload(
            session, upload_session_id=str(row["upload_session_id"])
        )
        return LearningMediaUploadProjection(
            session=_session_projection(row),
            authorization=authorization,
            artifact=_artifact_projection(artifact_row) if artifact_row else None,
            replayed=replayed,
        )


def _session_projection(row: RowMapping) -> LearningMediaUploadSession:
    return LearningMediaUploadSession(
        uploadSessionId=str(row["upload_session_id"]),
        artifactId=str(row["artifact_id"]),
        artifactVersionId=str(row["artifact_version_id"]),
        state=str(row["state"]),
        revision=int(row["revision"]),
        purpose=str(row["purpose"]),
        originalFilename=str(row["original_filename"]),
        mimeType=str(row["mime_type"]),
        byteSize=int(row["byte_size"]),
        contentSha256=str(row["content_sha256"]),
        durationMilliseconds=int(row["duration_milliseconds"]),
        failureCode=str(row["failure_code"]) if row["failure_code"] else None,
        expiresAt=row["expires_at"],
        updatedAt=row["updated_at"],
    )


def _artifact_projection(row: RowMapping) -> LearningMediaArtifact:
    return LearningMediaArtifact(
        artifactId=str(row["artifact_id"]),
        artifactVersionId=str(row["artifact_version_id"]),
        versionNo=int(row["version_no"]),
        purpose=str(row["purpose"]),
        state=str(row["state"]),
        mimeType=str(row["mime_type"]),
        byteSize=int(row["byte_size"]),
        contentSha256=str(row["content_sha256"]),
        durationMilliseconds=int(row["duration_milliseconds"]),
        retentionPolicyVersion=str(row["retention_policy_version"]),
        createdAt=row["created_at"],
    )


def _authorization_projection(value: Any) -> LearningMediaAuthorization:
    return LearningMediaAuthorization(
        objectRef=value.object_ref,
        url=value.url,
        requiredHeaders=value.required_headers,
        expiresAt=value.expires_at,
    )


def _verify_object(row: RowMapping, metadata: ObjectMetadata) -> None:
    expected = (
        metadata.object_ref == str(row["object_ref"]),
        metadata.byte_size == int(row["byte_size"]),
        metadata.content_sha256 == str(row["content_sha256"]),
        metadata.mime_type == str(row["mime_type"]),
        metadata.access == "private",
        metadata.server_side_encrypted,
    )
    if not all(expected):
        raise LearningMediaError(
            "learning_media_integrity_mismatch",
            "录音对象完整性校验失败，请重新录制。",
            status_code=409,
        )


def _require_replay(row: RowMapping, command_type: str, fingerprint: str) -> None:
    if row["command_type"] != command_type or row["request_fingerprint"] != fingerprint:
        raise _conflict("idempotency key was already used for another media command")


def _fingerprint(command_type: str, command: StartLearningMediaUploadCommand) -> str:
    return _hash(
        {
            "commandType": command_type,
            "payload": command.model_dump(by_alias=True, mode="json"),
        }
    )


def _hash(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _object_key(object_ref: str) -> str:
    marker = "/private/"
    if not object_ref.startswith("tos://") or marker not in object_ref:
        raise LearningMediaError(
            "learning_media_object_ref_invalid",
            "录音对象引用无效。",
            status_code=500,
        )
    return "private/" + object_ref.split(marker, 1)[1]


def _conflict(message: str) -> LearningMediaError:
    return LearningMediaError("learning_media_conflict", message, status_code=409)


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("learning-media timestamps require timezone")
    return current.astimezone(UTC)
