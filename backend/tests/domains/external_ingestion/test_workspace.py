from __future__ import annotations

from types import MethodType, SimpleNamespace
from uuid import uuid4

from app.contracts.external_learning.models import CandidateState, LearningTrack
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    PageSegment,
    QuestionUnit,
    RecognitionCandidatePayload,
    TextSpan,
    VisualFeatureHash,
)
from app.domains.external_ingestion.workspace import (
    ExternalImportWorkspaceService,
    _match_query,
)


class _Uploads:
    def __init__(self, upload: object) -> None:
        self._upload = upload

    def session(self, *, session_id):  # noqa: ANN001, ANN201
        del session_id
        return self._upload


class _Recognitions:
    def __init__(self) -> None:
        self.artifact_version_id = None

    def recognize(self, **kwargs):  # noqa: ANN003, ANN201
        self.artifact_version_id = kwargs["artifact_version_id"]
        return SimpleNamespace(
            recognition_revision_id=uuid4(),
            revision_no=1,
            state=CandidateState.CANDIDATE,
        )


class _RecognitionRepository:
    def __init__(self, existing: object | None) -> None:
        self._existing = existing

    def head(self, **_kwargs):  # noqa: ANN003, ANN201
        return self._existing


def test_deduplicated_import_processes_the_resolved_artifact_version() -> None:
    owner_user_id = uuid4()
    import_id = uuid4()
    transient_artifact_version_id = uuid4()
    resolved_artifact_version_id = uuid4()
    upload = SimpleNamespace(
        owner_user_id=owner_user_id,
        artifact_version_id=transient_artifact_version_id,
        resolved_artifact_version_id=resolved_artifact_version_id,
        recognition_eligible=True,
    )
    recognitions = _Recognitions()
    service = ExternalImportWorkspaceService.__new__(ExternalImportWorkspaceService)
    service._uploads = _Uploads(upload)  # type: ignore[attr-defined]
    service._recognitions = recognitions  # type: ignore[attr-defined]
    service._recognition_repository = _RecognitionRepository(None)  # type: ignore[attr-defined]
    service.confirm_recognition_and_match = MethodType(  # type: ignore[method-assign]
        lambda self, **kwargs: ("searched", kwargs),
        service,
    )

    result = service.process(
        owner_user_id=owner_user_id,
        import_id=import_id,
        learning_track=LearningTrack.XINGCE,
        processor_route="modal-exam-image-v1",
        idempotency_key="deduplicated-import-key",
    )

    assert result[0] == "searched"
    assert result[1]["learning_track"] == LearningTrack.XINGCE
    assert recognitions.artifact_version_id == resolved_artifact_version_id
    assert recognitions.artifact_version_id != transient_artifact_version_id


def test_deduplicated_import_reuses_existing_recognition() -> None:
    owner_user_id = uuid4()
    import_id = uuid4()
    resolved_artifact_version_id = uuid4()
    existing = SimpleNamespace(state=CandidateState.CONFIRMED)
    upload = SimpleNamespace(
        owner_user_id=owner_user_id,
        artifact_version_id=uuid4(),
        resolved_artifact_version_id=resolved_artifact_version_id,
        recognition_eligible=True,
    )
    recognitions = _Recognitions()
    service = ExternalImportWorkspaceService.__new__(ExternalImportWorkspaceService)
    service._uploads = _Uploads(upload)  # type: ignore[attr-defined]
    service._recognitions = recognitions  # type: ignore[attr-defined]
    service._recognition_repository = _RecognitionRepository(existing)  # type: ignore[attr-defined]
    service.workspace = MethodType(  # type: ignore[method-assign]
        lambda self, **kwargs: kwargs["preferred_recognition"],
        service,
    )

    result = service.process(
        owner_user_id=owner_user_id,
        import_id=import_id,
        learning_track=LearningTrack.XINGCE,
        processor_route="mineru-ocr-v1",
        idempotency_key="deduplicated-existing-recognition",
    )

    assert result is existing
    assert recognitions.artifact_version_id is None


def test_deduplicated_candidate_is_automatically_confirmed_and_searched() -> None:
    owner_user_id = uuid4()
    import_id = uuid4()
    existing = SimpleNamespace(
        recognition_revision_id=uuid4(),
        revision_no=3,
        state=CandidateState.CANDIDATE,
    )
    upload = SimpleNamespace(
        owner_user_id=owner_user_id,
        artifact_version_id=uuid4(),
        resolved_artifact_version_id=uuid4(),
        recognition_eligible=True,
    )
    recognitions = _Recognitions()
    service = ExternalImportWorkspaceService.__new__(ExternalImportWorkspaceService)
    service._uploads = _Uploads(upload)  # type: ignore[attr-defined]
    service._recognitions = recognitions  # type: ignore[attr-defined]
    service._recognition_repository = _RecognitionRepository(existing)  # type: ignore[attr-defined]
    service.confirm_recognition_and_match = MethodType(  # type: ignore[method-assign]
        lambda self, **kwargs: kwargs,
        service,
    )

    result = service.process(
        owner_user_id=owner_user_id,
        import_id=import_id,
        learning_track=LearningTrack.XINGCE,
        processor_route="mineru-ocr-v1",
        idempotency_key="deduplicated-candidate",
    )

    assert result["recognition_revision_id"] == existing.recognition_revision_id
    assert result["expected_revision_no"] == 3
    assert result["idempotency_key"] == "deduplicated-candidate:automatic-search"
    assert recognitions.artifact_version_id is None


def test_single_question_match_query_keeps_whole_input_visual_evidence() -> None:
    recognition, units = _recognition_with_units(page_numbers=(1,))

    query = _match_query(
        recognition,
        track=LearningTrack.XINGCE,
        question_unit_id=units[0].question_unit_id,
    )

    assert query.visual_fingerprints == ("a" * 64,)
    assert len(query.visual_feature_hashes) == 1


def test_multi_question_same_page_drops_global_visual_evidence() -> None:
    recognition, units = _recognition_with_units(page_numbers=(1, 1))

    queries = [
        _match_query(
            recognition,
            track=LearningTrack.XINGCE,
            question_unit_id=unit.question_unit_id,
        )
        for unit in units
    ]

    assert [query.prompt_text for query in queries] == ["题干1", "题干2"]
    assert all(query.visual_fingerprints == () for query in queries)
    assert all(query.visual_feature_hashes == () for query in queries)


def test_multi_page_book_scopes_one_page_fingerprint_to_each_question() -> None:
    recognition, units = _recognition_with_units(page_numbers=(1, 2))

    first = _match_query(
        recognition,
        track=LearningTrack.XINGCE,
        question_unit_id=units[0].question_unit_id,
    )
    second = _match_query(
        recognition,
        track=LearningTrack.XINGCE,
        question_unit_id=units[1].question_unit_id,
    )

    assert first.visual_fingerprints == ("a" * 64,)
    assert second.visual_fingerprints == ("b" * 64,)
    assert first.visual_feature_hashes == ()
    assert second.visual_feature_hashes == ()


def test_multi_question_book_drops_unscoped_visual_hash_collections() -> None:
    recognition, units = _recognition_with_units(
        page_numbers=(1, 2),
        visual_fingerprints=("a" * 64, "b" * 64, "c" * 64),
    )

    query = _match_query(
        recognition,
        track=LearningTrack.XINGCE,
        question_unit_id=units[0].question_unit_id,
    )

    assert query.visual_fingerprints == ()
    assert query.visual_feature_hashes == ()


def _recognition_with_units(
    *,
    page_numbers: tuple[int, ...],
    visual_fingerprints: tuple[str, ...] | None = None,
) -> tuple[SimpleNamespace, tuple[QuestionUnit, ...]]:
    spans = tuple(
        TextSpan(
            spanId=f"span-{index}",
            text=f"题干{index}",
            box=BoundingBox(
                pageNo=page_no,
                x=0.1,
                y=0.1 if index == 1 else 0.55,
                width=0.8,
                height=0.2,
            ),
            confidence=0.95,
        )
        for index, page_no in enumerate(page_numbers, start=1)
    )
    segments = tuple(
        PageSegment(
            segmentId=f"segment-{index}",
            segmentKind="question",
            box=span.box,
            textSpanRefs=(span.span_id,),
            confidence=0.95,
        )
        for index, span in enumerate(spans, start=1)
    )
    units = tuple(
        QuestionUnit(
            questionUnitId=f"{index:x}" * 64,
            ordinal=index,
            promptSegmentRefs=(segment.segment_id,),
        )
        for index, segment in enumerate(segments, start=1)
    )
    page_count = max(page_numbers)
    payload = RecognitionCandidatePayload(
        kind="exam_segmentation",
        textSpans=spans,
        segments=segments,
        questionUnits=units,
        pageImageSha256=tuple(f"{index + 8:x}" * 64 for index in range(page_count)),
        visualFingerprints=visual_fingerprints
        if visual_fingerprints is not None
        else tuple(f"{index + 10:x}" * 64 for index in range(page_count)),
        visualFeatureHashes=(
            VisualFeatureHash(
                phash="1" * 16,
                dhash="2" * 16,
                ahash="3" * 16,
                edgeDhash="4" * 16,
            ),
        ),
        processorRequestIdHash="d" * 64,
        processorOutputContract="recognition-processor-output@v1",
        requestHash="e" * 64,
    )
    return SimpleNamespace(candidate_payload=payload.model_dump(by_alias=True, mode="json")), units
