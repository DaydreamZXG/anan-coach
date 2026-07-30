from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import RecognitionKind
from app.core.config import Settings
from app.domains.ai.external_processing import (
    ExternalProcessingAuthorizationError,
    ExternalProcessingRepository,
    endpoint_fingerprint,
)
from app.domains.ai.governance.repository import (
    AIGovernanceReleaseError,
    AIGovernanceRepository,
)
from app.domains.ai.model_cost import (
    ModelCostConfigurationError,
    ModelPriceCard,
)
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
)
from app.domains.external_ingestion.recognition.models import ProcessorApproval
from app.domains.external_ingestion.recognition_runtime import (
    build_recognition_service,
)
from app.domains.xingce_learning.cause_agent.provider import (
    CauseAgentProvider,
    CauseAgentProviderConfigurationError,
)


@dataclass(frozen=True, slots=True)
class CapabilityReadiness:
    capability: str
    ready: bool
    reason: str
    provider_release_id: str | None


def cause_agent_readiness(
    session: Session,
    *,
    settings: Settings,
    at: datetime | None = None,
) -> CapabilityReadiness:
    """Fail-closed readiness for real-user Xingce cause processing."""

    release_id = settings.ai_xingce_cause_provider_release_id.strip()
    if not release_id:
        return _unavailable("xingce_cause_agent", "disabled", None)
    if not settings.ai_xingce_cause_provider_dpa_confirmed:
        return _unavailable(
            "xingce_cause_agent",
            "dpa_or_security_evidence_incomplete",
            release_id,
        )

    production_settings = replace(settings, environment="production")
    provider = CauseAgentProvider(production_settings)
    try:
        provider.validate_configuration()
        price_card = ModelPriceCard.from_settings(production_settings)
        endpoint = endpoint_fingerprint(production_settings.ai_provider_base_url)
    except (
        CauseAgentProviderConfigurationError,
        ModelCostConfigurationError,
        ValueError,
    ):
        return _unavailable(
            "xingce_cause_agent",
            "configuration_invalid",
            release_id,
        )

    if not _real_user_release_is_active(
        session,
        provider_release=release_id,
        endpoint=endpoint,
        purpose="cause_candidate_generate",
        data_categories=("learning_evidence", "learner_answer"),
        at=at,
    ):
        return _unavailable(
            "xingce_cause_agent",
            "real_user_processing_not_approved",
            release_id,
        )
    try:
        AIGovernanceRepository().require_current_deployment(
            session,
            scenario_key="xingce_cause_analysis",
            operation="cause_candidate_generate",
            provider=provider.provider_name,
            model=provider.model,
            prompt_version=provider.prompt_version,
            schema_version=provider.schema_version,
            price_version=price_card.version,
        )
    except AIGovernanceReleaseError:
        return _unavailable(
            "xingce_cause_agent",
            "release_not_approved",
            release_id,
        )
    return CapabilityReadiness(
        capability="xingce_cause_agent",
        ready=True,
        reason="ready",
        provider_release_id=release_id,
    )


def ocr_readiness(
    session: Session,
    *,
    settings: Settings,
    at: datetime | None = None,
) -> CapabilityReadiness:
    """Fail-closed readiness for the real-user photo/import OCR route."""

    release_id = settings.recognition_processor_release_id.strip() or None
    if not settings.recognition_processor_enabled:
        return _unavailable("ocr", "disabled", release_id)
    try:
        raw_approvals = json.loads(settings.recognition_processor_approvals_json)
        if not isinstance(raw_approvals, list) or not raw_approvals:
            raise ValueError("approval list is empty")
        approvals = tuple(ProcessorApproval.model_validate(item) for item in raw_approvals)
    except (TypeError, ValueError, ValidationError):
        return _unavailable(
            "ocr",
            "approval_evidence_incomplete",
            release_id,
        )

    checked_at = at or datetime.now(UTC)
    route = settings.recognition_default_import_route.strip()
    approval = next(
        (
            item
            for item in approvals
            if item.route == route
            and item.provider_release == release_id
            and item.valid_from <= checked_at < item.valid_until
            and (
                RecognitionKind.PRINTED_OCR in item.approved_kinds
                or RecognitionKind.EXAM_SEGMENTATION in item.approved_kinds
            )
        ),
        None,
    )
    if approval is None:
        return _unavailable(
            "ocr",
            "approval_evidence_incomplete",
            release_id,
        )
    try:
        build_recognition_service(session=session, settings=settings)
        endpoint = endpoint_fingerprint(settings.recognition_processor_endpoint)
    except (ProcessorConfigurationError, ValueError):
        return _unavailable("ocr", "configuration_invalid", release_id)

    if not _real_user_release_is_active(
        session,
        provider_release=release_id or "",
        endpoint=endpoint,
        purpose="recognition_process",
        data_categories=("uploaded_image",),
        at=checked_at,
    ):
        return _unavailable(
            "ocr",
            "real_user_processing_not_approved",
            release_id,
        )
    try:
        AIGovernanceRepository().require_current_deployment(
            session,
            scenario_key="recognition_printed_ocr",
            operation="recognition_process",
            provider=approval.provider,
            model=approval.model_release,
            prompt_version=approval.prompt_version,
            schema_version=approval.output_contract,
            price_version=approval.price_version,
        )
    except AIGovernanceReleaseError:
        return _unavailable("ocr", "release_not_approved", release_id)
    return CapabilityReadiness(
        capability="ocr",
        ready=True,
        reason="ready",
        provider_release_id=release_id,
    )


def _real_user_release_is_active(
    session: Session,
    *,
    provider_release: str,
    endpoint: str,
    purpose: str,
    data_categories: tuple[str, ...],
    at: datetime | None,
) -> bool:
    try:
        ExternalProcessingRepository().require_active_release(
            session,
            declared_provider_release=provider_release,
            provider_endpoint_fingerprint=endpoint,
            purpose=purpose,
            data_categories=data_categories,
            at=at,
        )
    except ExternalProcessingAuthorizationError:
        return False
    return True


def _unavailable(
    capability: str,
    reason: str,
    release_id: str | None,
) -> CapabilityReadiness:
    return CapabilityReadiness(
        capability=capability,
        ready=False,
        reason=reason,
        provider_release_id=release_id,
    )
