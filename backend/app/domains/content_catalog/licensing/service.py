from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Callable, Literal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    ContentLicenseRecord,
    LicenseDecision,
    LicenseSnapshotStatus,
    LicenseState,
    LicenseUseRequest,
    ReviewerReceipt,
    canonical_record_hash,
)
from app.domains.content_catalog.licensing.repository import (
    ContentLicenseRepository,
    StoredLicenseHead,
)
from app.platform.outbox import OutboxRepository


class ContentLicenseNotFound(LookupError):
    pass


class ContentLicenseVersionConflict(RuntimeError):
    pass


class ContentLicenseIdempotencyConflict(RuntimeError):
    pass


class ContentLicenseTransitionError(RuntimeError):
    pass


TransitionName = Literal[
    "review_submit",
    "activate",
    "reject",
    "suspend",
    "reinstate",
    "expire",
    "withdraw",
    "supersede",
    "abandon",
]


class ContentLicenseService:
    """Canonical content-license owner.

    Methods participate in the caller's SQL transaction. The record version, head,
    command receipt, authorization epoch and outbox facts are written atomically.
    """

    def __init__(
        self,
        *,
        session: Session,
        repository: ContentLicenseRepository | None = None,
        outbox: OutboxRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or ContentLicenseRepository()
        self._outbox = outbox or OutboxRepository()
        self._clock = clock or (lambda: datetime.now(UTC))

    def save_draft(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        expected_revision: int,
        record: ContentLicenseRecord,
    ) -> ContentLicenseRecord:
        if record.state != LicenseState.DRAFT:
            raise ContentLicenseTransitionError("draft_save requires a draft record")
        if record.content_license_id is None:
            raise ContentLicenseTransitionError("contentLicenseId is required")
        request = {
            "expectedRevision": expected_revision,
            "record": record.model_dump(by_alias=True, mode="json"),
        }
        replay, request_hash = self._begin(
            actor_id=actor_id,
            command_type="draft_save",
            idempotency_key=idempotency_key,
            content_license_id=record.content_license_id,
            request=request,
        )
        if replay is not None:
            return replay

        current = self._repository.head(
            self._session, content_license_id=record.content_license_id
        )
        if current is None:
            if expected_revision != 0 or record.revision != 1:
                raise ContentLicenseVersionConflict(
                    "new content-license records require expected revision zero"
                )
            if record.created_by_actor_id != actor_id:
                raise ContentLicenseTransitionError(
                    "the authenticated actor must own the initial draft"
                )
            if record.authorization_epoch != 0 or record.reviewer_receipt is not None:
                raise ContentLicenseTransitionError(
                    "drafts cannot create authorization authority"
                )
            if record.supersedes_content_license_id is not None:
                replaced = self._repository.head(
                    self._session,
                    content_license_id=record.supersedes_content_license_id,
                )
                if replaced is None:
                    raise ContentLicenseTransitionError(
                        "replacement draft refers to an unknown license"
                    )
                if replaced.record.content_version_id != record.content_version_id:
                    raise ContentLicenseTransitionError(
                        "replacement draft must govern the same content version"
                    )
            now = self._clock()
            prepared = _sealed(
                record.model_copy(update={"created_at": now, "updated_at": now})
            )
        else:
            self._require_revision(current.record, expected_revision)
            if current.record.state not in {LicenseState.DRAFT, LicenseState.REJECTED}:
                raise ContentLicenseTransitionError(
                    f"cannot revise a {current.record.state.value} record as draft"
                )
            if record.revision != current.record.revision + 1:
                raise ContentLicenseVersionConflict("draft revision must be contiguous")
            if record.content_version_id != current.record.content_version_id:
                raise ContentLicenseTransitionError(
                    "a license aggregate cannot change contentVersionId"
                )
            if record.created_by_actor_id != current.record.created_by_actor_id:
                raise ContentLicenseTransitionError("createdByActorId is immutable")
            if (
                record.supersedes_content_license_id
                != current.record.supersedes_content_license_id
            ):
                raise ContentLicenseTransitionError(
                    "supersedesContentLicenseId is immutable"
                )
            prepared = _sealed(
                record.model_copy(
                    update={
                        "authorization_epoch": 0,
                        "reviewer_receipt": None,
                        "created_at": current.record.created_at,
                        "updated_at": self._clock(),
                    }
                )
            )
        return self._persist(
            actor_id=actor_id,
            command_type="draft_save",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            record=prepared,
            predecessor=current,
        )

    def submit_review(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
    ) -> ContentLicenseRecord:
        return self._simple_transition(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            expected_revision=expected_revision,
            command_type="review_submit",
            allowed_from={LicenseState.DRAFT},
            target_state=LicenseState.IN_REVIEW,
        )

    def activate(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        reviewer_receipt: ReviewerReceipt,
    ) -> ContentLicenseRecord:
        return self._review_transition(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            expected_revision=expected_revision,
            command_type="activate",
            target_state=LicenseState.ACTIVE,
            receipt=reviewer_receipt,
            receipt_decision="approved",
            increment_epoch=True,
        )

    def reject(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        reviewer_receipt: ReviewerReceipt,
    ) -> ContentLicenseRecord:
        return self._review_transition(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            expected_revision=expected_revision,
            command_type="reject",
            target_state=LicenseState.REJECTED,
            receipt=reviewer_receipt,
            receipt_decision="rejected",
            increment_epoch=False,
        )

    def suspend(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        reason_code: str,
    ) -> ContentLicenseRecord:
        return self._simple_transition(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            expected_revision=expected_revision,
            command_type="suspend",
            allowed_from={LicenseState.ACTIVE},
            target_state=LicenseState.SUSPENDED,
            reason_code=reason_code,
            increment_epoch=True,
        )

    def reinstate(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        reviewer_receipt: ReviewerReceipt,
    ) -> ContentLicenseRecord:
        return self._review_transition(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            expected_revision=expected_revision,
            command_type="reinstate",
            target_state=LicenseState.ACTIVE,
            receipt=reviewer_receipt,
            receipt_decision="approved",
            allowed_from={LicenseState.SUSPENDED},
            increment_epoch=True,
        )

    def expire(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
    ) -> ContentLicenseRecord:
        return self._simple_transition(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            expected_revision=expected_revision,
            command_type="expire",
            allowed_from={LicenseState.ACTIVE, LicenseState.SUSPENDED},
            target_state=LicenseState.EXPIRED,
            increment_epoch=True,
            record_guard=self._require_ended_term,
        )

    def withdraw(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        evidence_ref: str,
    ) -> ContentLicenseRecord:
        return self._simple_transition(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            expected_revision=expected_revision,
            command_type="withdraw",
            allowed_from={LicenseState.ACTIVE, LicenseState.SUSPENDED},
            target_state=LicenseState.WITHDRAWN,
            reason_code=evidence_ref,
            increment_epoch=True,
        )

    def abandon(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        reason_code: str,
    ) -> ContentLicenseRecord:
        return self._simple_transition(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            expected_revision=expected_revision,
            command_type="abandon",
            allowed_from={
                LicenseState.DRAFT,
                LicenseState.IN_REVIEW,
                LicenseState.REJECTED,
            },
            target_state=LicenseState.ABANDONED,
            reason_code=reason_code,
        )

    def supersede(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        replacement_content_license_id: UUID,
    ) -> ContentLicenseRecord:
        request = {
            "contentLicenseId": str(content_license_id),
            "expectedRevision": expected_revision,
            "replacementContentLicenseId": str(replacement_content_license_id),
        }
        replay, request_hash = self._begin(
            actor_id=actor_id,
            command_type="supersede",
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            request=request,
            aggregate_already_locked=True,
        )
        if replay is not None:
            return replay
        for aggregate_id in sorted({str(content_license_id), str(replacement_content_license_id)}):
            self._repository.lock(self._session, key=f"content-license:{aggregate_id}")
        current = self._repository.head(
            self._session, content_license_id=content_license_id
        )
        replacement = self._repository.head(
            self._session, content_license_id=replacement_content_license_id
        )
        if current is None or replacement is None:
            raise ContentLicenseNotFound("license or replacement does not exist")
        if replacement.record.state != LicenseState.ACTIVE:
            raise ContentLicenseTransitionError("replacement license is not active")
        if replacement.record.content_version_id != current.record.content_version_id:
            raise ContentLicenseTransitionError("replacement must govern the same content")
        if replacement.record.supersedes_content_license_id != content_license_id:
            raise ContentLicenseTransitionError(
                "replacement lineage does not point to the superseded license"
            )
        if replacement.record.authorization_epoch <= current.record.authorization_epoch:
            raise ContentLicenseTransitionError(
                "replacement authorization epoch must be higher"
            )
        self._require_revision(current.record, expected_revision)
        if current.record.state not in {
            LicenseState.ACTIVE,
            LicenseState.SUSPENDED,
            LicenseState.EXPIRED,
            LicenseState.WITHDRAWN,
        }:
            raise ContentLicenseTransitionError(
                f"supersede cannot transition from {current.record.state.value}"
            )
        next_epoch = current.record.authorization_epoch
        if current.record.state in {LicenseState.ACTIVE, LicenseState.SUSPENDED}:
            next_epoch = self._allocate_epoch(current.record)
        record = _next_record(
            current.record,
            state=LicenseState.SUPERSEDED,
            authorization_epoch=next_epoch,
            updated_at=self._clock(),
        )
        return self._persist(
            actor_id=actor_id,
            command_type="supersede",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            record=record,
            predecessor=current,
            reason_code=str(replacement_content_license_id),
        )

    def evaluate(self, request: LicenseUseRequest) -> LicenseDecision:
        request_payload = request.model_dump(by_alias=True, mode="json")
        self._repository.lock(
            self._session,
            key=f"content-license:{request.content_license_id}",
        )
        current = self._repository.head(
            self._session, content_license_id=request.content_license_id
        )
        snapshot_material = {
            "request": request_payload,
            "licenseRevision": current.record.revision if current else None,
            "authorizationEpoch": (
                current.record.authorization_epoch if current else None
            ),
        }
        snapshot_key = _fingerprint(snapshot_material)
        self._repository.lock(self._session, key=f"license-snapshot:{snapshot_key}")
        replay = self._repository.decision_by_key(
            self._session, snapshot_key=snapshot_key
        )
        if replay is not None:
            return replay

        reason = self._decision_reason(current, request)
        record = current.record if current else None
        decision = LicenseDecision(
            snapshotId=uuid4(),
            allowed=reason == "explicit_grant",
            reason=reason,
            contentLicenseId=request.content_license_id,
            contentVersionId=request.content_version_id,
            licenseRevision=record.revision if record else None,
            authorizationEpoch=(
                record.authorization_epoch
                if record and record.authorization_epoch >= 1
                else None
            ),
            grantMatrixHash=(
                record.rights.grant_matrix.matrix_hash if record else None
            ),
            evidenceManifestHash=record.evidence_manifest_hash if record else None,
            evaluatedAt=self._clock(),
        )
        self._repository.insert_decision(
            self._session,
            snapshot_key=snapshot_key,
            request_payload=request_payload,
            decision=decision,
        )
        return decision

    def snapshot_status(self, *, snapshot_id: UUID) -> LicenseSnapshotStatus:
        decision = self._repository.decision_by_id(
            self._session, snapshot_id=snapshot_id
        )
        if decision is None:
            raise ContentLicenseNotFound(f"authorization snapshot {snapshot_id}")
        self._repository.lock(
            self._session,
            key=f"content-license:{decision.content_license_id}",
        )
        current = self._repository.head(
            self._session, content_license_id=decision.content_license_id
        )
        reason = self._snapshot_status_reason(decision, current)
        return LicenseSnapshotStatus(
            snapshotId=snapshot_id,
            valid=reason == "current",
            reason=reason,
            boundAuthorizationEpoch=decision.authorization_epoch,
            currentAuthorizationEpoch=(
                current.record.authorization_epoch
                if current and current.record.authorization_epoch >= 1
                else None
            ),
            checkedAt=self._clock(),
        )

    def _snapshot_status_reason(
        self,
        decision: LicenseDecision,
        current: StoredLicenseHead | None,
    ) -> Literal[
        "current",
        "snapshot_denied",
        "license_missing",
        "content_version_mismatch",
        "license_not_active",
        "authorization_epoch_changed",
        "term_not_started",
        "term_expired",
    ]:
        if not decision.allowed:
            return "snapshot_denied"
        if current is None:
            return "license_missing"
        record = current.record
        if decision.content_version_id != record.content_version_id:
            return "content_version_mismatch"
        if record.state != LicenseState.ACTIVE:
            return "license_not_active"
        if decision.authorization_epoch != record.authorization_epoch:
            return "authorization_epoch_changed"
        now = self._clock()
        term = record.rights.term
        if now < term.effective_at:
            return "term_not_started"
        if isinstance(term.expires_at, datetime) and now >= term.expires_at:
            return "term_expired"
        return "current"

    def _decision_reason(
        self,
        current: StoredLicenseHead | None,
        request: LicenseUseRequest,
    ) -> Literal[
        "explicit_grant",
        "license_unknown",
        "content_version_mismatch",
        "license_not_active",
        "authorization_epoch_stale",
        "term_not_started",
        "term_expired",
        "default_deny",
    ]:
        if current is None:
            return "license_unknown"
        record = current.record
        if request.content_version_id != record.content_version_id:
            return "content_version_mismatch"
        if record.state != LicenseState.ACTIVE:
            return "license_not_active"
        if (
            request.expected_authorization_epoch is not None
            and request.expected_authorization_epoch != record.authorization_epoch
        ):
            return "authorization_epoch_stale"
        term = record.rights.term
        if request.requested_at < term.effective_at:
            return "term_not_started"
        if isinstance(term.expires_at, datetime) and request.requested_at >= term.expires_at:
            return "term_expired"
        allowed = {
            cell.tuple_key() for cell in record.rights.grant_matrix.allowed_grant_cells
        }
        return "explicit_grant" if request.tuple_key() in allowed else "default_deny"

    def _simple_transition(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        command_type: TransitionName,
        allowed_from: set[LicenseState],
        target_state: LicenseState,
        reason_code: str | None = None,
        increment_epoch: bool = False,
        record_guard: Callable[[ContentLicenseRecord], None] | None = None,
    ) -> ContentLicenseRecord:
        request = {
            "contentLicenseId": str(content_license_id),
            "expectedRevision": expected_revision,
            "reasonCode": reason_code,
            "targetState": target_state.value,
        }
        replay, request_hash = self._begin(
            actor_id=actor_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            request=request,
        )
        if replay is not None:
            return replay
        current = self._repository.head(
            self._session, content_license_id=content_license_id
        )
        if current is None:
            raise ContentLicenseNotFound(str(content_license_id))
        self._require_revision(current.record, expected_revision)
        if current.record.state not in allowed_from:
            raise ContentLicenseTransitionError(
                f"{command_type} cannot transition from {current.record.state.value}"
            )
        if record_guard is not None:
            record_guard(current.record)
        next_epoch = (
            self._allocate_epoch(current.record)
            if increment_epoch
            else current.record.authorization_epoch
        )
        record = _next_record(
            current.record,
            state=target_state,
            authorization_epoch=next_epoch,
            updated_at=self._clock(),
        )
        return self._persist(
            actor_id=actor_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            record=record,
            predecessor=current,
            reason_code=reason_code,
        )

    def _review_transition(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        content_license_id: UUID,
        expected_revision: int,
        command_type: Literal["activate", "reject", "reinstate"],
        target_state: LicenseState,
        receipt: ReviewerReceipt,
        receipt_decision: Literal["approved", "rejected"],
        increment_epoch: bool,
        allowed_from: set[LicenseState] | None = None,
    ) -> ContentLicenseRecord:
        request = {
            "contentLicenseId": str(content_license_id),
            "expectedRevision": expected_revision,
            "receipt": receipt.model_dump(by_alias=True, mode="json"),
        }
        replay, request_hash = self._begin(
            actor_id=actor_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            content_license_id=content_license_id,
            request=request,
        )
        if replay is not None:
            return replay
        current = self._repository.head(
            self._session, content_license_id=content_license_id
        )
        if current is None:
            raise ContentLicenseNotFound(str(content_license_id))
        self._require_revision(current.record, expected_revision)
        valid_from = allowed_from or {LicenseState.IN_REVIEW}
        if current.record.state not in valid_from:
            raise ContentLicenseTransitionError(
                f"{command_type} cannot transition from {current.record.state.value}"
            )
        self._validate_receipt(
            current.record,
            actor_id=actor_id,
            receipt=receipt,
            expected_decision=receipt_decision,
        )
        record = _next_record(
            current.record,
            state=target_state,
            authorization_epoch=(
                self._allocate_epoch(current.record)
                if increment_epoch
                else current.record.authorization_epoch
            ),
            reviewer_receipt=receipt,
            updated_at=self._clock(),
        )
        return self._persist(
            actor_id=actor_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            record=record,
            predecessor=current,
        )

    def _begin(
        self,
        *,
        actor_id: str,
        command_type: str,
        idempotency_key: str,
        content_license_id: UUID,
        request: dict[str, object],
        aggregate_already_locked: bool = False,
    ) -> tuple[ContentLicenseRecord | None, str]:
        if not actor_id or not (8 <= len(idempotency_key) <= 260):
            raise ValueError("actor and a valid idempotency key are required")
        request_hash = _fingerprint(request)
        command_lock = f"content-license-command:{actor_id}:{command_type}:{idempotency_key}"
        self._repository.lock(self._session, key=command_lock)
        existing = self._repository.command_by_key(
            self._session,
            actor_id=actor_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ContentLicenseIdempotencyConflict(
                    "idempotency key already belongs to different immutable input"
                )
            return (
                ContentLicenseRecord.model_validate(existing.response_payload["record"]),
                request_hash,
            )
        if not aggregate_already_locked:
            self._repository.lock(
                self._session, key=f"content-license:{content_license_id}"
            )
        return None, request_hash

    def _persist(
        self,
        *,
        actor_id: str,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
        record: ContentLicenseRecord,
        predecessor: StoredLicenseHead | None,
        reason_code: str | None = None,
    ) -> ContentLicenseRecord:
        self._repository.insert_version(
            self._session,
            record=record,
            predecessor=predecessor,
        )
        event_ids: list[str] = []
        if predecessor is not None and (
            predecessor.record.authorization_epoch != record.authorization_epoch
        ):
            event = self._outbox.append(
                self._session,
                deduplication_key=(
                    f"content-license:{record.content_license_id}:"
                    f"authorization:{record.authorization_epoch}"
                ),
                aggregate_type="content_license",
                aggregate_id=str(record.content_license_id),
                event_type="content-license-authorization-changed@v1",
                payload={
                    "contentLicenseId": str(record.content_license_id),
                    "contentVersionId": record.content_version_id,
                    "revision": record.revision,
                    "authorizationEpoch": record.authorization_epoch,
                    "state": record.state.value,
                    "grantMatrixHash": record.rights.grant_matrix.matrix_hash,
                    "evidenceManifestHash": record.evidence_manifest_hash,
                    "reasonCode": reason_code,
                },
            )
            event_ids.append(str(event.event_id))
        event = self._outbox.append(
            self._session,
            deduplication_key=(
                f"content-license:{record.content_license_id}:revision:{record.revision}"
            ),
            aggregate_type="content_license",
            aggregate_id=str(record.content_license_id),
            event_type="content-license-record-changed@v1",
            payload={
                "contentLicenseId": str(record.content_license_id),
                "contentVersionId": record.content_version_id,
                "revision": record.revision,
                "authorizationEpoch": record.authorization_epoch,
                "state": record.state.value,
                "recordCanonicalHash": record.record_canonical_hash,
            },
        )
        event_ids.append(str(event.event_id))
        response: dict[str, object] = {
            "record": record.model_dump(by_alias=True, mode="json"),
            "eventIds": event_ids,
        }
        self._repository.insert_command(
            self._session,
            actor_id=actor_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            content_license_id=record.content_license_id,
            request_hash=request_hash,
            response_payload=response,
            created_at=self._clock(),
        )
        return record

    @staticmethod
    def _require_revision(record: ContentLicenseRecord, expected_revision: int) -> None:
        if record.revision != expected_revision:
            raise ContentLicenseVersionConflict(
                f"expected revision {expected_revision}, current revision is {record.revision}"
            )

    @staticmethod
    def _validate_receipt(
        record: ContentLicenseRecord,
        *,
        actor_id: str,
        receipt: ReviewerReceipt,
        expected_decision: Literal["approved", "rejected"],
    ) -> None:
        if actor_id == record.created_by_actor_id:
            raise ContentLicenseTransitionError(
                "license author cannot approve or reject their own record"
            )
        if receipt.reviewer_id != actor_id or receipt.decision != expected_decision:
            raise ContentLicenseTransitionError("reviewer receipt identity is invalid")
        if receipt.reviewed_record_hash != record.record_canonical_hash:
            raise ContentLicenseTransitionError("reviewer receipt binds another revision")
        if receipt.evidence_manifest_hash != record.evidence_manifest_hash:
            raise ContentLicenseTransitionError("evidence manifest changed after review")
        if receipt.grant_matrix_hash != record.rights.grant_matrix.matrix_hash:
            raise ContentLicenseTransitionError("grant matrix changed after review")

    def _require_ended_term(self, record: ContentLicenseRecord) -> None:
        expires_at = record.rights.term.expires_at
        if not isinstance(expires_at, datetime) or expires_at > self._clock():
            raise ContentLicenseTransitionError("authoritative license term has not ended")

    def _allocate_epoch(self, record: ContentLicenseRecord) -> int:
        self._repository.lock(
            self._session,
            key=f"content-license-epoch:{record.content_version_id}",
        )
        return self._repository.next_authorization_epoch(
            self._session,
            content_version_id=record.content_version_id,
        )


def _next_record(
    current: ContentLicenseRecord,
    *,
    state: LicenseState,
    authorization_epoch: int,
    updated_at: datetime,
    reviewer_receipt: ReviewerReceipt | None = None,
) -> ContentLicenseRecord:
    candidate = current.model_copy(
        update={
            "revision": current.revision + 1,
            "state": state,
            "authorization_epoch": authorization_epoch,
            "reviewer_receipt": reviewer_receipt or current.reviewer_receipt,
            "record_canonical_hash": "0" * 64,
            "updated_at": updated_at,
        }
    )
    return _sealed(candidate)


def _sealed(record: ContentLicenseRecord) -> ContentLicenseRecord:
    candidate = record.model_copy(update={"record_canonical_hash": "0" * 64})
    candidate = ContentLicenseRecord.model_validate(
        candidate.model_dump(by_alias=True, mode="json")
    )
    sealed = candidate.model_copy(
        update={"record_canonical_hash": canonical_record_hash(candidate)}
    )
    return ContentLicenseRecord.model_validate(
        sealed.model_dump(by_alias=True, mode="json")
    )


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
