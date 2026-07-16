from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class LearningMediaRepository:
    def lock_user(self, session: Session, *, user_id: str) -> None:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:user_id, 0))"),
            {"user_id": user_id},
        )

    def command(
        self, session: Session, *, user_id: str, command_id: str
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    "select * from learning.learning_media_commands "
                    "where user_id = cast(:user_id as uuid) and command_id = :command_id"
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )

    def insert_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command_type: str,
        target_id: str,
        request_fingerprint: str,
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.learning_media_commands (
                  user_id, command_id, command_type, target_id,
                  request_fingerprint, created_at
                ) values (
                  cast(:user_id as uuid), :command_id, :command_type,
                  cast(:target_id as uuid), :request_fingerprint, :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "command_type": command_type,
                "target_id": target_id,
                "request_fingerprint": request_fingerprint,
                "created_at": created_at,
            },
        )

    def insert_upload(
        self, session: Session, *, values: dict[str, Any]
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into learning.learning_media_upload_sessions (
                      upload_session_id, artifact_id, artifact_version_id, user_id,
                      purpose, state, revision, original_filename, mime_type,
                      byte_size, content_sha256, duration_milliseconds, object_ref,
                      upload_policy_version, retention_policy_version,
                      authorization_expires_at, created_at, updated_at, expires_at
                    ) values (
                      cast(:upload_session_id as uuid), cast(:artifact_id as uuid),
                      cast(:artifact_version_id as uuid), cast(:user_id as uuid),
                      :purpose, 'awaiting_upload', 1, :original_filename, :mime_type,
                      :byte_size, :content_sha256, :duration_milliseconds, :object_ref,
                      :upload_policy_version, :retention_policy_version,
                      :authorization_expires_at, :created_at, :created_at, :expires_at
                    ) returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def upload(
        self,
        session: Session,
        *,
        user_id: str,
        upload_session_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        suffix = " for update" if for_update else ""
        return (
            session.execute(
                text(
                    "select * from learning.learning_media_upload_sessions "
                    "where upload_session_id = cast(:upload_session_id as uuid) "
                    "and user_id = cast(:user_id as uuid)" + suffix
                ),
                {"upload_session_id": upload_session_id, "user_id": user_id},
            )
            .mappings()
            .one_or_none()
        )

    def upload_by_id(
        self,
        session: Session,
        *,
        upload_session_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        suffix = " for update" if for_update else ""
        return (
            session.execute(
                text(
                    "select * from learning.learning_media_upload_sessions "
                    "where upload_session_id = cast(:upload_session_id as uuid)" + suffix
                ),
                {"upload_session_id": upload_session_id},
            )
            .mappings()
            .one_or_none()
        )

    def transition(
        self,
        session: Session,
        *,
        upload_session_id: str,
        expected_revision: int,
        expected_state: str,
        to_state: str,
        updated_at: datetime,
        scanner_receipt: dict[str, Any] | None = None,
        failure_code: str | None = None,
        completed_at: datetime | None = None,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    update learning.learning_media_upload_sessions
                    set state = :to_state,
                        revision = revision + 1,
                        scanner_receipt = cast(:scanner_receipt as jsonb),
                        failure_code = :failure_code,
                        completed_at = :completed_at,
                        updated_at = :updated_at
                    where upload_session_id = cast(:upload_session_id as uuid)
                      and revision = :expected_revision and state = :expected_state
                    returning *
                    """
                ),
                {
                    "upload_session_id": upload_session_id,
                    "expected_revision": expected_revision,
                    "expected_state": expected_state,
                    "to_state": to_state,
                    "scanner_receipt": (
                        json.dumps(scanner_receipt, ensure_ascii=False, sort_keys=True)
                        if scanner_receipt is not None
                        else None
                    ),
                    "failure_code": failure_code,
                    "completed_at": completed_at,
                    "updated_at": updated_at,
                },
            )
            .mappings()
            .one_or_none()
        )

    def expire_unbound_upload(
        self,
        session: Session,
        *,
        upload_session_id: str,
        expected_revision: int,
        expected_state: str,
        expired_at: datetime,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    update learning.learning_media_upload_sessions upload
                    set state = 'expired',
                        revision = revision + 1,
                        failure_code = 'unbound_retention_expired',
                        updated_at = :expired_at
                    where upload.upload_session_id = cast(:upload_session_id as uuid)
                      and upload.revision = :expected_revision
                      and upload.state = :expected_state
                      and upload.expires_at <= :expired_at
                      and not exists (
                        select 1
                        from learning.learning_media_bindings binding
                        where binding.artifact_version_id = upload.artifact_version_id
                      )
                    returning *
                    """
                ),
                {
                    "upload_session_id": upload_session_id,
                    "expected_revision": expected_revision,
                    "expected_state": expected_state,
                    "expired_at": expired_at,
                },
            )
            .mappings()
            .one_or_none()
        )

    def append_audit(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.learning_media_audit_events (
                  event_id, upload_session_id, user_id, sequence_no, event_type,
                  from_state, to_state, evidence, occurred_at
                ) values (
                  cast(:event_id as uuid), cast(:upload_session_id as uuid),
                  cast(:user_id as uuid), :sequence_no, :event_type, :from_state,
                  :to_state, cast(:evidence as jsonb), :occurred_at
                )
                """
            ),
            {
                **values,
                "evidence": json.dumps(
                    values["evidence"], ensure_ascii=False, sort_keys=True
                ),
            },
        )

    def insert_artifact(
        self, session: Session, *, values: dict[str, Any]
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into learning.learning_media_artifact_versions (
                      artifact_version_id, artifact_id, user_id, version_no,
                      purpose, state, object_ref, mime_type, byte_size,
                      content_sha256, duration_milliseconds, scanner_receipt,
                      retention_policy_version, source_upload_session_id,
                      supersedes_artifact_version_id, created_at
                    ) values (
                      cast(:artifact_version_id as uuid), cast(:artifact_id as uuid),
                      cast(:user_id as uuid), 1, :purpose, 'available', :object_ref,
                      :mime_type, :byte_size, :content_sha256, :duration_milliseconds,
                      cast(:scanner_receipt as jsonb), :retention_policy_version,
                      cast(:source_upload_session_id as uuid), null, :created_at
                    ) returning *
                    """
                ),
                {
                    **values,
                    "scanner_receipt": json.dumps(
                        values["scanner_receipt"], ensure_ascii=False, sort_keys=True
                    ),
                },
            )
            .mappings()
            .one()
        )

    def insert_deleted_successor(
        self,
        session: Session,
        *,
        source: RowMapping,
        artifact_version_id: str,
        created_at: datetime,
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into learning.learning_media_artifact_versions (
                      artifact_version_id, artifact_id, user_id, version_no,
                      purpose, state, object_ref, mime_type, byte_size,
                      content_sha256, duration_milliseconds, scanner_receipt,
                      retention_policy_version, source_upload_session_id,
                      supersedes_artifact_version_id, created_at
                    ) values (
                      cast(:artifact_version_id as uuid), cast(:artifact_id as uuid),
                      cast(:user_id as uuid), :version_no, :purpose, 'deleted', null,
                      :mime_type, :byte_size, :content_sha256,
                      :duration_milliseconds, cast(:scanner_receipt as jsonb),
                      :retention_policy_version,
                      cast(:source_upload_session_id as uuid),
                      cast(:supersedes_artifact_version_id as uuid), :created_at
                    ) returning *
                    """
                ),
                {
                    "artifact_version_id": artifact_version_id,
                    "artifact_id": str(source["artifact_id"]),
                    "user_id": str(source["user_id"]),
                    "version_no": int(source["version_no"]) + 1,
                    "purpose": str(source["purpose"]),
                    "mime_type": str(source["mime_type"]),
                    "byte_size": int(source["byte_size"]),
                    "content_sha256": str(source["content_sha256"]),
                    "duration_milliseconds": int(source["duration_milliseconds"]),
                    "scanner_receipt": json.dumps(
                        dict(source["scanner_receipt"]),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "retention_policy_version": str(
                        source["retention_policy_version"]
                    ),
                    "source_upload_session_id": str(
                        source["source_upload_session_id"]
                    ),
                    "supersedes_artifact_version_id": str(
                        source["artifact_version_id"]
                    ),
                    "created_at": created_at,
                },
            )
            .mappings()
            .one()
        )

    def artifact_by_version(
        self,
        session: Session,
        *,
        artifact_version_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        suffix = " for update" if for_update else ""
        return (
            session.execute(
                text(
                    "select * from learning.learning_media_artifact_versions "
                    "where artifact_version_id = cast(:artifact_version_id as uuid)"
                    + suffix
                ),
                {"artifact_version_id": artifact_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def artifact_successor(
        self,
        session: Session,
        *,
        artifact_version_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    "select * from learning.learning_media_artifact_versions "
                    "where supersedes_artifact_version_id = "
                    "cast(:artifact_version_id as uuid)"
                ),
                {"artifact_version_id": artifact_version_id},
            )
            .mappings()
            .one_or_none()
        )

    def artifact_is_bound(
        self,
        session: Session,
        *,
        artifact_version_id: str,
    ) -> bool:
        return bool(
            session.execute(
                text(
                    "select exists ("
                    "select 1 from learning.learning_media_bindings "
                    "where artifact_version_id = cast(:artifact_version_id as uuid)"
                    ")"
                ),
                {"artifact_version_id": artifact_version_id},
            ).scalar_one()
        )

    def artifact_for_upload(
        self, session: Session, *, upload_session_id: str
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    "select * from learning.learning_media_artifact_versions "
                    "where source_upload_session_id = cast(:upload_session_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"upload_session_id": upload_session_id},
            )
            .mappings()
            .one_or_none()
        )

    def bind_available_artifacts(
        self,
        session: Session,
        *,
        user_id: str,
        artifact_version_ids: list[str],
        entity_type: str,
        entity_version_id: str,
        created_at: datetime,
    ) -> None:
        if len(artifact_version_ids) != len(set(artifact_version_ids)):
            raise ValueError("voice artifact versions must be unique")
        for artifact_version_id in artifact_version_ids:
            row = session.execute(
                text(
                    """
                    select artifact_version_id, artifact_id
                    from learning.learning_media_artifact_versions artifact
                    where artifact.artifact_version_id = cast(:artifact_version_id as uuid)
                      and artifact.user_id = cast(:user_id as uuid)
                      and artifact.state = 'available'
                      and artifact.purpose = 'reflection_voice'
                      and not exists (
                        select 1 from learning.learning_media_artifact_versions successor
                        where successor.supersedes_artifact_version_id = artifact.artifact_version_id
                      )
                    for share
                    """
                ),
                {"artifact_version_id": artifact_version_id, "user_id": user_id},
            ).mappings().one_or_none()
            if row is None:
                raise ValueError("voice artifact is unavailable or owned by another user")
            session.execute(
                text(
                    """
                    insert into learning.learning_media_bindings (
                      binding_id, user_id, artifact_version_id, artifact_id,
                      entity_type, entity_version_id, purpose, created_at
                    ) values (
                      gen_random_uuid(), cast(:user_id as uuid),
                      cast(:artifact_version_id as uuid), cast(:artifact_id as uuid),
                      :entity_type, cast(:entity_version_id as uuid),
                      'reflection_voice', :created_at
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "artifact_version_id": artifact_version_id,
                    "artifact_id": str(row["artifact_id"]),
                    "entity_type": entity_type,
                    "entity_version_id": entity_version_id,
                    "created_at": created_at,
                },
            )
