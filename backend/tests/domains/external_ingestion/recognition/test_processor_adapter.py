from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from app.contracts.external_learning.models import RecognitionKind
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    ProcessorDispatchUnknownError,
    ProcessorResponseError,
)
from app.domains.external_ingestion.recognition.models import (
    ProcessorApproval,
    ProcessorRequest,
)
from app.domains.external_ingestion.recognition.processor import (
    HTTPRecognitionProcessor,
    ProcessorRegistry,
)


NOW = datetime(2026, 7, 15, tzinfo=UTC)
PROVIDER_REPLAY_KEY = "ocr-v1-" + "a" * 64


def approval(*, release="approved-release"):
    return ProcessorApproval(
        policyVersion="ocr-policy@v1",
        route="ocr-primary",
        provider="vendor",
        providerRelease=release,
        modelRelease="ocr-model@v1",
        promptVersion="recognition-processor-policy@v1",
        priceVersion="ocr-price@v1",
        billingUnit="image",
        unitPriceCny="0.01",
        approvedKinds=(RecognitionKind.PRINTED_OCR,),
        approvedMimeTypes=("image/jpeg",),
        dataRegion="mainland_china",
        dpaEvidenceRef="dpa:1",
        securityReviewRef="security:1",
        approvalReceiptRef="approval:1",
        providerIdempotencyCapability="replayable_queryable",
        providerIdempotencyScope="provider_account",
        providerIdempotencyKeyHeader="Idempotency-Key",
        providerRequestIdSource="body:providerRequestId",
        providerReplayRetentionSeconds=604_800,
        providerIdempotencyEvidenceRef="provider-contract:1",
        validFrom=NOW - timedelta(days=1),
        validUntil=NOW + timedelta(days=1),
    )


def processor(*, response_release="approved-release", dispatch_events: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"] == PROVIDER_REPLAY_KEY
        assert request.headers["X-Processor-Release"] == "approved-release"
        return httpx.Response(
            200,
            headers={"X-Processor-Release": response_release},
            json={
                "contractVersion": "recognition-processor-output@v1",
                "providerRequestId": "provider-request-1",
                "textSpans": [
                    {
                        "spanId": "span-1",
                        "text": "题干",
                        "box": {
                            "pageNo": 1,
                            "x": 0.1,
                            "y": 0.1,
                            "width": 0.8,
                            "height": 0.1,
                        },
                        "confidence": 0.99,
                    }
                ],
                "confidence": 0.99,
            },
        )

    transport = httpx.MockTransport(handler)

    def client_factory() -> httpx.Client:
        if dispatch_events is not None:
            assert dispatch_events == ["claimed"]
        return httpx.Client(transport=transport)

    return HTTPRecognitionProcessor(
        endpoint="https://private-ocr.example.cn",
        bearer_token="test-secret",
        expected_provider_release="approved-release",
        client_factory=client_factory,
    )


def request():
    return ProcessorRequest(
        artifactVersionId=UUID("00000000-0000-0000-0000-000000000001"),
        ownerScopeHash="1" * 64,
        objectRef="tos://private/artifact-1",
        mimeType="image/jpeg",
        inputSha256="2" * 64,
        recognitionKind="printed_ocr",
        idempotencyKey=PROVIDER_REPLAY_KEY,
    )


def test_registry_rejects_implementation_release_not_bound_by_approval() -> None:
    with pytest.raises(ProcessorConfigurationError, match="does not match"):
        ProcessorRegistry((approval(release="different-release"),), {"ocr-primary": processor()})


def test_http_adapter_accepts_only_contract_valid_release_pinned_response() -> None:
    dispatch_events: list[str] = []
    fenced_request = request().model_copy(
        update={"before_provider_dispatch": lambda: dispatch_events.append("claimed")}
    )
    result = processor(dispatch_events=dispatch_events).process(fenced_request)

    assert result.contract_version == "recognition-processor-output@v1"
    assert result.text_spans[0].text == "题干"
    assert dispatch_events == ["claimed"]


def test_http_adapter_fails_closed_when_response_release_drifts() -> None:
    with pytest.raises(ProcessorResponseError, match="drifted"):
        processor(response_release="unapproved-release").process(request())


def test_http_adapter_distinguishes_received_invalid_response_from_unknown_dispatch() -> None:
    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, request=request)

    rejected_processor = HTTPRecognitionProcessor(
        endpoint="https://private-ocr.example.cn",
        bearer_token="test-secret",
        expected_provider_release="approved-release",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(rejected)),
    )
    with pytest.raises(ProcessorResponseError) as rejected_error:
        rejected_processor.process(request())
    assert not isinstance(rejected_error.value, ProcessorDispatchUnknownError)

    def timed_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("accepted request timed out", request=request)

    uncertain_processor = HTTPRecognitionProcessor(
        endpoint="https://private-ocr.example.cn",
        bearer_token="test-secret",
        expected_provider_release="approved-release",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(timed_out)),
    )
    with pytest.raises(ProcessorDispatchUnknownError):
        uncertain_processor.process(request())


@pytest.mark.parametrize(
    "endpoint",
    ["http://private-ocr.example.cn", "private-ocr.example.cn", "https://host/path?q=1"],
)
def test_http_adapter_rejects_non_https_or_ambiguous_endpoint(endpoint) -> None:
    with pytest.raises(ProcessorConfigurationError):
        HTTPRecognitionProcessor(
            endpoint=endpoint,
            bearer_token="secret",
            expected_provider_release="approved-release",
        )
