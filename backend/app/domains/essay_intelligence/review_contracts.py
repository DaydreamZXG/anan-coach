from __future__ import annotations

from hashlib import sha256
import json


GROUNDING_CONTRACT_VERSION = "essay-grounding-v2"
REVIEW_CONTRACT_VERSION = "essay-review-v2"
GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION = "essay-provider-review-v4"
RUBRIC_CONTRACT_VERSION = "guokao-essay-rubric-v1"
GROUNDED_REVIEW_PROMPT_VERSION = "grounded-essay-review-v4"
GROUNDED_REVIEW_PROVIDER_NAME = "openai_compatible"
REVIEW_EXECUTION_CONTRACT_VERSION = "essay-review-execution-v1"


def _fingerprint(value: object) -> str:
    """Stable content digest used to fingerprint review payloads across the domain."""
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def review_execution_fingerprint(
    *,
    input_fingerprint: str,
    execution_attempt_no: int,
    provider: str,
    model: str,
) -> str:
    """Identify one immutable model execution independently from review content."""
    if len(input_fingerprint) != 64:
        raise ValueError("Review input fingerprint must be a SHA-256 hex digest")
    if execution_attempt_no < 1:
        raise ValueError("Review execution attempt number must be positive")
    if not provider.strip() or not model.strip():
        raise ValueError("Review execution provider and model are required")
    payload = {
        "executionContractVersion": REVIEW_EXECUTION_CONTRACT_VERSION,
        "executionAttemptNo": execution_attempt_no,
        "groundingContractVersion": GROUNDING_CONTRACT_VERSION,
        "reviewContractVersion": REVIEW_CONTRACT_VERSION,
        "providerSchemaVersion": GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
        "rubricContractVersion": RUBRIC_CONTRACT_VERSION,
        "promptVersion": GROUNDED_REVIEW_PROMPT_VERSION,
        "provider": provider,
        "model": model,
        "inputFingerprint": input_fingerprint,
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
