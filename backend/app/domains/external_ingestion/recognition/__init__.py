"""Fail-closed recognition pipeline for private external-learning artifacts."""

from app.domains.external_ingestion.recognition.models import (
    ArtifactInput,
    ProcessorApproval,
    RecognitionEvidenceDecision,
)
from app.domains.external_ingestion.recognition.service import RecognitionService

__all__ = [
    "ArtifactInput",
    "ProcessorApproval",
    "RecognitionEvidenceDecision",
    "RecognitionService",
]
