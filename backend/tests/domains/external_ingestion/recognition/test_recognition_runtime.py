from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.domains.external_ingestion.recognition.errors import ProcessorConfigurationError
from app.domains.external_ingestion.recognition.models import ProcessorApproval
from app.domains.external_ingestion.recognition_runtime import (
    _approved_endpoint,
    build_recognition_service,
)


def _settings(**overrides) -> Settings:
    values = {
        "recognition_processor_endpoint": "https://recognition.example.com/v1",
        "recognition_processor_adapter": "private_contract",
        "recognition_processor_https_host_allowlist": "recognition.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_runtime_accepts_approved_https_origin() -> None:
    assert _approved_endpoint(_settings()) == "https://recognition.example.com/v1"


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://34.96.236.81:8317/v1",
        "ftp://recognition.example.com/v1",
        "https://other.example.com/v1",
    ),
)
def test_runtime_rejects_plaintext_and_unapproved_routes(endpoint: str) -> None:
    with pytest.raises(ProcessorConfigurationError):
        _approved_endpoint(_settings(recognition_processor_endpoint=endpoint))


def test_runtime_keeps_approved_https_as_default() -> None:
    settings = _settings(
        recognition_processor_endpoint="https://recognition.example.com/v1",
        recognition_processor_adapter="private_contract",
        recognition_processor_https_host_allowlist="recognition.example.com",
    )

    assert _approved_endpoint(settings) == "https://recognition.example.com/v1"


def test_runtime_rejects_processor_construction_when_not_enabled() -> None:
    with pytest.raises(ProcessorConfigurationError, match="not enabled"):
        build_recognition_service(session=None, settings=_settings())  # type: ignore[arg-type]


def _modal_approval() -> str:
    return json.dumps(
        [
            {
                "policyVersion": "modal-ocr-policy-v1",
                "route": "modal-exam-image-v1",
                "provider": "operator-modal-paddleocr",
                "providerRelease": "modal-paddleocr-vl-v1-20260727-r1",
                "modelRelease": "PaddleOCR-VL-v1",
                "promptVersion": "paddleocr-vl-layout-parser-v1",
                "priceVersion": "modal-t4-estimate-20260727",
                "billingUnit": "image",
                "unitPriceCny": "0.10",
                "approvedKinds": ["printed_ocr", "exam_segmentation"],
                "approvedMimeTypes": ["image/jpeg", "image/png"],
                "dataRegion": "operator_selected_external",
                "dpaEvidenceRef": "https://modal.com/docs/guide/security#data-retention",
                "securityReviewRef": "ops/modal/anan_ocr_service.py",
                "approvalReceiptRef": "operator-modal-ocr-20260727",
                "providerIdempotencyCapability": "replayable_queryable",
                "providerIdempotencyScope": "provider_account",
                "providerIdempotencyKeyHeader": "Idempotency-Key",
                "providerRequestIdSource": "X-Request-ID",
                "providerReplayRetentionSeconds": 604800,
                "providerIdempotencyEvidenceRef": ("operator-modal-ocr-idempotency-20260727"),
                "validFrom": "2026-07-27T00:00:00Z",
                "validUntil": "2027-07-27T00:00:00Z",
            }
        ]
    )


def test_approval_records_operator_accepted_external_processing_truthfully() -> None:
    approval = json.loads(_modal_approval())[0]
    approval["providerDataHandling"] = "operator_accepted_external_processing"
    approval["providerRetentionSeconds"] = None

    parsed = ProcessorApproval.model_validate(approval)

    assert parsed.provider_data_handling == "operator_accepted_external_processing"
    assert parsed.provider_retention_seconds is None


def test_legacy_staging_approval_uses_nonreplayable_mvp_defaults() -> None:
    approval = json.loads(_modal_approval())[0]
    for field in (
        "providerIdempotencyCapability",
        "providerIdempotencyScope",
        "providerIdempotencyKeyHeader",
        "providerRequestIdSource",
        "providerReplayRetentionSeconds",
        "providerIdempotencyEvidenceRef",
    ):
        approval.pop(field)

    parsed = ProcessorApproval.model_validate(approval)

    assert parsed.provider_idempotency_capability == "nonreplayable_audited"
    assert parsed.provider_idempotency_scope == "endpoint"
    assert parsed.provider_idempotency_key_header == "Idempotency-Key"
    assert parsed.provider_request_id_source == "X-Request-ID"
    assert parsed.provider_replay_retention_seconds == 0
    assert parsed.provider_idempotency_evidence_ref == "mvp-runtime-default@v1"


def test_runtime_constructs_release_pinned_modal_processor() -> None:
    service = build_recognition_service(
        session=None,  # type: ignore[arg-type]
        operation_session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        settings=_settings(
            recognition_processor_enabled=True,
            recognition_processor_endpoint="https://operator--recognize.modal.run",
            recognition_processor_token="private-modal-token",
            recognition_processor_release_id="modal-paddleocr-vl-v1-20260727-r1",
            recognition_processor_adapter="modal_paddle_ocr",
            recognition_processor_actual_model="PaddleOCR-VL-v1",
            recognition_processor_max_input_bytes=10 * 1024 * 1024,
            recognition_processor_max_output_bytes=2 * 1024 * 1024,
            recognition_processor_max_pages=1,
            recognition_processor_https_host_allowlist=("operator--recognize.modal.run"),
            recognition_processor_approvals_json=_modal_approval(),
            tos_access_key="test-access-key",
            tos_secret_key="test-secret-key",
            tos_endpoint="https://tos.example.com",
        ),
    )

    assert service is not None


def test_runtime_rejects_modal_multi_page_configuration() -> None:
    with pytest.raises(ProcessorConfigurationError, match="one image page"):
        build_recognition_service(
            session=None,  # type: ignore[arg-type]
            settings=_settings(
                recognition_processor_enabled=True,
                recognition_processor_endpoint=("https://operator--recognize.modal.run"),
                recognition_processor_token="private-modal-token",
                recognition_processor_release_id=("modal-paddleocr-vl-v1-20260727-r1"),
                recognition_processor_adapter="modal_paddle_ocr",
                recognition_processor_actual_model="PaddleOCR-VL-v1",
                recognition_processor_max_input_bytes=10 * 1024 * 1024,
                recognition_processor_max_output_bytes=2 * 1024 * 1024,
                recognition_processor_max_pages=2,
                recognition_processor_https_host_allowlist=("operator--recognize.modal.run"),
                recognition_processor_approvals_json=_modal_approval(),
                tos_access_key="test-access-key",
                tos_secret_key="test-secret-key",
                tos_endpoint="https://tos.example.com",
            ),
        )
