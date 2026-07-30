from __future__ import annotations

from contextlib import contextmanager
import gzip
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

import httpx
from PIL import Image
import pytest

from app.domains.external_ingestion.recognition.errors import ProcessorResponseError
from app.domains.external_ingestion.recognition.grok_vision import (
    GrokVisionRecognitionProcessor,
)
from app.domains.external_ingestion.recognition.models import (
    OMRExpectedItem,
    OMRLayout,
    ProcessorRequest,
)


PROVIDER_REPLAY_KEY = "ocr-v1-" + "b" * 64


class FakePrivateObjectReader:
    def __init__(self, source: Path) -> None:
        self.source = source

    @contextmanager
    def open(self, **_kwargs):
        yield self.source


def _image(tmp_path: Path) -> Path:
    target = tmp_path / "source.png"
    Image.new("RGB", (120, 80), "white").save(target)
    return target


def _request(source: Path, *, kind: str = "printed_ocr") -> ProcessorRequest:
    values: dict[str, object] = {
        "artifactVersionId": UUID("00000000-0000-0000-0000-000000000001"),
        "ownerScopeHash": "1" * 64,
        "objectRef": "tos://private-bucket/private/source.png",
        "mimeType": "image/png",
        "inputSha256": sha256(source.read_bytes()).hexdigest(),
        "pageCount": 1,
        "recognitionKind": kind,
        "idempotencyKey": PROVIDER_REPLAY_KEY,
    }
    if kind == "answer_sheet_omr":
        values["omrLayout"] = OMRLayout(
            packetVersionId=UUID("00000000-0000-0000-0000-000000000002"),
            items=(
                OMRExpectedItem(itemNo=1, optionLabels=("A", "B", "C", "D")),
                OMRExpectedItem(itemNo=2, optionLabels=("A", "B", "C", "D")),
            ),
        )
    return ProcessorRequest.model_validate(values)


def _processor(
    source: Path,
    *,
    payload: dict[str, object],
    actual_model: str = "grok-4.5-build-free",
    compressed_response: bool = False,
    maximum_output_bytes: int = 1024 * 1024,
    dispatch_events: list[str] | None = None,
) -> GrokVisionRecognitionProcessor:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://34.96.236.81:8317/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer private-test-token"
        assert request.headers["Idempotency-Key"] == PROVIDER_REPLAY_KEY
        body = json.loads(request.content)
        assert body["model"] == "grok-4.5"
        envelope = {
            "id": "provider-request-1",
            "model": actual_model,
            "choices": [
                {
                    "message": {
                        "content": "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
                    }
                }
            ],
        }
        if compressed_response:
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=gzip.compress(json.dumps(envelope).encode()),
            )
        return httpx.Response(200, json=envelope)

    transport = httpx.MockTransport(handler)

    def client_factory() -> httpx.Client:
        if dispatch_events is not None:
            assert dispatch_events == ["claimed"]
        return httpx.Client(transport=transport)

    return GrokVisionRecognitionProcessor(
        endpoint="http://34.96.236.81:8317/v1",
        bearer_token="private-test-token",
        expected_provider_release="grok-4.5-build-free@operator-proxy-v1",
        requested_model="grok-4.5",
        expected_actual_model="grok-4.5-build-free",
        source_reader=FakePrivateObjectReader(source),  # type: ignore[arg-type]
        maximum_input_bytes=8 * 1024 * 1024,
        maximum_output_bytes=maximum_output_bytes,
        maximum_pages=5,
        client_factory=client_factory,
    )


def _ocr_payload() -> dict[str, object]:
    return {
        "textSpans": [
            {
                "text": "资料分析题干",
                "box": {
                    "pageNo": 1,
                    "x": 0.1,
                    "y": 0.2,
                    "width": 0.6,
                    "height": 0.1,
                },
                "confidence": 0.98,
            }
        ],
        "segments": [],
        "omrMarks": [],
        "confidence": 0.98,
    }


def test_grok_vision_accepts_code_fenced_schema_and_seals_local_evidence(
    tmp_path: Path,
) -> None:
    source = _image(tmp_path)
    dispatch_events: list[str] = []
    request = _request(source).model_copy(
        update={"before_provider_dispatch": lambda: dispatch_events.append("claimed")}
    )

    result = _processor(
        source,
        payload=_ocr_payload(),
        dispatch_events=dispatch_events,
    ).process(request)

    assert result.provider_request_id == "provider-request-1"
    assert result.text_spans[0].text == "资料分析题干"
    assert result.text_spans[0].span_id.startswith("span-1-")
    assert len(result.page_image_sha256) == 1
    assert len(result.page_image_sha256[0]) == 64
    assert len(result.visual_fingerprints[0]) == 64
    assert dispatch_events == ["claimed"]


def test_grok_vision_accepts_gzip_response_without_double_decoding(
    tmp_path: Path,
) -> None:
    source = _image(tmp_path)

    result = _processor(
        source,
        payload=_ocr_payload(),
        compressed_response=True,
    ).process(_request(source))

    assert result.provider_request_id == "provider-request-1"
    assert result.text_spans[0].text == "资料分析题干"


def test_grok_vision_rejects_actual_model_release_drift(tmp_path: Path) -> None:
    source = _image(tmp_path)

    with pytest.raises(ProcessorResponseError, match="model release drifted"):
        _processor(
            source,
            payload=_ocr_payload(),
            actual_model="some-other-model",
        ).process(_request(source))


def test_grok_vision_rejects_omr_item_set_drift(tmp_path: Path) -> None:
    source = _image(tmp_path)
    payload = {
        "textSpans": [],
        "segments": [],
        "omrMarks": [
            {
                "itemNo": 1,
                "selectedOptions": ["A"],
                "box": {
                    "pageNo": 1,
                    "x": 0.1,
                    "y": 0.2,
                    "width": 0.1,
                    "height": 0.1,
                },
                "confidence": 0.9,
                "ambiguous": False,
            }
        ],
        "confidence": 0.9,
    }

    with pytest.raises(ProcessorResponseError, match="sealed packet"):
        _processor(source, payload=payload).process(_request(source, kind="answer_sheet_omr"))


def test_grok_vision_rejects_oversized_provider_response(tmp_path: Path) -> None:
    source = _image(tmp_path)

    with pytest.raises(ProcessorResponseError, match="exceeds its approved capacity"):
        _processor(
            source,
            payload=_ocr_payload(),
            maximum_output_bytes=32,
        ).process(_request(source))
