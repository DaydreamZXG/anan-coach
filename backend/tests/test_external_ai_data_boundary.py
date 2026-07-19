from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.ai.external_processing import (
    ExternalProcessingAuthorizationError,
    ExternalProcessingRepository,
    endpoint_fingerprint,
    validate_minimized_external_payload,
)
from app.main import create_app


def test_public_processor_disclosure_exposes_blocked_reasons_without_endpoint_hash(
    db_session_factory: sessionmaker[Session],
) -> None:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    response = TestClient(app).get("/api/v2/privacy/external-ai-processors")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["contractVersion"] == "external-processor-disclosure@v1"
    grok = next(
        item
        for item in payload["processors"]
        if item["processorReleaseId"] == "grok-vision-operator-proxy-v1"
    )
    assert grok["realUserStatus"] == "blocked"
    assert grok["transportSecurity"] == "plaintext_http_blocked"
    assert "endpointFingerprint" not in grok


def test_real_user_authorization_requires_exact_immutable_approved_release(
    db_session: Session,
) -> None:
    repository = ExternalProcessingRepository()
    with pytest.raises(ExternalProcessingAuthorizationError):
        repository.require_active_release(
            db_session,
            declared_provider_release="gemini-web2api-2026-07-18.1",
            provider_endpoint_fingerprint=endpoint_fingerprint(
                "https://gemini-web2api.ergouapi.co/v1"
            ),
            purpose="companion_answer",
            data_categories=("learner_question",),
        )

    now = datetime.now(UTC)
    release_id = "contracted-processor-release-v1"
    endpoint_hash = endpoint_fingerprint("https://processor.example.com/v1")
    db_session.execute(
        text(
            """
            insert into ai_audit.processor_release_records (
              processor_release_record_id, provider_key, provider_release,
              display_name, operator_name, endpoint_fingerprint,
              transport_security, real_user_status, purposes, data_categories,
              data_region, model_training_use, retention_policy,
              retention_seconds, deletion_support, evidence,
              evidence_fingerprint, verified_at, valid_from, valid_until,
              published_at
            ) values (
              :record_id, 'contracted_processor', :provider_release,
              'Contracted Processor', 'Example Operator', :endpoint_fingerprint,
              'tls', 'approved', '["companion_answer"]'::jsonb,
              '["learner_question"]'::jsonb, 'mainland_china',
              'contractually_prohibited', 'zero_retention', 0,
              'not_applicable_zero_retention',
              '{
                "operatorIdentity":"legal-entity-v1",
                "dataProcessingTerms":"dpa-v1",
                "trainingUse":"prohibited-v1",
                "retention":"zero-v1",
                "transportReview":"tls-review-v1"
              }'::jsonb,
              repeat('a', 64), :now, :now, :valid_until, :now
            )
            """
        ),
        {
            "record_id": release_id,
            "provider_release": "processor-contract-v1",
            "endpoint_fingerprint": endpoint_hash,
            "now": now,
            "valid_until": now + timedelta(days=30),
        },
    )
    assert repository.require_active_release(
        db_session,
        declared_provider_release="processor-contract-v1",
        provider_endpoint_fingerprint=endpoint_hash,
        purpose="companion_answer",
        data_categories=("learner_question",),
        at=now,
    ) == release_id

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "update ai_audit.processor_release_records "
                "set operator_name = 'Changed' "
                "where processor_release_record_id = :record_id"
            ),
            {"record_id": release_id},
        )
    db_session.rollback()


def test_external_payload_minimizer_rejects_account_and_storage_fields() -> None:
    validate_minimized_external_payload(
        {"question": "test", "evidence": [{"score": 80}]}
    )
    with pytest.raises(ValueError, match="ownerUserId"):
        validate_minimized_external_payload(
            {"question": "test", "context": {"ownerUserId": "sensitive"}}
        )
    with pytest.raises(ValueError, match="objectRef"):
        validate_minimized_external_payload(
            {"image": {"objectRef": "private/source.png"}}
        )


def test_processor_endpoint_fingerprint_rejects_embedded_secret_material() -> None:
    with pytest.raises(ValueError, match="credentials"):
        endpoint_fingerprint("https://user:secret@processor.example.com/v1")
    with pytest.raises(ValueError, match="query or fragment"):
        endpoint_fingerprint("https://processor.example.com/v1?api_key=secret")
