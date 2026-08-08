from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module, util
import json
from pathlib import Path
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.external_learning.models import (
    CandidateState,
    RecognitionKind,
    RecognitionRevision,
)
from app.core.config import Settings
from app.domains.ai.capability_readiness import ocr_readiness
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.ai.governance import repository as governance_repository_module
from app.domains.ai.governance.catalog import scenario_policy
from app.domains.ai.governance.evaluation import (
    evaluate_suite,
    load_golden_manifest,
    reference_observations,
)
from app.domains.ai.governance.repository import AIGovernanceRepository
from app.domains.external_ingestion.recognition.errors import ProcessorConfigurationError
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    ProcessorApproval,
    ProcessorResult,
    TextSpan,
    enforce_provider_dispatch_fence,
)
from app.domains.external_ingestion.recognition.operation_contracts import (
    RecognitionOperationSpec,
)
from app.domains.external_ingestion.recognition.operations import (
    SQLRecognitionOperationCoordinator,
)
from app.domains.external_ingestion.recognition.processor import ProcessorRegistry
from app.domains.external_ingestion.recognition.repository import (
    SQLRecognitionRepository,
)
from app.domains.external_ingestion.recognition.service import RecognitionService
from app.workers.external_learning import _validate_worker_settings


NOW = datetime(2026, 7, 30, 8, tzinfo=UTC)
PRODUCTION_OCR_ENDPOINT = "https://ocr-selection.example.com/v1"


class CountingProcessor:
    provider_release = "release-2026-07-01"

    def __init__(self) -> None:
        self.calls = 0

    def process(self, request):
        enforce_provider_dispatch_fence(request)
        self.calls += 1
        return ProcessorResult(
            providerRequestId=f"durability-provider-{self.calls}",
            textSpans=(
                TextSpan(
                    spanId="span-1",
                    text="行政许可测试题",
                    box=BoundingBox(
                        pageNo=1,
                        x=0.1,
                        y=0.1,
                        width=0.8,
                        height=0.1,
                    ),
                    confidence=0.99,
                ),
            ),
            pageImageSha256=("2" * 64,),
            visualFingerprints=("3" * 64,),
            confidence=0.99,
        )


class BlockingDispatchProcessor(CountingProcessor):
    def __init__(self, *, local_preparation_started: Event, release_preparation: Event) -> None:
        super().__init__()
        self._local_preparation_started = local_preparation_started
        self._release_preparation = release_preparation

    def process(self, request):
        self._local_preparation_started.set()
        if not self._release_preparation.wait(timeout=10):
            raise RuntimeError("test local preparation was not released")
        return super().process(request)


def _approval() -> ProcessorApproval:
    return ProcessorApproval(
        policyVersion="ocr-private@2026-07",
        route="ocr-primary",
        provider="vendor",
        providerRelease=CountingProcessor.provider_release,
        modelRelease="ocr-model@v1",
        promptVersion="recognition-processor-policy@v1",
        priceVersion="ocr-price@v1",
        billingUnit="image",
        unitPriceCny="0.01",
        approvedKinds=(RecognitionKind.PRINTED_OCR,),
        approvedMimeTypes=("image/jpeg",),
        dataRegion="mainland_china",
        dpaEvidenceRef="dpa:recognition-durability",
        securityReviewRef="security:recognition-durability",
        approvalReceiptRef="approval:recognition-durability",
        providerIdempotencyCapability="replayable_queryable",
        providerIdempotencyScope="provider_account",
        providerIdempotencyKeyHeader="Idempotency-Key",
        providerRequestIdSource="body:providerRequestId",
        providerReplayRetentionSeconds=604_800,
        providerIdempotencyEvidenceRef="provider-contract:recognition-durability",
        validFrom=NOW - timedelta(days=1),
        validUntil=NOW + timedelta(days=30),
    )


def _coordinator(
    factory: sessionmaker[Session],
) -> SQLRecognitionOperationCoordinator:
    return SQLRecognitionOperationCoordinator(
        session_factory=factory,
        settings=Settings(),
        processor_endpoint_fingerprint="e" * 64,
    )


def _service(
    *,
    session: Session,
    factory: sessionmaker[Session],
    processor: CountingProcessor,
    at: datetime,
    candidate_ttl: timedelta = timedelta(days=7),
) -> RecognitionService:
    return RecognitionService(
        repository=SQLRecognitionRepository(session),
        operations=_coordinator(factory),
        processors=ProcessorRegistry(
            (_approval(),),
            {"ocr-primary": processor},
        ),
        clock=lambda: at,
        candidate_ttl=candidate_ttl,
    )


def _seed_owner_artifact(
    factory: sessionmaker[Session],
) -> tuple[UUID, UUID]:
    owner = uuid4()
    artifact = uuid4()
    with factory() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (:owner, 'OCR durable test')"
            ),
            {"owner": str(owner)},
        )
        session.execute(
            text(
                """
                insert into learning.source_artifact_versions (
                  artifact_version_id, artifact_id, owner_user_id, version_no,
                  source_kind, state, object_ref, mime_type, content_sha256,
                  byte_size, page_count, supersedes_artifact_version_id,
                  contract_payload, created_at
                ) values (
                  :artifact_version_id, :artifact_id, :owner_user_id, 1,
                  'camera_image', 'available', :object_ref, 'image/jpeg',
                  :sha, 1024, 1, null, '{}'::jsonb, :created_at
                )
                """
            ),
            {
                "artifact_version_id": str(artifact),
                "artifact_id": str(uuid4()),
                "owner_user_id": str(owner),
                "object_ref": f"tos://private/{owner}/{artifact}",
                "sha": "1" * 64,
                "created_at": NOW,
            },
        )
        session.commit()
    return owner, artifact


def _production_ocr_settings() -> Settings:
    approval_payload = _approval().model_dump(by_alias=True, mode="json")
    return Settings(
        environment="production",
        recognition_release_gate_enabled=True,
        recognition_processor_enabled=True,
        recognition_processor_endpoint=PRODUCTION_OCR_ENDPOINT,
        recognition_processor_token="test-ocr-token",
        recognition_processor_release_id=CountingProcessor.provider_release,
        recognition_processor_adapter="private_contract",
        recognition_processor_https_host_allowlist="ocr-selection.example.com",
        recognition_processor_approvals_json=json.dumps([approval_payload]),
        recognition_default_import_route="ocr-primary",
    )


def test_personal_beta_runtime_approval_does_not_require_empty_release_registry(
    db_session_factory: sessionmaker[Session],
) -> None:
    settings = replace(
        _production_ocr_settings(),
        recognition_release_gate_enabled=False,
    )
    coordinator = SQLRecognitionOperationCoordinator(
        session_factory=db_session_factory,
        settings=settings,
        processor_endpoint_fingerprint=endpoint_fingerprint(PRODUCTION_OCR_ENDPOINT),
    )
    with db_session_factory() as session:
        deployment_id, evaluation_state = coordinator._deployment(
            session=session,
            spec=RecognitionOperationSpec(
                operation_id=uuid4(),
                operation_key="recognition:runtime-approved:test",
                owner_user_id=uuid4(),
                artifact_version_id=uuid4(),
                scenario_key="recognition_printed_ocr",
                operation="recognition_process",
                provider="vendor",
                provider_release=CountingProcessor.provider_release,
                prompt_version="recognition-processor-policy@v1",
                model="ocr-model@v1",
                schema_version="recognition-processor-output@v1",
                price_version="ocr-price@v1",
                billing_unit="image",
                unit_count=1,
                unit_price_cny=Decimal("0.01"),
                input_fingerprint="1" * 64,
                request_hash="2" * 64,
                started_at=NOW,
            ),
        )

    assert deployment_id is None
    assert evaluation_state == "runtime_approved"


def _insert_active_ocr_release(session: Session) -> None:
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
              'ocr-selection-release-record', 'ocr-selection-provider',
              :provider_release, 'OCR selection test processor',
              'OCR selection test operator', :endpoint_fingerprint,
              'tls', 'approved', '["recognition_process"]'::jsonb,
              '["uploaded_image"]'::jsonb, 'mainland_china',
              'contractually_prohibited', 'zero_retention', 0,
              'not_applicable_zero_retention',
              '{
                "operatorIdentity":"operator-v1",
                "dataProcessingTerms":"dpa-v1",
                "trainingUse":"prohibited-v1",
                "retention":"zero-v1",
                "transportReview":"tls-v1"
              }'::jsonb,
              repeat('a', 64), :verified_at, :valid_from, :valid_until,
              :published_at
            )
            """
        ),
        {
            "provider_release": CountingProcessor.provider_release,
            "endpoint_fingerprint": endpoint_fingerprint(PRODUCTION_OCR_ENDPOINT),
            "verified_at": NOW - timedelta(days=2),
            "valid_from": NOW - timedelta(days=1),
            "valid_until": NOW + timedelta(days=30),
            "published_at": NOW - timedelta(days=1),
        },
    )


def _publish_current_ocr_deployment_leaves(
    session: Session,
) -> tuple[UUID, UUID]:
    suite = next(
        item
        for item in load_golden_manifest().suites
        if item.scenario_key == "recognition_printed_ocr"
    )
    repository = AIGovernanceRepository()
    repository.register_evaluation_suite(session, suite=suite)
    policy = scenario_policy("recognition_printed_ocr")
    deployment_ids: list[UUID] = []
    for release_id, price_version, maximum_cost in (
        ("ocr-compatible-earlier", "ocr-price@v1", Decimal("100")),
        ("ocr-incompatible-later", "ocr-price@v2", Decimal("101")),
    ):
        report = evaluate_suite(
            suite=suite,
            observations=reference_observations(suite),
            maximum_mean_cost_cny=maximum_cost,
            maximum_p95_cost_cny=maximum_cost,
        )
        evaluation_run_id = repository.record_evaluation_run(
            session,
            report=report,
            operation="recognition_process",
            provider="vendor",
            model="ocr-model@v1",
            model_tier=policy.model_tier,
            prompt_version="recognition-processor-policy@v1",
            schema_version="recognition-processor-output@v1",
            price_version=price_version,
        )
        deployment_ids.append(
            repository.publish_scenario_deployment(
                session,
                release_id=release_id,
                scenario_key="recognition_printed_ocr",
                operation="recognition_process",
                provider="vendor",
                model="ocr-model@v1",
                prompt_version="recognition-processor-policy@v1",
                schema_version="recognition-processor-output@v1",
                evaluation_run_id=evaluation_run_id,
            )
        )
    compatible_id, incompatible_id = deployment_ids
    return compatible_id, incompatible_id


def test_readiness_and_durable_coordinator_share_price_compatible_deployment(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governance_times = iter(NOW + timedelta(microseconds=index) for index in range(5))

    class _GovernanceClock:
        @staticmethod
        def now(timezone) -> datetime:
            assert timezone is UTC
            return next(governance_times)

    monkeypatch.setattr(governance_repository_module, "datetime", _GovernanceClock)
    settings = _production_ocr_settings()
    with db_session_factory() as session:
        _insert_active_ocr_release(session)
        compatible_id, incompatible_id = _publish_current_ocr_deployment_leaves(session)
        current_leaves = (
            session.execute(
                text(
                    """
                    select deployment.deployment_id, evaluation.price_version
                      from ai_audit.scenario_deployments deployment
                      join ai_audit.release_evaluation_runs evaluation
                        on evaluation.evaluation_run_id =
                           deployment.evaluation_run_id
                     where deployment.scenario_key = 'recognition_printed_ocr'
                       and deployment.operation = 'recognition_process'
                       and not exists (
                         select 1
                           from ai_audit.scenario_deployments successor
                          where successor.supersedes_deployment_id =
                                deployment.deployment_id
                       )
                     order by deployment.published_at desc
                    """
                )
            )
            .mappings()
            .all()
        )
        session.commit()

    assert [
        (UUID(str(row["deployment_id"])), str(row["price_version"])) for row in current_leaves
    ] == [
        (incompatible_id, "ocr-price@v2"),
        (compatible_id, "ocr-price@v1"),
    ]
    with db_session_factory() as session:
        readiness = ocr_readiness(session, settings=settings, at=NOW)
    assert (readiness.ready, readiness.reason) == (True, "ready")

    owner, artifact = _seed_owner_artifact(db_session_factory)
    processor = CountingProcessor()
    with db_session_factory() as session:
        service = RecognitionService(
            repository=SQLRecognitionRepository(session),
            operations=SQLRecognitionOperationCoordinator(
                session_factory=db_session_factory,
                settings=settings,
                processor_endpoint_fingerprint=endpoint_fingerprint(PRODUCTION_OCR_ENDPOINT),
            ),
            processors=ProcessorRegistry(
                (_approval(),),
                {"ocr-primary": processor},
            ),
            clock=lambda: NOW,
        )
        revision = service.recognize(
            owner_user_id=owner,
            artifact_version_id=artifact,
            recognition_kind=RecognitionKind.PRINTED_OCR,
            processor_route="ocr-primary",
            idempotency_key="price-compatible-selection",
        )
        session.rollback()

    assert processor.calls == 1
    with db_session_factory() as session:
        selected = (
            session.execute(
                text(
                    """
                    select deployment_id, evaluation_state, status, usage_state,
                           entitlement_state, reservation_id
                      from ai_audit.external_ai_operation_ledger
                     where operation_id = :operation_id
                    """
                ),
                {"operation_id": str(revision.recognition_revision_id)},
            )
            .mappings()
            .one()
        )
        incompatible_passed = session.execute(
            text(
                """
                select count(*)
                  from ai_audit.external_ai_operation_ledger
                 where deployment_id = :deployment_id
                   and evaluation_state = 'passed'
                """
            ),
            {"deployment_id": str(incompatible_id)},
        ).scalar_one()
        reservation_status = session.execute(
            text(
                "select status from commerce.feature_usage_reservations "
                "where reservation_id = :reservation_id"
            ),
            {"reservation_id": str(selected["reservation_id"])},
        ).scalar_one()
    assert (
        selected["deployment_id"],
        selected["evaluation_state"],
        selected["status"],
        selected["usage_state"],
        selected["entitlement_state"],
        reservation_status,
    ) == (
        compatible_id,
        "passed",
        "succeeded",
        "actual",
        "consumed",
        "consumed",
    )
    assert incompatible_passed == 0

    blocked_owner, blocked_artifact = _seed_owner_artifact(db_session_factory)
    unapproved_price = _approval().model_copy(update={"price_version": "ocr-price@unapproved"})
    with db_session_factory() as session:
        blocked_service = RecognitionService(
            repository=SQLRecognitionRepository(session),
            operations=SQLRecognitionOperationCoordinator(
                session_factory=db_session_factory,
                settings=settings,
                processor_endpoint_fingerprint=endpoint_fingerprint(PRODUCTION_OCR_ENDPOINT),
            ),
            processors=ProcessorRegistry(
                (unapproved_price,),
                {"ocr-primary": processor},
            ),
            clock=lambda: NOW,
        )
        with pytest.raises(
            ProcessorConfigurationError,
            match="no evaluated production deployment",
        ):
            blocked_service.recognize(
                owner_user_id=blocked_owner,
                artifact_version_id=blocked_artifact,
                recognition_kind=RecognitionKind.PRINTED_OCR,
                processor_route="ocr-primary",
                idempotency_key="unapproved-price-fails-closed",
            )
        session.rollback()

    assert processor.calls == 1
    with db_session_factory() as session:
        blocked_side_effects = (
            session.execute(
                text(
                    """
                    select
                      (
                        select count(*)
                          from ai_audit.external_ai_operation_ledger
                         where user_id = :owner
                      ) as ledger_rows,
                      (
                        select count(*)
                          from ai_audit.external_processing_receipts
                         where user_id = :owner
                      ) as dispatch_receipts,
                      (
                        select count(*)
                          from commerce.feature_usage_reservations
                         where user_id = :owner
                      ) as quota_reservations
                    """
                ),
                {"owner": str(blocked_owner)},
            )
            .mappings()
            .one()
        )
    assert dict(blocked_side_effects) == {
        "ledger_rows": 0,
        "dispatch_receipts": 0,
        "quota_reservations": 0,
    }


def test_migration_87_is_chained_after_worker_principal_authority() -> None:
    migration = import_module(
        "migrations.versions.20260730_87_recognition_operation_terminal_authority"
    )
    assert migration.revision == "20260730_87"
    assert migration.down_revision == "20260730_86"


def test_migration_88_is_linear_and_forward_only() -> None:
    migration = import_module("migrations.versions.20260730_88_recognition_reconciliation_outcomes")
    assert migration.revision == "20260730_88"
    assert migration.down_revision == "20260730_87"
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


def test_worker_reconciliation_age_preserves_provider_timeout_margin() -> None:
    with pytest.raises(ValueError, match="provider timeout safety margin"):
        _validate_worker_settings(Settings(recognition_operation_reconciliation_age_seconds=599))
    _validate_worker_settings(Settings(recognition_operation_reconciliation_age_seconds=600))


def test_stale_running_operation_reconciles_once_to_outcome_unknown_and_refunds_quota(
    db_session_factory: sessionmaker[Session],
) -> None:
    owner, artifact = _seed_owner_artifact(db_session_factory)
    operation_id = uuid4()
    spec = RecognitionOperationSpec(
        operation_id=operation_id,
        operation_key=f"recognition:{operation_id}",
        owner_user_id=owner,
        artifact_version_id=artifact,
        request_hash="9" * 64,
        scenario_key="recognition_printed_ocr",
        operation="recognition_process",
        provider="vendor",
        provider_release=CountingProcessor.provider_release,
        model="ocr-model@v1",
        prompt_version="recognition-processor-policy@v1",
        schema_version="recognition-processor-output@v1",
        price_version="ocr-price@v1",
        billing_unit="image",
        unit_count=1,
        unit_price_cny=Decimal("0.01"),
        input_fingerprint="1" * 64,
        started_at=NOW,
    )
    coordinator = _coordinator(db_session_factory)
    coordinator.begin(spec=spec)

    with db_session_factory() as session:
        database_url = session.get_bind().url.render_as_string(hide_password=False)
    script_path = (
        Path(__file__).resolve().parents[5] / "ops" / "scripts" / "audit_external_ai_boundary.py"
    )
    module_spec = util.spec_from_file_location(
        "test_audit_external_ai_boundary",
        script_path,
    )
    assert module_spec is not None and module_spec.loader is not None
    audit_module = util.module_from_spec(module_spec)
    module_spec.loader.exec_module(audit_module)
    report, passed = audit_module.audit(
        verify_private_object=False,
        settings=Settings(database_url=database_url),
    )
    assert passed is False
    assert report["checks"]["recognition_nonterminal_operations"] == 1
    assert report["checks"]["recognition_quota_lifecycle_mismatches"] == 0
    assert report["recognitionReceiptUsageStateCounts"] == {"planned": 1}
    assert "not standalone proof" in report["receiptSemantics"]

    reconciled = coordinator.reconcile_stale(
        stale_before=NOW + timedelta(minutes=15),
        completed_at=NOW + timedelta(minutes=16),
        limit=10,
    )
    replay = coordinator.begin(spec=spec)
    reconciled_again = coordinator.reconcile_stale(
        stale_before=NOW + timedelta(minutes=30),
        completed_at=NOW + timedelta(minutes=31),
        limit=10,
    )

    assert reconciled.operations == 1
    assert reconciled.refunded_reservations == 1
    assert reconciled_again.operations == 0
    assert (replay.created, replay.status, replay.usage_state) == (
        False,
        "outcome_unknown",
        "unknown",
    )
    with db_session_factory() as session:
        row = (
            session.execute(
                text(
                    """
                select operation.status, operation.usage_state,
                       operation.entitlement_state, operation.total_cost_cny,
                       operation.error_code,
                       reservation.status as reservation_status
                  from ai_audit.external_ai_operation_ledger operation
                  join commerce.feature_usage_reservations reservation
                    on reservation.reservation_id = operation.reservation_id
                 where operation.operation_id = :operation_id
                """
                ),
                {"operation_id": str(operation_id)},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "status": "outcome_unknown",
        "usage_state": "unknown",
        "entitlement_state": "released",
        "total_cost_cny": None,
        "error_code": "recognition_stale_outcome_unknown",
        "reservation_status": "refunded",
    }


def test_late_provider_success_cannot_replace_reconciled_outcome_unknown(
    db_session_factory: sessionmaker[Session],
) -> None:
    owner, artifact = _seed_owner_artifact(db_session_factory)
    operation_id = uuid4()
    spec = RecognitionOperationSpec(
        operation_id=operation_id,
        operation_key=f"recognition:{operation_id}",
        owner_user_id=owner,
        artifact_version_id=artifact,
        request_hash="8" * 64,
        scenario_key="recognition_printed_ocr",
        operation="recognition_process",
        provider="vendor",
        provider_release=CountingProcessor.provider_release,
        model="ocr-model@v1",
        prompt_version="recognition-processor-policy@v1",
        schema_version="recognition-processor-output@v1",
        price_version="ocr-price@v1",
        billing_unit="image",
        unit_count=1,
        unit_price_cny=Decimal("0.01"),
        input_fingerprint="1" * 64,
        started_at=NOW,
    )
    coordinator = _coordinator(db_session_factory)
    coordinator.begin(spec=spec)
    coordinator.reconcile_stale(
        stale_before=NOW + timedelta(minutes=15),
        completed_at=NOW + timedelta(minutes=16),
        limit=10,
    )
    late_revision = RecognitionRevision(
        recognitionRevisionId=operation_id,
        artifactVersionId=artifact,
        ownerUserId=owner,
        revisionNo=1,
        recognitionKind=RecognitionKind.PRINTED_OCR,
        state=CandidateState.CANDIDATE,
        processorPolicyVersion="ocr-private@2026-07",
        processorRoute="ocr-primary",
        inputSha256="1" * 64,
        outputSha256="5" * 64,
        candidatePayload={"late": True},
        expiresAt=NOW + timedelta(days=7),
        createdAt=NOW,
    )

    terminal = coordinator.succeed(
        operation_id=operation_id,
        revision=late_revision,
        output_fingerprint="5" * 64,
        latency_ms=1_000_000,
        completed_at=NOW + timedelta(minutes=17),
    )

    assert (terminal.status, terminal.usage_state) == (
        "outcome_unknown",
        "unknown",
    )
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from learning.recognition_revisions "
                    "where recognition_revision_id = :operation_id"
                ),
                {"operation_id": str(operation_id)},
            ).scalar_one()
            == 0
        )
        row = (
            session.execute(
                text(
                    """
                select operation.entitlement_state,
                       reservation.status as reservation_status
                  from ai_audit.external_ai_operation_ledger operation
                  join commerce.feature_usage_reservations reservation
                    on reservation.reservation_id = operation.reservation_id
                 where operation.operation_id = :operation_id
                """
                ),
                {"operation_id": str(operation_id)},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "entitlement_state": "released",
        "reservation_status": "refunded",
    }


def test_provider_dispatch_claim_resets_the_reconciliation_cutoff(
    db_session_factory: sessionmaker[Session],
) -> None:
    owner, artifact = _seed_owner_artifact(db_session_factory)
    operation_id = uuid4()
    spec = RecognitionOperationSpec(
        operation_id=operation_id,
        operation_key=f"recognition:{operation_id}",
        owner_user_id=owner,
        artifact_version_id=artifact,
        request_hash="7" * 64,
        scenario_key="recognition_printed_ocr",
        operation="recognition_process",
        provider="vendor",
        provider_release=CountingProcessor.provider_release,
        model="ocr-model@v1",
        prompt_version="recognition-processor-policy@v1",
        schema_version="recognition-processor-output@v1",
        price_version="ocr-price@v1",
        billing_unit="image",
        unit_count=1,
        unit_price_cny=Decimal("0.01"),
        input_fingerprint="1" * 64,
        started_at=NOW,
    )
    coordinator = _coordinator(db_session_factory)
    coordinator.begin(spec=spec)

    claimed = coordinator.claim_provider_dispatch(
        operation_id=operation_id,
        claimed_at=NOW + timedelta(minutes=14),
    )
    protected = coordinator.reconcile_stale(
        stale_before=NOW + timedelta(minutes=10),
        completed_at=NOW + timedelta(minutes=16),
        limit=10,
    )
    closed = coordinator.reconcile_stale(
        stale_before=NOW + timedelta(minutes=15),
        completed_at=NOW + timedelta(minutes=16),
        limit=10,
    )

    assert (claimed.status, claimed.usage_state) == ("running", "planned")
    assert protected.operations == 0
    assert closed.operations == 1
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select provider_dispatch_claimed_at "
                "from ai_audit.external_ai_operation_ledger "
                "where operation_id = :operation_id"
            ),
            {"operation_id": str(operation_id)},
        ).scalar_one() == NOW + timedelta(minutes=14)


def test_reconciliation_fences_provider_io_that_finishes_local_preparation_late(
    db_session_factory: sessionmaker[Session],
) -> None:
    owner, artifact = _seed_owner_artifact(db_session_factory)
    local_preparation_started = Event()
    release_preparation = Event()
    processor = BlockingDispatchProcessor(
        local_preparation_started=local_preparation_started,
        release_preparation=release_preparation,
    )
    errors: list[Exception] = []

    def recognize() -> None:
        with db_session_factory() as session:
            try:
                _service(
                    session=session,
                    factory=db_session_factory,
                    processor=processor,
                    at=NOW,
                ).recognize(
                    owner_user_id=owner,
                    artifact_version_id=artifact,
                    recognition_kind=RecognitionKind.PRINTED_OCR,
                    processor_route="ocr-primary",
                    idempotency_key="late-local-preparation",
                )
            except Exception as exc:
                errors.append(exc)
                session.rollback()

    request_thread = Thread(target=recognize, daemon=True)
    request_thread.start()
    assert local_preparation_started.wait(timeout=10)
    with db_session_factory() as session:
        operation_id = session.execute(
            text(
                """
                select operation_id
                  from ai_audit.external_ai_operation_ledger
                 where user_id = :owner and status = 'running'
                """
            ),
            {"owner": str(owner)},
        ).scalar_one()

    reconciled = _coordinator(db_session_factory).reconcile_stale(
        stale_before=NOW + timedelta(minutes=15),
        completed_at=NOW + timedelta(minutes=16),
        limit=10,
    )
    release_preparation.set()
    request_thread.join(timeout=10)

    assert request_thread.is_alive() is False
    assert reconciled.operations == 1
    assert processor.calls == 0
    assert errors
    with db_session_factory() as session:
        row = (
            session.execute(
                text(
                    """
                    select status, usage_state, provider_dispatch_claimed_at
                      from ai_audit.external_ai_operation_ledger
                     where operation_id = :operation_id
                    """
                ),
                {"operation_id": str(operation_id)},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "status": "outcome_unknown",
        "usage_state": "unknown",
        "provider_dispatch_claimed_at": None,
    }


def test_real_postgres_running_terminal_acl_and_quota_state_matrix(
    db_session_factory: sessionmaker[Session],
) -> None:
    owner, artifact = _seed_owner_artifact(db_session_factory)
    operation_id = uuid4()
    spec = RecognitionOperationSpec(
        operation_id=operation_id,
        operation_key=f"recognition:{operation_id}",
        owner_user_id=owner,
        artifact_version_id=artifact,
        request_hash="4" * 64,
        scenario_key="recognition_printed_ocr",
        operation="recognition_process",
        provider="vendor",
        provider_release=CountingProcessor.provider_release,
        model="ocr-model@v1",
        prompt_version="recognition-processor-policy@v1",
        schema_version="recognition-processor-output@v1",
        price_version="ocr-price@v1",
        billing_unit="image",
        unit_count=1,
        unit_price_cny=Decimal("0.01"),
        input_fingerprint="1" * 64,
        started_at=NOW,
    )
    coordinator = _coordinator(db_session_factory)

    running = coordinator.begin(spec=spec)
    assert running.created is True
    assert (running.status, running.usage_state) == ("running", "planned")
    with db_session_factory() as session:
        stored = (
            session.execute(
                text(
                    """
                select status, usage_state, entitlement_state, total_cost_cny,
                       output_fingerprint, latency_ms, error_code, completed_at,
                       reservation_id, reservation_source
                  from ai_audit.external_ai_operation_ledger
                 where operation_id = :operation_id
                """
                ),
                {"operation_id": str(operation_id)},
            )
            .mappings()
            .one()
        )
        assert stored["status"] == "running"
        assert stored["usage_state"] == "planned"
        assert stored["entitlement_state"] == "reserved"
        assert stored["reservation_id"] is not None
        assert stored["reservation_source"] == "catalog"
        assert all(
            stored[key] is None
            for key in (
                "total_cost_cny",
                "output_fingerprint",
                "latency_ms",
                "error_code",
                "completed_at",
            )
        )
        assert (
            session.execute(
                text(
                    "select count(*) from ai_audit.external_processing_receipts "
                    "where external_operation_id = :operation_id"
                ),
                {"operation_id": str(operation_id)},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select receipt_phase from ai_audit.external_processing_receipts "
                    "where external_operation_id = :operation_id"
                ),
                {"operation_id": str(operation_id)},
            ).scalar_one()
            == "authorized_dispatch_intent"
        )
        assert (
            session.execute(
                text(
                    "select status from commerce.feature_usage_reservations "
                    "where reservation_id = :reservation_id"
                ),
                {"reservation_id": str(stored["reservation_id"])},
            ).scalar_one()
            == "reserved"
        )
        assert (
            session.execute(
                text(
                    "select has_table_privilege("
                    "'public', 'ai_audit.external_ai_operation_ledger', "
                    "'INSERT,UPDATE,DELETE')"
                )
            ).scalar_one()
            is False
        )
        assert (
            session.execute(
                text(
                    "select has_function_privilege("
                    "'public', "
                    "'ai_audit.guard_external_ai_operation_terminal_transition()', "
                    "'EXECUTE')"
                )
            ).scalar_one()
            is False
        )

    with db_session_factory() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "update ai_audit.external_ai_operation_ledger "
                    "set provider = 'tampered' "
                    "where operation_id = :operation_id"
                ),
                {"operation_id": str(operation_id)},
            )
            session.commit()
        session.rollback()

    failed = coordinator.fail(
        operation_id=operation_id,
        error_code="processor_response_invalid",
        usage_state="unknown",
        latency_ms=19,
        completed_at=NOW + timedelta(seconds=1),
    )
    assert (failed.status, failed.usage_state) == ("outcome_unknown", "unknown")
    replay = coordinator.fail(
        operation_id=operation_id,
        error_code="different_error_must_not_replace_terminal",
        usage_state="actual",
        latency_ms=99,
        completed_at=NOW + timedelta(seconds=2),
    )
    assert (replay.status, replay.usage_state) == ("outcome_unknown", "unknown")

    with db_session_factory() as session:
        stored = (
            session.execute(
                text(
                    """
                select status, usage_state, entitlement_state, total_cost_cny,
                       output_fingerprint, latency_ms, error_code, completed_at,
                       reservation_id
                  from ai_audit.external_ai_operation_ledger
                 where operation_id = :operation_id
                """
                ),
                {"operation_id": str(operation_id)},
            )
            .mappings()
            .one()
        )
        assert stored["status"] == "outcome_unknown"
        assert stored["usage_state"] == "unknown"
        assert stored["entitlement_state"] == "released"
        assert stored["total_cost_cny"] is None
        assert stored["output_fingerprint"] is None
        assert stored["latency_ms"] == 19
        assert stored["error_code"] == "processor_response_invalid"
        assert (
            session.execute(
                text(
                    "select status from commerce.feature_usage_reservations "
                    "where reservation_id = :reservation_id"
                ),
                {"reservation_id": str(stored["reservation_id"])},
            ).scalar_one()
            == "refunded"
        )
        assert (
            session.execute(
                text(
                    "select count(*) from commerce.entitlement_audit_logs "
                    "where user_id = :owner and action = 'quota_refunded'"
                ),
                {"owner": str(owner)},
            ).scalar_one()
            == 1
        )

    with db_session_factory() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    """
                    update ai_audit.external_ai_operation_ledger
                       set status = 'succeeded',
                           usage_state = 'actual',
                           total_cost_cny = 0.01,
                           output_fingerprint = :output,
                           error_code = null
                     where operation_id = :operation_id
                    """
                ),
                {"operation_id": str(operation_id), "output": "5" * 64},
            )
            session.commit()
        session.rollback()

    existing = coordinator.begin(spec=spec)
    assert existing.created is False
    assert (existing.status, existing.usage_state) == (
        "outcome_unknown",
        "unknown",
    )

    actual_operation_id = uuid4()
    actual_spec = replace(
        spec,
        operation_id=actual_operation_id,
        operation_key=f"recognition:{actual_operation_id}",
        request_hash="6" * 64,
    )
    coordinator.begin(spec=actual_spec)
    actual = coordinator.fail(
        operation_id=actual_operation_id,
        error_code="processor_response_invalid",
        usage_state="actual",
        latency_ms=23,
        completed_at=NOW + timedelta(seconds=3),
    )
    assert (actual.status, actual.usage_state) == ("failed", "actual")
    with db_session_factory() as session:
        actual_row = (
            session.execute(
                text(
                    """
                select total_cost_cny, output_fingerprint, entitlement_state,
                       reservation_id
                  from ai_audit.external_ai_operation_ledger
                 where operation_id = :operation_id
                """
                ),
                {"operation_id": str(actual_operation_id)},
            )
            .mappings()
            .one()
        )
        assert actual_row["total_cost_cny"] == Decimal("0.01000000")
        assert actual_row["output_fingerprint"] is None
        assert actual_row["entitlement_state"] == "released"
        assert (
            session.execute(
                text(
                    "select status from commerce.feature_usage_reservations "
                    "where reservation_id = :reservation_id"
                ),
                {"reservation_id": str(actual_row["reservation_id"])},
            ).scalar_one()
            == "refunded"
        )

    local_operation_id = uuid4()
    local_spec = replace(
        spec,
        operation_id=local_operation_id,
        operation_key=f"recognition:{local_operation_id}",
        request_hash="7" * 64,
    )
    coordinator.begin(spec=local_spec)
    local_failure = coordinator.fail(
        operation_id=local_operation_id,
        error_code="processor_not_dispatched",
        usage_state="not_dispatched",
        latency_ms=3,
        completed_at=NOW + timedelta(seconds=4),
    )
    assert (local_failure.status, local_failure.usage_state) == (
        "failed",
        "not_dispatched",
    )
    with db_session_factory() as session:
        local_row = (
            session.execute(
                text(
                    """
                select total_cost_cny, entitlement_state, reservation_id
                  from ai_audit.external_ai_operation_ledger
                 where operation_id = :operation_id
                """
                ),
                {"operation_id": str(local_operation_id)},
            )
            .mappings()
            .one()
        )
        assert local_row["total_cost_cny"] == Decimal("0E-8")
        assert local_row["entitlement_state"] == "released"
        assert (
            session.execute(
                text(
                    "select status from commerce.feature_usage_reservations "
                    "where reservation_id = :reservation_id"
                ),
                {"reservation_id": str(local_row["reservation_id"])},
            ).scalar_one()
            == "refunded"
        )


@pytest.mark.parametrize("command", ["recognize", "reprocess"])
def test_receipt_and_terminal_survive_business_rollback_and_retry_never_redispatches(
    db_session_factory: sessionmaker[Session],
    command: str,
) -> None:
    owner, artifact = _seed_owner_artifact(db_session_factory)
    source = None
    if command == "reprocess":
        with db_session_factory() as session:
            source = _service(
                session=session,
                factory=db_session_factory,
                processor=CountingProcessor(),
                at=NOW,
                candidate_ttl=timedelta(seconds=1),
            ).recognize(
                owner_user_id=owner,
                artifact_version_id=artifact,
                recognition_kind=RecognitionKind.PRINTED_OCR,
                processor_route="ocr-primary",
                idempotency_key="durable-source",
            )
            session.commit()

    processor = CountingProcessor()
    target_key = f"durable-{command}-rollback"
    with db_session_factory() as session:
        service = _service(
            session=session,
            factory=db_session_factory,
            processor=processor,
            at=NOW + timedelta(seconds=2),
        )
        if command == "recognize":
            revision = service.recognize(
                owner_user_id=owner,
                artifact_version_id=artifact,
                recognition_kind=RecognitionKind.PRINTED_OCR,
                processor_route="ocr-primary",
                idempotency_key=target_key,
            )
        else:
            assert source is not None
            revision = service.reprocess(
                owner_user_id=owner,
                recognition_revision_id=source.recognition_revision_id,
                expected_revision_no=1,
                processor_route="ocr-primary",
                idempotency_key=target_key,
            )
        operation_id = revision.recognition_revision_id
        session.rollback()

    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from learning.recognition_revisions "
                    "where recognition_revision_id = :operation_id"
                ),
                {"operation_id": str(operation_id)},
            ).scalar_one()
            == 1
        )
        persisted = SQLRecognitionRepository(session).revision(recognition_revision_id=operation_id)
        assert persisted == revision
        terminal = (
            session.execute(
                text(
                    """
                select status, usage_state, entitlement_state, reservation_id
                  from ai_audit.external_ai_operation_ledger
                 where operation_id = :operation_id
                """
                ),
                {"operation_id": str(operation_id)},
            )
            .mappings()
            .one()
        )
        assert (terminal["status"], terminal["usage_state"]) == (
            "succeeded",
            "actual",
        )
        assert terminal["entitlement_state"] == "consumed"
        assert (
            session.execute(
                text(
                    "select count(*) from ai_audit.external_processing_receipts "
                    "where external_operation_id = :operation_id"
                ),
                {"operation_id": str(operation_id)},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select status from commerce.feature_usage_reservations "
                    "where reservation_id = :reservation_id"
                ),
                {"reservation_id": str(terminal["reservation_id"])},
            ).scalar_one()
            == "consumed"
        )

    with db_session_factory() as session:
        service = _service(
            session=session,
            factory=db_session_factory,
            processor=processor,
            at=NOW + timedelta(seconds=3),
        )
        if command == "recognize":
            replayed = service.recognize(
                owner_user_id=owner,
                artifact_version_id=artifact,
                recognition_kind=RecognitionKind.PRINTED_OCR,
                processor_route="ocr-primary",
                idempotency_key=target_key,
            )
        else:
            assert source is not None
            replayed = service.reprocess(
                owner_user_id=owner,
                recognition_revision_id=source.recognition_revision_id,
                expected_revision_no=1,
                processor_route="ocr-primary",
                idempotency_key=target_key,
            )
        assert replayed == revision
        session.rollback()
    assert processor.calls == 1
