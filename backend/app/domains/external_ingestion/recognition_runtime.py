from __future__ import annotations

import json
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.external_ingestion.recognition.errors import ProcessorConfigurationError
from app.domains.external_ingestion.recognition.grok_vision import (
    GrokVisionRecognitionProcessor,
)
from app.domains.external_ingestion.recognition.models import ProcessorApproval
from app.domains.external_ingestion.recognition.processor import (
    HTTPRecognitionProcessor,
    ProcessorRegistry,
)
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition.service import RecognitionService
from app.internal_services.private_objects import PrivateObjectReader


def build_recognition_service(*, session: Session, settings: Settings) -> RecognitionService:
    endpoint = _approved_endpoint(settings)
    try:
        raw = json.loads(settings.recognition_processor_approvals_json)
        if not isinstance(raw, list) or not raw:
            raise ValueError("approval list is empty")
        approvals = tuple(ProcessorApproval.model_validate(item) for item in raw)
    except (ValueError, TypeError) as exc:
        raise ProcessorConfigurationError(
            "RECOGNITION_PROCESSOR_APPROVALS_JSON is invalid"
        ) from exc
    adapter = settings.recognition_processor_adapter.strip().casefold()
    plaintext_http = urlsplit(endpoint).scheme.casefold() == "http"
    if any(
        (approval.transport_security == "plaintext_http_operator_accepted")
        != plaintext_http
        for approval in approvals
    ):
        raise ProcessorConfigurationError(
            "recognition approval transport does not match the configured endpoint"
        )
    if adapter == "private_contract":
        processor = HTTPRecognitionProcessor(
            endpoint=endpoint,
            bearer_token=settings.recognition_processor_token,
            expected_provider_release=settings.recognition_processor_release_id,
        )
    elif adapter == "grok_vision_chat_completions":
        if any(
            approval.model_release
            != settings.recognition_processor_actual_model.strip()
            for approval in approvals
        ):
            raise ProcessorConfigurationError(
                "Grok recognition approval model release does not match runtime"
            )
        processor = GrokVisionRecognitionProcessor(
            endpoint=endpoint,
            bearer_token=settings.recognition_processor_token,
            expected_provider_release=settings.recognition_processor_release_id,
            requested_model=settings.recognition_processor_model,
            expected_actual_model=settings.recognition_processor_actual_model,
            source_reader=PrivateObjectReader(settings),
            maximum_input_bytes=settings.recognition_processor_max_input_bytes,
            maximum_output_bytes=settings.recognition_processor_max_output_bytes,
            maximum_pages=settings.recognition_processor_max_pages,
        )
    else:
        raise ProcessorConfigurationError(
            "RECOGNITION_PROCESSOR_ADAPTER is unsupported"
        )
    return RecognitionService(
        repository=SQLRecognitionRepository(session, environment=settings.environment),
        processors=ProcessorRegistry(
            approvals,
            {approval.route: processor for approval in approvals},
        ),
    )


def build_recognition_decision_service(*, session: Session) -> RecognitionService:
    """Build the user-decision path without constructing an external processor.

    Confirmation and rejection only append reviewed revisions; they must remain
    available when an answer confirmation atomically confirms its OMR lineage.
    """
    return RecognitionService(
        repository=SQLRecognitionRepository(session),
        processors=ProcessorRegistry((), {}),
    )


def _approved_endpoint(settings: Settings) -> str:
    value = settings.recognition_processor_endpoint
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").casefold()
        _ = parsed.port
    except ValueError as exc:
        raise ProcessorConfigurationError("recognition processor endpoint is invalid") from exc
    allowed = {
        item.strip().casefold()
        for item in settings.recognition_processor_https_host_allowlist.split(",")
        if item.strip()
    }
    structurally_valid = (
        bool(hostname)
        and hostname in allowed
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )
    if not structurally_valid:
        raise ProcessorConfigurationError(
            "recognition processor endpoint is not an approved host"
        )
    scheme = parsed.scheme.casefold()
    if scheme == "https":
        return value.rstrip("/")
    insecure_origin = settings.recognition_processor_insecure_http_origin.rstrip("/")
    endpoint_origin = f"{scheme}://{parsed.netloc}"
    if not (
        scheme == "http"
        and settings.recognition_processor_adapter.strip().casefold()
        == "grok_vision_chat_completions"
        and settings.recognition_processor_allow_insecure_http
        and insecure_origin
        and endpoint_origin == insecure_origin
    ):
        raise ProcessorConfigurationError(
            "recognition processor plaintext HTTP origin is not explicitly approved"
        )
    return value.rstrip("/")
