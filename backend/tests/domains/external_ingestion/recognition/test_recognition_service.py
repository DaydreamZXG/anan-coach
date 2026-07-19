from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.contracts.external_learning.models import CandidateState, RecognitionKind
from app.domains.external_ingestion.recognition.errors import (
    ProcessorNotApproved,
    RecognitionIdempotencyConflict,
    RecognitionNotFound,
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
        self.locks: list[str] = []
        self.operations: list[dict[str, object]] = []

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
        if revision.recognition_revision_id in self.revisions:
            raise AssertionError("duplicate recognition revision")
        self.revisions[revision.recognition_revision_id] = revision

    def record_external_operation(self, *, values):
        self.operations.append(dict(values))


class Processor:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    @property
    def provider_release(self):
        return "release-2026-07-01"

    def process(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("provider unavailable")
        return ProcessorResult(
            providerRequestId=f"provider-request-{self.calls}",
            textSpans=(
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
        validFrom=NOW - timedelta(days=1),
        validUntil=valid_until,
    )


def service(repo: Repo, processor: Processor, *, approval_value=None) -> RecognitionService:
    return RecognitionService(
        repository=repo,
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
    assert not any(
        (before.may_affect_ability, before.may_affect_cause, before.may_affect_plan)
    )
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


def test_provider_failure_writes_nothing_and_retry_recovers() -> None:
    repo = Repo()
    processor = Processor(failures=1)
    pipeline = service(repo, processor)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        recognize(pipeline)
    assert repo.revisions == {}

    recovered = recognize(pipeline)
    assert recovered.state == CandidateState.CANDIDATE
    assert processor.calls == 2


@pytest.mark.parametrize(
    "registry, expected",
    [
        (ProcessorRegistry((), {}), "not fully approved"),
        (
            ProcessorRegistry(
                (approval(valid_until=NOW),), {"private-ocr-primary": Processor()}
            ),
            "outside its approved",
        ),
    ],
)
def test_unconfigured_or_expired_processor_fails_closed(registry, expected) -> None:
    pipeline = RecognitionService(repository=Repo(), processors=registry, clock=lambda: NOW)

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
        update={
            "text_spans": (
                payload.text_spans[0].model_copy(update={"text": "修订后的题干"}),
            )
        }
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
        processors=ProcessorRegistry((approval(),), {"private-ocr-primary": processor}),
        clock=lambda: NOW,
        candidate_ttl=timedelta(seconds=1),
    )
    candidate = recognize(pipeline)
    later = RecognitionService(
        repository=repo,
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
                    base["segments"][0].model_copy(
                        update={"text_span_refs": ("missing-span",)}
                    ),
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
