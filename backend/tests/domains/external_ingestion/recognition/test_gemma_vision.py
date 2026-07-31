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
from app.domains.external_ingestion.recognition.gemma_vision import (
    GemmaVisionRecognitionProcessor,
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
    actual_model: str = "gemma-4-31b-it",
    response_model: str | None = None,
) -> GemmaVisionRecognitionProcessor:
    envelope_model = response_model if response_model is not None else actual_model
    envelope = json.dumps(
        {"model": envelope_model, "id": "ocr-test-relay-001", "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}
    ).encode("utf-8")

    def client_factory() -> httpx.Client:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=envelope)
        )
        return httpx.Client(transport=transport)

    return GemmaVisionRecognitionProcessor(
        endpoint="https://tp.ergou.pro",
        bearer_token="test-token",
        expected_provider_release="gemma-relay-2026-08-01",
        requested_model="gemma-4-31b-it",
        expected_actual_model=actual_model,
        source_reader=FakePrivateObjectReader(source),
        maximum_input_bytes=1_000_000,
        maximum_output_bytes=1_000_000,
        maximum_pages=1,
        client_factory=client_factory,
    )


def _omr_payload() -> dict[str, object]:
    return {
        "textSpans": [
            {"text": "答题卡", "box": {"pageNo": 1, "x": 0.36, "y": 0.03, "width": 0.12, "height": 0.03}, "confidence": 1.0},
            {"text": "1.", "box": {"pageNo": 1, "x": 0.12, "y": 0.1, "width": 0.03, "height": 0.03}, "confidence": 1.0},
        ],
        "segments": [],
        "omrMarks": [
            {"itemNo": 1, "selectedOptions": ["A"], "box": {"pageNo": 1, "x": 0.2, "y": 0.1, "width": 0.05, "height": 0.05}, "confidence": 1.0, "ambiguous": False},
            {"itemNo": 2, "selectedOptions": ["C"], "box": {"pageNo": 1, "x": 0.2, "y": 0.18, "width": 0.05, "height": 0.05}, "confidence": 1.0, "ambiguous": False},
        ],
        "confidence": 1.0,
    }


def test_gemma_relay_parses_omr_marks(tmp_path: Path) -> None:
    source = _image(tmp_path)
    request = _request(source, kind="answer_sheet_omr")
    processor = _processor(source, payload=_omr_payload())

    result = processor.process(request)

    assert result.provider_request_id == "ocr-test-relay-001"
    assert [mark.item_no for mark in result.omr_marks] == [1, 2]
    assert list(result.omr_marks[0].selected_options) == ["A"]
    assert list(result.omr_marks[1].selected_options) == ["C"]
    assert result.text_spans[0].text == "答题卡"
    assert result.confidence == 1.0


def test_gemma_relay_rejects_model_drift(tmp_path: Path) -> None:
    source = _image(tmp_path)
    request = _request(source, kind="answer_sheet_omr")
    processor = _processor(
        source, payload=_omr_payload(), response_model="some-other-model"
    )

    with pytest.raises(ProcessorResponseError, match="model release drifted"):
        processor.process(request)


def test_gemma_relay_rejects_option_outside_labels(tmp_path: Path) -> None:
    source = _image(tmp_path)
    request = _request(source, kind="answer_sheet_omr")
    payload = _omr_payload()
    payload["omrMarks"][0]["selectedOptions"] = ["E"]  # 只有 A-D，E 越界
    processor = _processor(source, payload=payload)

    with pytest.raises(ProcessorResponseError, match="outside its approved labels"):
        processor.process(request)
