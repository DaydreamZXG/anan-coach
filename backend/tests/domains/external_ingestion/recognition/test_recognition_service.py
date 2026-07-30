from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts.external_learning.models import CandidateState, RecognitionKind
from app.domains.external_ingestion.recognition.errors import (
    ProcessorNotApproved,
    RecognitionIdempotencyConflict,
    RecognitionNotFound,
    RecognitionOperationPersistenceError,
    RecognitionOperationReconciliationRequired,
    RecognitionTransitionError,
    RecognitionVersionConflict,
)
from app.domains.external_ingestion.recognition.models import (
    ArtifactInput,
    BoundingBox,
    ProcessorApproval,
    ProcessorResult,
    PageSegment,
    RecognitionCandidatePayload,
    QuestionUnit,
    TextSpan,
)
from app.domains.external_ingestion.recognition.processor import ProcessorRegistry
from app.domains.external_ingestion.recognition.operation_contracts import (
    RecognitionOperationState,
)
from app.domains.external_ingestion.recognition.service import RecognitionService
from app.domains.external_ingestion.recognition.service import _question_units


NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)
OWNER = UUID("00000000-0000-0000-0000-000000000101")
OTHER = UUID("00000000-0000-0000-0000-000000000202")
ARTIFACT = UUID("00000000-0000-0000-0000-000000000301")


class Repo:
    def __init__(self) -> None:
        self.artifacts = {
            (ARTIFACT, OWNER): ArtifactInput(
                artifactVersionId=ARTIFACT,
                ownerUserId=OWNER,
                state="available",
                sourceKind="camera_image",
                objectRef="tos://private/user-101/artifact-301",
                mimeType="image/jpeg",
                contentSha256="1" * 64,
            )
        }
        self.revisions = {}
        self.insert_failures = 0
        self.locks: list[str] = []
        self.operations = Operations(self)

    def lock(self, *, key: str) -> None:
        self.locks.append(key)

    def artifact(self, *, artifact_version_id, owner_user_id):
        return self.artifacts.get((artifact_version_id, owner_user_id))

    def revision(self, *, recognition_revision_id):
        return self.revisions.get(recognition_revision_id)

    def head(self, *, artifact_version_id, owner_user_id, recognition_kind):
        matches = [
            item
            for item in self.revisions.values()
            if item.artifact_version_id == artifact_version_id
            and item.owner_user_id == owner_user_id
            and item.recognition_kind.value == recognition_kind
        ]
        return max(matches, key=lambda item: item.revision_no, default=None)

    def insert(self, revision):
        if self.insert_failures > 0:
            self.insert_failures -= 1
            raise RuntimeError("revision persistence failed")
        if revision.recognition_revision_id in self.revisions:
            raise AssertionError("duplicate recognition revision")
        self.revisions[revision.recognition_revision_id] = revision


class Operations:
    def __init__(self, repository: Repo) -> None:
        self.repository = repository
        self.rows = {}
        self.receipts: list[UUID] = []
        self.quota_states: dict[UUID, str] = {}
        self.quota_transitions: list[tuple[UUID, str]] = []
        self.fail_persistence_errors = 0

    def begin(self, *, spec):
        existing = self.rows.get(spec.operation_id)
        if existing is not None:
            if existing["spec"].request_hash != spec.request_hash:
                raise RecognitionIdempotencyConflict("recognition idempotency key request changed")
            return self._state(spec.operation_id, created=False)
        self.rows[spec.operation_id] = {
            "spec": spec,
            "status": "running",
            "usage_state": "planned",
            "output_fingerprint": None,
            "error_code": None,
            "total_cost_cny": None,
        }
        self.receipts.append(spec.operation_id)
        self.quota_states[spec.operation_id] = "reserved"
        return self._state(spec.operation_id, created=True)

    def succeed(
        self,
        *,
        operation_id,
        revision,
        output_fingerprint,
        latency_ms,
        completed_at,
    ):
        row = self.rows[operation_id]
        if row["status"] == "running":
            self.repository.insert(revision)
            row.update(
                status="succeeded",
                usage_state="actual",
                output_fingerprint=output_fingerprint,
                total_cost_cny=Decimal("0.01"),
                latency_ms=latency_ms,
                completed_at=completed_at,
            )
            self.quota_states[operation_id] = "consumed"
            self.quota_transitions.append((operation_id, "consumed"))
        return self._state(operation_id, created=False)

    def fail(
        self,
        *,
        operation_id,
        error_code,
        usage_state,
        latency_ms,
        completed_at,
    ):
        if self.fail_persistence_errors > 0:
            self.fail_persistence_errors -= 1
            raise RuntimeError("terminal database unavailable")
        row = self.rows[operation_id]
        if row["status"] == "running":
            terminal_status = "outcome_unknown" if usage_state == "unknown" else "failed"
            row.update(
                status=terminal_status,
                usage_state=usage_state,
                total_cost_cny=(
                    Decimal("0.01")
                    if usage_state == "actual"
                    else Decimal("0")
                    if usage_state == "not_dispatched"
                    else None
                ),
                error_code=error_code,
                latency_ms=latency_ms,
                completed_at=completed_at,
            )
            self.quota_states[operation_id] = "refunded"
            self.quota_transitions.append((operation_id, "refunded"))
        return self._state(operation_id, created=False)

    def _state(self, operation_id: UUID, *, created: bool):
        row = self.rows[operation_id]
        spec = row["spec"]
        return RecognitionOperationState(
            operation_id=operation_id,
            operation_key=spec.operation_key,
            status=row["status"],
            usage_state=row["usage_state"],
            created=created,
        )


class Processor:
    def __init__(self, *, failures: int = 0, malformed_results: int = 0) -> None:
        self.failures = failures
        self.malformed_results = malformed_results
        self.calls = 0
        self.requests = []

    @property
    def provider_release(self):
        return "release-2026-07-01"

    def process(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.calls <= self.failures:
            raise TimeoutError("provider accepted the request and then timed out")
        malformed = self.calls <= self.failures + self.malformed_results
        return ProcessorResult(
            providerRequestId=f"provider-request-{self.calls}",
            textSpans=()
            if malformed
            else (
                TextSpan(
                    spanId="span-1",
                    text="下列说法正确的是",
                    box=BoundingBox(pageNo=1, x=0.1, y=0.1, width=0.8, height=0.1),
                    confidence=0.97,
                ),
            ),
            pageImageSha256=("2" * 64,),
            visualFingerprints=("3" * 64,),
            confidence=0.97,
        )


def approval(*, valid_until: datetime = NOW + timedelta(days=30)) -> ProcessorApproval:
    return ProcessorApproval(
        policyVersion="ocr-private-cn@2026-07-15",
        route="private-ocr-primary",
        provider="approved-ocr-vendor",
        providerRelease="release-2026-07-01",
        modelRelease="ocr-model-2026-07-01",
        promptVersion="recognition-processor-policy@v1",
        priceVersion="ocr-price@2026-07",
        billingUnit="image",
        unitPriceCny="0.01",
        approvedKinds=(RecognitionKind.PRINTED_OCR,),
        approvedMimeTypes=("image/jpeg",),
        dataRegion="mainland_china",
        dpaEvidenceRef="dpa:ocr:2026",
        securityReviewRef="security-review:ocr:2026-07",
        approvalReceiptRef="approval:ocr:2026-07",
        providerIdempotencyCapability="replayable_queryable",
        providerIdempotencyScope="provider_account",
        providerIdempotencyKeyHeader="Idempotency-Key",
        providerRequestIdSource="body:providerRequestId",
        providerReplayRetentionSeconds=604_800,
        providerIdempotencyEvidenceRef="provider-contract:ocr:2026-07",
        validFrom=NOW - timedelta(days=1),
        validUntil=valid_until,
    )


def service(repo: Repo, processor: Processor, *, approval_value=None) -> RecognitionService:
    return RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=ProcessorRegistry(
            (approval_value or approval(),), {"private-ocr-primary": processor}
        ),
        clock=lambda: NOW,
    )


def recognize(value: RecognitionService, *, key: str = "recognize-1"):
    return value.recognize(
        owner_user_id=OWNER,
        artifact_version_id=ARTIFACT,
        recognition_kind=RecognitionKind.PRINTED_OCR,
        processor_route="private-ocr-primary",
        idempotency_key=key,
    )


def test_candidate_is_immutable_and_cannot_affect_learning_until_user_confirmation() -> None:
    repo = Repo()
    processor = Processor()
    pipeline = service(repo, processor)

    candidate = recognize(pipeline)
    before = pipeline.evidence_decision(candidate)
    confirmed = pipeline.confirm(
        owner_user_id=OWNER,
        recognition_revision_id=candidate.recognition_revision_id,
        expected_revision_no=1,
        idempotency_key="confirm-1",
    )
    after = pipeline.evidence_decision(confirmed)

    assert candidate.state == CandidateState.CANDIDATE
    assert confirmed.state == CandidateState.CONFIRMED
    assert confirmed.revision_no == 2
    assert confirmed.recognition_revision_id != candidate.recognition_revision_id
    assert before.allowed is False
    assert not any((before.may_affect_ability, before.may_affect_cause, before.may_affect_plan))
    assert after.allowed is True
    assert all((after.may_affect_ability, after.may_affect_cause, after.may_affect_plan))


def test_recognition_replay_returns_same_revision_without_recalling_provider() -> None:
    repo = Repo()
    processor = Processor()
    pipeline = service(repo, processor)

    first = recognize(pipeline)
    replay = recognize(pipeline)

    assert replay == first
    assert processor.calls == 1
    assert len(repo.revisions) == 1


def test_provider_replay_key_is_server_namespaced_and_cross_owner_isolated() -> None:
    first_repo = Repo()
    first_processor = Processor()
    recognize(service(first_repo, first_processor), key="client-reused-key")

    second_repo = Repo()
    second_repo.artifacts[(ARTIFACT, OTHER)] = ArtifactInput(
        artifactVersionId=ARTIFACT,
        ownerUserId=OTHER,
        state="available",
        sourceKind="camera_image",
        objectRef="tos://private/user-202/artifact-301",
        mimeType="image/jpeg",
        contentSha256="2" * 64,
    )
    second_processor = Processor()
    second_pipeline = service(second_repo, second_processor)
    second_pipeline.recognize(
        owner_user_id=OTHER,
        artifact_version_id=ARTIFACT,
        recognition_kind=RecognitionKind.PRINTED_OCR,
        processor_route="private-ocr-primary",
        idempotency_key="client-reused-key",
    )

    first_key = first_processor.requests[0].idempotency_key
    second_key = second_processor.requests[0].idempotency_key
    assert first_key.startswith("ocr-v1-")
    assert second_key.startswith("ocr-v1-")
    assert "client-reused-key" not in first_key
    assert first_key != second_key


def test_same_idempotency_key_with_changed_request_is_rejected() -> None:
    repo = Repo()
    processor = Processor()
    pipeline = service(repo, processor)
    recognize(pipeline)

    with pytest.raises(RecognitionIdempotencyConflict):
        pipeline.recognize(
            owner_user_id=OWNER,
            artifact_version_id=ARTIFACT,
            recognition_kind=RecognitionKind.PRINTED_OCR,
            processor_route="changed-route",
            idempotency_key="recognize-1",
        )


@pytest.mark.parametrize("failure_mode", ["accepted_timeout", "malformed_response"])
def test_initial_provider_failure_closes_terminal_and_same_key_never_redispatches(
    failure_mode: str,
) -> None:
    repo = Repo()
    processor = Processor(
        failures=1 if failure_mode == "accepted_timeout" else 0,
        malformed_results=1 if failure_mode == "malformed_response" else 0,
    )
    pipeline = service(repo, processor)

    expected_error = TimeoutError if failure_mode == "accepted_timeout" else ValueError
    with pytest.raises(expected_error):
        recognize(pipeline)
    assert repo.revisions == {}
    operation_id = next(iter(repo.operations.rows))
    operation = repo.operations.rows[operation_id]
    assert operation["status"] == (
        "outcome_unknown" if failure_mode == "accepted_timeout" else "failed"
    )
    expected_usage_state = "unknown" if failure_mode == "accepted_timeout" else "actual"
    assert operation["usage_state"] == expected_usage_state
    assert operation["total_cost_cny"] == (
        None if failure_mode == "accepted_timeout" else Decimal("0.01")
    )
    assert operation["output_fingerprint"] is None
    assert repo.operations.receipts == [operation_id]
    assert repo.operations.quota_states[operation_id] == "refunded"

    with pytest.raises(RecognitionOperationReconciliationRequired):
        recognize(pipeline)
    assert processor.calls == 1
    assert repo.operations.quota_transitions.count((operation_id, "refunded")) == 1


def test_initial_running_operation_fails_closed_when_terminal_write_is_unavailable() -> None:
    repo = Repo()
    repo.operations.fail_persistence_errors = 1
    processor = Processor(failures=1)
    pipeline = service(repo, processor)

    with pytest.raises(RecognitionOperationPersistenceError):
        recognize(pipeline, key="recognize-running-unknown")
    operation_id = next(iter(repo.operations.rows))
    assert repo.operations.rows[operation_id]["status"] == "running"
    assert repo.operations.rows[operation_id]["usage_state"] == "planned"
    assert repo.operations.quota_states[operation_id] == "reserved"

    with pytest.raises(RecognitionOperationReconciliationRequired):
        recognize(pipeline, key="recognize-running-unknown")
    assert processor.calls == 1


@pytest.mark.parametrize(
    "registry, expected",
    [
        (ProcessorRegistry((), {}), "not fully approved"),
        (
            ProcessorRegistry((approval(valid_until=NOW),), {"private-ocr-primary": Processor()}),
            "outside its approved",
        ),
    ],
)
def test_unconfigured_or_expired_processor_fails_closed(registry, expected) -> None:
    repo = Repo()
    pipeline = RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=registry,
        clock=lambda: NOW,
    )

    with pytest.raises(ProcessorNotApproved, match=expected):
        recognize(pipeline)


def test_user_correction_creates_new_candidate_and_preserves_visual_source() -> None:
    repo = Repo()
    pipeline = service(repo, Processor())
    source = recognize(pipeline)
    original = RecognitionCandidatePayload.model_validate(source.candidate_payload)
    corrected_input = original.model_copy(
        update={
            "text_spans": (
                original.text_spans[0].model_copy(update={"text": "下列说法不正确的是"}),
            )
        }
    )

    corrected = pipeline.correct(
        owner_user_id=OWNER,
        recognition_revision_id=source.recognition_revision_id,
        expected_revision_no=1,
        corrected_payload=corrected_input,
        reason="OCR 漏了“不”字",
        idempotency_key="correct-1",
    )
    payload = RecognitionCandidatePayload.model_validate(corrected.candidate_payload)

    assert corrected.revision_no == 2
    assert payload.text_spans[0].text == "下列说法不正确的是"
    assert payload.predecessor_revision_id == source.recognition_revision_id
    assert payload.visual_fingerprints == original.visual_fingerprints
    assert pipeline.evidence_decision(corrected).allowed is False


def test_stale_competing_confirmation_is_rejected_after_first_head_wins() -> None:
    repo = Repo()
    pipeline = service(repo, Processor())
    candidate = recognize(pipeline)
    pipeline.confirm(
        owner_user_id=OWNER,
        recognition_revision_id=candidate.recognition_revision_id,
        expected_revision_no=1,
        idempotency_key="confirm-winner",
    )

    with pytest.raises(RecognitionVersionConflict):
        pipeline.reject(
            owner_user_id=OWNER,
            recognition_revision_id=candidate.recognition_revision_id,
            expected_revision_no=1,
            idempotency_key="reject-loser",
        )


def test_new_correction_revokes_old_confirmed_revision_current_authority() -> None:
    repo = Repo()
    pipeline = service(repo, Processor())
    candidate = recognize(pipeline)
    confirmed = pipeline.confirm(
        owner_user_id=OWNER,
        recognition_revision_id=candidate.recognition_revision_id,
        expected_revision_no=1,
        idempotency_key="confirm-before-correction",
    )
    payload = RecognitionCandidatePayload.model_validate(confirmed.candidate_payload)
    corrected_input = payload.model_copy(
        update={"text_spans": (payload.text_spans[0].model_copy(update={"text": "修订后的题干"}),)}
    )

    corrected = pipeline.correct(
        owner_user_id=OWNER,
        recognition_revision_id=confirmed.recognition_revision_id,
        expected_revision_no=2,
        corrected_payload=corrected_input,
        reason="确认后发现仍有错字",
        idempotency_key="correction-after-confirmation",
    )

    assert pipeline.evidence_decision(confirmed).reason == "revision_superseded"
    assert pipeline.evidence_decision(corrected).reason == "candidate_unconfirmed"


def test_owner_isolation_returns_not_found_instead_of_leaking_revision() -> None:
    repo = Repo()
    pipeline = service(repo, Processor())
    candidate = recognize(pipeline)

    with pytest.raises(RecognitionNotFound):
        pipeline.confirm(
            owner_user_id=OTHER,
            recognition_revision_id=candidate.recognition_revision_id,
            expected_revision_no=1,
            idempotency_key="other-confirm",
        )


def test_expired_candidate_cannot_be_confirmed_or_admitted() -> None:
    repo = Repo()
    processor = Processor()
    pipeline = RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=ProcessorRegistry((approval(),), {"private-ocr-primary": processor}),
        clock=lambda: NOW,
        candidate_ttl=timedelta(seconds=1),
    )
    candidate = recognize(pipeline)
    later = RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=ProcessorRegistry((approval(),), {"private-ocr-primary": processor}),
        clock=lambda: NOW + timedelta(seconds=2),
    )

    with pytest.raises(RecognitionTransitionError, match="expired"):
        later.confirm(
            owner_user_id=OWNER,
            recognition_revision_id=candidate.recognition_revision_id,
            expected_revision_no=1,
            idempotency_key="late-confirm",
        )
    assert later.evidence_decision(candidate).reason == "revision_expired"

    recovered = later.reprocess(
        owner_user_id=OWNER,
        recognition_revision_id=candidate.recognition_revision_id,
        expected_revision_no=1,
        processor_route="private-ocr-primary",
        idempotency_key="reprocess-expired",
    )
    replay = later.reprocess(
        owner_user_id=OWNER,
        recognition_revision_id=candidate.recognition_revision_id,
        expected_revision_no=1,
        processor_route="private-ocr-primary",
        idempotency_key="reprocess-expired",
    )

    assert recovered.revision_no == 2
    assert recovered.expires_at > NOW + timedelta(seconds=2)
    assert replay == recovered


@pytest.mark.parametrize("failure_mode", ["accepted_timeout", "malformed_response"])
def test_reprocess_provider_failure_closes_terminal_and_same_key_never_redispatches(
    failure_mode: str,
) -> None:
    repo = Repo()
    initial_processor = Processor()
    initial = RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=ProcessorRegistry((approval(),), {"private-ocr-primary": initial_processor}),
        clock=lambda: NOW,
        candidate_ttl=timedelta(seconds=1),
    )
    source = recognize(initial, key="recognize-before-reprocess-failure")
    processor = Processor(
        failures=1 if failure_mode == "accepted_timeout" else 0,
        malformed_results=1 if failure_mode == "malformed_response" else 0,
    )
    later = RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=ProcessorRegistry((approval(),), {"private-ocr-primary": processor}),
        clock=lambda: NOW + timedelta(seconds=2),
    )

    expected_error = TimeoutError if failure_mode == "accepted_timeout" else ValueError
    with pytest.raises(expected_error):
        later.reprocess(
            owner_user_id=OWNER,
            recognition_revision_id=source.recognition_revision_id,
            expected_revision_no=1,
            processor_route="private-ocr-primary",
            idempotency_key="reprocess-provider-failure",
        )
    failed = [
        (operation_id, row)
        for operation_id, row in repo.operations.rows.items()
        if row["status"] == ("outcome_unknown" if failure_mode == "accepted_timeout" else "failed")
    ]
    assert len(failed) == 1
    operation_id, operation = failed[0]
    expected_usage_state = "unknown" if failure_mode == "accepted_timeout" else "actual"
    assert operation["usage_state"] == expected_usage_state
    assert operation["total_cost_cny"] == (
        None if failure_mode == "accepted_timeout" else Decimal("0.01")
    )
    assert repo.operations.quota_states[operation_id] == "refunded"

    with pytest.raises(RecognitionOperationReconciliationRequired):
        later.reprocess(
            owner_user_id=OWNER,
            recognition_revision_id=source.recognition_revision_id,
            expected_revision_no=1,
            processor_route="private-ocr-primary",
            idempotency_key="reprocess-provider-failure",
        )
    assert processor.calls == 1
    assert repo.operations.quota_transitions.count((operation_id, "refunded")) == 1


@pytest.mark.parametrize("command", ["recognize", "reprocess"])
def test_post_provider_revision_persistence_failure_is_actual_and_never_redispatches(
    command: str,
) -> None:
    repo = Repo()
    source = None
    if command == "reprocess":
        source_pipeline = RecognitionService(
            repository=repo,
            operations=repo.operations,
            processors=ProcessorRegistry((approval(),), {"private-ocr-primary": Processor()}),
            clock=lambda: NOW,
            candidate_ttl=timedelta(seconds=1),
        )
        source = recognize(
            source_pipeline,
            key="recognize-before-revision-persistence-failure",
        )

    repo.insert_failures = 1
    processor = Processor()
    pipeline = RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=ProcessorRegistry((approval(),), {"private-ocr-primary": processor}),
        clock=lambda: NOW + timedelta(seconds=2),
    )

    def dispatch() -> None:
        if command == "recognize":
            recognize(pipeline, key="recognize-revision-persistence-failure")
            return
        assert source is not None
        pipeline.reprocess(
            owner_user_id=OWNER,
            recognition_revision_id=source.recognition_revision_id,
            expected_revision_no=1,
            processor_route="private-ocr-primary",
            idempotency_key="reprocess-revision-persistence-failure",
        )

    with pytest.raises(RecognitionOperationPersistenceError):
        dispatch()

    failed = [
        (operation_id, row)
        for operation_id, row in repo.operations.rows.items()
        if row["status"] == "failed"
    ]
    assert len(failed) == 1
    operation_id, operation = failed[0]
    assert operation["usage_state"] == "actual"
    assert operation["total_cost_cny"] == Decimal("0.01")
    assert operation["output_fingerprint"] is None
    assert repo.operations.quota_states[operation_id] == "refunded"
    assert repo.operations.quota_transitions.count((operation_id, "refunded")) == 1

    with pytest.raises(RecognitionOperationReconciliationRequired):
        dispatch()
    assert processor.calls == 1


def test_reprocess_running_operation_fails_closed_when_terminal_write_is_unavailable() -> None:
    repo = Repo()
    initial = RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=ProcessorRegistry((approval(),), {"private-ocr-primary": Processor()}),
        clock=lambda: NOW,
        candidate_ttl=timedelta(seconds=1),
    )
    source = recognize(initial, key="recognize-before-running-reprocess")
    repo.operations.fail_persistence_errors = 1
    processor = Processor(failures=1)
    later = RecognitionService(
        repository=repo,
        operations=repo.operations,
        processors=ProcessorRegistry((approval(),), {"private-ocr-primary": processor}),
        clock=lambda: NOW + timedelta(seconds=2),
    )

    with pytest.raises(RecognitionOperationPersistenceError):
        later.reprocess(
            owner_user_id=OWNER,
            recognition_revision_id=source.recognition_revision_id,
            expected_revision_no=1,
            processor_route="private-ocr-primary",
            idempotency_key="reprocess-running-unknown",
        )
    running = [
        (operation_id, row)
        for operation_id, row in repo.operations.rows.items()
        if row["status"] == "running"
    ]
    assert len(running) == 1
    operation_id, operation = running[0]
    assert operation["usage_state"] == "planned"
    assert repo.operations.quota_states[operation_id] == "reserved"

    with pytest.raises(RecognitionOperationReconciliationRequired):
        later.reprocess(
            owner_user_id=OWNER,
            recognition_revision_id=source.recognition_revision_id,
            expected_revision_no=1,
            processor_route="private-ocr-primary",
            idempotency_key="reprocess-running-unknown",
        )
    assert processor.calls == 1


def test_text_span_ids_are_unique_and_segment_refs_must_resolve() -> None:
    box = BoundingBox(pageNo=1, x=0.1, y=0.1, width=0.8, height=0.1)
    span = TextSpan(spanId="span-1", text="题干", box=box, confidence=0.9)
    base = {
        "kind": RecognitionKind.EXAM_SEGMENTATION,
        "textSpans": (span,),
        "segments": (
            PageSegment(
                segmentId="segment-1",
                segmentKind="question",
                box=box,
                textSpanRefs=("span-1",),
                confidence=0.9,
            ),
        ),
        "questionUnits": (
            QuestionUnit(
                questionUnitId="7" * 64,
                ordinal=1,
                promptSegmentRefs=("segment-1",),
            ),
        ),
        "processorRequestIdHash": "8" * 64,
        "processorOutputContract": "recognition-processor-output@v1",
        "requestHash": "9" * 64,
    }

    valid = RecognitionCandidatePayload.model_validate(base)
    assert valid.segments[0].text_span_refs == ("span-1",)

    with pytest.raises(ValueError, match="span ids must be unique"):
        RecognitionCandidatePayload.model_validate(
            {**base, "textSpans": (span, span.model_copy(update={"text": "材料"}))}
        )
    with pytest.raises(ValueError, match="must resolve"):
        RecognitionCandidatePayload.model_validate(
            {
                **base,
                "segments": (
                    base["segments"][0].model_copy(update={"text_span_refs": ("missing-span",)}),
                ),
            }
        )
    with pytest.raises(ValueError, match="textSpanRefs must be unique"):
        PageSegment(
            segmentId="segment-duplicate-ref",
            segmentKind="question",
            box=box,
            textSpanRefs=("span-1", "span-1"),
            confidence=0.9,
        )


def test_multi_question_segmentation_shares_material_and_isolates_options() -> None:
    def segment(segment_id: str, kind: str, y: float) -> PageSegment:
        return PageSegment(
            segmentId=segment_id,
            segmentKind=kind,
            box=BoundingBox(pageNo=1, x=0.1, y=y, width=0.8, height=0.08),
            confidence=0.95,
        )

    units = _question_units(
        input_sha256="a" * 64,
        segments=(
            segment("material-1", "material", 0.05),
            segment("question-1", "question", 0.20),
            segment("options-1", "options", 0.30),
            segment("question-2", "question", 0.50),
            segment("options-2", "options", 0.60),
        ),
    )

    assert [unit.ordinal for unit in units] == [1, 2]
    assert units[0].material_segment_refs == ("material-1",)
    assert units[1].material_segment_refs == ("material-1",)
    assert units[0].option_segment_refs == ("options-1",)
    assert units[1].option_segment_refs == ("options-2",)
    assert units[0].question_unit_id != units[1].question_unit_id
