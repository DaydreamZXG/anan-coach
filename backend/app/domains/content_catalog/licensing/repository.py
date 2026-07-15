from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    ContentLicenseRecord,
    LicenseDecision,
    canonical_record_hash,
)


@dataclass(frozen=True)
class StoredCommand:
    request_hash: str
    response_payload: dict[str, object]


@dataclass(frozen=True)
class StoredLicenseHead:
    record_version_id: UUID
    record: ContentLicenseRecord


class ContentLicenseRepository:
    def lock(self, session: Session, *, key: str) -> None:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    def command_by_key(
        self,
        session: Session,
        *,
        actor_id: str,
        command_type: str,
        idempotency_key: str,
    ) -> StoredCommand | None:
        row = session.execute(
            text(
                """
                select request_hash, response_payload
                from product.content_license_commands
                where actor_id = :actor_id
                  and command_type = :command_type
                  and idempotency_key = :idempotency_key
                """
            ),
            {
                "actor_id": actor_id,
                "command_type": command_type,
                "idempotency_key": idempotency_key,
            },
        ).mappings().one_or_none()
        if row is None:
            return None
        return StoredCommand(
            request_hash=str(row["request_hash"]),
            response_payload=dict(row["response_payload"]),
        )

    def head(
        self,
        session: Session,
        *,
        content_license_id: UUID,
    ) -> StoredLicenseHead | None:
        row = session.execute(
            text(
                """
                select version.record_version_id, version.record_payload
                from product.content_license_heads head
                join product.content_license_record_versions version
                  on version.record_version_id = head.record_version_id
                where head.content_license_id = :content_license_id
                """
            ),
            {"content_license_id": str(content_license_id)},
        ).mappings().one_or_none()
        if row is None:
            return None
        record = ContentLicenseRecord.model_validate(row["record_payload"])
        if canonical_record_hash(record) != record.record_canonical_hash:
            raise RuntimeError("stored content-license record hash is invalid")
        return StoredLicenseHead(
            record_version_id=row["record_version_id"],
            record=record,
        )

    def next_authorization_epoch(
        self,
        session: Session,
        *,
        content_version_id: str,
    ) -> int:
        value = session.execute(
            text(
                """
                select coalesce(max(authorization_epoch), 0) + 1
                from product.content_license_record_versions
                where content_version_id = :content_version_id
                """
            ),
            {"content_version_id": content_version_id},
        ).scalar_one()
        return int(value)

    def insert_version(
        self,
        session: Session,
        *,
        record: ContentLicenseRecord,
        predecessor: StoredLicenseHead | None,
    ) -> UUID:
        record_version_id = uuid4()
        payload = record.model_dump(by_alias=True, mode="json")
        session.execute(
            text(
                """
                insert into product.content_license_record_versions (
                  record_version_id, content_license_id, content_version_id,
                  revision, authorization_epoch, state, created_by_actor_id,
                  closed_coverage_hash, evidence_manifest_hash,
                  grant_matrix_hash, record_canonical_hash, record_payload,
                  reviewer_receipt, supersedes_content_license_id,
                  supersedes_record_version_id,
                  created_at, updated_at
                ) values (
                  :record_version_id, :content_license_id, :content_version_id,
                  :revision, :authorization_epoch, :state, :created_by_actor_id,
                  :closed_coverage_hash, :evidence_manifest_hash,
                  :grant_matrix_hash, :record_canonical_hash,
                  cast(:record_payload as jsonb), cast(:reviewer_receipt as jsonb),
                  :supersedes_content_license_id, :supersedes_record_version_id,
                  :created_at, :updated_at
                )
                """
            ),
            {
                "record_version_id": str(record_version_id),
                "content_license_id": str(record.content_license_id),
                "content_version_id": record.content_version_id,
                "revision": record.revision,
                "authorization_epoch": record.authorization_epoch,
                "state": record.state.value,
                "created_by_actor_id": record.created_by_actor_id,
                "closed_coverage_hash": record.closed_coverage_hash,
                "evidence_manifest_hash": record.evidence_manifest_hash,
                "grant_matrix_hash": record.rights.grant_matrix.matrix_hash,
                "record_canonical_hash": record.record_canonical_hash,
                "record_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "reviewer_receipt": (
                    json.dumps(
                        record.reviewer_receipt.model_dump(by_alias=True, mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if record.reviewer_receipt is not None
                    else None
                ),
                "supersedes_content_license_id": (
                    str(record.supersedes_content_license_id)
                    if record.supersedes_content_license_id
                    else None
                ),
                "supersedes_record_version_id": (
                    str(predecessor.record_version_id) if predecessor else None
                ),
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            },
        )
        row = session.execute(
            text(
                """
                insert into product.content_license_heads (
                  content_license_id, content_version_id, record_version_id,
                  revision, authorization_epoch, state, updated_at
                ) values (
                  :content_license_id, :content_version_id, :record_version_id,
                  :revision, :authorization_epoch, :state, :updated_at
                )
                on conflict (content_license_id) do update
                set content_version_id = excluded.content_version_id,
                    record_version_id = excluded.record_version_id,
                    revision = excluded.revision,
                    authorization_epoch = excluded.authorization_epoch,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                where product.content_license_heads.revision = excluded.revision - 1
                returning record_version_id
                """
            ),
            {
                "content_license_id": str(record.content_license_id),
                "content_version_id": record.content_version_id,
                "record_version_id": str(record_version_id),
                "revision": record.revision,
                "authorization_epoch": record.authorization_epoch,
                "state": record.state.value,
                "updated_at": record.updated_at,
            },
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError("content-license head compare-and-swap failed")
        return record_version_id

    def insert_command(
        self,
        session: Session,
        *,
        actor_id: str,
        command_type: str,
        idempotency_key: str,
        content_license_id: UUID,
        request_hash: str,
        response_payload: dict[str, object],
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into product.content_license_commands (
                  command_id, actor_id, command_type, idempotency_key,
                  content_license_id, request_hash, response_payload, created_at
                ) values (
                  :command_id, :actor_id, :command_type, :idempotency_key,
                  :content_license_id, :request_hash,
                  cast(:response_payload as jsonb), :created_at
                )
                """
            ),
            {
                "command_id": str(uuid4()),
                "actor_id": actor_id,
                "command_type": command_type,
                "idempotency_key": idempotency_key,
                "content_license_id": str(content_license_id),
                "request_hash": request_hash,
                "response_payload": json.dumps(
                    response_payload, ensure_ascii=False, sort_keys=True
                ),
                "created_at": created_at,
            },
        )

    def decision_by_key(
        self,
        session: Session,
        *,
        snapshot_key: str,
    ) -> LicenseDecision | None:
        payload = session.execute(
            text(
                """
                select decision_payload
                from product.content_license_authorization_snapshots
                where snapshot_key = :snapshot_key
                """
            ),
            {"snapshot_key": snapshot_key},
        ).scalar_one_or_none()
        return LicenseDecision.model_validate(payload) if payload is not None else None

    def decision_by_id(
        self,
        session: Session,
        *,
        snapshot_id: UUID,
    ) -> LicenseDecision | None:
        payload = session.execute(
            text(
                """
                select decision_payload
                from product.content_license_authorization_snapshots
                where snapshot_id = :snapshot_id
                """
            ),
            {"snapshot_id": str(snapshot_id)},
        ).scalar_one_or_none()
        return LicenseDecision.model_validate(payload) if payload is not None else None

    def insert_decision(
        self,
        session: Session,
        *,
        snapshot_key: str,
        request_payload: dict[str, object],
        decision: LicenseDecision,
    ) -> None:
        decision_payload = decision.model_dump(by_alias=True, mode="json")
        session.execute(
            text(
                """
                insert into product.content_license_authorization_snapshots (
                  snapshot_id, snapshot_key, content_license_id, content_version_id,
                  license_revision, authorization_epoch, request_payload,
                  decision_payload, allowed, reason, evaluated_at
                ) values (
                  :snapshot_id, :snapshot_key, :content_license_id,
                  :content_version_id, :license_revision, :authorization_epoch,
                  cast(:request_payload as jsonb), cast(:decision_payload as jsonb),
                  :allowed, :reason, :evaluated_at
                )
                """
            ),
            {
                "snapshot_id": str(decision.snapshot_id),
                "snapshot_key": snapshot_key,
                "content_license_id": str(decision.content_license_id),
                "content_version_id": decision.content_version_id,
                "license_revision": decision.license_revision,
                "authorization_epoch": decision.authorization_epoch,
                "request_payload": json.dumps(
                    request_payload, ensure_ascii=False, sort_keys=True
                ),
                "decision_payload": json.dumps(
                    decision_payload, ensure_ascii=False, sort_keys=True
                ),
                "allowed": decision.allowed,
                "reason": decision.reason,
                "evaluated_at": decision.evaluated_at,
            },
        )
