from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.config import get_settings
from app.core.database_compatibility import (
    ArtifactDatabaseCompatibility,
    DatabaseCompatibilityError,
    assert_database_is_compatible,
    artifact_database_revision,
    current_database_revisions,
    load_artifact_database_compatibility,
)
from app.contracts.guokao_xingce import GUOKAO_XINGCE_MODULES
from app.db.session import get_db_session
from app.domains.ai.capability_readiness import CapabilityReadiness
from app.domains.data_rights.worker import DataRightsWorker
from app.domains.external_ingestion.answering.worker import ExternalAIAnswerWorker
from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from app.domains.learning_media.worker import LearningMediaWorker
from app.domains.learning_packages.worker import PacketRenderWorker
from app.domains.operations import router as operations_router
from app.domains.private_knowledge.worker import PrivateKnowledgeDeletionWorker
from app.main import create_app
from app.platform.jobs import JOB_REGISTRY, BackgroundJobRepository
from app.workers.external_learning import ExternalLearningWorker
from tests.heartbeat_principal_helpers import (
    record_heartbeat_as_principal,
)


class _IdleUploadService:
    def recover_pending(self, **_kwargs: object) -> tuple[()]:
        return ()

    def expire_stale(self, **_kwargs: object) -> tuple[()]:
        return ()


def test_test_database_fixture_reaches_artifact_head(db_engine: Engine) -> None:
    assert current_database_revisions(db_engine.url.render_as_string(hide_password=False)) == (
        artifact_database_revision(),
    )
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                select exists (
                  select 1
                  from ai_audit.processor_release_records
                  where processor_release_record_id =
                    'hunyuan-cloud-function-companion-20260728-1'
                )
                """
                )
            ).scalar_one()
            is True
        )


def test_database_revision_authority_is_public_under_hostile_search_path(
    db_engine: Engine,
) -> None:
    shadow_schema = "shadow_revision_authority"
    with db_engine.begin() as connection:
        connection.execute(text(f"drop schema if exists {shadow_schema} cascade"))
        connection.execute(text(f"create schema {shadow_schema}"))
        connection.execute(
            text(
                f"""
                create table {shadow_schema}.alembic_version (
                  version_num varchar(255) not null
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                insert into {shadow_schema}.alembic_version (version_num)
                values ('shadow-revision')
                """
            )
        )
    database_url = db_engine.url.render_as_string(hide_password=False)
    separator = "&" if "?" in database_url else "?"
    hostile_url = (
        f"{database_url}{separator}"
        f"options=-csearch_path%3D{shadow_schema}"
    )
    try:
        assert current_database_revisions(hostile_url) == (
            artifact_database_revision(),
        )
    finally:
        with db_engine.begin() as connection:
            connection.execute(text(f"drop schema if exists {shadow_schema} cascade"))


def test_artifact_compatibility_accepts_only_declared_database_revision(
    db_engine: Engine,
) -> None:
    compatibility = load_artifact_database_compatibility(
        Path(__file__).resolve().parents[2] / "ops" / "release-db-compatibility.env"
    )
    database_url = db_engine.url.render_as_string(hide_password=False)

    assert (
        assert_database_is_compatible(
            compatibility,
            database_url=database_url,
        )
        == artifact_database_revision()
    )
    with db_engine.begin() as connection:
        connection.execute(text("update alembic_version set version_num = 'incompatible-revision'"))
    with pytest.raises(DatabaseCompatibilityError, match="is not compatible"):
        assert_database_is_compatible(
            compatibility,
            database_url=database_url,
        )


def test_current_head_standard_workers_start_idle(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEKNORA_PRIVATE_KNOWLEDGE_MODE", "canonical")
    settings = Settings()
    jobs = BackgroundJobRepository()
    external_worker = ExternalLearningWorker(
        session=db_session,
        settings=settings,
        packet_worker=PacketRenderWorker(
            renderer=object(),
            license_evaluator=object(),
            jobs=jobs,
        ),
        answer_worker=ExternalAIAnswerWorker(
            settings=settings,
            provider=object(),
        ),
        upload_service=_IdleUploadService(),
        jobs=jobs,
        learning_media_worker=LearningMediaWorker(
            storage=object(),
            scanner=object(),
            jobs=jobs,
        ),
        private_knowledge_worker=PrivateKnowledgeDeletionWorker(
            storage=object(),
            jobs=jobs,
        ),
    )

    assert external_worker.run_once(now=datetime.now(UTC)) is False
    assert (
        DataRightsWorker(
            storage=object(),
            settings=settings,
        ).process_next(db_session)
        is None
    )


def test_ready_rejects_revision_outside_artifact_compatibility_set(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory.begin() as session:
        session.execute(text("update alembic_version set version_num = 'stale-revision'"))

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "database schema is not at the required release"


def test_ready_accepts_newer_database_declared_by_old_head_artifact(
    db_session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_revision = artifact_database_revision()
    compatibility = _synthetic_old_head_compatibility(
        tmp_path,
        compatible_revisions=("synthetic_old_head", database_revision),
    )
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(db_session_factory)
    settings = Settings(
        required_database_revision=compatibility.migration_head,
        compatible_database_revisions=compatibility.compatible_revisions,
    )
    monkeypatch.setattr(operations_router, "get_settings", lambda: settings)

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 200, response.text
    assert response.json()["migration"] == database_revision


def test_ready_rejects_newer_database_not_declared_by_old_head_artifact(
    db_session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compatibility = _synthetic_old_head_compatibility(
        tmp_path,
        compatible_revisions=("synthetic_old_head",),
    )
    settings = Settings(
        required_database_revision=compatibility.migration_head,
        compatible_database_revisions=compatibility.compatible_revisions,
    )
    monkeypatch.setattr(operations_router, "get_settings", lambda: settings)

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "database schema is not at the required release"


def test_ready_rejects_missing_position_rule_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "position rule release authority is not ready (phase=no_release)"
    )


def test_ready_accepts_closed_position_business_window_for_non_selection_routes(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    now = datetime.now(UTC)
    _seed_release(
        db_session_factory,
        activated_at=now - timedelta(days=366),
        window_opens_at=now - timedelta(days=367),
        window_closes_at=now - timedelta(days=365),
    )
    _seed_knowledge_release(db_session_factory)

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 200, response.text
    assert response.json()["positionReleasePhase"] == "closed"


def test_ready_accepts_upcoming_position_business_window_for_preparation_routes(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    now = datetime.now(UTC)
    _seed_release(
        db_session_factory,
        activated_at=now,
        window_opens_at=now + timedelta(days=1),
        window_closes_at=now + timedelta(days=8),
    )
    _seed_knowledge_release(db_session_factory)

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 200, response.text
    assert response.json()["positionReleasePhase"] == "upcoming"


def test_ready_rejects_missing_knowledge_map_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "knowledge map release is not ready"


def test_ready_rejects_legacy_active_knowledge_map_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(
        db_session_factory,
        release_version="knowledge-map@legacy-pre-20260727-65:guokao:xingce",
        generator_version="legacy:generator-version-unknown",
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "knowledge map release is not ready"


def test_release_activation_rejects_expired_knowledge_map_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    with pytest.raises(
        IntegrityError,
        match="knowledge-map release activation freshness is invalid",
    ):
        _seed_knowledge_release(
            db_session_factory,
            fresh_until=datetime.now(UTC) - timedelta(days=1),
        )


def test_release_activation_rejects_time_before_generation(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    with pytest.raises(
        IntegrityError,
        match="knowledge-map release activation freshness is invalid",
    ):
        _seed_knowledge_release(
            db_session_factory,
            activated_at=datetime.now(UTC) - timedelta(days=1),
        )


def test_ready_rejects_release_without_actionable_target_node(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(
        db_session_factory,
        near5_occurrence_count=0,
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "knowledge map release is not ready"


def test_binding_authority_rejects_release_without_its_exact_vocabulary(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    with pytest.raises(
        IntegrityError,
        match="knowledge-map question binding authority is invalid",
    ):
        _seed_knowledge_release(db_session_factory, seed_vocabulary=False)


def test_ready_rejects_vocabulary_with_duplicate_canonical_code(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    duplicate_codes = tuple(
        module.code if module.code != "data_analysis" else "judgment_reasoning"
        for module in GUOKAO_XINGCE_MODULES
    )
    _seed_knowledge_release(
        db_session_factory,
        canonical_codes=duplicate_codes,
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "knowledge map release is not ready"


def test_ready_rejects_rotated_canonical_module_code_name_pairs(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    codes = tuple(module.code for module in GUOKAO_XINGCE_MODULES)
    _seed_knowledge_release(
        db_session_factory,
        canonical_codes=codes[1:] + codes[:1],
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "knowledge map release is not ready"


def test_ready_rejects_release_missing_an_actionable_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(
        db_session_factory,
        missing_actionable_module="政治理论",
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "knowledge map release is not ready"


def test_ready_rejects_multiple_active_knowledge_releases(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    with db_session_factory.begin() as session:
        session.execute(text("drop index product.product_knowledge_map_one_active_release_idx"))
    _seed_knowledge_release(
        db_session_factory,
        release_version="knowledge-map@ready-test-first",
    )
    _seed_knowledge_release(
        db_session_factory,
        release_version="knowledge-map@ready-test-second",
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "knowledge map release is not ready"


def test_ready_reports_active_position_rule_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    window_opens_at, window_closes_at = _seed_release(
        db_session_factory,
        activated_at=datetime.now(UTC),
    )
    _seed_knowledge_release(db_session_factory)
    with db_session_factory() as session:
        position_release_content_digest = session.execute(
            text(
                """
                select content_digest
                from product.position_rule_releases
                where status = 'active'
                """
            )
        ).scalar_one()

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert datetime.fromisoformat(payload["time"]).tzinfo is not None
    assert payload == {
        "status": "ready",
        "migration": get_settings().required_database_revision,
        "positionRuleRelease": "guokao-2026-rules-v1",
        "positionDataVersion": "guokao-2026-source-sha256-test",
        "positionReleasePhase": "open",
        "positionWindowKind": "primary",
        "positionWindowOpensAt": window_opens_at.isoformat(),
        "positionWindowClosesAt": window_closes_at.isoformat(),
        "positionWindowSourceSha256": "b" * 64,
        "positionReleaseContentDigest": position_release_content_digest,
        "knowledgeMapRelease": "knowledge-map@ready-test-v1",
        "knowledgeMapSourceDataVersion": "question-bank@ready-test-v1",
        "jobLeaseContract": "JOB-LEASE@v1",
        "time": payload["time"],
    }


def test_ready_rejects_a_registered_queue_past_its_age_budget(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(db_session_factory)
    with db_session_factory.begin() as session:
        BackgroundJobRepository().enqueue(
            session,
            job_key="readiness:cause-agent:stale",
            job_type="xingce_cause_agent_v1",
            input_version="cause-agent-run@v1",
            payload={"runId": str(uuid4())},
            run_after=datetime.now(UTC) - timedelta(minutes=6),
        )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "background job consumers are not ready"


def test_local_ready_accepts_an_idle_empty_queue_without_a_recent_heartbeat(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                delete from platform.background_job_consumer_heartbeats
                where consumer_unit = 'gongkao-dazi-cause-agent-worker'
                """
            )
        )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 200


def test_production_ready_rejects_an_idle_mandatory_consumer_without_a_recent_heartbeat(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                delete from platform.background_job_consumer_heartbeats
                where consumer_unit = 'gongkao-dazi-cause-agent-worker'
                """
            )
        )
    monkeypatch.setattr(
        operations_router,
        "get_settings",
        lambda: Settings(environment="production"),
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "background job consumers are not ready"


def test_ready_rejects_a_legacy_unregistered_active_job(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(text("alter table platform.background_jobs disable trigger all"))
        session.execute(
            text(
                """
                insert into platform.background_jobs (
                  job_id, job_key, job_type, input_version, payload,
                  status, attempt_count, max_attempts, run_after
                ) values (
                  cast(:job_id as uuid), 'legacy:orphan-readiness',
                  'legacy_orphan_v1', 'legacy@v1', '{}'::jsonb,
                  'queued', 0, 3, statement_timestamp()
                )
                """
            ),
            {"job_id": str(uuid4())},
        )
        session.execute(text("alter table platform.background_jobs enable trigger all"))

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "background job consumers are not ready"


@pytest.mark.parametrize(
    "environment",
    ("production", "staging", "preproduction"),
)
def test_deployed_ready_rejects_disabled_ocr_and_cause_capabilities(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(db_session_factory)
    settings = Settings(environment=environment)
    monkeypatch.setattr(operations_router, "get_settings", lambda: settings)

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "required AI capabilities are not ready: ocr=disabled, xingce_cause_agent=disabled"
    )


def test_ready_requires_release_for_an_approved_external_ai_scenario(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_alembic_revision(db_session_factory)
    _seed_release(db_session_factory, activated_at=datetime.now(UTC))
    _seed_knowledge_release(db_session_factory)
    settings = Settings(
        environment="production",
        ai_companion_provider_dpa_confirmed=True,
        ai_companion_provider_release_id="missing-release",
    )
    monkeypatch.setattr(operations_router, "get_settings", lambda: settings)
    monkeypatch.setattr(
        operations_router,
        "ocr_readiness",
        lambda *_args, **_kwargs: CapabilityReadiness(
            capability="ocr",
            ready=True,
            reason="ready",
            provider_release_id="approved-ocr",
        ),
    )
    monkeypatch.setattr(
        operations_router,
        "cause_agent_readiness",
        lambda *_args, **_kwargs: CapabilityReadiness(
            capability="xingce_cause_agent",
            ready=True,
            reason="ready",
            provider_release_id="approved-cause",
        ),
    )

    response = _client(db_session_factory).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "external AI processor release is not ready"


def _client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def _synthetic_old_head_compatibility(
    tmp_path: Path,
    *,
    compatible_revisions: tuple[str, ...],
) -> ArtifactDatabaseCompatibility:
    backend_root = tmp_path / "old-artifact" / "backend"
    versions = backend_root / "migrations" / "versions"
    versions.mkdir(parents=True)
    (backend_root / "alembic.ini").write_text(
        "[alembic]\nscript_location = %(here)s/migrations\n",
        encoding="utf-8",
    )
    (versions / "synthetic_old_head.py").write_text(
        "revision = 'synthetic_old_head'\n"
        "down_revision = None\n"
        "branch_labels = None\n"
        "depends_on = None\n",
        encoding="utf-8",
    )
    contract = backend_root.parent / "ops" / "release-db-compatibility.env"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        "ARTIFACT_DATABASE_COMPATIBILITY_CONTRACT="
        "artifact-db-compatibility@v1\n"
        "ARTIFACT_DATABASE_MIGRATION_HEAD=synthetic_old_head\n"
        "ARTIFACT_DATABASE_COMPATIBLE_REVISIONS="
        f"{','.join(compatible_revisions)}\n",
        encoding="utf-8",
    )
    return load_artifact_database_compatibility(
        contract,
        backend_root=backend_root,
    )


def _seed_alembic_revision(db_session_factory: sessionmaker[Session]) -> None:
    with db_session_factory.begin() as session:
        session.execute(
            text("create table if not exists alembic_version (version_num varchar(32) not null)")
        )
        session.execute(text("delete from alembic_version"))
        session.execute(
            text("insert into alembic_version (version_num) values (:revision)"),
            {"revision": get_settings().required_database_revision},
        )


def _seed_release(
    db_session_factory: sessionmaker[Session],
    *,
    activated_at: datetime,
    window_opens_at: datetime | None = None,
    window_closes_at: datetime | None = None,
) -> tuple[datetime, datetime]:
    release_id = str(uuid4())
    opens_at = window_opens_at or datetime.now(UTC) - timedelta(days=1)
    closes_at = window_closes_at or datetime.now(UTC) + timedelta(days=1)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  cast(:release_id as uuid), 'guokao', 2026,
                  'guokao-2026-rules-v1', 'guokao-2026-source-sha256-test',
                  'draft', null
                )
                """
            ),
            {
                "release_id": release_id,
            },
        )
        session.execute(
            text(
                """
                insert into product.position_release_windows (
                  window_id, release_id, window_kind, sequence_no,
                  opens_at, closes_at, official_source_url,
                  official_source_title, official_source_published_at,
                  official_source_received_at, official_source_sha256,
                  official_source_receipt
                ) values (
                  cast(:window_id as uuid), cast(:release_id as uuid),
                  'primary', 1, :opens_at, :closes_at,
                  'https://example.gov.cn/guokao/2026/announcement',
                  '2026 年度中央机关公开遴选公告',
                  :published_at, :received_at, :source_sha256,
                  'readiness-fixture-receipt'
                )
                """
            ),
            {
                "window_id": str(uuid4()),
                "release_id": release_id,
                "opens_at": opens_at,
                "closes_at": closes_at,
                "published_at": opens_at - timedelta(days=7),
                "received_at": opens_at - timedelta(days=6),
                "source_sha256": "b" * 64,
            },
        )
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'active', activated_at = :activated_at
                where release_id = cast(:release_id as uuid)
                """
            ),
            {
                "release_id": release_id,
                "activated_at": activated_at,
            },
        )
        for consumer_unit, consumer_entrypoint in sorted(
            {
                (
                    registration.consumer_unit,
                    str(registration.consumer_entrypoint),
                )
                for registration in JOB_REGISTRY
                if registration.deployment_state == "deployed"
            }
        ):
            record_heartbeat_as_principal(
                session,
                consumer_unit=consumer_unit,
                consumer_entrypoint=consumer_entrypoint,
                process_instance_id=uuid4(),
            )
    return opens_at, closes_at


def _seed_knowledge_release(
    db_session_factory: sessionmaker[Session],
    *,
    release_version: str = "knowledge-map@ready-test-v1",
    generator_version: str = "refresh_knowledge_map_nodes_v3",
    fresh_until: datetime | None = None,
    activated_at: datetime | None = None,
    near5_occurrence_count: int = 3,
    seed_vocabulary: bool = True,
    canonical_codes: tuple[str, ...] | None = None,
    missing_actionable_module: str | None = None,
) -> None:
    release_id = uuid4()
    modules = tuple(GUOKAO_XINGCE_MODULES)
    effective_codes = canonical_codes or tuple(module.code for module in modules)
    if len(effective_codes) != len(modules):
        raise ValueError("canonical_codes must provide exactly six module codes")
    actionable_modules = tuple(
        module for module in modules if module.display_name != missing_actionable_module
    )
    effective_fresh_until = fresh_until or datetime.now(UTC) + timedelta(days=30)
    generated_at = min(
        datetime.now(UTC) - timedelta(minutes=5),
        effective_fresh_until - timedelta(minutes=5),
    )
    with db_session_factory.begin() as session:
        if seed_vocabulary:
            vocabulary_row_count = session.execute(
                text(
                    """
                    select count(*)::integer
                    from product.vocab_module_alias_map
                    where version = 'vocab@v1'
                      and action = 'canonical'
                    """
                )
            ).scalar_one()
            if vocabulary_row_count == 0:
                session.execute(
                    text(
                        """
                        insert into product.vocab_module_alias_map (
                          version, raw_module_name, action, canonical_code,
                          local_question_count, basis
                        ) values (
                          'vocab@v1', :raw_module_name, 'canonical',
                          :canonical_code, 1, 'release readiness fixture'
                        )
                        """
                    ),
                    [
                        {
                            "raw_module_name": module.display_name,
                            "canonical_code": code,
                        }
                        for module, code in zip(modules, effective_codes, strict=True)
                    ],
                )
            elif vocabulary_row_count != len(modules):
                raise AssertionError("readiness fixture found incomplete vocab@v1")
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id, release_version, exam_scope, track,
                  source_data_version, vocab_version, generator_version,
                  source_fingerprint, generated_at, fresh_until, status,
                  node_count, activated_at
                ) values (
                  cast(:release_id as uuid), :release_version, 'guokao', 'xingce',
                  'question-bank@ready-test-v1', 'vocab@v1', :generator_version,
                  repeat('a', 64), :generated_at, :fresh_until, 'draft',
                  :node_count, null
                )
                """
            ),
            {
                "release_id": str(release_id),
                "release_version": release_version,
                "generator_version": generator_version,
                "node_count": len(actionable_modules),
                "generated_at": generated_at,
                "fresh_until": effective_fresh_until,
                "activated_at": datetime.now(UTC),
            },
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_nodes (
                  release_id, node_id, exam_scope, track, module_name,
                  node_type, tag_type, tag_value, display_path, aliases,
                  support_status, near5_occurrence_count,
                  stable_for_old_questions, method_card_ids, evidence, status
                ) values (
                  cast(:release_id as uuid), :node_id, 'guokao',
                  'xingce', :module_name, 'knowledge_point', 'knowledge_point',
                  :tag_value, :display_path, '[]'::jsonb,
                  'text_supported', :near5_occurrence_count, true, '[]'::jsonb,
                  '{"test":"release-readiness"}'::jsonb, 'published'
                )
                """
            ),
            [
                {
                    "release_id": str(release_id),
                    "node_id": f"ready-test-{module.code}",
                    "module_name": module.display_name,
                    "tag_value": f"{module.display_name}测试考点",
                    "display_path": (f"行测/{module.display_name}/{module.display_name}测试考点"),
                    "near5_occurrence_count": near5_occurrence_count,
                }
                for module in actionable_modules
            ],
        )
        question_ids: dict[str, str] = {}
        content_catalog = SQLQuestionContentCatalog(session)
        for module in actionable_modules:
            question_id = f"ready-{release_id}-{module.code}"
            question_ids[module.code] = question_id
            session.execute(
                text(
                    """
                    insert into product.questions (
                      question_id, domain, status, module_name, question_type,
                      stem, answer, visual_required, latest_year,
                      occurrence_count
                    ) values (
                      :question_id, 'objective', 'active', :module_name,
                      '知识点', :stem, 'B', false, 2026, 1
                    )
                    """
                ),
                {
                    "question_id": question_id,
                    "module_name": module.display_name,
                    "stem": f"{module.display_name} readiness 题",
                },
            )
            session.execute(
                text(
                    """
                    insert into product.question_options (
                      question_id, option_index, label, option_text
                    ) values (
                      :question_id, :option_index, :label, :option_text
                    )
                    """
                ),
                [
                    {
                        "question_id": question_id,
                        "option_index": option_index,
                        "label": label,
                        "option_text": f"选项 {label}",
                    }
                    for option_index, label in enumerate(("A", "B", "C", "D"))
                ],
            )
            session.execute(
                text(
                    """
                    insert into product.question_tags (
                      question_tag_id, question_id, tag_type, tag_value,
                      sort_order
                    ) values (
                      :question_tag_id, :question_id, 'knowledge_point',
                      :tag_value, 1
                    )
                    """
                ),
                {
                    "question_tag_id": f"qt-ready-{release_id}-{module.code}",
                    "question_id": question_id,
                    "tag_value": f"{module.display_name}测试考点",
                },
            )
            content_catalog.publish_current(question_id=question_id)
        effective_code_by_module = {
            module.display_name: code for module, code in zip(modules, effective_codes, strict=True)
        }
        binding_rows = [
            {
                "node_id": f"ready-test-{module.code}",
                "question_id": question_ids[module.code],
                "module_name": module.display_name,
                "module_code": effective_code_by_module[module.display_name],
            }
            for module in actionable_modules
        ]
        session.execute(
            text(
                """
                with prepared as (
                  select
                    release.release_id,
                    binding_input.node_id,
                    binding_input.module_name,
                    binding_input.module_code,
                    head.content_version_id,
                    binding_input.question_id
                  from product.knowledge_map_releases release
                  cross join jsonb_to_recordset(
                    cast(:binding_rows as jsonb)
                  ) as binding_input(
                    node_id text,
                    question_id text,
                    module_name text,
                    module_code text
                  )
                  join product.knowledge_map_nodes node
                    on node.release_id = release.release_id
                   and node.node_id = binding_input.node_id
                  join product.question_content_heads head
                    on head.question_id = binding_input.question_id
                  where release.release_id = cast(:release_id as uuid)
                )
                insert into product.knowledge_map_question_bindings (
                  release_id, node_id, question_id, content_version_id,
                  module_code, canonical_module, binding_kind,
                  source_tag_type, source_tag_value, delivery_eligible,
                  visual_required, shared_context_required, latest_year,
                  occurrence_count, latest_recent_exam_year,
                  recent_occurrence_count, latest_old_exam_year,
                  old_occurrence_count, binding_fingerprint, created_at
                )
                select
                  prepared.release_id,
                  prepared.node_id,
                  prepared.question_id,
                  prepared.content_version_id,
                  prepared.module_code,
                  prepared.module_name,
                  'direct',
                  'knowledge_point',
                  prepared.module_name || '测试考点',
                  true,
                  false,
                  false,
                  2026,
                  1,
                  2026,
                  1,
                  null,
                  0,
                  product.knowledge_map_question_binding_row_fingerprint(
                    row(
                      prepared.release_id,
                      prepared.node_id,
                      prepared.question_id,
                      prepared.content_version_id,
                      prepared.module_code,
                      prepared.module_name,
                      'direct',
                      'knowledge_point',
                      prepared.module_name || '测试考点',
                      true,
                      false,
                      false,
                      2026,
                      1,
                      2026,
                      1,
                      null,
                      0,
                      repeat('0', 64),
                      transaction_timestamp()
                    )::product.knowledge_map_question_bindings
                  ),
                  transaction_timestamp()
                from prepared
                """
            ),
            {
                "release_id": str(release_id),
                "binding_rows": json.dumps(binding_rows),
            },
        )
        session.execute(
            text(
                """
                with manifest as (
                  select *
                  from product.knowledge_map_question_binding_manifest(
                    cast(:release_id as uuid)
                  )
                )
                update product.knowledge_map_releases release
                set
                  question_binding_count = manifest.binding_count,
                  question_binding_fingerprint =
                    manifest.binding_fingerprint
                from manifest
                where release.release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": str(release_id)},
        )
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'active', activated_at = :activated_at
                where release_id = cast(:release_id as uuid)
                  and status = 'draft'
                """
            ),
            {
                "release_id": str(release_id),
                "activated_at": activated_at or datetime.now(UTC),
            },
        )
