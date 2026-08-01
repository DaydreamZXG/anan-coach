from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Callable
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
    ProcessorRequest,
    ProcessorResult,
    TextSpan,
    enforce_provider_dispatch_fence,
)
from app.internal_services.private_objects import PrivateObjectReader

# MinerU 公益中转 / 官方接口路径。请求格式与 mineru.net 官方一致，只是替换 base URL。
CDN_BASE = "https://cdn-mineru.openxlab.org.cn"
POLL_INTERVAL_SECONDS = 3.0
MAX_POLL_SECONDS = 180.0


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _MinerUFileResponse(_StrictModel):
    key: str
    put_url: str = Field(alias="putUrl")
    download_url: str = Field(alias="downloadUrl")
    headers: dict[str, str] = Field(default_factory=dict)


class _MinerUTaskResponse(_StrictModel):
    task_id: str = Field(alias="taskId")


class _MinerUTaskStatus(_StrictModel):
    task_id: str = Field(alias="taskId")
    state: str
    err_msg: str = Field(default="", alias="errMsg")
    full_zip_url: str = Field(default="", alias="fullZipUrl")
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
                pdf_bytes = self._prepare_pdf(source, mime_type=request.mime_type)
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
                file_data = _MinerUFileResponse.model_validate(
                    file_resp.json()["data"]
                )
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
        return ProcessorResult(
            providerRequestId=provider_request_id,
            textSpans=spans,
            segments=(),
            omrMarks=(),
            pageImageSha256=(sha256(pdf_bytes).hexdigest(),),
            visualFingerprints=(),
            confidence=_confidence_from_blocks(layout_blocks),
        )

    def _prepare_pdf(self, source: Path, *, mime_type: str) -> bytes:
        """把源文件统一转成单页 PDF 再喂 MinerU（它对 PDF/文档的解析最稳）。"""
        try:
            with Image.open(source) as opened:
                prepared = prepare_for_ocr(opened, enable_crop=True, max_long_edge=2200)
        except Exception as exc:
            raise ProcessorResponseError("MinerU image cannot be decoded") from exc
        buffer = BytesIO()
        prepared.image.save(buffer, "PDF", resolution=200)
        pdf = buffer.getvalue()
        if len(pdf) > self._maximum_input_bytes:
            raise ProcessorResponseError("MinerU rendered PDF exceeds its approved input capacity")
        return pdf

    def _poll(self, client: httpx.Client, headers: dict[str, str], task_id: str) -> _MinerUTaskStatus:
        deadline = _now() + MAX_POLL_SECONDS
        while _now() < deadline:
            resp = client.get(f"{self._endpoint}/extract/task/{task_id}", headers=headers, timeout=30)
            if resp.status_code != 200:
                raise ProcessorResponseError(
                    f"MinerU task poll rejected: HTTP {resp.status_code}"
                )
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
            raise ProcessorResponseError(f"MinerU result download rejected: HTTP {resp.status_code}")
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
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return ()
    spans: list[TextSpan] = []
    for index, line in enumerate(lines):
        # 跳过纯图片引用行（![](images/...)），无文字内容。
        if re.match(r"^!\s*\[.*\]\(.*\)$", line):
            continue
        spans.append(
            TextSpan(
                spanId=f"{provider_request_id}:m:{index}",
                text=line,
                # MinerU 不返回文本坐标；用整页占位归一化框，语义是"无法定位"。
                box=BoundingBox(pageNo=1, x=0.0, y=0.0, width=1.0, height=1.0),
                confidence=0.9,
            )
        )
    return tuple(spans)


def _confidence_from_blocks(para_blocks: int) -> float:
    # 有解析块置信度更高；无块（超长截图等）给低置信度，调用方可据此降级。
    return 0.9 if para_blocks > 0 else 0.3


def _now() -> float:
    return datetime.now(UTC).timestamp()
