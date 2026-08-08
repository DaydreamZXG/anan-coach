from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import time
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from app.contracts.external_learning.models import (
    CandidateState,
    RecognitionKind,
    RecognitionRevision,
)
from app.domains.external_ingestion.recognition.errors import (
    ArtifactNotProcessable,
    ProcessorDispatchUnknownError,
    ProcessorNotDispatchedError,
    ProcessorResponseError,
    RecognitionIdempotencyConflict,
    RecognitionNotFound,
    RecognitionOperationPersistenceError,
    RecognitionOperationReconciliationRequired,
    RecognitionTransitionError,
    RecognitionVersionConflict,
)
from app.domains.external_ingestion.recognition.models import (
    ArtifactInput,
    RecognitionCandidatePayload,
    RecognitionEvidenceDecision,
    OMRLayout,
    ProcessorApproval,
    ProcessorRequest,
    QuestionUnit,
    provider_replay_key,
)
from app.domains.external_ingestion.recognition.operation_contracts import (
    RecognitionOperationPort,
    RecognitionOperationSpec,
)
from app.domains.external_ingestion.recognition.processor import ProcessorRegistry
from app.domains.external_ingestion.recognition.repository import RecognitionRepositoryPort


class RecognitionService:
    def __init__(
        self,
        *,
        repository: RecognitionRepositoryPort,
        processors: ProcessorRegistry,
        operations: RecognitionOperationPort | None = None,
        clock: Callable[[], datetime] | None = None,
        candidate_ttl: timedelta = timedelta(days=7),
    ) -> None:
        if candidate_ttl <= timedelta(0):
            raise ValueError("recognition candidate TTL must be positive")
        self._repository = repository
        self._processors = processors
        self._operations = operations
        self._clock = clock or (lambda: datetime.now(UTC))
        self._candidate_ttl = candidate_ttl

    def recognize(
        self,
        *,
        owner_user_id: UUID,
        artifact_version_id: UUID,
        recognition_kind: RecognitionKind,
        processor_route: str,
        idempotency_key: str,
        omr_layout: OMRLayout | None = None,
    ) -> RecognitionRevision:
        now = self._clock()
        request_data = {
            "ownerUserId": str(owner_user_id),
            "artifactVersionId": str(artifact_version_id),
            "recognitionKind": recognition_kind.value,
            "processorRoute": processor_route,
            "omrLayout": (
                omr_layout.model_dump(by_alias=True, mode="json")
                if omr_layout is not None
                else None
            ),
        }
        request_hash = _hash(request_data)
        revision_id = _command_id(owner_user_id, "recognize", idempotency_key)
        existing = self._repository.revision(recognition_revision_id=revision_id)
        if existing is not None:
            self._assert_replay(existing, request_hash)
            return existing

        lock_key = _aggregate_lock(owner_user_id, artifact_version_id, recognition_kind)
        self._repository.lock(key=lock_key)
        existing = self._repository.revision(recognition_revision_id=revision_id)
        if existing is not None:
            self._assert_replay(existing, request_hash)
            return existing
        current = self._repository.head(
            artifact_version_id=artifact_version_id,
            owner_user_id=owner_user_id,
            recognition_kind=recognition_kind.value,
        )
        if current is not None:
            # Starting recognition is an idempotent "ensure result" command.
            # A lost client receipt or a second import click must recover the
            # current immutable revision instead of forcing another upload.
            return current

        artifact = self._repository.artifact(
            artifact_version_id=artifact_version_id,
            owner_user_id=owner_user_id,
        )
        if artifact is None:
            raise ArtifactNotProcessable(
                "artifact does not exist in the available state for this owner"
            )
        approval, processor = self._processors.resolve(
            route=processor_route,
            kind=recognition_kind,
            mime_type=artifact.mime_type,
            at=now,
        )
        processor_request = ProcessorRequest(
            artifactVersionId=artifact.artifact_version_id,
            ownerScopeHash=_hash({"ownerUserId": str(owner_user_id)}),
            objectRef=artifact.object_ref,
            mimeType=artifact.mime_type,
            inputSha256=artifact.content_sha256,
            pageCount=artifact.page_count,
            recognitionKind=recognition_kind,
            idempotencyKey=provider_replay_key(
                owner_user_id=owner_user_id,
                operation="recognize",
                command_id=revision_id,
            ),
            omrLayout=omr_layout,
            before_provider_dispatch=lambda: self._claim_provider_dispatch(revision_id),
        )
        operation = self._operation_spec(
            operation_id=revision_id,
            owner_user_id=owner_user_id,
            artifact=artifact,
            recognition_kind=recognition_kind,
            approval=approval,
            request_hash=request_hash,
            started_at=now,
        )
        self._begin_external_operation(operation)
        started = time.monotonic()
        provider_response_received = False
        try:
            result = processor.process(processor_request)
            provider_response_received = True
            payload = RecognitionCandidatePayload(
                kind=recognition_kind,
                textSpans=result.text_spans,
                segments=result.segments,
                questionUnits=(
                    _question_units(
                        input_sha256=artifact.content_sha256,
                        segments=result.segments,
                    )
                    if recognition_kind == RecognitionKind.EXAM_SEGMENTATION
                    else ()
                ),
                omrMarks=result.omr_marks,
                pageImageSha256=result.page_image_sha256,
                visualFingerprints=result.visual_fingerprints,
                visualFeatureHashes=result.visual_feature_hashes,
                processorRequestIdHash=sha256(
                    result.provider_request_id.encode("utf-8")
                ).hexdigest(),
                processorOutputContract=result.contract_version,
                requestHash=request_hash,
            )
            output_payload = payload.model_dump(by_alias=True, mode="json")
            revision = RecognitionRevision(
                recognitionRevisionId=revision_id,
                artifactVersionId=artifact_version_id,
                ownerUserId=owner_user_id,
                revisionNo=1,
                recognitionKind=recognition_kind,
                state=CandidateState.CANDIDATE,
                processorPolicyVersion=approval.policy_version,
                processorRoute=approval.route,
                inputSha256=artifact.content_sha256,
                outputSha256=_hash(output_payload),
                candidatePayload=output_payload,
                confidence=result.confidence,
                expiresAt=now + self._candidate_ttl,
                createdAt=now,
            )
        except Exception as exc:
            self._complete_external_failure(
                operation_id=operation.operation_id,
                error_code=_processing_error_code(exc),
                usage_state=_failure_usage_state(
                    exc,
                    provider_response_received=provider_response_received,
                ),
                latency_ms=_latency_ms(started),
            )
            raise
        self._complete_external_success(
            operation_id=operation.operation_id,
            revision=revision,
            output_fingerprint=revision.output_sha256,
            latency_ms=_latency_ms(started),
        )
        return revision

    def correct(
        self,
        *,
        owner_user_id: UUID,
        recognition_revision_id: UUID,
        expected_revision_no: int,
        corrected_payload: RecognitionCandidatePayload,
        reason: str,
        idempotency_key: str,
    ) -> RecognitionRevision:
        if not reason.strip():
            raise RecognitionTransitionError("a user correction requires a reason")
        source = self._owned_revision(owner_user_id, recognition_revision_id)
        now = self._clock()
        if source.state not in {CandidateState.CANDIDATE, CandidateState.CONFIRMED}:
            raise RecognitionTransitionError(
                "only candidate or confirmed recognition can be corrected"
            )
        if source.expires_at <= now:
            raise RecognitionTransitionError(
                "expired recognition must be reprocessed before correction"
            )
        secure_payload = RecognitionCandidatePayload(
            kind=source.recognition_kind,
            textSpans=corrected_payload.text_spans,
            segments=corrected_payload.segments,
            questionUnits=corrected_payload.question_units,
            omrMarks=corrected_payload.omr_marks,
            pageImageSha256=_payload(source).page_image_sha256,
            visualFingerprints=_payload(source).visual_fingerprints,
            processorRequestIdHash=_payload(source).processor_request_id_hash,
            processorOutputContract=_payload(source).processor_output_contract,
            requestHash="0" * 64,
            predecessorRevisionId=source.recognition_revision_id,
            correctionReason=reason.strip(),
        )
        request_data = {
            "sourceRevisionId": str(source.recognition_revision_id),
            "expectedRevisionNo": expected_revision_no,
            "payload": secure_payload.model_dump(by_alias=True, mode="json"),
        }
        return self._revise(
            owner_user_id=owner_user_id,
            source=source,
            expected_revision_no=expected_revision_no,
            state=CandidateState.CANDIDATE,
            payload=secure_payload,
            confidence=None,
            operation="correct",
            idempotency_key=idempotency_key,
            request_hash=_hash(request_data),
            now=now,
        )

    def reprocess(
        self,
        *,
        owner_user_id: UUID,
        recognition_revision_id: UUID,
        expected_revision_no: int,
        processor_route: str,
        idempotency_key: str,
        omr_layout: OMRLayout | None = None,
    ) -> RecognitionRevision:
        source = self._owned_revision(owner_user_id, recognition_revision_id)
        now = self._clock()
        if (source.recognition_kind == RecognitionKind.ANSWER_SHEET_OMR) != (
            omr_layout is not None
        ):
            raise RecognitionTransitionError(
                "answer-sheet OMR reprocessing requires its sealed packet layout"
            )
        request_hash = _hash(
            {
                "sourceRevisionId": str(source.recognition_revision_id),
                "expectedRevisionNo": expected_revision_no,
                "processorRoute": processor_route,
                "omrLayout": (
                    omr_layout.model_dump(by_alias=True, mode="json")
                    if omr_layout is not None
                    else None
                ),
            }
        )
        revision_id = _command_id(owner_user_id, "reprocess", idempotency_key)
        existing = self._repository.revision(recognition_revision_id=revision_id)
        if existing is not None:
            self._assert_replay(existing, request_hash)
            return existing
        self._repository.lock(
            key=_aggregate_lock(owner_user_id, source.artifact_version_id, source.recognition_kind)
        )
        existing = self._repository.revision(recognition_revision_id=revision_id)
        if existing is not None:
            self._assert_replay(existing, request_hash)
            return existing
        head = self._repository.head(
            artifact_version_id=source.artifact_version_id,
            owner_user_id=owner_user_id,
            recognition_kind=source.recognition_kind.value,
        )
        if (
            head is None
            or head.recognition_revision_id != source.recognition_revision_id
            or head.revision_no != expected_revision_no
        ):
            raise RecognitionVersionConflict("recognition head changed before reprocessing")
        if head.state not in {CandidateState.REJECTED, CandidateState.EXPIRED} and (
            head.expires_at > now
        ):
            raise RecognitionTransitionError(
                "current recognition must be reviewed or corrected, not reprocessed"
            )
        artifact = self._repository.artifact(
            artifact_version_id=source.artifact_version_id,
            owner_user_id=owner_user_id,
        )
        if artifact is None:
            raise ArtifactNotProcessable(
                "artifact does not exist in the available state for this owner"
            )
        approval, processor = self._processors.resolve(
            route=processor_route,
            kind=source.recognition_kind,
            mime_type=artifact.mime_type,
            at=now,
        )
        operation = self._operation_spec(
            operation_id=revision_id,
            owner_user_id=owner_user_id,
            artifact=artifact,
            recognition_kind=source.recognition_kind,
            approval=approval,
            request_hash=request_hash,
            started_at=now,
        )
        self._begin_external_operation(operation)
        started = time.monotonic()
        provider_response_received = False
        try:
            result = processor.process(
                ProcessorRequest(
                    artifactVersionId=artifact.artifact_version_id,
                    ownerScopeHash=_hash({"ownerUserId": str(owner_user_id)}),
                    objectRef=artifact.object_ref,
                    mimeType=artifact.mime_type,
                    inputSha256=artifact.content_sha256,
                    pageCount=artifact.page_count,
                    recognitionKind=source.recognition_kind,
                    idempotencyKey=provider_replay_key(
                        owner_user_id=owner_user_id,
                        operation="reprocess",
                        command_id=revision_id,
                    ),
                    omrLayout=omr_layout,
                    before_provider_dispatch=lambda: self._claim_provider_dispatch(revision_id),
                )
            )
            provider_response_received = True
            payload = RecognitionCandidatePayload(
                kind=source.recognition_kind,
                textSpans=result.text_spans,
                segments=result.segments,
                questionUnits=(
                    _question_units(
                        input_sha256=artifact.content_sha256,
                        segments=result.segments,
                    )
                    if source.recognition_kind == RecognitionKind.EXAM_SEGMENTATION
                    else ()
                ),
                omrMarks=result.omr_marks,
                pageImageSha256=result.page_image_sha256,
                visualFingerprints=result.visual_fingerprints,
                visualFeatureHashes=result.visual_feature_hashes,
                processorRequestIdHash=sha256(
                    result.provider_request_id.encode("utf-8")
                ).hexdigest(),
                processorOutputContract=result.contract_version,
                requestHash=request_hash,
                predecessorRevisionId=source.recognition_revision_id,
            )
            dumped = payload.model_dump(by_alias=True, mode="json")
            revision = RecognitionRevision(
                recognitionRevisionId=revision_id,
                artifactVersionId=source.artifact_version_id,
                ownerUserId=owner_user_id,
                revisionNo=head.revision_no + 1,
                recognitionKind=source.recognition_kind,
                state=CandidateState.CANDIDATE,
                processorPolicyVersion=approval.policy_version,
                processorRoute=approval.route,
                inputSha256=artifact.content_sha256,
                outputSha256=_hash(dumped),
                candidatePayload=dumped,
                confidence=result.confidence,
                supersedesRecognitionRevisionId=source.recognition_revision_id,
                expiresAt=now + self._candidate_ttl,
                createdAt=now,
            )
        except Exception as exc:
            self._complete_external_failure(
                operation_id=operation.operation_id,
                error_code=_processing_error_code(exc),
                usage_state=_failure_usage_state(
                    exc,
                    provider_response_received=provider_response_received,
                ),
                latency_ms=_latency_ms(started),
            )
            raise
        self._complete_external_success(
            operation_id=operation.operation_id,
            revision=revision,
            output_fingerprint=revision.output_sha256,
            latency_ms=_latency_ms(started),
        )
        return revision

    def _operation_spec(
        self,
        *,
        operation_id: UUID,
        owner_user_id: UUID,
        artifact: ArtifactInput,
        recognition_kind: RecognitionKind,
        approval: ProcessorApproval,
        request_hash: str,
        started_at: datetime,
    ) -> RecognitionOperationSpec:
        unit_count = (artifact.page_count or 1) if approval.billing_unit in {"page", "image"} else 1
        scenario_key = {
            RecognitionKind.PRINTED_OCR: "recognition_printed_ocr",
            RecognitionKind.HANDWRITING_OCR: "recognition_handwriting_ocr",
            RecognitionKind.EXAM_SEGMENTATION: "recognition_printed_ocr",
            RecognitionKind.ANSWER_SHEET_OMR: "recognition_score_report",
        }[recognition_kind]
        return RecognitionOperationSpec(
            operation_id=operation_id,
            operation_key=f"recognition:{operation_id}",
            owner_user_id=owner_user_id,
            artifact_version_id=artifact.artifact_version_id,
            request_hash=request_hash,
            scenario_key=scenario_key,
            operation="recognition_process",
            provider=approval.provider,
            model=approval.model_release,
            provider_release=approval.provider_release,
            prompt_version=approval.prompt_version,
            schema_version=approval.output_contract,
            price_version=approval.price_version,
            billing_unit=approval.billing_unit,
            unit_count=unit_count,
            unit_price_cny=approval.unit_price_cny,
            input_fingerprint=artifact.content_sha256,
            started_at=started_at,
        )

    def _begin_external_operation(self, spec: RecognitionOperationSpec) -> None:
        operations = self._required_operations()
        state = operations.begin(spec=spec)
        if not state.created:
            raise RecognitionOperationReconciliationRequired(
                "recognition operation already dispatched; reconcile its durable "
                f"{state.status}/{state.usage_state} state before retry"
            )

    def _claim_provider_dispatch(self, operation_id: UUID) -> None:
        state = self._required_operations().claim_provider_dispatch(
            operation_id=operation_id,
            claimed_at=self._clock(),
        )
        if state.status != "running":
            raise ProcessorNotDispatchedError(
                "recognition operation closed before provider dispatch"
            )

    def _complete_external_success(
        self,
        *,
        operation_id: UUID,
        revision: RecognitionRevision,
        output_fingerprint: str,
        latency_ms: int,
    ) -> None:
        operations = self._required_operations()
        completed_at = self._clock()
        try:
            state = operations.succeed(
                operation_id=operation_id,
                revision=revision,
                output_fingerprint=output_fingerprint,
                latency_ms=latency_ms,
                completed_at=completed_at,
            )
        except Exception as exc:
            try:
                operations.fail(
                    operation_id=operation_id,
                    error_code="recognition_terminal_success_persistence_failed",
                    usage_state="actual",
                    latency_ms=latency_ms,
                    completed_at=completed_at,
                )
            except Exception as terminal_exc:
                raise RecognitionOperationPersistenceError(
                    "recognition operation remains running and requires reconciliation"
                ) from terminal_exc
            raise RecognitionOperationPersistenceError(
                "recognition result could not be durably finalized"
            ) from exc
        if state.status != "succeeded" or state.usage_state != "actual":
            raise RecognitionOperationPersistenceError(
                "recognition success conflicts with an existing terminal operation"
            )

    def _complete_external_failure(
        self,
        *,
        operation_id: UUID,
        error_code: str,
        usage_state: Literal["actual", "unknown", "not_dispatched"],
        latency_ms: int,
    ) -> None:
        try:
            state = self._required_operations().fail(
                operation_id=operation_id,
                error_code=error_code,
                usage_state=usage_state,
                latency_ms=latency_ms,
                completed_at=self._clock(),
            )
        except Exception as exc:
            raise RecognitionOperationPersistenceError(
                "recognition operation remains running and requires reconciliation"
            ) from exc
        expected_status = "outcome_unknown" if usage_state == "unknown" else "failed"
        if state.status != expected_status or state.usage_state != usage_state:
            raise RecognitionOperationPersistenceError(
                "recognition failure conflicts with an existing terminal operation"
            )

    def _required_operations(self) -> RecognitionOperationPort:
        if self._operations is None:
            raise RecognitionOperationPersistenceError(
                "durable recognition operation coordinator is not configured"
            )
        return self._operations

    def confirm(
        self,
        *,
        owner_user_id: UUID,
        recognition_revision_id: UUID,
        expected_revision_no: int,
        idempotency_key: str,
    ) -> RecognitionRevision:
        source = self._owned_revision(owner_user_id, recognition_revision_id)
        if source.state != CandidateState.CANDIDATE:
            raise RecognitionTransitionError("only a candidate revision can be confirmed")
        now = self._clock()
        if source.expires_at <= now:
            raise RecognitionTransitionError("an expired recognition candidate cannot be confirmed")
        return self._revise(
            owner_user_id=owner_user_id,
            source=source,
            expected_revision_no=expected_revision_no,
            state=CandidateState.CONFIRMED,
            payload=_payload(source),
            confidence=source.confidence,
            operation="confirm",
            idempotency_key=idempotency_key,
            request_hash=_hash(
                {
                    "sourceRevisionId": str(source.recognition_revision_id),
                    "expectedRevisionNo": expected_revision_no,
                    "decision": "confirmed",
                }
            ),
            now=now,
        )

    def reject(
        self,
        *,
        owner_user_id: UUID,
        recognition_revision_id: UUID,
        expected_revision_no: int,
        idempotency_key: str,
    ) -> RecognitionRevision:
        source = self._owned_revision(owner_user_id, recognition_revision_id)
        if source.state != CandidateState.CANDIDATE:
            raise RecognitionTransitionError("only a candidate revision can be rejected")
        return self._revise(
            owner_user_id=owner_user_id,
            source=source,
            expected_revision_no=expected_revision_no,
            state=CandidateState.REJECTED,
            payload=_payload(source),
            confidence=source.confidence,
            operation="reject",
            idempotency_key=idempotency_key,
            request_hash=_hash(
                {
                    "sourceRevisionId": str(source.recognition_revision_id),
                    "expectedRevisionNo": expected_revision_no,
                    "decision": "rejected",
                }
            ),
            now=self._clock(),
        )

    def evidence_decision(
        self, revision: RecognitionRevision, *, at: datetime | None = None
    ) -> RecognitionEvidenceDecision:
        now = at or self._clock()
        head = self._repository.head(
            artifact_version_id=revision.artifact_version_id,
            owner_user_id=revision.owner_user_id,
            recognition_kind=revision.recognition_kind.value,
        )
        if head is None or head.recognition_revision_id != revision.recognition_revision_id:
            allowed = False
            reason = "revision_superseded"
        elif revision.state == CandidateState.CONFIRMED and revision.expires_at > now:
            allowed = True
            reason = "confirmed_revision"
        elif revision.expires_at <= now or revision.state == CandidateState.EXPIRED:
            allowed = False
            reason = "revision_expired"
        elif revision.state == CandidateState.REJECTED:
            allowed = False
            reason = "revision_rejected"
        else:
            allowed = False
            reason = "candidate_unconfirmed"
        return RecognitionEvidenceDecision(
            allowed=allowed,
            reason=reason,
            recognitionRevisionId=revision.recognition_revision_id,
            mayAffectAbility=allowed,
            mayAffectCause=allowed,
            mayAffectPlan=allowed,
        )

    def _revise(
        self,
        *,
        owner_user_id: UUID,
        source: RecognitionRevision,
        expected_revision_no: int,
        state: CandidateState,
        payload: RecognitionCandidatePayload,
        confidence: float | None,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        now: datetime,
    ) -> RecognitionRevision:
        revision_id = _command_id(owner_user_id, operation, idempotency_key)
        existing = self._repository.revision(recognition_revision_id=revision_id)
        if existing is not None:
            self._assert_replay(existing, request_hash)
            return existing
        self._repository.lock(
            key=_aggregate_lock(owner_user_id, source.artifact_version_id, source.recognition_kind)
        )
        existing = self._repository.revision(recognition_revision_id=revision_id)
        if existing is not None:
            self._assert_replay(existing, request_hash)
            return existing
        head = self._repository.head(
            artifact_version_id=source.artifact_version_id,
            owner_user_id=owner_user_id,
            recognition_kind=source.recognition_kind.value,
        )
        if (
            head is None
            or head.recognition_revision_id != source.recognition_revision_id
            or head.revision_no != expected_revision_no
        ):
            raise RecognitionVersionConflict("recognition head changed before revision")
        sealed = payload.model_copy(
            update={
                "request_hash": request_hash,
                "predecessor_revision_id": source.recognition_revision_id,
            }
        )
        dumped = sealed.model_dump(by_alias=True, mode="json")
        revision = RecognitionRevision(
            recognitionRevisionId=revision_id,
            artifactVersionId=source.artifact_version_id,
            ownerUserId=owner_user_id,
            revisionNo=head.revision_no + 1,
            recognitionKind=source.recognition_kind,
            state=state,
            processorPolicyVersion=source.processor_policy_version,
            processorRoute=("user_correction" if operation == "correct" else "user_review"),
            inputSha256=source.input_sha256,
            outputSha256=_hash(dumped),
            candidatePayload=dumped,
            confidence=confidence,
            supersedesRecognitionRevisionId=source.recognition_revision_id,
            expiresAt=source.expires_at,
            confirmedAt=now if state == CandidateState.CONFIRMED else None,
            createdAt=now,
        )
        self._repository.insert(revision)
        return revision

    def _owned_revision(
        self, owner_user_id: UUID, recognition_revision_id: UUID
    ) -> RecognitionRevision:
        revision = self._repository.revision(recognition_revision_id=recognition_revision_id)
        if revision is None or revision.owner_user_id != owner_user_id:
            raise RecognitionNotFound("recognition revision was not found for this owner")
        return revision

    @staticmethod
    def _assert_replay(revision: RecognitionRevision, request_hash: str) -> None:
        if _payload(revision).request_hash != request_hash:
            raise RecognitionIdempotencyConflict(
                "idempotency key was already used for a different recognition request"
            )


def _payload(revision: RecognitionRevision) -> RecognitionCandidatePayload:
    return RecognitionCandidatePayload.model_validate(revision.candidate_payload)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


def _question_units(*, input_sha256: str, segments: tuple) -> tuple[QuestionUnit, ...]:
    """Deterministically group processor segments into independent questions.

    Material remains shared by consecutive prompts until the processor emits a new
    material block. Options bind to the closest preceding prompt. The provider does
    not control unit identifiers, which keeps correction and replay lineage stable.
    """

    ordered = sorted(
        segments,
        key=lambda item: (
            item.box.page_no,
            round(item.box.y, 8),
            round(item.box.x, 8),
            item.segment_id,
        ),
    )
    active_materials: list[str] = []
    material_after_prompt = False
    units: list[dict[str, object]] = []
    for segment in ordered:
        if segment.segment_kind == "material":
            if units and not material_after_prompt:
                active_materials = []
            active_materials.append(segment.segment_id)
            material_after_prompt = True
            continue
        if segment.segment_kind in {"question", "essay_prompt"}:
            units.append(
                {
                    "prompts": [segment.segment_id],
                    "options": [],
                    "materials": list(active_materials),
                }
            )
            material_after_prompt = False
            continue
        if segment.segment_kind == "options" and units:
            options = units[-1]["options"]
            assert isinstance(options, list)
            options.append(segment.segment_id)

    sealed: list[QuestionUnit] = []
    for ordinal, unit in enumerate(units, start=1):
        identity = {
            "inputSha256": input_sha256,
            "ordinal": ordinal,
            "promptSegmentRefs": unit["prompts"],
            "optionSegmentRefs": unit["options"],
            "materialSegmentRefs": unit["materials"],
        }
        sealed.append(
            QuestionUnit(
                questionUnitId=_hash(identity),
                ordinal=ordinal,
                promptSegmentRefs=tuple(unit["prompts"]),
                optionSegmentRefs=tuple(unit["options"]),
                materialSegmentRefs=tuple(unit["materials"]),
            )
        )
    if not sealed:
        raise ArtifactNotProcessable(
            "segmentation output contains no independently matchable question prompt"
        )
    return tuple(sealed)


def _latency_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _processing_error_code(exc: Exception) -> str:
    declared = getattr(exc, "code", None)
    if isinstance(declared, str) and declared.strip():
        normalized = declared.strip().casefold()
        if all(character.isalnum() or character == "_" for character in normalized):
            return normalized[:160]
    if isinstance(exc, (TypeError, ValueError)):
        return "recognition_output_contract_invalid"
    return "recognition_processing_failed"


def _failure_usage_state(
    exc: Exception,
    *,
    provider_response_received: bool,
) -> Literal["actual", "unknown", "not_dispatched"]:
    if isinstance(exc, ProcessorNotDispatchedError):
        return "not_dispatched"
    if provider_response_received:
        return "actual"
    if isinstance(exc, ProcessorResponseError) and not isinstance(
        exc, ProcessorDispatchUnknownError
    ):
        return "actual"
    return "unknown"


def _command_id(owner_user_id: UUID, operation: str, idempotency_key: str) -> UUID:
    if not idempotency_key.strip():
        raise ValueError("idempotency key is required")
    return uuid5(NAMESPACE_URL, f"recognition:{owner_user_id}:{operation}:{idempotency_key}")


def _aggregate_lock(
    owner_user_id: UUID, artifact_version_id: UUID, recognition_kind: RecognitionKind
) -> str:
    return f"recognition:{owner_user_id}:{artifact_version_id}:{recognition_kind.value}"
