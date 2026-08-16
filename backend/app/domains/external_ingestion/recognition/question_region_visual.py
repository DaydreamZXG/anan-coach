from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from math import ceil, floor

from PIL import Image, ImageOps

from app.domains.external_ingestion.recognition.models import (
    NormalizedRegionBox,
    PageSegment,
    QuestionRegionVisualEvidence,
    QuestionUnit,
    VisualFeatureHash,
)
from app.domains.external_ingestion.recognition.visual_hash import (
    fingerprint_sha256,
    matching_feature_hashes,
)


@dataclass(frozen=True)
class _QuestionAnchor:
    unit: QuestionUnit
    page_no: int
    prompt_top: float
    prompt_bottom: float
    referenced_bottom: float | None


def derive_question_region_visual_evidence(
    *,
    question_units: tuple[QuestionUnit, ...],
    segments: tuple[PageSegment, ...],
    rendered_page_images: tuple[bytes, ...],
) -> tuple[QuestionRegionVisualEvidence, ...]:
    """Derive safe per-question hashes from trusted, already-rendered page bytes.

    The crop boundary is intentionally conservative: a full-width vertical band
    starts at the question prompt and ends at the next prompt on the same page (or
    page bottom). Ambiguous prompt overlap, unresolved references, cross-page units
    and referenced content crossing that boundary produce no evidence.
    """

    if not question_units or not segments or not rendered_page_images:
        return ()

    by_segment_id = {item.segment_id: item for item in segments}
    anchors_by_page: dict[int, list[_QuestionAnchor]] = {}
    for unit in question_units:
        prompt_segments = [by_segment_id.get(ref) for ref in unit.prompt_segment_refs]
        if any(item is None for item in prompt_segments):
            continue
        resolved_prompts = tuple(item for item in prompt_segments if item is not None)
        prompt_pages = {item.box.page_no for item in resolved_prompts}
        if len(prompt_pages) != 1:
            continue
        page_no = next(iter(prompt_pages))
        if not 1 <= page_no <= len(rendered_page_images):
            continue

        all_refs = (
            *unit.prompt_segment_refs,
            *unit.option_segment_refs,
            *unit.material_segment_refs,
        )
        referenced = [by_segment_id.get(ref) for ref in all_refs]
        referenced_bottom: float | None = None
        if all(item is not None for item in referenced):
            resolved = tuple(item for item in referenced if item is not None)
            referenced_pages = {item.box.page_no for item in resolved}
            if referenced_pages == {page_no}:
                referenced_bottom = max(item.box.y + item.box.height for item in resolved)

        anchors_by_page.setdefault(page_no, []).append(
            _QuestionAnchor(
                unit=unit,
                page_no=page_no,
                prompt_top=min(item.box.y for item in resolved_prompts),
                prompt_bottom=max(
                    item.box.y + item.box.height for item in resolved_prompts
                ),
                referenced_bottom=referenced_bottom,
            )
        )

    decoded_pages = _decode_pages(rendered_page_images)
    evidence: list[QuestionRegionVisualEvidence] = []
    for page_no, page_anchors in anchors_by_page.items():
        page = decoded_pages.get(page_no)
        if page is None:
            continue
        ordered = sorted(
            page_anchors,
            key=lambda item: (
                item.prompt_top,
                item.prompt_bottom,
                item.unit.ordinal,
                item.unit.question_unit_id,
            ),
        )
        overlapping = _overlapping_unit_ids(ordered)
        for index, anchor in enumerate(ordered):
            if (
                anchor.unit.question_unit_id in overlapping
                or anchor.referenced_bottom is None
            ):
                continue
            next_top = ordered[index + 1].prompt_top if index + 1 < len(ordered) else 1.0
            if next_top <= anchor.prompt_top:
                continue
            if anchor.referenced_bottom > next_top + 1e-9:
                continue
            region = _region_evidence(
                anchor=anchor,
                bottom=next_top,
                page=page,
                source_page_bytes=rendered_page_images[page_no - 1],
            )
            if region is not None:
                evidence.append(region)

    return tuple(sorted(evidence, key=lambda item: _ordinal(item, question_units)))


def _decode_pages(rendered_page_images: tuple[bytes, ...]) -> dict[int, Image.Image]:
    decoded: dict[int, Image.Image] = {}
    for page_no, source in enumerate(rendered_page_images, start=1):
        if not source:
            continue
        try:
            with Image.open(BytesIO(source)) as opened:
                decoded[page_no] = ImageOps.exif_transpose(opened).convert("RGB")
        except (OSError, ValueError):
            continue
    return decoded


def _overlapping_unit_ids(anchors: list[_QuestionAnchor]) -> set[str]:
    overlapping: set[str] = set()
    for index, left in enumerate(anchors):
        for right in anchors[index + 1 :]:
            if right.prompt_top >= left.prompt_bottom:
                break
            if left.prompt_top < right.prompt_bottom and right.prompt_top < left.prompt_bottom:
                overlapping.add(left.unit.question_unit_id)
                overlapping.add(right.unit.question_unit_id)
    return overlapping


def _region_evidence(
    *,
    anchor: _QuestionAnchor,
    bottom: float,
    page: Image.Image,
    source_page_bytes: bytes,
) -> QuestionRegionVisualEvidence | None:
    width, height = page.size
    if width <= 0 or height <= 0:
        return None
    top_px = max(0, min(height - 1, floor(anchor.prompt_top * height)))
    bottom_px = max(top_px + 1, min(height, ceil(bottom * height)))
    if bottom_px <= top_px:
        return None
    crop = page.crop((0, top_px, width, bottom_px))
    raw_hashes = matching_feature_hashes(crop)
    if not raw_hashes:
        return None
    feature_hashes = tuple(VisualFeatureHash.model_validate(item) for item in raw_hashes)
    fingerprints = tuple(fingerprint_sha256(item) for item in raw_hashes)
    return QuestionRegionVisualEvidence(
        questionUnitId=anchor.unit.question_unit_id,
        pageNo=anchor.page_no,
        box=NormalizedRegionBox(
            x=0.0,
            y=top_px / height,
            width=1.0,
            height=(bottom_px - top_px) / height,
        ),
        sourcePageImageSha256=sha256(source_page_bytes).hexdigest(),
        visualFingerprints=fingerprints,
        visualFeatureHashes=feature_hashes,
    )


def _ordinal(
    evidence: QuestionRegionVisualEvidence,
    question_units: tuple[QuestionUnit, ...],
) -> int:
    return next(
        item.ordinal
        for item in question_units
        if item.question_unit_id == evidence.question_unit_id
    )
