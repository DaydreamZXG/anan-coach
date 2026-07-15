"""Deterministic, licensed text-and-visual matching for external learning."""

from app.domains.external_ingestion.matching.models import (
    EvidenceAdmission,
    IndexedContent,
    MatchingPolicy,
    MatchQuery,
)
from app.domains.external_ingestion.matching.service import MatchingService
from app.domains.external_ingestion.matching.runtime import (
    MatchingRuntimeConfig,
    build_matching_service,
    build_matching_runtime,
)

__all__ = [
    "EvidenceAdmission",
    "IndexedContent",
    "MatchingPolicy",
    "MatchingRuntimeConfig",
    "MatchingService",
    "MatchQuery",
    "build_matching_service",
    "build_matching_runtime",
]
