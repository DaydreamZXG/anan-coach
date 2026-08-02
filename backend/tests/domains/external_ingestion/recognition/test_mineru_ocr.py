from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import io
import json
import zipfile
from pathlib import Path
from uuid import UUID

import httpx
from PIL import Image
import pytest

from app.domains.external_ingestion.recognition.errors import ProcessorResponseError
from app.domains.external_ingestion.recognition.mineru_ocr import MinerURecognitionProcessor
from app.domains.external_ingestion.recognition.models import ProcessorRequest


PROVIDER_REPLAY_KEY = "ocr-v1-" + "b" * 64


class FakePrivateObjectReader:
    def __init__(self, source: Path) -> None:
        self.source = source

    @contextmanager
    def open(self, **_kwargs):
        yield self.source


def _image(tmp_path: Path) -> Path:
    target = tmp_path / "source.png"
    Image.new("RGB", (400, 200), "white").save(target)
    return target


def _request(source: Path, *, recognition_kind: str = "printed_ocr") -> ProcessorRequest:
    return ProcessorRequest.model_validate(
        {
            "artifactVersionId": UUID("00000000-0000-0000-0000-000000000001"),
            "ownerScopeHash": "1" * 64,
            "objectRef": "tos://private-bucket/private/source.png",
            "mimeType": "image/png",
            "inputSha256": sha256(source.read_bytes()).hexdigest(),
            "pageCount": 1,
            "recognitionKind": recognition_kind,
            "idempotencyKey": PROVIDER_REPLAY_KEY,
        }
    )


def _mineru_zip(markdown: str, para_blocks: int) -> bytes:
    buffer = io.BytesIO()
    layout = {"pdf_info": [{"para_blocks": [{} for _ in range(para_blocks)]}]}
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("full.md", markdown)
        zf.writestr("layout.json", json.dumps(layout))
        zf.writestr("origin.pdf", b"%PDF-1.4")
    return buffer.getvalue()


def _processor(
    source: Path,
    *,
    markdown: str = "下列哪项说法正确？\nA、地球是圆的\nB、太阳从西边升起",
    para_blocks: int = 3,
) -> MinerURecognitionProcessor:
    result_zip = _mineru_zip(markdown, para_blocks)
    task_id = "task-test-001"

    def client_factory() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/file"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "key": "api-upload/test/key",
                            "putUrl": "https://upload.test/put",
                            "downloadUrl": "https://cdn.test/download",
                            "previewUrl": "https://cdn.test/preview",
                            "headers": {"Content-Type": "application/octet-stream"},
                        },
                    },
                )
            if url.endswith("/extract/task") and request.method == "POST":
                return httpx.Response(
                    200,
                    json={"code": 0, "msg": "ok", "data": {"task_id": task_id}},
                )
            if f"/extract/task/{task_id}" in url:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "msg": "ok",
                        "data": {
                            "task_id": task_id,
                            "state": "done",
                            "full_zip_url": "https://cdn.test/result.zip",
                        },
                    },
                )
            if url.startswith("https://cdn.test/result.zip"):
                return httpx.Response(200, content=result_zip)
            if url.startswith("https://upload.test/"):
                return httpx.Response(200)
            if url.startswith("https://cdn.test/download"):
                return httpx.Response(200, content=b"downloaded")
            return httpx.Response(404)

        return httpx.Client(transport=httpx.MockTransport(handler))

    return MinerURecognitionProcessor(
        endpoint="https://ocr.eo.mk/api/v4",
        bearer_token="test-token",
        expected_provider_release="mineru-2026-08-01",
        source_reader=FakePrivateObjectReader(source),
        maximum_input_bytes=1_000_000,
        maximum_output_bytes=2_000_000,
        maximum_pages=1,
        client_factory=client_factory,
    )


def test_mineru_parses_markdown_into_spans(tmp_path: Path) -> None:
    source = _image(tmp_path)
    request = _request(source)
    processor = _processor(source)

    result = processor.process(request)

    assert result.provider_request_id.startswith("mineru:")
    texts = [span.text for span in result.text_spans]
    assert "下列哪项说法正确？" in texts
    assert any("地球是圆的" in text for text in texts)
    assert result.confidence == 0.9
    assert len(result.visual_fingerprints) == 1
    assert len(result.visual_fingerprints[0]) == 64
    assert len(result.visual_feature_hashes) == 1
    assert all(
        len(value) == 16
        for value in result.visual_feature_hashes[0].model_dump().values()
    )


def test_mineru_derives_ordered_segments_for_exam_import(tmp_path: Path) -> None:
    source = _image(tmp_path)
    processor = _processor(source)

    result = processor.process(_request(source, recognition_kind="exam_segmentation"))

    assert [segment.segment_kind for segment in result.segments] == ["question", "options"]
    prompt, options = result.segments
    assert prompt.box.y < options.box.y
    assert len(options.text_span_refs) == 2


def test_mineru_splits_prompt_and_inline_options(tmp_path: Path) -> None:
    source = _image(tmp_path)
    processor = _processor(
        source,
        markdown="最适合做上文标题的是（）。 A “井”里与“井”外 B “相对”才是真理",
    )

    result = processor.process(_request(source, recognition_kind="exam_segmentation"))

    assert [span.text for span in result.text_spans] == [
        "最适合做上文标题的是（）。",
        "A “井”里与“井”外",
        "B “相对”才是真理",
    ]
    assert [segment.segment_kind for segment in result.segments] == ["question", "options"]


def test_mineru_low_confidence_on_empty_blocks(tmp_path: Path) -> None:
    source = _image(tmp_path)
    request = _request(source)
    processor = _processor(source, markdown="", para_blocks=0)

    result = processor.process(request)

    # 无解析块 → 低置信度（超长截图等不可辨识场景）。
    assert result.confidence == 0.3
    assert result.text_spans == ()


def test_mineru_seals_visual_only_exam_for_image_matching(tmp_path: Path) -> None:
    source = _image(tmp_path)
    processor = _processor(source, markdown="", para_blocks=0)

    result = processor.process(_request(source, recognition_kind="exam_segmentation"))

    assert result.text_spans == ()
    assert len(result.visual_feature_hashes) == 1
    assert [segment.segment_kind for segment in result.segments] == ["question"]
    assert result.segments[0].text_span_refs == ()


def test_mineru_rejects_failed_task(tmp_path: Path) -> None:
    source = _image(tmp_path)

    def client_factory() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/file"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "key": "api-upload/test/key",
                            "putUrl": "https://upload.test/put",
                            "downloadUrl": "https://cdn.test/download",
                            "headers": {},
                        },
                    },
                )
            if url.endswith("/extract/task"):
                return httpx.Response(200, json={"code": 0, "data": {"task_id": "task-fail"}})
            if "/extract/task/task-fail" in url:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"task_id": "task-fail", "state": "failed", "err_msg": "bad file"},
                    },
                )
            if url.startswith("https://upload.test/"):
                return httpx.Response(200)
            return httpx.Response(404)

        return httpx.Client(transport=httpx.MockTransport(handler))

    processor = MinerURecognitionProcessor(
        endpoint="https://ocr.eo.mk/api/v4",
        bearer_token="test-token",
        expected_provider_release="mineru-2026-08-01",
        source_reader=FakePrivateObjectReader(source),
        maximum_input_bytes=1_000_000,
        maximum_output_bytes=2_000_000,
        maximum_pages=1,
        client_factory=client_factory,
    )

    with pytest.raises(ProcessorResponseError, match="task failed"):
        processor.process(_request(source))
