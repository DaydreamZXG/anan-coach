from __future__ import annotations

import json
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.external_ingestion.recognition.errors import ProcessorConfigurationError
from app.domains.external_ingestion.recognition.models import ProcessorApproval
from app.domains.external_ingestion.recognition.processor import (
    HTTPRecognitionProcessor,
    ProcessorRegistry,
)
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition.service import RecognitionService


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
    processor = HTTPRecognitionProcessor(
        endpoint=endpoint,
        bearer_token=settings.recognition_processor_token,
        expected_provider_release=settings.recognition_processor_release_id,
    )
    return RecognitionService(
        repository=SQLRecognitionRepository(session),
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
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or hostname not in allowed
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProcessorConfigurationError(
            "recognition processor endpoint is not an approved HTTPS host"
        )
    return value.rstrip("/")
