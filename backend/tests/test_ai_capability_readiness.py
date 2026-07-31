from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ai.capability_readiness import (
    cause_agent_readiness,
    ocr_readiness,
)
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.ai.governance.catalog import scenario_policy
from app.domains.ai.governance.evaluation import (
    evaluate_suite,
    load_golden_manifest,
    reference_observations,
)
from app.domains.ai.governance.repository import AIGovernanceRepository
from app.domains.external_ingestion.recognition.models import ProcessorApproval


def test_blocked_provider_and_disabled_ocr_are_unavailable(
    db_session: Session,
) -> None:
    cause = cause_agent_readiness(
        db_session,
        settings=_cause_settings(
            endpoint="https://gemini-web2api.ergouapi.co/v1",
            release_id="gemini-web2api-cause-2026-07-18.1",
        ),
    )
    ocr = ocr_readiness(db_session, settings=Settings())

    assert (cause.ready, cause.reason) == (
        False,
        "real_user_processing_not_approved",
    )
    assert (ocr.ready, ocr.reason) == (False, "disabled")


def test_ocr_approval_without_verified_provider_replay_boundary_is_rejected() -> None:
    now = datetime.now(UTC)
    payload = json.loads(
        _ocr_settings(
            endpoint="https://approved-ocr.example.com/v1",
            release_id="ocr-approved-v1",
            now=now,
        ).recognition_processor_approvals_json
    )[0]
    payload.pop("providerIdempotencyCapability")

    with pytest.raises(ValidationError):
        ProcessorApproval.model_validate(payload)


def test_only_approved_enabled_processors_are_real_user_ready(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    cause_endpoint = "https://approved-cause.example.com/v1"
    cause_release = "cause-approved-v1"
    _insert_processor_release(
        db_session,
        record_id="cause-approved-record-v1",
        provider_key="approved_cause",
        provider_release=cause_release,
        endpoint=cause_endpoint,
        purposes=("cause_candidate_generate",),
        data_categories=("learning_evidence", "learner_answer"),
        now=now,
    )
    _publish_deployment(
        db_session,
        scenario_key="xingce_cause_analysis",
        operation="cause_candidate_generate",
        provider=f"xingce_cause_provider@{cause_release}",
        model="gemini-3.5-flash",
        prompt_version="xingce-cause-prompt@v4",
        schema_version="cause-candidate@v2",
    )

    ocr_endpoint = "https://approved-ocr.example.com/v1"
    ocr_release = "ocr-approved-v1"
    _insert_processor_release(
        db_session,
        record_id="ocr-approved-record-v1",
        provider_key="approved_ocr",
        provider_release=ocr_release,
        endpoint=ocr_endpoint,
        purposes=("recognition_process",),
        data_categories=("uploaded_image",),
        now=now,
    )
    _publish_deployment(
        db_session,
        scenario_key="recognition_printed_ocr",
        operation="recognition_process",
        provider="approved-ocr-provider",
        model="ocr-model-v1",
        prompt_version="recognition-processor-policy@v1",
        schema_version="recognition-processor-output@v1",
    )
    db_session.commit()

    cause = cause_agent_readiness(
        db_session,
        settings=_cause_settings(
            endpoint=cause_endpoint,
            release_id=cause_release,
        ),
        at=now,
    )
    ocr = ocr_readiness(
        db_session,
        settings=_ocr_settings(
            endpoint=ocr_endpoint,
            release_id=ocr_release,
            now=now,
        ),
        at=now,
    )

    assert (cause.ready, cause.reason, cause.provider_release_id) == (
        True,
        "ready",
        cause_release,
    )
    assert (ocr.ready, ocr.reason, ocr.provider_release_id) == (
        True,
        "ready",
        ocr_release,
    )


def _cause_settings(*, endpoint: str, release_id: str) -> Settings:
    host = endpoint.removeprefix("https://").split("/", 1)[0]
    return Settings(
        environment="production",
        ai_api_key="test-provider-key",
        ai_provider_base_url=endpoint,
        ai_xingce_cause_model="gemini-3.5-flash",
        ai_xingce_cause_provider_release_id=release_id,
        ai_xingce_cause_provider_https_host_allowlist=host,
        ai_xingce_cause_provider_dpa_confirmed=True,
        ai_xingce_cause_provider_idempotency_capability="replayable_queryable",
        ai_xingce_cause_provider_idempotency_key_header="Idempotency-Key",
        ai_xingce_cause_provider_request_id_header="X-Provider-Request-Id",
        ai_xingce_cause_provider_replay_retention_seconds=604_800,
        ai_xingce_cause_max_input_bytes=1_000_000,
        ai_xingce_cause_max_output_bytes=100_000,
        ai_price_version="capability-readiness-price@v1",
        ai_input_cny_per_million_tokens="1",
        ai_cached_input_cny_per_million_tokens="0",
        ai_output_cny_per_million_tokens="1",
    )


def _ocr_settings(
    *,
    endpoint: str,
    release_id: str,
    now: datetime,
) -> Settings:
    host = endpoint.removeprefix("https://").split("/", 1)[0]
    approval = {
        "policyVersion": "ocr-policy@v1",
        "route": "ocr-primary",
        "provider": "approved-ocr-provider",
        "providerRelease": release_id,
        "modelRelease": "ocr-model-v1",
        "promptVersion": "recognition-processor-policy@v1",
        "priceVersion": "ocr-price@v1",
        "billingUnit": "image",
        "unitPriceCny": "0.01",
        "approvedKinds": ["printed_ocr", "exam_segmentation"],
        "approvedMimeTypes": ["image/jpeg", "image/png"],
        "dataRegion": "mainland_china",
        "dpaEvidenceRef": "dpa:approved-v1",
        "securityReviewRef": "security:approved-v1",
        "approvalReceiptRef": "approval:approved-v1",
        "transportSecurity": "tls",
        "providerDataHandling": "contracted_zero_retention",
        "modelTrainingUse": "prohibited",
        "providerRetentionSeconds": 0,
        "providerIdempotencyCapability": "replayable_queryable",
        "providerIdempotencyScope": "provider_account",
        "providerIdempotencyKeyHeader": "Idempotency-Key",
        "providerRequestIdSource": "body:providerRequestId",
        "providerReplayRetentionSeconds": 604800,
        "providerIdempotencyEvidenceRef": "provider-contract:approved-v1",
        "validFrom": (now - timedelta(days=1)).isoformat(),
        "validUntil": (now + timedelta(days=30)).isoformat(),
    }
    return Settings(
        environment="production",
        recognition_processor_enabled=True,
        recognition_processor_endpoint=endpoint,
        recognition_processor_token="test-ocr-token",
        recognition_processor_release_id=release_id,
        recognition_processor_adapter="private_contract",
        recognition_processor_https_host_allowlist=host,
        recognition_processor_approvals_json=json.dumps([approval]),
        recognition_default_import_route="ocr-primary",
    )


def _insert_processor_release(
    session: Session,
    *,
    record_id: str,
    provider_key: str,
    provider_release: str,
    endpoint: str,
    purposes: tuple[str, ...],
    data_categories: tuple[str, ...],
    now: datetime,
) -> None:
    session.execute(
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
              :record_id, :provider_key, :provider_release,
              'Approved test processor', 'Approved test operator',
              :endpoint_fingerprint, 'tls', 'approved',
              cast(:purposes as jsonb), cast(:data_categories as jsonb),
              'mainland_china', 'contractually_prohibited', 'zero_retention',
              0, 'not_applicable_zero_retention',
              '{
                "operatorIdentity":"operator-v1",
                "dataProcessingTerms":"dpa-v1",
                "trainingUse":"prohibited-v1",
                "retention":"zero-v1",
                "transportReview":"tls-v1"
              }'::jsonb,
              repeat('a', 64), :now, :now, :valid_until, :now
            )
            """
        ),
        {
            "record_id": record_id,
            "provider_key": provider_key,
            "provider_release": provider_release,
            "endpoint_fingerprint": endpoint_fingerprint(endpoint),
            "purposes": json.dumps(purposes),
            "data_categories": json.dumps(data_categories),
            "now": now,
            "valid_until": now + timedelta(days=30),
        },
    )


def _publish_deployment(
    session: Session,
    *,
    scenario_key: str,
    operation: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
) -> None:
    suite = next(
        item for item in load_golden_manifest().suites if item.scenario_key == scenario_key
    )
    repository = AIGovernanceRepository()
    repository.register_evaluation_suite(session, suite=suite)
    report = evaluate_suite(
        suite=suite,
        observations=reference_observations(suite),
        maximum_mean_cost_cny=Decimal("100"),
        maximum_p95_cost_cny=Decimal("100"),
    )
    policy = scenario_policy(scenario_key)  # type: ignore[arg-type]
    run_id = repository.record_evaluation_run(
        session,
        report=report,
        operation=operation,
        provider=provider,
        model=model,
        model_tier=policy.model_tier,
        prompt_version=prompt_version,
        schema_version=schema_version,
        price_version=(
            "ocr-price@v1"
            if scenario_key == "recognition_printed_ocr"
            else "capability-readiness-price@v1"
        ),
    )
    repository.publish_scenario_deployment(
        session,
        release_id=f"capability-readiness-{scenario_key}",
        scenario_key=scenario_key,  # type: ignore[arg-type]
        operation=operation,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        schema_version=schema_version,
        evaluation_run_id=run_id,
    )
