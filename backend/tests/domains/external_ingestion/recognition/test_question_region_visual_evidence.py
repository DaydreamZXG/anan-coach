from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image, ImageDraw

from app.contracts.external_learning.models import LearningTrack
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    PageSegment,
    ProcessorResult,
    QuestionRegionVisualEvidence,
    QuestionUnit,
    RecognitionCandidatePayload,
    TextSpan,
    VisualFeatureHash,
)
from app.domains.external_ingestion.recognition.question_region_visual import (
    derive_question_region_visual_evidence,
)
from app.domains.external_ingestion.recognition.service import (
    _question_region_visual_evidence,
    _secure_correction_payload,
    _trusted_rendered_page_images,
)
from app.domains.external_ingestion.workspace import _match_query


def test_same_page_questions_receive_distinct_region_hashes_and_match_queries() -> None:
    page = _page_image()
    spans, segments, units = _layout(
        (
            (1, 0.10, 0.10, 1, 0.28),
            (1, 0.55, 0.10, 1, 0.72),
        )
    )

    evidence = derive_question_region_visual_evidence(
        question_units=units,
        segments=segments,
        rendered_page_images=(page,),
    )
    payload = _payload(
        spans=spans,
        segments=segments,
        units=units,
        page_count=1,
        evidence=evidence,
    )
    recognition = SimpleNamespace(
        candidate_payload=payload.model_dump(by_alias=True, mode="json")
    )
    queries = tuple(
        _match_query(
            recognition,
            track=LearningTrack.XINGCE,
            question_unit_id=unit.question_unit_id,
        )
        for unit in units
    )

    assert len(evidence) == 2
    assert evidence[0].visual_fingerprints != evidence[1].visual_fingerprints
    assert queries[0].visual_fingerprints == evidence[0].visual_fingerprints
    assert queries[1].visual_fingerprints == evidence[1].visual_fingerprints
    assert queries[0].visual_feature_hashes == evidence[0].visual_feature_hashes
    assert queries[1].visual_feature_hashes == evidence[1].visual_feature_hashes
    dumped = payload.model_dump(by_alias=True, mode="json")
    assert "trusted_rendered_page_images" not in dumped
    assert "data:image" not in str(dumped)


def test_overlapping_or_cross_page_questions_fall_back_without_visual_evidence() -> None:
    pages = (_page_image(), _page_image(invert=True))
    spans, segments, units = _layout(
        (
            (1, 0.10, 0.25, None, None),
            (1, 0.20, 0.20, None, None),
            (1, 0.70, 0.08, 2, 0.10),
        )
    )

    evidence = derive_question_region_visual_evidence(
        question_units=units,
        segments=segments,
        rendered_page_images=pages,
    )
    payload = _payload(
        spans=spans,
        segments=segments,
        units=units,
        page_count=2,
        evidence=evidence,
    )
    recognition = SimpleNamespace(
        candidate_payload=payload.model_dump(by_alias=True, mode="json")
    )
    queries = tuple(
        _match_query(
            recognition,
            track=LearningTrack.XINGCE,
            question_unit_id=unit.question_unit_id,
        )
        for unit in units
    )

    assert evidence == ()
    assert all(query.visual_fingerprints == () for query in queries)
    assert all(query.visual_feature_hashes == () for query in queries)


def test_region_visual_derivation_is_deterministic_for_exact_replay() -> None:
    page = _page_image()
    _spans, segments, units = _layout(
        (
            (1, 0.10, 0.10, 1, 0.28),
            (1, 0.55, 0.10, 1, 0.72),
        )
    )

    first = derive_question_region_visual_evidence(
        question_units=units,
        segments=segments,
        rendered_page_images=(page,),
    )
    second = derive_question_region_visual_evidence(
        question_units=units,
        segments=segments,
        rendered_page_images=(page,),
    )

    assert first == second
    assert tuple(item.model_dump_json(by_alias=True) for item in first) == tuple(
        item.model_dump_json(by_alias=True) for item in second
    )


def test_text_correction_preserves_frozen_region_evidence_and_legacy_payloads_remain_safe() -> None:
    page = _page_image()
    spans, segments, units = _layout(
        (
            (1, 0.10, 0.10, 1, 0.28),
            (1, 0.55, 0.10, 1, 0.72),
        )
    )
    evidence = derive_question_region_visual_evidence(
        question_units=units,
        segments=segments,
        rendered_page_images=(page,),
    )
    source = _payload(
        spans=spans,
        segments=segments,
        units=units,
        page_count=1,
        evidence=evidence,
    )
    corrected = source.model_copy(
        update={
            "text_spans": tuple(
                item.model_copy(update={"text": item.text + "（用户修订）"})
                for item in source.text_spans
            )
        }
    )

    sealed = _secure_correction_payload(
        source_payload=source,
        corrected_payload=corrected,
        predecessor_revision_id=uuid4(),
        reason="修正识别文字",
    )

    assert sealed.question_region_visual_evidence == source.question_region_visual_evidence
    assert sealed.visual_feature_hashes == source.visual_feature_hashes
    legacy_data = source.model_dump(by_alias=True, mode="json")
    legacy_data.pop("questionRegionVisualEvidence")
    legacy = RecognitionCandidatePayload.model_validate(legacy_data)
    query = _match_query(
        SimpleNamespace(candidate_payload=legacy_data),
        track=LearningTrack.XINGCE,
        question_unit_id=legacy.question_units[0].question_unit_id,
    )
    assert legacy.question_region_visual_evidence == ()
    assert query.visual_fingerprints == ()
    assert query.visual_feature_hashes == ()


def test_region_visual_derivation_is_thread_safe_and_has_no_shared_mutable_state() -> None:
    page = _page_image()
    _spans, segments, units = _layout(
        (
            (1, 0.10, 0.10, 1, 0.28),
            (1, 0.55, 0.10, 1, 0.72),
        )
    )

    def derive() -> tuple[QuestionRegionVisualEvidence, ...]:
        return derive_question_region_visual_evidence(
            question_units=units,
            segments=segments,
            rendered_page_images=(page,),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(lambda _index: derive(), range(24)))

    assert results
    assert all(result == results[0] for result in results)


def test_untrusted_processor_cannot_inject_region_source_pages() -> None:
    page = _page_image()
    _spans, segments, units = _layout(((1, 0.10, 0.10, 1, 0.28),))
    result = ProcessorResult(
        providerRequestId="remote-provider-request",
        segments=segments,
        pageImageSha256=("a" * 64,),
        trusted_rendered_page_images=(page,),
    )

    class UntrustedProcessor:
        pass

    class TrustedLocalProcessor:
        supplies_trusted_rendered_pages = True

    assert _trusted_rendered_page_images(
        processor=UntrustedProcessor(), result=result
    ) == ()
    assert _question_region_visual_evidence(
        processor=UntrustedProcessor(),
        result=result,
        question_units=units,
    ) == ()
    assert _trusted_rendered_page_images(
        processor=TrustedLocalProcessor(), result=result
    ) == (page,)
    assert len(
        _question_region_visual_evidence(
            processor=TrustedLocalProcessor(),
            result=result,
            question_units=units,
        )
    ) == 1
    assert "trusted_rendered_page_images" not in result.model_dump()


def _page_image(*, invert: bool = False) -> bytes:
    image = Image.new("RGB", (480, 960), "white")
    draw = ImageDraw.Draw(image)
    upper = "navy" if invert else "red"
    lower = "orange" if invert else "blue"
    draw.rectangle((0, 80, 479, 500), fill=upper)
    draw.rectangle((0, 520, 479, 959), fill=lower)
    for offset in range(0, 420, 40):
        draw.line((0, 90 + offset, 479, 90 + offset), fill="white", width=5)
    for offset in range(0, 440, 55):
        draw.line((offset, 520, 479 - offset // 2, 959), fill="yellow", width=7)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _layout(
    specs: tuple[tuple[int, float, float, int | None, float | None], ...]
) -> tuple[tuple[TextSpan, ...], tuple[PageSegment, ...], tuple[QuestionUnit, ...]]:
    spans: list[TextSpan] = []
    segments: list[PageSegment] = []
    units: list[QuestionUnit] = []
    for ordinal, (prompt_page, prompt_y, prompt_height, option_page, option_y) in enumerate(
        specs, start=1
    ):
        prompt_span = TextSpan(
            spanId=f"prompt-span-{ordinal}",
            text=f"题干{ordinal}",
            box=BoundingBox(
                pageNo=prompt_page,
                x=0.08,
                y=prompt_y,
                width=0.84,
                height=prompt_height,
            ),
            confidence=0.98,
        )
        prompt_segment = PageSegment(
            segmentId=f"prompt-{ordinal}",
            segmentKind="question",
            box=prompt_span.box,
            textSpanRefs=(prompt_span.span_id,),
            confidence=0.98,
        )
        spans.append(prompt_span)
        segments.append(prompt_segment)
        option_refs: tuple[str, ...] = ()
        if option_page is not None and option_y is not None:
            option_span = TextSpan(
                spanId=f"option-span-{ordinal}",
                text=f"A. 选项{ordinal}",
                box=BoundingBox(
                    pageNo=option_page,
                    x=0.10,
                    y=option_y,
                    width=0.80,
                    height=0.08,
                ),
                confidence=0.97,
            )
            option_segment = PageSegment(
                segmentId=f"options-{ordinal}",
                segmentKind="options",
                box=option_span.box,
                textSpanRefs=(option_span.span_id,),
                confidence=0.97,
            )
            spans.append(option_span)
            segments.append(option_segment)
            option_refs = (option_segment.segment_id,)
        units.append(
            QuestionUnit(
                questionUnitId=f"{ordinal:x}" * 64,
                ordinal=ordinal,
                promptSegmentRefs=(prompt_segment.segment_id,),
                optionSegmentRefs=option_refs,
            )
        )
    return tuple(spans), tuple(segments), tuple(units)


def _payload(
    *,
    spans: tuple[TextSpan, ...],
    segments: tuple[PageSegment, ...],
    units: tuple[QuestionUnit, ...],
    page_count: int,
    evidence: tuple[QuestionRegionVisualEvidence, ...],
) -> RecognitionCandidatePayload:
    return RecognitionCandidatePayload(
        kind="exam_segmentation",
        textSpans=spans,
        segments=segments,
        questionUnits=units,
        pageImageSha256=tuple(f"{index + 8:x}" * 64 for index in range(page_count)),
        visualFingerprints=tuple(f"{index + 10:x}" * 64 for index in range(page_count)),
        visualFeatureHashes=(
            VisualFeatureHash(
                phash="1" * 16,
                dhash="2" * 16,
                ahash="3" * 16,
                edgeDhash="4" * 16,
            ),
        ),
        questionRegionVisualEvidence=evidence,
        processorRequestIdHash="d" * 64,
        processorOutputContract="recognition-processor-output@v1",
        requestHash="e" * 64,
    )
