"""Synthetic-only provider shadow for the grounded teach-back chain."""

from app.domains.teach_back_provider_shadow.contracts import (
    ProviderCallAuditCandidate,
    TeachBackProviderShadowResult,
)
from app.domains.teach_back_provider_shadow.provider import (
    TeachBackProviderConfig,
    TeachBackProviderConfigurationError,
    TeachBackProviderError,
    TeachBackShadowProvider,
)
from app.domains.teach_back_provider_shadow.service import (
    TeachBackShadowPreconditionError,
    run_provider_shadow,
    shadow_idempotency_key,
)

__all__ = [
    "ProviderCallAuditCandidate",
    "TeachBackProviderConfig",
    "TeachBackProviderConfigurationError",
    "TeachBackProviderError",
    "TeachBackProviderShadowResult",
    "TeachBackShadowPreconditionError",
    "TeachBackShadowProvider",
    "run_provider_shadow",
    "shadow_idempotency_key",
]
