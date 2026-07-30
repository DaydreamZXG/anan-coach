from __future__ import annotations

from collections.abc import Callable
import json
from urllib.parse import urlsplit

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.external_ingestion.recognition.errors import ProcessorConfigurationError
from app.domains.external_ingestion.recognition.grok_vision import (
    GrokVisionRecognitionProcessor,
)
from app.domains.external_ingestion.recognition.modal_paddle import (
    ModalPaddleOCRRecognitionProcessor,
)
from app.domains.external_ingestion.recognition.models import ProcessorApproval
from app.domains.external_ingestion.recognition.operations import (
    SQLRecognitionOperationCoordinator,
)
from app.domains.external_ingestion.recognition.processor import (
    HTTPRecognitionProcessor,
    ProcessorRegistry,
)
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition.service import RecognitionService
from app.internal_services.private_objects import PrivateObjectReader


def build_recognition_service(
    *,
    session: Session,
    settings: Settings,
    operation_session_factory: Callable[[], Session] | None = None,
) -> RecognitionService:
    if not settings.recognition_processor_enabled:
        raise ProcessorConfigurationError("recognition processor is not enabled")
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
    if any(approval.transport_security != "tls" for approval in approvals):
        raise ProcessorConfigurationError("recognition approvals must require TLS")
    expected_request_id_source = {
        "private_contract": "body:providerRequestId",
        "grok_vision_chat_completions": "body:id",
        "modal_paddle_ocr": "X-Request-ID",
    }.get(adapter)
    if expected_request_id_source is not None and any(
        approval.provider_request_id_source != expected_request_id_source for approval in approvals
    ):
        raise ProcessorConfigurationError(
            "recognition provider request identity source does not match its adapter"
        )
    if adapter == "private_contract":
        processor = HTTPRecognitionProcessor(
            endpoint=endpoint,
            bearer_token=settings.recognition_processor_token,
            expected_provider_release=settings.recognition_processor_release_id,
        )
    elif adapter == "grok_vision_chat_completions":
        if any(
            approval.model_release != settings.recognition_processor_actual_model.strip()
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
    elif adapter == "modal_paddle_ocr":
        if settings.recognition_processor_max_pages != 1:
            raise ProcessorConfigurationError(
                "Modal recognition is approved for one image page per request"
            )
        if any(
            approval.model_release != settings.recognition_processor_actual_model.strip()
            for approval in approvals
        ):
            raise ProcessorConfigurationError(
                "Modal recognition approval model release does not match runtime"
            )
        processor = ModalPaddleOCRRecognitionProcessor(
            endpoint=endpoint,
            bearer_token=settings.recognition_processor_token,
            expected_provider_release=settings.recognition_processor_release_id,
            expected_model_release=settings.recognition_processor_actual_model,
            source_reader=PrivateObjectReader(settings),
            maximum_input_bytes=settings.recognition_processor_max_input_bytes,
            maximum_output_bytes=settings.recognition_processor_max_output_bytes,
        )
    else:
        raise ProcessorConfigurationError("RECOGNITION_PROCESSOR_ADAPTER is unsupported")
    if operation_session_factory is None:
        operation_session_factory = sessionmaker(
            bind=session.get_bind(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return RecognitionService(
        repository=SQLRecognitionRepository(session),
        operations=SQLRecognitionOperationCoordinator(
            session_factory=operation_session_factory,
            settings=settings,
            processor_endpoint_fingerprint=endpoint_fingerprint(endpoint),
        ),
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
        raise ProcessorConfigurationError("recognition processor endpoint is not an approved host")
    if parsed.scheme.casefold() != "https":
        raise ProcessorConfigurationError(
            "recognition processor must use HTTPS for learner content"
        )
    return value.rstrip("/")
