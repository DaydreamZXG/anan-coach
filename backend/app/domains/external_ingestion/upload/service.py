from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Callable
from uuid import UUID, uuid4, uuid5

from app.contracts.external_learning.models import (
    SourceArtifactState,
    SourceArtifactVersion,
)
from app.domains.external_ingestion.upload.models import (
    CompleteUploadResult,
    ExpectedUploadPart,
    MultipartUploadHandle,
    ObjectMetadata,
    ScanReceipt,
    ScanVerdict,
    ScannerRuntimeApproval,
    StartUploadRequest,
    StartUploadResult,
    UploadAuditEvent,
    UploadAuthorization,
    UploadContractError,
    UploadPolicy,
    UploadMode,
    UploadPlan,
    UploadSession,
    UploadState,
    default_upload_policy,
)
from app.domains.external_ingestion.upload.ports import (
    MalwareScannerPort,
    PrivateObjectStoragePort,
    UploadRepositoryPort,
    UploadReservationPort,
)


_UPLOAD_ID_NAMESPACE = UUID("acb2d61e-0d83-4fa8-b8db-a4f9c829fcd9")


class UploadNotFound(LookupError):
    pass


class UploadOwnershipError(PermissionError):
    pass


class UploadIdempotencyConflict(RuntimeError):
    pass


class UploadIntegrityError(RuntimeError):
    pass


class UploadUnavailable(RuntimeError):
    pass


class UploadRecoveryRequired(RuntimeError):
    pass


class UploadService:
    """Coordinates private ingestion without exposing unverified bytes downstream.

    Repository writes participate in the composition root's SQL transaction. All
    external operations carry stable operation keys and are required to replay.
    """

    def __init__(
        self,
        *,
        repository: UploadRepositoryPort,
        object_storage: PrivateObjectStoragePort,
        scanner: MalwareScannerPort | None,
        scanner_approval: ScannerRuntimeApproval | None,
        reservations: UploadReservationPort,
        policy: UploadPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._scanner = scanner
        self._scanner_approval = scanner_approval
        self._reservations = reservations
        self._policy = policy or default_upload_policy()
        self._clock = clock or (lambda: datetime.now(UTC))

    def start(self, request: StartUploadRequest) -> StartUploadResult:
        plan = self._policy.plan(request)
        request_hash = _request_hash(request)
        now = self._clock()
        command_type = "source_upload_start"

        with self._repository.command_guard(
            owner_user_id=request.owner_user_id,
            command_type=command_type,
            idempotency_key=request.idempotency_key,
        ):
            existing = self._repository.command_receipt(
                owner_user_id=request.owner_user_id,
                command_type=command_type,
                idempotency_key=request.idempotency_key,
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise UploadIdempotencyConflict(
                        "idempotency key already belongs to different immutable input"
                    )
                session = self._required_owned_session(
                    owner_user_id=request.owner_user_id,
                    session_id=existing.session_id,
                )
                if session.state not in {
                    UploadState.AWAITING_UPLOAD,
                    UploadState.SCAN_FAILED,
                }:
                    raise UploadUnavailable(
                        f"upload start already resolved as {session.state.value}"
                    )
                authorization = self._authorize(session, now=now)
                return StartUploadResult(
                    session=session, authorization=authorization, replayed=True
                )

            identity_scope = (
                f"{request.owner_user_id}:{request.idempotency_key}:{request_hash}"
            )
            session_id = uuid5(_UPLOAD_ID_NAMESPACE, f"session:{identity_scope}")
            artifact_id = uuid5(_UPLOAD_ID_NAMESPACE, f"artifact:{identity_scope}")
            artifact_version_id = uuid5(
                _UPLOAD_ID_NAMESPACE, f"artifact-version:{identity_scope}"
            )
            reservation_key = _stable_key(
                "upload-reservation",
                str(request.owner_user_id),
                request.idempotency_key,
                request_hash,
            )
            reservation = self._reservations.reserve(
                owner_user_id=request.owner_user_id,
                reservation_key=reservation_key,
                byte_size=request.byte_size,
            )
            expires_at = now + timedelta(seconds=self._policy.session_ttl_seconds)
            object_key = self._object_key(
                owner_user_id=request.owner_user_id,
                session_id=session_id,
                artifact_version_id=artifact_version_id,
                extension=plan.extension,
                now=now,
            )
            multipart_handle = None
            try:
                if plan.mode == UploadMode.MULTIPART:
                    multipart_handle = self._object_storage.initiate_multipart(
                        object_key=object_key,
                        mime_type=request.mime_type,
                        content_sha256=request.content_sha256,
                        operation_key=_multipart_operation_key(session_id),
                    )
                authorization = self._new_authorization(
                    plan=plan,
                    object_key=object_key,
                    mime_type=request.mime_type,
                    byte_size=request.byte_size,
                    content_sha256=request.content_sha256,
                    expires_at=expires_at,
                    multipart_handle=multipart_handle,
                )
                self._validate_authorization(
                    authorization,
                    owner_user_id=request.owner_user_id,
                    expected_object_key=object_key,
                    now=now,
                    expires_at=expires_at,
                    expected_mode=plan.mode,
                    expected_parts=(
                        plan.expected_parts
                        if plan.mode == UploadMode.MULTIPART
                        else (
                            ExpectedUploadPart(
                                part_number=1,
                                byte_size=request.byte_size,
                                content_sha256=request.content_sha256,
                            ),
                        )
                    ),
                    expected_provider_upload_id=(
                        multipart_handle.provider_upload_id
                        if multipart_handle is not None
                        else None
                    ),
                    expected_mime_type=request.mime_type,
                )
                session = UploadSession(
                    session_id=session_id,
                    artifact_id=artifact_id,
                    artifact_version_id=artifact_version_id,
                    owner_user_id=request.owner_user_id,
                    revision=1,
                    state=UploadState.AWAITING_UPLOAD,
                    source_kind=request.source_kind,
                    original_filename=request.original_filename,
                    extension=plan.extension,
                    mime_type=request.mime_type,
                    byte_size=request.byte_size,
                    content_sha256=request.content_sha256,
                    page_count=request.page_count,
                    object_ref=authorization.object_ref,
                    reservation_id=reservation.reservation_id,
                    upload_policy_version=self._policy.policy_version,
                    upload_mode=plan.mode,
                    provider_upload_id=(
                        multipart_handle.provider_upload_id
                        if multipart_handle is not None
                        else None
                    ),
                    part_size=plan.part_size,
                    expected_parts=plan.expected_parts,
                    uploaded_parts=(),
                    created_at=now,
                    expires_at=expires_at,
                    updated_at=now,
                )
                self._repository.insert_session_with_command(
                    session=session,
                    command_type=command_type,
                    idempotency_key=request.idempotency_key,
                    request_hash=request_hash,
                )
                self._repository.append_audit(
                    self._audit(
                        session=session,
                        event_type="upload_session_started",
                        from_state=None,
                        details={
                            "policyVersion": session.upload_policy_version,
                            "byteSize": session.byte_size,
                            "sourceKind": session.source_kind.value,
                            "uploadMode": session.upload_mode.value,
                            "partCount": len(session.expected_parts),
                        },
                    )
                )
            except Exception:
                if multipart_handle is not None:
                    self._object_storage.abort_multipart(
                        object_ref=multipart_handle.object_ref,
                        provider_upload_id=multipart_handle.provider_upload_id,
                        operation_key=_stable_key(
                            "multipart-abort-start", str(session_id)
                        ),
                    )
                self._reservations.release(
                    reservation_id=reservation.reservation_id,
                    reason="upload_authorization_failed",
                )
                raise

            return StartUploadResult(
                session=session, authorization=authorization, replayed=False
            )

    def complete(
        self, *, owner_user_id: UUID, session_id: UUID
    ) -> CompleteUploadResult:
        with self._repository.session_guard(session_id=session_id):
            session = self._required_owned_session(
                owner_user_id=owner_user_id, session_id=session_id
            )
            if session.terminal:
                return CompleteUploadResult(
                    session=session,
                    artifact=self._repository.artifact_for_session(
                        session_id=session_id
                    ),
                    replayed=True,
                )
            now = self._clock()
            if session.state == UploadState.REJECTING:
                failed = self._finish_rejection(session=session, now=now)
                return CompleteUploadResult(
                    session=failed, artifact=None, replayed=False
                )
            if session.state == UploadState.QUARANTINING:
                quarantined = self._finish_quarantine(session=session, now=now)
                return CompleteUploadResult(
                    session=quarantined,
                    artifact=self._repository.artifact_for_session(
                        session_id=session_id
                    ),
                    replayed=False,
                )
            if now >= session.expires_at and session.state in {
                UploadState.AWAITING_UPLOAD,
                UploadState.ASSEMBLING,
                UploadState.UPLOADED,
                UploadState.SCAN_FAILED,
            }:
                expired = self._expire_locked(session=session, now=now)
                return CompleteUploadResult(
                    session=expired, artifact=None, replayed=False
                )

            if session.state == UploadState.AWAITING_UPLOAD and (
                session.upload_mode == UploadMode.MULTIPART
            ):
                session = self._begin_multipart_completion(session=session, now=now)
            elif session.state == UploadState.ASSEMBLING:
                session = self._finish_multipart_completion(session=session, now=now)
            if session.state in {
                UploadState.AWAITING_UPLOAD,
                UploadState.UPLOADED,
                UploadState.SCAN_FAILED,
            }:
                session = self._verify_and_scan(session=session, now=now)
            elif session.state == UploadState.SCANNING:
                session = self._scan(session=session)
            if session.state == UploadState.SCAN_FAILED:
                return CompleteUploadResult(
                    session=session,
                    artifact=None,
                    replayed=False,
                )
            if session.state == UploadState.QUARANTINED:
                return CompleteUploadResult(
                    session=session,
                    artifact=self._repository.artifact_for_session(
                        session_id=session_id
                    ),
                    replayed=False,
                )
            if session.state != UploadState.FINALIZING:
                raise UploadRecoveryRequired(
                    f"upload cannot finalize from {session.state.value}"
                )
            return self._finalize_clean(session=session, now=self._clock())

    def recognition_artifact(
        self, *, owner_user_id: UUID, session_id: UUID
    ) -> SourceArtifactVersion:
        session = self._required_owned_session(
            owner_user_id=owner_user_id, session_id=session_id
        )
        artifact = self._repository.artifact_for_session(session_id=session_id)
        if (
            not session.recognition_eligible
            or artifact is None
            or artifact.state != SourceArtifactState.AVAILABLE
            or artifact.owner_user_id != owner_user_id
        ):
            raise UploadUnavailable(
                "only a clean, available, same-owner artifact may enter recognition"
            )
        return artifact

    def expire_stale(self, *, before: datetime, limit: int = 100) -> tuple[UUID, ...]:
        if limit <= 0 or limit > 1_000:
            raise ValueError("expiry batch limit must be between 1 and 1000")
        expired: list[UUID] = []
        for session_id in self._repository.stale_session_ids(
            before=before, limit=limit
        ):
            with self._repository.session_guard(session_id=session_id):
                session = self._repository.session(session_id=session_id)
                if (
                    session is None
                    or session.terminal
                    or session.expires_at > before
                    or session.state
                    not in {UploadState.AWAITING_UPLOAD, UploadState.SCAN_FAILED}
                ):
                    continue
                self._expire_locked(session=session, now=self._clock())
                expired.append(session_id)
        return tuple(expired)

    def recover_pending(
        self, *, before: datetime, limit: int = 100
    ) -> tuple[UUID, ...]:
        if limit <= 0 or limit > 1_000:
            raise ValueError("recovery batch limit must be between 1 and 1000")
        recovered: list[UUID] = []
        for session_id in self._repository.recovery_session_ids(
            before=before, limit=limit
        ):
            session = self._repository.session(session_id=session_id)
            if session is None or session.terminal or session.updated_at >= before:
                continue
            result = self.complete(
                owner_user_id=session.owner_user_id, session_id=session_id
            )
            if result.session.terminal:
                recovered.append(session_id)
        return tuple(recovered)

    def _begin_multipart_completion(
        self, *, session: UploadSession, now: datetime
    ) -> UploadSession:
        if session.provider_upload_id is None or not session.expected_parts:
            raise UploadRecoveryRequired("multipart session has no provider manifest")
        uploaded = self._object_storage.uploaded_parts(
            object_ref=session.object_ref,
            provider_upload_id=session.provider_upload_id,
        )
        expected_shape = tuple(
            (item.part_number, item.byte_size) for item in session.expected_parts
        )
        uploaded_shape = tuple((item.part_number, item.byte_size) for item in uploaded)
        if uploaded_shape != expected_shape:
            raise UploadIntegrityError(
                "provider multipart manifest differs from the authorized parts"
            )
        assembling = session.transition(
            UploadState.ASSEMBLING, at=now, uploaded_parts=uploaded
        )
        self._persist_transition(
            previous=session,
            replacement=assembling,
            event_type="upload_multipart_assembly_started",
            details={"partCount": len(uploaded)},
        )
        return self._finish_multipart_completion(
            session=assembling, now=self._clock()
        )

    def _finish_multipart_completion(
        self, *, session: UploadSession, now: datetime
    ) -> UploadSession:
        if session.provider_upload_id is None or not session.uploaded_parts:
            raise UploadRecoveryRequired("multipart assembly intent is incomplete")
        self._object_storage.complete_multipart(
            object_ref=session.object_ref,
            provider_upload_id=session.provider_upload_id,
            parts=session.uploaded_parts,
            operation_key=_multipart_operation_key(session.session_id),
        )
        uploaded = session.transition(UploadState.UPLOADED, at=now)
        self._persist_transition(
            previous=session,
            replacement=uploaded,
            event_type="upload_multipart_assembled",
            details={"partCount": len(session.uploaded_parts)},
        )
        return uploaded

    def _verify_and_scan(
        self, *, session: UploadSession, now: datetime
    ) -> UploadSession:
        metadata = self._object_storage.head_private(object_ref=session.object_ref)
        try:
            self._validate_metadata(session=session, metadata=metadata)
        except UploadIntegrityError:
            rejecting = session.transition(
                UploadState.REJECTING,
                at=now,
                failure_code="object_integrity_failed",
            )
            self._persist_transition(
                previous=session,
                replacement=rejecting,
                event_type="upload_integrity_rejected",
                details={},
            )
            self._finish_rejection(session=rejecting, now=self._clock())
            raise

        scanning = session.transition(
            UploadState.SCANNING, at=now, failure_code=None
        )
        self._persist_transition(
            previous=session,
            replacement=scanning,
            event_type="upload_scan_started",
            details={},
        )
        return self._scan(session=scanning)

    def _scan(self, *, session: UploadSession) -> UploadSession:
        if self._scanner is None:
            return self._complete_metadata_validation(session=session)
        if self._scanner_approval is None:
            raise UploadContractError("enabled malware scanner has no runtime approval")
        try:
            receipt = self._scanner.scan(
                object_ref=session.object_ref,
                expected_sha256=session.content_sha256,
                operation_key=_stable_key("scan", str(session.session_id)),
            )
            if (
                receipt.scanner_policy_version
                != self._scanner_approval.scanner_policy_version
                or receipt.provider_route != self._scanner_approval.provider_route
                or receipt.data_processing_agreement_version
                != self._scanner_approval.data_processing_agreement_version
                or receipt.scanned_at < session.created_at
                or receipt.scanned_at > self._clock() + timedelta(minutes=5)
            ):
                raise UploadContractError(
                    "scanner receipt is outside the attributable upload window"
                )
        except Exception as error:
            failed = session.transition(
                UploadState.SCAN_FAILED,
                at=self._clock(),
                failure_code="scanner_unavailable",
            )
            self._persist_transition(
                previous=session,
                replacement=failed,
                event_type="upload_scan_failed",
                details={"errorType": type(error).__name__},
            )
            return failed

        if receipt.verdict == ScanVerdict.UNDETERMINED:
            failed = session.transition(
                UploadState.SCAN_FAILED,
                at=self._clock(),
                scan_receipt=receipt,
                failure_code="scanner_undetermined",
            )
            self._persist_transition(
                previous=session,
                replacement=failed,
                event_type="upload_scan_undetermined",
                details={"scanId": receipt.scan_id},
            )
            return failed

        authoritative_page_count: int | None = None
        if session.mime_type == "application/pdf":
            authoritative_page_count = receipt.detected_page_count
            if authoritative_page_count is None:
                failed = session.transition(
                    UploadState.SCAN_FAILED,
                    at=self._clock(),
                    scan_receipt=receipt,
                    failure_code="scanner_missing_pdf_page_count",
                )
                self._persist_transition(
                    previous=session,
                    replacement=failed,
                    event_type="upload_scan_invalid_pdf_metadata",
                    details={"scanId": receipt.scan_id},
                )
                return failed

        page_count_exceeds_limit = (
            authoritative_page_count is not None
            and authoritative_page_count > self._policy.maximum_pdf_pages
        )
        declared_page_count_mismatch = (
            authoritative_page_count is not None
            and session.page_count is not None
            and authoritative_page_count != session.page_count
        )
        if (
            receipt.verdict == ScanVerdict.MALICIOUS
            or receipt.content_sha256 != session.content_sha256
            or receipt.detected_mime_type != session.mime_type
            or page_count_exceeds_limit
            or declared_page_count_mismatch
        ):
            reason = (
                "malware_detected"
                if receipt.verdict == ScanVerdict.MALICIOUS
                else (
                    "detected_content_sha256_mismatch"
                    if receipt.content_sha256 != session.content_sha256
                    else (
                        "detected_mime_mismatch"
                        if receipt.detected_mime_type != session.mime_type
                        else (
                            "detected_page_count_exceeds_limit"
                            if page_count_exceeds_limit
                            else "detected_page_count_mismatch"
                        )
                    )
                )
            )
            return self._quarantine(
                session=session,
                receipt=receipt,
                reason=reason,
                now=self._clock(),
                authoritative_page_count=authoritative_page_count,
            )

        finalizing = session.transition(
            UploadState.FINALIZING,
            at=self._clock(),
            scan_receipt=receipt,
            failure_code=None,
            page_count=authoritative_page_count,
        )
        self._persist_transition(
            previous=session,
            replacement=finalizing,
            event_type="upload_scan_clean",
            details={"scanId": receipt.scan_id},
        )
        return finalizing

    def _complete_metadata_validation(self, *, session: UploadSession) -> UploadSession:
        """Finish MVP uploads after private-object integrity validation.

        The object store metadata was already checked against the signed upload
        authorization in ``_verify_and_scan``.  The optional malware scanner is
        deliberately not part of the small-test launch path.
        """
        if session.mime_type == "application/pdf" and session.page_count is None:
            failed = session.transition(
                UploadState.SCAN_FAILED,
                at=self._clock(),
                failure_code="pdf_page_count_required",
            )
            self._persist_transition(
                previous=session,
                replacement=failed,
                event_type="upload_metadata_validation_failed",
                details={"reason": "pdf_page_count_required"},
            )
            return failed
        receipt = ScanReceipt(
            scan_id=f"metadata-{session.session_id}",
            verdict=ScanVerdict.CLEAN,
            detected_mime_type=session.mime_type,
            content_sha256=session.content_sha256,
            scanner_policy_version="private-object-integrity@v1",
            provider_route="local-private-object-metadata",
            data_processing_agreement_version="not-applicable",
            definition_version="not-applicable",
            scanned_at=self._clock(),
            detected_page_count=(
                session.page_count if session.mime_type == "application/pdf" else None
            ),
        )
        finalizing = session.transition(
            UploadState.FINALIZING,
            at=self._clock(),
            scan_receipt=receipt,
            failure_code=None,
        )
        self._persist_transition(
            previous=session,
            replacement=finalizing,
            event_type="upload_metadata_validated",
            details={},
        )
        return finalizing

    def _finalize_clean(
        self, *, session: UploadSession, now: datetime
    ) -> CompleteUploadResult:
        with self._repository.content_guard(
            owner_user_id=session.owner_user_id,
            content_sha256=session.content_sha256,
        ):
            duplicate = self._repository.available_duplicate(
                owner_user_id=session.owner_user_id,
                source_kind=session.source_kind,
                content_sha256=session.content_sha256,
                byte_size=session.byte_size,
                mime_type=session.mime_type,
            )
            if duplicate is not None:
                self._object_storage.delete_private(
                    object_ref=session.object_ref,
                    operation_key=_stable_key(
                        "deduplicate-delete", str(session.session_id)
                    ),
                )
                self._reservations.release(
                    reservation_id=session.reservation_id,
                    reason="same_owner_duplicate",
                )
                resolved = session.transition(
                    UploadState.DEDUPLICATED,
                    at=now,
                    resolved_artifact_version_id=duplicate.artifact_version_id,
                )
                self._persist_transition(
                    previous=session,
                    replacement=resolved,
                    event_type="upload_same_owner_deduplicated",
                    details={
                        "artifactVersionId": str(duplicate.artifact_version_id)
                    },
                )
                return CompleteUploadResult(
                    session=resolved, artifact=duplicate, replayed=False
                )

            self._reservations.commit(
                reservation_id=session.reservation_id,
                artifact_version_id=session.artifact_version_id,
            )
            artifact = self._artifact(
                session=session,
                state=SourceArtifactState.AVAILABLE,
                object_ref=session.object_ref,
                created_at=now,
            )
            self._repository.insert_artifact(
                session_id=session.session_id, artifact=artifact
            )
            available = session.transition(
                UploadState.AVAILABLE,
                at=now,
                resolved_artifact_version_id=artifact.artifact_version_id,
            )
            self._persist_transition(
                previous=session,
                replacement=available,
                event_type="upload_available",
                details={"artifactVersionId": str(artifact.artifact_version_id)},
            )
            return CompleteUploadResult(
                session=available, artifact=artifact, replayed=False
            )

    def _quarantine(
        self,
        *,
        session: UploadSession,
        receipt: ScanReceipt,
        reason: str,
        now: datetime,
        authoritative_page_count: int | None,
    ) -> UploadSession:
        intent = session.transition(
            UploadState.QUARANTINING,
            at=now,
            scan_receipt=receipt,
            failure_code=reason,
            page_count=authoritative_page_count,
        )
        self._persist_transition(
            previous=session,
            replacement=intent,
            event_type="upload_quarantine_started",
            details={"reason": reason, "scanId": receipt.scan_id},
        )
        return self._finish_quarantine(session=intent, now=self._clock())

    def _finish_quarantine(
        self, *, session: UploadSession, now: datetime
    ) -> UploadSession:
        if session.scan_receipt is None or not session.failure_code:
            raise UploadRecoveryRequired("quarantine intent has no scanner evidence")
        quarantined_ref = self._object_storage.quarantine_private(
            object_ref=session.object_ref,
            operation_key=_stable_key("quarantine", str(session.session_id)),
        )
        self._reservations.release(
            reservation_id=session.reservation_id, reason=session.failure_code
        )
        artifact = self._artifact(
            session=session,
            state=SourceArtifactState.QUARANTINED,
            object_ref=quarantined_ref,
            created_at=now,
        )
        self._repository.insert_artifact(
            session_id=session.session_id, artifact=artifact
        )
        quarantined = session.transition(
            UploadState.QUARANTINED,
            at=now,
            resolved_artifact_version_id=artifact.artifact_version_id,
            scan_receipt=session.scan_receipt,
            failure_code=session.failure_code,
        )
        self._persist_transition(
            previous=session,
            replacement=quarantined,
            event_type="upload_quarantined",
            details={
                "reason": session.failure_code,
                "scanId": session.scan_receipt.scan_id,
            },
        )
        return quarantined

    def _finish_rejection(
        self, *, session: UploadSession, now: datetime
    ) -> UploadSession:
        if not session.failure_code:
            raise UploadRecoveryRequired("rejection intent has no failure code")
        self._delete_and_release(
            session=session,
            reason=session.failure_code,
            operation_suffix="integrity-failure",
        )
        failed = session.transition(
            UploadState.FAILED, at=now, failure_code=session.failure_code
        )
        self._persist_transition(
            previous=session,
            replacement=failed,
            event_type="upload_rejection_completed",
            details={"reason": session.failure_code},
        )
        return failed

    def _expire_locked(self, *, session: UploadSession, now: datetime) -> UploadSession:
        self._delete_and_release(
            session=session,
            reason="upload_session_expired",
            operation_suffix="expiry",
        )
        expired = session.transition(
            UploadState.EXPIRED, at=now, failure_code="upload_session_expired"
        )
        self._persist_transition(
            previous=session,
            replacement=expired,
            event_type="upload_session_expired",
            details={},
        )
        return expired

    def _delete_and_release(
        self, *, session: UploadSession, reason: str, operation_suffix: str
    ) -> None:
        try:
            operation_key = _stable_key(operation_suffix, str(session.session_id))
            if (
                session.upload_mode == UploadMode.MULTIPART
                and session.provider_upload_id is not None
                and session.state == UploadState.AWAITING_UPLOAD
            ):
                self._object_storage.abort_multipart(
                    object_ref=session.object_ref,
                    provider_upload_id=session.provider_upload_id,
                    operation_key=operation_key,
                )
            else:
                if (
                    session.state == UploadState.ASSEMBLING
                    and session.provider_upload_id is not None
                    and session.uploaded_parts
                ):
                    self._object_storage.complete_multipart(
                        object_ref=session.object_ref,
                        provider_upload_id=session.provider_upload_id,
                        parts=session.uploaded_parts,
                        operation_key=_stable_key(
                            "multipart", str(session.session_id)
                        ),
                    )
                self._object_storage.delete_private(
                    object_ref=session.object_ref, operation_key=operation_key
                )
            self._reservations.release(
                reservation_id=session.reservation_id, reason=reason
            )
        except Exception as error:
            raise UploadRecoveryRequired(
                "object cleanup and reservation release must complete before termination"
            ) from error

    def _authorize(self, session: UploadSession, *, now: datetime) -> UploadAuthorization:
        if now >= session.expires_at:
            raise UploadUnavailable("upload session authorization has expired")
        plan = UploadPlan(
            extension=session.extension,
            mode=session.upload_mode,
            part_size=session.part_size,
            expected_parts=session.expected_parts,
        )
        multipart_handle = (
            None
            if session.provider_upload_id is None
            else self._multipart_handle(
                object_ref=session.object_ref,
                provider_upload_id=session.provider_upload_id,
            )
        )
        authorization = self._new_authorization(
            plan=plan,
            object_key=_key_from_object_ref(session.object_ref),
            mime_type=session.mime_type,
            byte_size=session.byte_size,
            content_sha256=session.content_sha256,
            expires_at=session.expires_at,
            multipart_handle=multipart_handle,
        )
        self._validate_authorization(
            authorization,
            owner_user_id=session.owner_user_id,
            expected_object_key=_key_from_object_ref(session.object_ref),
            now=now,
            expires_at=session.expires_at,
            expected_mode=session.upload_mode,
            expected_parts=(
                session.expected_parts
                if session.upload_mode == UploadMode.MULTIPART
                else (
                    ExpectedUploadPart(
                        part_number=1,
                        byte_size=session.byte_size,
                        content_sha256=session.content_sha256,
                    ),
                )
            ),
            expected_provider_upload_id=session.provider_upload_id,
            expected_mime_type=session.mime_type,
        )
        return authorization

    def _new_authorization(
        self,
        *,
        plan: UploadPlan,
        object_key: str,
        mime_type: str,
        byte_size: int,
        content_sha256: str,
        expires_at: datetime,
        multipart_handle: MultipartUploadHandle | None,
    ) -> UploadAuthorization:
        if plan.mode == UploadMode.SINGLE_PUT:
            part = self._object_storage.authorize_single_put(
                object_key=object_key,
                mime_type=mime_type,
                byte_size=byte_size,
                content_sha256=content_sha256,
                expires_at=expires_at,
            )
            return UploadAuthorization(
                object_ref=part.object_ref,
                mode=UploadMode.SINGLE_PUT,
                expires_at=expires_at,
                parts=(part,),
            )
        if multipart_handle is None:
            raise UploadRecoveryRequired("multipart upload has no provider handle")
        handle = multipart_handle
        parts = tuple(
            self._object_storage.authorize_multipart_part(
                object_ref=handle.object_ref,
                provider_upload_id=handle.provider_upload_id,
                part=part,
                expires_at=expires_at,
            )
            for part in plan.expected_parts
        )
        return UploadAuthorization(
            object_ref=handle.object_ref,
            mode=UploadMode.MULTIPART,
            expires_at=expires_at,
            parts=parts,
            provider_upload_id=handle.provider_upload_id,
            part_size=plan.part_size,
        )

    def _required_owned_session(
        self, *, owner_user_id: UUID, session_id: UUID
    ) -> UploadSession:
        session = self._repository.session(session_id=session_id)
        if session is None:
            raise UploadNotFound(str(session_id))
        if session.owner_user_id != owner_user_id:
            raise UploadOwnershipError("upload session belongs to another user")
        return session

    def _validate_authorization(
        self,
        authorization: UploadAuthorization,
        *,
        owner_user_id: UUID,
        expected_object_key: str,
        now: datetime,
        expires_at: datetime,
        expected_mode: UploadMode,
        expected_parts: tuple[ExpectedUploadPart, ...],
        expected_provider_upload_id: str | None,
        expected_mime_type: str,
    ) -> None:
        if _key_from_object_ref(authorization.object_ref) != expected_object_key:
            raise UploadContractError("object provider changed the bound object key")
        expected_prefix = f"{self._policy.object_namespace}/{owner_user_id.hex}/"
        if not expected_object_key.startswith(expected_prefix):
            raise UploadContractError("private object key is outside the owner namespace")
        if not now < authorization.expires_at <= expires_at:
            raise UploadContractError("upload authorization expiry exceeds the session")
        if len(authorization.object_ref) > 500:
            raise UploadContractError("object reference exceeds the artifact contract")
        if any(item.object_ref != authorization.object_ref for item in authorization.parts):
            raise UploadContractError("part authorization changed the bound object")
        if authorization.mode != expected_mode:
            raise UploadContractError("storage adapter changed the upload mode")
        if authorization.provider_upload_id != expected_provider_upload_id:
            raise UploadContractError("storage adapter changed the provider upload ID")
        expected_manifest = tuple(
            (item.part_number, item.byte_size, item.content_sha256)
            for item in expected_parts
        )
        actual_manifest = tuple(
            (item.part_number, item.byte_size, item.content_sha256)
            for item in authorization.parts
        )
        if actual_manifest != expected_manifest:
            raise UploadContractError("part authorization manifest is inconsistent")
        for item in authorization.parts:
            if not now < item.expires_at <= authorization.expires_at:
                raise UploadContractError("part authorization expiry is invalid")
            headers = {name.casefold(): value for name, value in item.required_headers.items()}
            if (
                headers.get("content-length") != str(item.byte_size)
                or headers.get("x-tos-content-sha256") != item.content_sha256
            ):
                raise UploadContractError(
                    "part authorization does not bind size and hash"
                )
        if authorization.mode == UploadMode.SINGLE_PUT:
            headers = {
                name.casefold(): value
                for name, value in authorization.parts[0].required_headers.items()
            }
            if (
                headers.get("content-type") != expected_mime_type
                or headers.get("x-tos-acl") != "private"
            ):
                raise UploadContractError(
                    "single PUT authorization does not bind MIME and private ACL"
                )

    @staticmethod
    def _validate_metadata(
        *, session: UploadSession, metadata: ObjectMetadata
    ) -> None:
        if metadata.object_ref != session.object_ref:
            raise UploadIntegrityError("storage returned metadata for another object")
        if metadata.access != "private" or not metadata.server_side_encrypted:
            raise UploadIntegrityError("object is not private and encrypted")
        if (
            metadata.byte_size != session.byte_size
            or metadata.content_sha256 != session.content_sha256
            or metadata.mime_type != session.mime_type
        ):
            raise UploadIntegrityError("object integrity differs from the upload intent")

    def _persist_transition(
        self,
        *,
        previous: UploadSession,
        replacement: UploadSession,
        event_type: str,
        details: dict[str, object],
    ) -> None:
        self._repository.save_session(
            expected_revision=previous.revision, session=replacement
        )
        self._repository.append_audit(
            self._audit(
                session=replacement,
                event_type=event_type,
                from_state=previous.state,
                details=details,
            )
        )

    def _audit(
        self,
        *,
        session: UploadSession,
        event_type: str,
        from_state: UploadState | None,
        details: dict[str, object],
    ) -> UploadAuditEvent:
        return UploadAuditEvent(
            event_id=uuid4(),
            deduplication_key=_stable_key(
                "upload-audit",
                str(session.session_id),
                str(session.revision),
                event_type,
            ),
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
            event_type=event_type,
            from_state=from_state,
            to_state=session.state,
            details=details,
            created_at=session.updated_at,
        )

    def _object_key(
        self,
        *,
        owner_user_id: UUID,
        session_id: UUID,
        artifact_version_id: UUID,
        extension: str,
        now: datetime,
    ) -> str:
        return (
            f"{self._policy.object_namespace}/{owner_user_id.hex}/"
            f"{session_id.hex[:2]}/{session_id.hex}/"
            f"{artifact_version_id.hex}{extension}"
        )

    @staticmethod
    def _multipart_handle(
        *, object_ref: str, provider_upload_id: str
    ) -> MultipartUploadHandle:
        return MultipartUploadHandle(
            object_ref=object_ref, provider_upload_id=provider_upload_id
        )

    @staticmethod
    def _artifact(
        *,
        session: UploadSession,
        state: SourceArtifactState,
        object_ref: str,
        created_at: datetime,
    ) -> SourceArtifactVersion:
        return SourceArtifactVersion(
            artifactVersionId=session.artifact_version_id,
            artifactId=session.artifact_id,
            ownerUserId=session.owner_user_id,
            versionNo=1,
            sourceKind=session.source_kind,
            state=state,
            objectRef=object_ref,
            mimeType=session.mime_type,
            contentSha256=session.content_sha256,
            byteSize=session.byte_size,
            pageCount=session.page_count,
            createdAt=created_at,
        )


def _request_hash(request: StartUploadRequest) -> str:
    payload = {
        "ownerUserId": str(request.owner_user_id),
        "sourceKind": request.source_kind.value,
        "originalFilename": request.original_filename,
        "mimeType": request.mime_type,
        "byteSize": request.byte_size,
        "contentSha256": request.content_sha256,
        "pageCount": request.page_count,
        "partSha256s": list(request.part_sha256s),
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _stable_key(*parts: str) -> str:
    return sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _multipart_operation_key(session_id: UUID) -> str:
    return _stable_key("multipart", str(session_id))


def _key_from_object_ref(object_ref: str) -> str:
    prefix = "tos://"
    if not object_ref.startswith(prefix):
        raise UploadContractError("object references must use the tos://bucket/key form")
    bucket_and_key = object_ref[len(prefix) :]
    _, separator, key = bucket_and_key.partition("/")
    if not separator or not key or key.startswith("/") or ".." in key.split("/"):
        raise UploadContractError("object reference contains an unsafe key")
    return key
