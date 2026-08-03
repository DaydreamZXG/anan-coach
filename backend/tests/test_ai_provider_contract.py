from __future__ import annotations

from hashlib import sha256

import httpx
import pytest

from app.domains.ai.provider_contract import (
    NONREPLAYABLE_AUDITED,
    parse_provider_json_object,
    provider_failure_is_retryable,
    provider_request_audit,
    validate_provider_audit_contract,
)


def test_provider_json_transport_accepts_only_bare_or_single_fenced_objects() -> None:
    assert parse_provider_json_object('{"answer":"ok"}') == {"answer": "ok"}
    assert parse_provider_json_object('```json\n{"answer":"ok"}\n```') == {
        "answer": "ok"
    }
    with pytest.raises(ValueError):
        parse_provider_json_object('result: {"answer":"ok"}')
    with pytest.raises(ValueError):
        parse_provider_json_object('[{"answer":"ok"}]')


def test_nonreplayable_provider_declares_zero_retention_and_body_request_id() -> None:
    validate_provider_audit_contract(
        idempotency_capability=NONREPLAYABLE_AUDITED,
        idempotency_key_header="Idempotency-Key",
        request_id_source="body:id",
        replay_retention_seconds=0,
    )
    with pytest.raises(ValueError, match="zero replay retention"):
        validate_provider_audit_contract(
            idempotency_capability=NONREPLAYABLE_AUDITED,
            idempotency_key_header="Idempotency-Key",
            request_id_source="body:id",
            replay_retention_seconds=60,
        )


def test_response_body_request_id_is_fingerprinted_without_persisting_raw_id() -> None:
    response = httpx.Response(200, request=httpx.Request("POST", "https://ai.example"))
    source, fingerprint = provider_request_audit(
        response=response,
        envelope={"id": "chatcmpl-sensitive-upstream-id"},
        configured_source="body:id",
    )
    assert source == "body:id"
    assert fingerprint == sha256(b"chatcmpl-sensitive-upstream-id").hexdigest()


def test_nonreplayable_provider_failures_are_never_automatically_retried() -> None:
    assert not provider_failure_is_retryable(
        idempotency_capability=NONREPLAYABLE_AUDITED
    )
    assert not provider_failure_is_retryable(
        idempotency_capability=NONREPLAYABLE_AUDITED,
        status_code=503,
    )
    assert provider_failure_is_retryable(
        idempotency_capability="replayable_queryable",
        status_code=503,
    )
