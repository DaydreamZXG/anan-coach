from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

import httpx
from PIL import Image
import pytest

from app.domains.external_ingestion.recognition.errors import ProcessorResponseError
from app.domains.external_ingestion.recognition.modal_paddle import (
    ModalPaddleOCRRecognitionProcessor,
)
from app.domains.external_ingestion.recognition.models import ProcessorRequest
from app.domains.external_ingestion.recognition.service import _failure_usage_state


PROVIDER_RELEASE = "modal-paddleocr-vl-v1-20260727-r1"
MODEL_RELEASE = "PaddleOCR-VL-v1"
PROVIDER_REPLAY_KEY = "ocr-v1-" + "c" * 64


class FakePrivateObjectReader:
    def __init__(self, source: Path) -> None:
        self.source = source

    @contextmanager
    def open(self, **kwargs):
        assert kwargs["object_ref"] == "tos://private-bucket/private/source.png"
        assert kwargs["expected_sha256"] == sha256(self.source.read_bytes()).hexdigest()
        assert kwargs["maximum_bytes"] == 8 * 1024 * 1024
        assert kwargs["suffix"] == ".png"
        yield self.source


def _image(tmp_path: Path) -> Path:
    target = tmp_path / "source.png"
    Image.new("RGB", (1_000, 1_200), "white").save(target)
    return target


def _request(source: Path, *, kind: str = "exam_segmentation") -> ProcessorRequest:
    return ProcessorRequest.model_validate(
        {
            "artifactVersionId": UUID("00000000-0000-0000-0000-000000000001"),
            "ownerScopeHash": "1" * 64,
            "objectRef": "tos://private-bucket/private/source.png",
            "mimeType": "image/png",
            "inputSha256": sha256(source.read_bytes()).hexdigest(),
            "pageCount": 1,
            "recognitionKind": kind,
            "idempotencyKey": PROVIDER_REPLAY_KEY,
        }
    )


def _payload(source: Path) -> dict[str, object]:
    blocks = [
        ("2025 年两地公共预算收入情况。", (80, 100, 920, 230), "paragraph"),
        ("根据材料，下列说法正确的是？", (80, 360, 920, 430), "paragraph"),
        ("A. 甲地收入高于乙地", (80, 500, 920, 560), "list"),
        ("B. 乙地同比增速更高", (80, 590, 920, 650), "list"),
        ("C. 两地收入均下降", (80, 680, 920, 740), "list"),
        ("D. 无法判断", (80, 770, 920, 830), "list"),
    ]
    normalized = []
    for order, (text, bbox, label) in enumerate(blocks, start=1):
        x1, y1, x2, y2 = bbox
        normalized.append(
            {
                "text": text,
                "bbox": [x1, y1, x2, y2],
                "label": label,
                "order": order,
                "confidence": 0.96,
                "cx": (x1 + x2) / 2,
                "cy": (y1 + y2) / 2,
                "bw": x2 - x1,
                "bh": y2 - y1,
            }
        )
    return {
        "schema_version": "anan-modal-ocr-response@v1",
        "provider_release": PROVIDER_RELEASE,
        "model_release": MODEL_RELEASE,
        "provider_request_id": "modal-request-1",
        "input_sha256": sha256(source.read_bytes()).hexdigest(),
        "selected_angle": 0,
        "width": 1_000,
        "height": 1_200,
        "score": 120.0,
        "text": "\n".join(item[0] for item in blocks),
        "blocks": normalized,
        "candidate_summary": [
            {
                "angle": 0,
                "score": 120.0,
                "elapsed_ms": 1_000,
                "width": 1_000,
                "height": 1_200,
            }
        ],
        "elapsed_ms": 1_000,
    }


def _processor(
    source: Path,
    *,
    mutate_payload=None,
    release_header: str = PROVIDER_RELEASE,
    dispatch_events: list[str] | None = None,
) -> ModalPaddleOCRRecognitionProcessor:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://operator--recognize.modal.run"
        assert request.headers["Authorization"] == "Bearer private-modal-token"
        assert request.headers["Idempotency-Key"] == PROVIDER_REPLAY_KEY
        body = json.loads(request.content)
        assert body["schema_version"] == "anan-modal-ocr-request@v1"
        assert body["input_sha256"] == sha256(source.read_bytes()).hexdigest()
        assert body["mime_type"] == "image/png"
        payload = _payload(source)
        if mutate_payload is not None:
            mutate_payload(payload)
        return httpx.Response(
            200,
            headers={
                "X-Processor-Release": release_header,
                "X-Request-ID": "modal-request-1",
            },
            json=payload,
        )

    transport = httpx.MockTransport(handler)

    def client_factory() -> httpx.Client:
        if dispatch_events is not None:
            assert dispatch_events == ["claimed"]
        return httpx.Client(transport=transport)

    return ModalPaddleOCRRecognitionProcessor(
        endpoint="https://operator--recognize.modal.run",
        bearer_token="private-modal-token",
        expected_provider_release=PROVIDER_RELEASE,
        expected_model_release=MODEL_RELEASE,
        source_reader=FakePrivateObjectReader(source),  # type: ignore[arg-type]
        maximum_input_bytes=8 * 1024 * 1024,
        maximum_output_bytes=2 * 1024 * 1024,
        client_factory=client_factory,
    )


def test_modal_paddle_seals_text_segments_and_local_visual_evidence(
    tmp_path: Path,
) -> None:
    source = _image(tmp_path)
    dispatch_events: list[str] = []
    request = _request(source).model_copy(
        update={"before_provider_dispatch": lambda: dispatch_events.append("claimed")}
    )

    result = _processor(source, dispatch_events=dispatch_events).process(request)

    assert result.provider_request_id == "modal-request-1"
    assert [segment.segment_kind for segment in result.segments] == [
        "question",
        "options",
        "material",
    ]
    assert len(result.segments[1].text_span_refs) == 4
    assert result.text_spans[0].text.startswith("2025 年")
    assert len(result.page_image_sha256[0]) == 64
    assert len(result.visual_fingerprints[0]) == 64
    assert result.confidence == pytest.approx(0.96)
    assert dispatch_events == ["claimed"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("provider_release", "other-release", "provider release drifted"),
        ("model_release", "other-model", "model release drifted"),
        ("input_sha256", "0" * 64, "input identity drifted"),
    ),
)
def test_modal_paddle_rejects_provider_identity_drift(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    source = _image(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        payload[field] = value

    with pytest.raises(ProcessorResponseError, match=message):
        _processor(source, mutate_payload=mutate).process(_request(source))


def test_modal_paddle_rejects_release_header_drift(tmp_path: Path) -> None:
    source = _image(tmp_path)

    with pytest.raises(ProcessorResponseError, match="provider release drifted"):
        _processor(source, release_header="other-release").process(_request(source))


def test_modal_paddle_rejects_unapproved_recognition_kind(tmp_path: Path) -> None:
    source = _image(tmp_path)

    with pytest.raises(ProcessorResponseError, match="kind is not supported"):
        _processor(source).process(_request(source, kind="handwriting_ocr"))


def test_modal_local_decode_failure_is_proven_not_dispatched_and_has_zero_usage(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corrupt.png"
    source.write_bytes(b"not-an-image")
    client_creations = 0

    def client_factory() -> httpx.Client:
        nonlocal client_creations
        client_creations += 1
        raise AssertionError("provider client must not be created")

    processor = ModalPaddleOCRRecognitionProcessor(
        endpoint="https://operator--recognize.modal.run",
        bearer_token="private-modal-token",
        expected_provider_release=PROVIDER_RELEASE,
        expected_model_release=MODEL_RELEASE,
        source_reader=FakePrivateObjectReader(source),  # type: ignore[arg-type]
        maximum_input_bytes=8 * 1024 * 1024,
        maximum_output_bytes=2 * 1024 * 1024,
        client_factory=client_factory,
    )

    with pytest.raises(ProcessorResponseError) as caught:
        processor.process(_request(source))

    assert client_creations == 0
    assert caught.value.code == "processor_not_dispatched"
    assert _failure_usage_state(caught.value, provider_response_received=False) == "not_dispatched"
