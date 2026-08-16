"""Offline-only student-岸岸 prompt isolation and shadow validation."""

from app.domains.teach_back_shadow.contracts import (
    GroundingClaim,
    GroundingVerificationOutput,
    GroundingValidationResult,
    ShadowPreparationInput,
    ShadowPreparationResult,
    StudentReproductionOutput,
)
from app.domains.teach_back_shadow.service import (
    STUDENT_ANAN_SYSTEM_PROMPT,
    prepare_shadow_evaluation,
    validate_grounding_output,
)

__all__ = [
    "GroundingClaim",
    "GroundingValidationResult",
    "GroundingVerificationOutput",
    "STUDENT_ANAN_SYSTEM_PROMPT",
    "ShadowPreparationInput",
    "ShadowPreparationResult",
    "StudentReproductionOutput",
    "prepare_shadow_evaluation",
    "validate_grounding_output",
]
