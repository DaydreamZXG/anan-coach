from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any

import httpx

from app.core.config import Settings


REPLAYABLE_QUERYABLE = "replayable_queryable"
NONREPLAYABLE_AUDITED = "nonreplayable_audited"
BODY_REQUEST_ID_SOURCE = "body:id"
_BODY_REQUEST_ID_SOURCE = re.compile(r"body:[A-Za-z][A-Za-z0-9_]*")
_JSON_CODE_FENCE = re.compile(
    r"\A```(?:json)?\s*(?P<payload>\{.*\})\s*```\Z",
    re.DOTALL | re.IGNORECASE,
)


def parse_provider_json_object(value: object) -> dict[str, Any]:
    """Normalize one exact JSON object from OpenAI-compatible text gateways.

    Some gateways wrap otherwise valid structured output in a single JSON code
    fence even when response_format is supplied. Extra prose, arrays and scalar
    values remain invalid; this is transport normalization, not schema repair.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("provider content must be non-empty text")
    candidate = value.strip()
    match = _JSON_CODE_FENCE.fullmatch(candidate)
    if match is not None:
        candidate = match.group("payload")
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("provider content must contain exactly one JSON object")
    return parsed


def validate_processing_approval(
    settings: Settings,
    *,
    dpa_confirmed: bool,
) -> None:
    del settings
    if not dpa_confirmed:
        raise ValueError(
            "verified processor DPA/data terms are required; an operator decision cannot "
            "substitute for processor evidence"
        )


def validate_provider_audit_contract(
    *,
    idempotency_capability: str,
    idempotency_key_header: str,
    request_id_source: str,
    replay_retention_seconds: int,
) -> None:
    capability = idempotency_capability.strip()
    if capability not in {REPLAYABLE_QUERYABLE, NONREPLAYABLE_AUDITED}:
        raise ValueError(
            "provider requires verified replayable_queryable or nonreplayable_audited idempotency"
        )
    if re.fullmatch(r"[A-Za-z0-9-]+", idempotency_key_header.strip()) is None:
        raise ValueError("provider IDEMPOTENCY_KEY_HEADER is required")
    source = request_id_source.strip()
    if (
        _BODY_REQUEST_ID_SOURCE.fullmatch(source) is None
        and re.fullmatch(r"[A-Za-z0-9-]+", source) is None
    ):
        raise ValueError("provider REQUEST_ID_HEADER or body:id source is required")
    if capability == REPLAYABLE_QUERYABLE and replay_retention_seconds < 604_800:
        raise ValueError("provider REPLAY_RETENTION_SECONDS must be at least seven days")
    if capability == NONREPLAYABLE_AUDITED and replay_retention_seconds != 0:
        raise ValueError("non-replayable providers must declare zero replay retention")


def provider_failure_is_retryable(
    *, idempotency_capability: str, status_code: int | None = None
) -> bool:
    capability = idempotency_capability.strip()
    if capability and capability != REPLAYABLE_QUERYABLE:
        return False
    if status_code is None:
        return True
    return status_code in {408, 429} or status_code >= 500


def provider_request_audit(
    *,
    response: httpx.Response,
    envelope: dict[str, Any],
    configured_source: str,
) -> tuple[str | None, str | None]:
    source = configured_source.strip()
    if _BODY_REQUEST_ID_SOURCE.fullmatch(source) is not None:
        raw = envelope.get(source.removeprefix("body:"))
    else:
        source = source.casefold()
        raw = response.headers.get(source) if source else None
    request_id = raw.strip() if isinstance(raw, str) and raw.strip() else None
    if request_id is None:
        return None, None
    return source, sha256(request_id.encode("utf-8")).hexdigest()
