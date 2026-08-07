from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Callable, Literal, Protocol
import zipfile

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    ProcessorDispatchUnknownError,
    ProcessorNotDispatchedError,
    ProcessorResponseError,
)
from app.domains.external_ingestion.recognition.image_preprocess import prepare_for_ocr
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    PageSegment,
    ProcessorRequest,
    ProcessorResult,
    TextSpan,
    VisualFeatureHash,
    enforce_provider_dispatch_fence,
)
from app.domains.external_ingestion.recognition.visual_hash import (
    fingerprint_sha256,
    matching_feature_hashes,
)
from app.internal_services.private_objects import PrivateObjectReader
from app.domains.xfyun.formula import FormulaRecognition, XfyunFormulaError

# MinerU 公益中转 / 官方接口路径。请求格式与 mineru.net 官方一致，只是替换 base URL。
CDN_BASE = "https://cdn-mineru.openxlab.org.cn"
POLL_INTERVAL_SECONDS = 3.0
MAX_POLL_SECONDS = 180.0


class _StrictModel(BaseModel):
    # Provider responses evolve by adding observational fields (for example
    # previewUrl/extract_progress). Keep required fields typed, but tolerate
    # additive response metadata so a harmless rollout does not break OCR.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class _MinerUFileResponse(_StrictModel):
    key: str
    put_url: str = Field(alias="putUrl")
    download_url: str = Field(alias="downloadUrl")
    preview_url: str = Field(default="", alias="previewUrl")
    headers: dict[str, str] = Field(default_factory=dict)


class _MinerUTaskResponse(_StrictModel):
    task_id: str = Field(alias="taskId")


class _MinerUTaskStatus(_StrictModel):
    task_id: str = Field(alias="taskId")
    state: str
    err_msg: str = Field(default="", alias="errMsg")
    err_code: int | None = Field(default=None, alias="errCode")
    full_zip_url: str = Field(default="", alias="fullZipUrl")
    is_chem: bool = False
    can_retry: bool = False


class MinerURecognitionProcessor:
    """MinerU 文档解析适配器（走公益高速通道 ocr.eo.mk）。

    流程：读私有对象 → 预处理（裁剪/降采样）→ 转 PDF → 上传拿 putUrl →
    提交解析任务（url=cdn-prefix+key）→ 轮询到 done → 下载 zip 解析 full.md →
    封成 ProcessorResult.textSpans。
    """

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        expected_provider_release: str,
        source_reader: PrivateObjectReader,
        maximum_input_bytes: int,
        maximum_output_bytes: int,
        maximum_pages: int,
        timeout_seconds: float = 220,
        client_factory: Callable[[], httpx.Client] | None = None,
        formula_client: FormulaRecognizer | None = None,
    ) -> None:
        if not bearer_token.strip() or not expected_provider_release.strip():
            raise ProcessorConfigurationError("MinerU recognition provider identity is incomplete")
        if maximum_input_bytes <= 0 or maximum_output_bytes <= 0 or maximum_pages <= 0:
            raise ProcessorConfigurationError(
                "MinerU recognition input, output and page limits must be positive"
            )
        self._endpoint = endpoint.rstrip("/")
        self._token = bearer_token
        self._release = expected_provider_release
        self._source_reader = source_reader
        self._maximum_input_bytes = maximum_input_bytes
        self._maximum_output_bytes = maximum_output_bytes
        self._maximum_pages = maximum_pages
        self._formula_client = formula_client
        self._client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(timeout_seconds, connect=min(20, timeout_seconds)),
                follow_redirects=True,
            )
        )

    @property
    def provider_release(self) -> str:
        return self._release

    def process(self, request: ProcessorRequest) -> ProcessorResult:
        try:
            suffix = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "application/pdf": ".pdf",
            }.get(request.mime_type)
            if suffix is None:
                raise ValueError("MinerU recognition input MIME is unsupported")
            with self._source_reader.open(
                object_ref=request.object_ref,
                expected_sha256=request.input_sha256,
                maximum_bytes=self._maximum_input_bytes,
                prefix="anan-mineru-",
                suffix=suffix,
            ) as source:
                pdf_bytes, formula_image_bytes, source_feature_hashes = self._prepare_pdf(
                    source, mime_type=request.mime_type
                )
        except ProcessorNotDispatchedError:
            raise
        except Exception as exc:
            message = str(exc).strip() or "MinerU recognition input failed before provider dispatch"
            raise ProcessorNotDispatchedError(message) from exc

        enforce_provider_dispatch_fence(request)
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            with self._client_factory() as client:
                # 1. 拿上传地址
                file_resp = client.post(f"{self._endpoint}/file", headers=headers, timeout=60)
                if file_resp.status_code != 200:
                    raise ProcessorResponseError(
                        f"MinerU /file rejected: HTTP {file_resp.status_code}"
                    )
                file_data = _MinerUFileResponse.model_validate(file_resp.json()["data"])
                # 2. 上传文件到 OSS
                upload = client.put(
                    file_data.put_url, content=pdf_bytes, headers=file_data.headers, timeout=120
                )
                if upload.status_code not in (200, 201, 204):
                    raise ProcessorResponseError(
                        f"MinerU upload rejected: HTTP {upload.status_code}"
                    )
                # 3. 提交解析任务（url 必须用 cdn-prefix+key，downloadUrl 带签名会导致读取失败）
                task_url = f"{CDN_BASE}/{file_data.key}"
                task_resp = client.post(
                    f"{self._endpoint}/extract/task",
                    headers=headers,
                    json={"url": task_url, "model_version": "vlm", "enable_formula": True},
                    timeout=60,
                )
                if task_resp.status_code != 200:
                    raise ProcessorResponseError(
                        f"MinerU extract/task rejected: HTTP {task_resp.status_code}"
                    )
                task = _MinerUTaskResponse.model_validate(task_resp.json()["data"])
                # 4. 轮询状态
                status = self._poll(client, headers, task.task_id)
                # 5. 下载结果 zip 并解析
                markdown, layout_blocks = self._fetch_result(
                    client, status.full_zip_url, maximum_bytes=self._maximum_output_bytes
                )
        except ProcessorResponseError:
            raise
        except httpx.HTTPStatusError as exc:
            raise ProcessorResponseError("MinerU provider rejected the request") from exc
        except httpx.HTTPError as exc:
            raise ProcessorDispatchUnknownError("MinerU dispatch outcome is unknown") from exc

        provider_request_id = f"mineru:{task.task_id}"
        spans = _spans_from_markdown(markdown, provider_request_id)
        spans, provider_request_id = _apply_formula_enrichment(
            self._formula_client,
            image=formula_image_bytes,
            primary_spans=spans,
            provider_request_id=provider_request_id,
        )
        segments = (
            _exam_segments(spans) if request.recognition_kind.value == "exam_segmentation" else ()
        )
        return ProcessorResult(
            providerRequestId=provider_request_id,
            textSpans=spans,
            segments=segments,
            omrMarks=(),
            pageImageSha256=(sha256(pdf_bytes).hexdigest(),),
            visualFingerprints=tuple(
                fingerprint_sha256(item.model_dump()) for item in source_feature_hashes
            ),
            visualFeatureHashes=source_feature_hashes,
            confidence=_confidence_from_blocks(layout_blocks),
        )

    def _prepare_pdf(
        self, source: Path, *, mime_type: str
    ) -> tuple[bytes, bytes, tuple[VisualFeatureHash, ...]]:
        """把源文件统一转成单页 PDF 再喂 MinerU（它对 PDF/文档的解析最稳）。"""
        try:
            with Image.open(source) as opened:
                prepared = prepare_for_ocr(opened, enable_crop=True, max_long_edge=2200)
        except Exception as exc:
            raise ProcessorResponseError("MinerU image cannot be decoded") from exc
        hashes = tuple(
            VisualFeatureHash.model_validate(item)
            for item in matching_feature_hashes(prepared.image)
        )
        formula_buffer = BytesIO()
        prepared.image.convert("RGB").save(formula_buffer, "JPEG", quality=92, optimize=True)
        buffer = BytesIO()
        prepared.image.save(buffer, "PDF", resolution=200)
        pdf = buffer.getvalue()
        if len(pdf) > self._maximum_input_bytes:
            raise ProcessorResponseError("MinerU rendered PDF exceeds its approved input capacity")
        return pdf, formula_buffer.getvalue(), hashes

    def _poll(
        self, client: httpx.Client, headers: dict[str, str], task_id: str
    ) -> _MinerUTaskStatus:
        deadline = _now() + MAX_POLL_SECONDS
        while _now() < deadline:
            resp = client.get(
                f"{self._endpoint}/extract/task/{task_id}", headers=headers, timeout=30
            )
            if resp.status_code != 200:
                raise ProcessorResponseError(f"MinerU task poll rejected: HTTP {resp.status_code}")
            status = _MinerUTaskStatus.model_validate(resp.json()["data"])
            if status.state == "done":
                if not status.full_zip_url:
                    raise ProcessorResponseError("MinerU task done without a result zip")
                return status
            if status.state in ("failed", "error"):
                raise ProcessorResponseError(
                    f"MinerU task failed: {status.err_msg or status.state}"
                )
            import time

            time.sleep(POLL_INTERVAL_SECONDS)
        raise ProcessorResponseError("MinerU task polling timed out")

    def _fetch_result(
        self, client: httpx.Client, zip_url: str, *, maximum_bytes: int
    ) -> tuple[str, int]:
        resp = client.get(zip_url, timeout=120)
        if resp.status_code != 200:
            raise ProcessorResponseError(
                f"MinerU result download rejected: HTTP {resp.status_code}"
            )
        if len(resp.content) > maximum_bytes:
            raise ProcessorResponseError("MinerU result exceeds its approved output capacity")
        try:
            with zipfile.ZipFile(BytesIO(resp.content)) as zf:
                names = zf.namelist()
                md_name = next((n for n in names if n == "full.md"), None)
                if md_name is None:
                    return "", 0
                markdown = zf.read(md_name).decode("utf-8", errors="replace")
                layout_blocks = 0
                layout_name = next((n for n in names if n == "layout.json"), None)
                if layout_name is not None:
                    try:
                        layout = json.loads(zf.read(layout_name))
                        layout_blocks = len(layout.get("pdf_info", [{}])[0].get("para_blocks", []))
                    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
                        layout_blocks = 0
                return markdown, layout_blocks
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ProcessorResponseError("MinerU result zip is corrupt") from exc


def _spans_from_markdown(markdown: str, provider_request_id: str) -> tuple[TextSpan, ...]:
    """把 full.md 的文本行拆成 TextSpan（每行一个，坐标不可得则整段一个）。"""
    lines = [
        logical_line
        for source_line in markdown.splitlines()
        for logical_line in _split_inline_options(source_line.strip())
        if logical_line
    ]
    if not lines:
        return ()
    spans: list[TextSpan] = []
    usable_lines = [line for line in lines if not re.match(r"^!\s*\[.*\]\(.*\)$", line)]
    line_count = len(usable_lines)
    for index, line in enumerate(usable_lines):
        # 跳过纯图片引用行（![](images/...)），无文字内容。
        spans.append(
            TextSpan(
                spanId=f"{provider_request_id}:m:{index}",
                text=line,
                # MinerU markdown 不带坐标；按文本行顺序生成不重叠的近似框，
                # 保留题干/选项的阅读顺序，但不伪装成精确视觉定位。
                box=BoundingBox(
                    pageNo=1,
                    x=0.0,
                    y=index / line_count,
                    width=1.0,
                    height=1.0 / line_count,
                ),
                confidence=0.9,
            )
        )
    return tuple(spans)


def _spans_from_formula_lines(
    lines: tuple[object, ...],
    *,
    provider_request_id: str,
) -> tuple[TextSpan, ...]:
    usable = [line for line in lines if str(getattr(line, "text", "")).strip()]
    count = len(usable)
    return tuple(
        TextSpan(
            spanId=f"{provider_request_id}:f:{index}",
            text=str(getattr(line, "text")),
            box=BoundingBox(
                pageNo=1,
                x=0.0,
                y=index / count,
                width=1.0,
                height=1.0 / count,
            ),
            confidence=float(getattr(line, "confidence", 0.0)),
        )
        for index, line in enumerate(usable)
    )


def _apply_formula_enrichment(
    formula_client: FormulaRecognizer | None,
    *,
    image: bytes,
    primary_spans: tuple[TextSpan, ...],
    provider_request_id: str,
) -> tuple[tuple[TextSpan, ...], str]:
    if formula_client is None:
        return primary_spans, provider_request_id
    try:
        result = formula_client.recognize(image)
    except XfyunFormulaError:
        # Formula recognition is a specialist enhancement. Preserve the
        # primary MinerU result and make the degraded provider outcome
        # observable through the processor request identity.
        return primary_spans, f"{provider_request_id}|xfyun:unavailable"
    enriched_id = f"{provider_request_id}|xfyun:{result.request_id}"
    if not result.has_formula:
        return primary_spans, enriched_id
    return (
        _spans_from_formula_lines(result.lines, provider_request_id=enriched_id),
        enriched_id,
    )


class FormulaRecognizer(Protocol):
    def recognize(self, image: bytes) -> FormulaRecognition: ...


_OPTION_MARKER = re.compile(r"(?:^|[\s\n])([A-H])(?:[.．、:：)）]\s*|\s+(?=\S))")
_OPTION_PREFIX = re.compile(r"^\s*([A-H])(?:[.．、:：)）]\s*|\s+(?=\S))")
_QUESTION_CUES = (
    "下列说法",
    "下列选项",
    "正确的是",
    "错误的是",
    "不正确的是",
    "最恰当的是",
    "最合适的是",
    "最可能的是",
    "最适合",
    "最符合",
    "不能推出",
    "可以推出",
    "由此可知",
    "主要说明",
    "意在说明",
    "这段文字",
    "根据材料",
    "根据上述",
)


def _split_inline_options(line: str) -> tuple[str, ...]:
    """Split MinerU lines that contain a prompt and several inline A-H options."""
    if not line:
        return ()
    option_starts = [match.start(1) for match in _OPTION_MARKER.finditer(line)]
    if not option_starts:
        return (line,)
    boundaries = sorted({0, *option_starts, len(line)})
    return tuple(
        piece
        for start, end in zip(boundaries, boundaries[1:])
        if (piece := line[start:end].strip())
    )


def _exam_segments(spans: tuple[TextSpan, ...]) -> tuple[PageSegment, ...]:
    """Derive one-question semantic segments from MinerU's ordered markdown lines."""
    if not spans:
        # A visual-only question is still matchable by its source-image hashes.
        # Seal one empty prompt region so the normal question-unit pipeline can
        # carry the image evidence without inventing OCR text.
        return (
            PageSegment(
                segmentId="mineru-segment-question-visual-only",
                segmentKind="question",
                box=BoundingBox(pageNo=1, x=0, y=0, width=1, height=1),
                textSpanRefs=(),
                confidence=0,
            ),
        )
    option_indexes = {index for index, span in enumerate(spans) if _looks_like_options(span.text)}
    non_options = [index for index in range(len(spans)) if index not in option_indexes]
    if not non_options:
        raise ProcessorResponseError("MinerU recognition found options without a question")
    first_option = min(option_indexes) if option_indexes else None
    prompt_candidates = [
        index
        for index in non_options
        if _looks_like_question(spans[index].text)
        and (first_option is None or index < first_option)
    ]
    if prompt_candidates:
        prompt_index = prompt_candidates[-1]
    elif first_option is not None:
        predecessors = [index for index in non_options if index < first_option]
        if not predecessors:
            raise ProcessorResponseError("MinerU recognition cannot locate the question prompt")
        prompt_index = predecessors[-1]
    else:
        prompt_index = non_options[-1]

    material_indexes = [index for index in non_options if index < prompt_index]
    trailing_indexes = [index for index in non_options if index > prompt_index]
    segments = [_segment("prompt", "question", (spans[prompt_index],))]
    if option_indexes:
        segments.append(
            _segment("options", "options", tuple(spans[i] for i in sorted(option_indexes)))
        )
    if material_indexes:
        segments.append(_segment("material", "material", tuple(spans[i] for i in material_indexes)))
    if trailing_indexes:
        segments.append(_segment("trailing", "unknown", tuple(spans[i] for i in trailing_indexes)))
    return tuple(segments)


def _segment(
    name: str,
    kind: Literal["question", "options", "material", "answer_area", "essay_prompt", "unknown"],
    spans: tuple[TextSpan, ...],
) -> PageSegment:
    identity = sha256(
        (name + "\0" + "\0".join(span.span_id for span in spans)).encode("utf-8")
    ).hexdigest()[:20]
    left = min(span.box.x for span in spans)
    top = min(span.box.y for span in spans)
    right = max(span.box.x + span.box.width for span in spans)
    bottom = max(span.box.y + span.box.height for span in spans)
    return PageSegment(
        segmentId=f"mineru-segment-{name}-{identity}",
        segmentKind=kind,
        box=BoundingBox(pageNo=1, x=left, y=top, width=right - left, height=bottom - top),
        textSpanRefs=tuple(span.span_id for span in spans),
        confidence=sum(span.confidence for span in spans) / len(spans),
    )


def _looks_like_question(text: str) -> bool:
    normalized = text.strip()
    return normalized.endswith(("?", "？")) or any(cue in normalized for cue in _QUESTION_CUES)


def _looks_like_options(text: str) -> bool:
    return bool(_OPTION_PREFIX.match(text)) or len(_OPTION_MARKER.findall(text)) >= 2


def _confidence_from_blocks(para_blocks: int) -> float:
    # 有解析块置信度更高；无块（超长截图等）给低置信度，调用方可据此降级。
    return 0.9 if para_blocks > 0 else 0.3


def _now() -> float:
    return datetime.now(UTC).timestamp()
