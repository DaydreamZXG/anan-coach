from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import time
from uuid import NAMESPACE_URL, UUID, uuid5

from app.contracts.external_learning.models import (
    CandidateState,
    RecognitionKind,
    RecognitionRevision,
)
from app.domains.external_ingestion.recognition.errors import (
    ArtifactNotProcessable,
    RecognitionIdempotencyConflict,
    RecognitionNotFound,
    RecognitionTransitionError,
    RecognitionVersionConflict,
)
from app.domains.external_ingestion.recognition.models import (
    RecognitionCandidatePayload,
    RecognitionEvidenceDecision,
    OMRLayout,
    ProcessorRequest,
    QuestionUnit,
)
from app.domains.external_ingestion.recognition.processor import ProcessorRegistry
from app.domains.external_ingestion.recognition.repository import RecognitionRepositoryPort


class RecognitionService:
    def __init__(
        self,
        *,
        repository: RecognitionRepositoryPort,
        processors: ProcessorRegistry,
        clock: Callable[[], datetime] | None = None,
        candidate_ttl: timedelta = timedelta(days=7),
    ) -> None:
        if candidate_ttl <= timedelta(0):
            raise ValueError("recognition candidate TTL must be positive")
        self._repository = repository
        self._processors = processors
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
        if self._repository.head(
            artifact_version_id=artifact_version_id,
            owner_user_id=owner_user_id,
            recognition_kind=recognition_kind.value,
        ) is not None:
            raise RecognitionVersionConflict(
                "initial recognition already exists; use correction or a distinct kind"
            )

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
            idempotencyKey=idempotency_key,
            omrLayout=omr_layout,
        )
        started = time.monotonic()
        result = processor.process(processor_request)
        latency_ms = int((time.monotonic() - started) * 1000)
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
        self._repository.insert(revision)
        self._record_external_cost(
            revision=revision,
            artifact=artifact,
            approval=approval,
            latency_ms=latency_ms,
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
            key=_aggregate_lock(
                owner_user_id, source.artifact_version_id, source.recognition_kind
            )
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
        started = time.monotonic()
        result = processor.process(
            ProcessorRequest(
                artifactVersionId=artifact.artifact_version_id,
                ownerScopeHash=_hash({"ownerUserId": str(owner_user_id)}),
                objectRef=artifact.object_ref,
                mimeType=artifact.mime_type,
                inputSha256=artifact.content_sha256,
                pageCount=artifact.page_count,
                recognitionKind=source.recognition_kind,
                idempotencyKey=idempotency_key,
                omrLayout=omr_layout,
            )
        )
        latency_ms = int((time.monotonic() - started) * 1000)
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
        self._repository.insert(revision)
        self._record_external_cost(
            revision=revision,
            artifact=artifact,
            approval=approval,
            latency_ms=latency_ms,
        )
        return revision

    def _record_external_cost(
        self,
        *,
        revision: RecognitionRevision,
        artifact,
        approval,
        latency_ms: int,
    ) -> None:
        unit_count = (
            artifact.page_count or 1
            if approval.billing_unit in {"page", "image"}
            else 1
        )
        scenario_key = {
            RecognitionKind.PRINTED_OCR: "recognition_printed_ocr",
            RecognitionKind.HANDWRITING_OCR: "recognition_handwriting_ocr",
            RecognitionKind.EXAM_SEGMENTATION: "recognition_printed_ocr",
            RecognitionKind.ANSWER_SHEET_OMR: "recognition_score_report",
        }[revision.recognition_kind]
        self._repository.record_external_operation(
            values={
                "operation_id": str(revision.recognition_revision_id),
                "operation_key": f"recognition:{revision.recognition_revision_id}",
                "user_id": str(revision.owner_user_id),
                "scenario_key": scenario_key,
                "operation": "recognition_process",
                "provider": approval.provider,
                "model": approval.model_release,
                "provider_release": approval.provider_release,
                "prompt_version": approval.prompt_version,
                "schema_version": approval.output_contract,
                "price_version": approval.price_version,
                "billing_unit": approval.billing_unit,
                "unit_count": unit_count,
                "unit_price_cny": approval.unit_price_cny,
                "input_fingerprint": revision.input_sha256,
                "output_fingerprint": revision.output_sha256,
                "evidence_refs": json.dumps(
                    [
                        {
                            "kind": "source_artifact",
                            "sourceId": str(revision.artifact_version_id),
                            "version": revision.input_sha256,
                            "anchor": "artifact",
                        }
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "latency_ms": latency_ms,
                "completed_at": self._clock(),
            }
        )

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
            key=_aggregate_lock(
                owner_user_id, source.artifact_version_id, source.recognition_kind
            )
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
        revision = self._repository.revision(
            recognition_revision_id=recognition_revision_id
        )
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


def _command_id(owner_user_id: UUID, operation: str, idempotency_key: str) -> UUID:
    if not idempotency_key.strip():
        raise ValueError("idempotency key is required")
    return uuid5(NAMESPACE_URL, f"recognition:{owner_user_id}:{operation}:{idempotency_key}")


def _aggregate_lock(
    owner_user_id: UUID, artifact_version_id: UUID, recognition_kind: RecognitionKind
) -> str:
    return f"recognition:{owner_user_id}:{artifact_version_id}:{recognition_kind.value}"
