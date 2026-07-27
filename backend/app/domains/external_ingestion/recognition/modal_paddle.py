from __future__ import annotations

import base64
from contextlib import AbstractContextManager
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
from typing import Callable, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.contracts.external_learning.models import RecognitionKind
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    ProcessorResponseError,
)
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    PageSegment,
    ProcessorRequest,
    ProcessorResult,
    TextSpan,
)


_OPTION_MARKER = re.compile(r"(?:^|[\s\n])([A-H])[.．、:：)）]")
_OPTION_PREFIX = re.compile(r"^\s*([A-H])[.．、:：)）]\s*")
_QUESTION_CUES = (
    "下列说法",
    "下列选项",
    "正确的是",
    "错误的是",
    "不正确的是",
    "最恰当的是",
    "最合适的是",
    "最可能的是",
    "不能推出",
    "可以推出",
    "由此可知",
    "主要说明",
    "意在说明",
    "这段文字",
    "根据材料",
    "根据上述",
)
_ESSAY_CUES = (
    "作答要求",
    "请结合给定资料",
    "请根据给定资料",
    "请概括",
    "请分析",
    "请提出",
    "提出对策",
    "撰写一篇",
    "写一篇",
    "谈谈你的理解",
)
class _PrivateSourceReader(Protocol):
    def open(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        maximum_bytes: int,
        prefix: str,
        suffix: str,
    ) -> AbstractContextManager[Path]: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ModalBlock(_StrictModel):
    text: str = Field(min_length=1, max_length=100_000)
    bbox: tuple[float, float, float, float]
    label: str = Field(min_length=1, max_length=100)
    order: int | None = None
    confidence: float = Field(ge=0, le=1)
    cx: float
    cy: float
    bw: float
    bh: float

    @model_validator(mode="after")
    def valid_geometry(self) -> "_ModalBlock":
        x1, y1, x2, y2 = self.bbox
        values = (x1, y1, x2, y2, self.cx, self.cy, self.bw, self.bh)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("block geometry must be finite")
        if x2 <= x1 or y2 <= y1 or self.bw <= 0 or self.bh <= 0:
            raise ValueError("block geometry must have positive area")
        return self


class _CandidateSummary(_StrictModel):
    angle: Literal[0, 90, 180, 270]
    score: float = Field(allow_inf_nan=False)
    elapsed_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class _ModalResponse(_StrictModel):
    schema_version: Literal["anan-modal-ocr-response@v1"]
    provider_release: str = Field(min_length=1)
    model_release: str = Field(min_length=1)
    provider_request_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_angle: Literal[0, 90, 180, 270]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    score: float = Field(allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=500_000)
    blocks: tuple[_ModalBlock, ...] = Field(min_length=1, max_length=2_000)
    candidate_summary: tuple[_CandidateSummary, ...] = Field(
        min_length=1,
        max_length=4,
    )
    elapsed_ms: int = Field(ge=0)


class ModalPaddleOCRRecognitionProcessor:
    """Authenticated Modal PaddleOCR-VL adapter sealed into the product contract."""

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        expected_provider_release: str,
        expected_model_release: str,
        source_reader: _PrivateSourceReader,
        maximum_input_bytes: int,
        maximum_output_bytes: int,
        timeout_seconds: float = 300,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ProcessorConfigurationError(
                "Modal recognition endpoint must be an absolute HTTPS URL"
            )
        if not all(
            value.strip()
            for value in (
                bearer_token,
                expected_provider_release,
                expected_model_release,
            )
        ):
            raise ProcessorConfigurationError("Modal recognition provider identity is incomplete")
        if maximum_input_bytes <= 0 or maximum_output_bytes <= 0:
            raise ProcessorConfigurationError(
                "Modal recognition input and output limits must be positive"
            )
        self._endpoint = endpoint
        self._token = bearer_token
        self._release = expected_provider_release
        self._model_release = expected_model_release
        self._source_reader = source_reader
        self._maximum_input_bytes = maximum_input_bytes
        self._maximum_output_bytes = maximum_output_bytes
        self._client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(
                    timeout_seconds,
                    connect=min(20.0, timeout_seconds),
                ),
                follow_redirects=False,
            )
        )

    @property
    def provider_release(self) -> str:
        return self._release

    def process(self, request: ProcessorRequest) -> ProcessorResult:
        if request.mime_type not in {"image/jpeg", "image/png"}:
            raise ProcessorResponseError("Modal recognition supports JPEG and PNG only")
        if request.page_count not in {None, 1}:
            raise ProcessorResponseError("Modal recognition accepts one image page per request")
        if request.recognition_kind not in {
            RecognitionKind.PRINTED_OCR,
            RecognitionKind.EXAM_SEGMENTATION,
        }:
            raise ProcessorResponseError("Modal recognition kind is not supported")
        suffix = ".jpg" if request.mime_type == "image/jpeg" else ".png"
        try:
            with self._source_reader.open(
                object_ref=request.object_ref,
                expected_sha256=request.input_sha256,
                maximum_bytes=self._maximum_input_bytes,
                prefix="anan-modal-paddle-",
                suffix=suffix,
            ) as source:
                raw = source.read_bytes()
                if not raw or len(raw) > self._maximum_input_bytes:
                    raise ProcessorResponseError(
                        "Modal recognition input exceeds its approved capacity"
                    )
                page_hash, visual_fingerprint = _local_image_evidence(
                    raw,
                    expected_mime_type=request.mime_type,
                )
                payload = {
                    "schema_version": "anan-modal-ocr-request@v1",
                    "mime_type": request.mime_type,
                    "input_sha256": request.input_sha256,
                    "image_base64": base64.b64encode(raw).decode("ascii"),
                    "mode": "fast",
                    "max_candidates": 3,
                }
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                headers = {
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": request.idempotency_key,
                }
                with self._client_factory() as client:
                    with client.stream(
                        "POST",
                        self._endpoint,
                        headers=headers,
                        content=encoded,
                    ) as streamed:
                        response = _bounded_response(
                            streamed,
                            maximum_bytes=self._maximum_output_bytes,
                        )
                response.raise_for_status()
        except ProcessorResponseError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise ProcessorResponseError(
                "Modal recognition provider or private source is unavailable"
            ) from exc
        return self._result(
            response=response,
            request=request,
            page_hash=page_hash,
            visual_fingerprint=visual_fingerprint,
        )

    def _result(
        self,
        *,
        response: httpx.Response,
        request: ProcessorRequest,
        page_hash: str,
        visual_fingerprint: str,
    ) -> ProcessorResult:
        try:
            payload = _ModalResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ProcessorResponseError(
                "Modal recognition returned no schema-valid result"
            ) from exc
        release_header = response.headers.get("X-Processor-Release", "").strip()
        request_header = response.headers.get("X-Request-ID", "").strip()
        if release_header != self._release or payload.provider_release != self._release:
            raise ProcessorResponseError("Modal recognition provider release drifted")
        if payload.model_release != self._model_release:
            raise ProcessorResponseError("Modal recognition model release drifted")
        if payload.input_sha256 != request.input_sha256:
            raise ProcessorResponseError("Modal recognition input identity drifted")
        if not request_header or request_header != payload.provider_request_id:
            raise ProcessorResponseError("Modal recognition request identity drifted")
        spans = _text_spans(payload)
        segments = (
            _exam_segments(spans)
            if request.recognition_kind == RecognitionKind.EXAM_SEGMENTATION
            else ()
        )
        return ProcessorResult(
            providerRequestId=payload.provider_request_id,
            textSpans=spans,
            segments=segments,
            pageImageSha256=(page_hash,),
            visualFingerprints=(visual_fingerprint,),
            confidence=_aggregate_confidence(spans),
        )


def _text_spans(payload: _ModalResponse) -> tuple[TextSpan, ...]:
    spans: list[TextSpan] = []
    for index, block in enumerate(payload.blocks, start=1):
        box = _normalized_box(
            block.bbox,
            page_width=payload.width,
            page_height=payload.height,
        )
        text = block.text.strip()
        if not text:
            continue
        identity = sha256(f"{index}\0{text}\0{box.x:.6f}\0{box.y:.6f}".encode("utf-8")).hexdigest()[
            :20
        ]
        spans.append(
            TextSpan(
                spanId=f"modal-span-{index}-{identity}",
                text=text,
                box=box,
                confidence=block.confidence,
            )
        )
    if not spans:
        raise ProcessorResponseError("Modal recognition returned no usable text")
    return tuple(spans)


def _exam_segments(spans: tuple[TextSpan, ...]) -> tuple[PageSegment, ...]:
    option_indexes = {index for index, span in enumerate(spans) if _looks_like_options(span.text)}
    non_options = [index for index in range(len(spans)) if index not in option_indexes]
    if not non_options:
        raise ProcessorResponseError("Modal recognition found options without a question")
    first_option = min(option_indexes) if option_indexes else None
    prompt_candidates = [
        index
        for index in non_options
        if _looks_like_question(spans[index].text)
        and (first_option is None or index < first_option)
    ]
    if prompt_candidates:
        prompt_index = prompt_candidates[-1]
    elif option_indexes:
        assert first_option is not None
        predecessors = [index for index in non_options if index < first_option]
        if not predecessors:
            raise ProcessorResponseError("Modal recognition cannot locate the question prompt")
        prompt_index = predecessors[-1]
    else:
        prompt_index = non_options[-1]
    prompt_kind = (
        "essay_prompt"
        if not option_indexes and _looks_like_essay_prompt(spans[prompt_index].text)
        else "question"
    )
    material_indexes = [
        index
        for index in non_options
        if index < prompt_index
        and spans[index].text.strip()
        and spans[index].text.strip() != spans[prompt_index].text.strip()
    ]
    trailing_indexes = [index for index in non_options if index > prompt_index]
    segments = [
        _segment(
            "prompt",
            prompt_kind,
            (spans[prompt_index],),
            confidence=spans[prompt_index].confidence,
        )
    ]
    if option_indexes:
        options = tuple(spans[index] for index in sorted(option_indexes))
        segments.append(
            _segment(
                "options",
                "options",
                options,
                confidence=_aggregate_confidence(options),
            )
        )
    if material_indexes:
        materials = tuple(spans[index] for index in material_indexes)
        segments.append(
            _segment(
                "material",
                "material",
                materials,
                confidence=_aggregate_confidence(materials),
            )
        )
    if trailing_indexes:
        trailing = tuple(spans[index] for index in trailing_indexes)
        segments.append(
            _segment(
                "trailing",
                "unknown",
                trailing,
                confidence=_aggregate_confidence(trailing),
            )
        )
    return tuple(segments)


def _segment(
    name: str,
    kind: Literal[
        "question",
        "options",
        "material",
        "answer_area",
        "essay_prompt",
        "unknown",
    ],
    spans: tuple[TextSpan, ...],
    *,
    confidence: float,
) -> PageSegment:
    box = _union_box(tuple(span.box for span in spans))
    identity = sha256(
        (name + "\0" + "\0".join(span.span_id for span in spans)).encode("utf-8")
    ).hexdigest()[:20]
    return PageSegment(
        segmentId=f"modal-segment-{name}-{identity}",
        segmentKind=kind,
        box=box,
        textSpanRefs=tuple(span.span_id for span in spans),
        confidence=confidence,
    )


def _looks_like_question(text: str) -> bool:
    normalized = text.strip()
    return (
        normalized.endswith(("?", "？"))
        or any(cue in normalized for cue in _QUESTION_CUES)
        or _looks_like_essay_prompt(normalized)
    )


def _looks_like_options(text: str) -> bool:
    return bool(_OPTION_PREFIX.match(text)) or len(_OPTION_MARKER.findall(text)) >= 2


def _looks_like_essay_prompt(text: str) -> bool:
    return any(cue in text for cue in _ESSAY_CUES)


def _normalized_box(
    coordinates: tuple[float, float, float, float],
    *,
    page_width: int,
    page_height: int,
) -> BoundingBox:
    x1, y1, x2, y2 = coordinates
    left = min(max(min(x1, x2) / page_width, 0.0), 0.999999)
    top = min(max(min(y1, y2) / page_height, 0.0), 0.999999)
    right = min(max(max(x1, x2) / page_width, left + 0.000001), 1.0)
    bottom = min(max(max(y1, y2) / page_height, top + 0.000001), 1.0)
    return BoundingBox(
        pageNo=1,
        x=left,
        y=top,
        width=right - left,
        height=bottom - top,
    )


def _union_box(boxes: tuple[BoundingBox, ...]) -> BoundingBox:
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.x + box.width for box in boxes)
    bottom = max(box.y + box.height for box in boxes)
    return BoundingBox(
        pageNo=1,
        x=left,
        y=top,
        width=right - left,
        height=bottom - top,
    )


def _aggregate_confidence(spans: tuple[TextSpan, ...]) -> float:
    return sum(span.confidence for span in spans) / len(spans)


def _local_image_evidence(
    raw: bytes,
    *,
    expected_mime_type: str,
) -> tuple[str, str]:
    try:
        with Image.open(BytesIO(raw)) as opened:
            source_format = str(opened.format or "").upper()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:
        raise ProcessorResponseError("Modal recognition image cannot be decoded") from exc
    expected_format = "JPEG" if expected_mime_type == "image/jpeg" else "PNG"
    if source_format != expected_format:
        raise ProcessorResponseError("Modal recognition image MIME does not match its bytes")
    dimension_bytes = f"{image.width}x{image.height}:RGB".encode()
    page_hash = sha256(dimension_bytes + image.tobytes()).hexdigest()
    gray = image.convert("L").resize((17, 16), Image.Resampling.LANCZOS)
    pixels = list(gray.get_flattened_data())
    value = 0
    for y in range(16):
        offset = y * 17
        for x in range(16):
            value = (value << 1) | int(pixels[offset + x] > pixels[offset + x + 1])
    return page_hash, f"{value:064x}"


def _bounded_response(
    response: httpx.Response,
    *,
    maximum_bytes: int,
) -> httpx.Response:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > maximum_bytes:
                raise ProcessorResponseError(
                    "Modal recognition response exceeds its approved capacity"
                )
        except ValueError as exc:
            raise ProcessorResponseError(
                "Modal recognition response has an invalid content length"
            ) from exc
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > maximum_bytes:
            raise ProcessorResponseError("Modal recognition response exceeds its approved capacity")
        chunks.append(chunk)
    headers = httpx.Headers(response.headers)
    for name in ("Content-Encoding", "Content-Length", "Transfer-Encoding"):
        headers.pop(name, None)
    return httpx.Response(
        status_code=response.status_code,
        headers=headers,
        content=b"".join(chunks),
        request=response.request,
    )
