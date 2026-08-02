from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domains.ai.capability_readiness import (
    cause_agent_readiness,
    ocr_readiness,
)
from app.domains.ai.external_processing import (
    ExternalProcessingAuthorizationError,
    ExternalProcessingRepository,
    endpoint_fingerprint,
)
from app.domains.coach_notifications.template import (
    CoachTemplateConfigurationError,
    CoachTemplateRelease,
)
from app.db.session import get_db_session
from app.domains.qualification.repository import QualificationRepository
from app.domains.operations.support import (
    SupportAuthenticationError,
    SupportConfigurationError,
    SupportJwtVerifier,
    SupportOrderNotFound,
    SupportOrderView,
    SupportPrincipal,
    read_order_for_support,
)
from app.platform.jobs import (
    JOB_CONSUMER_HEARTBEAT_TTL,
    JOB_LEASE_CONTRACT_VERSION,
    JOB_REGISTRY,
    JobRegistration,
    job_registration_enabled,
)
from app.platform.job_consumer_principals import (
    JOB_CONSUMER_PRINCIPALS,
)


router = APIRouter(tags=["operations"])
_LOCAL_READINESS_ENVIRONMENTS = frozenset({"local", "development", "test", "local_capacity"})


@lru_cache(maxsize=1)
def _support_verifier() -> SupportJwtVerifier:
    return SupportJwtVerifier(get_settings())


def _support_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> SupportPrincipal:
    if authorization is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        return _support_verifier().verify(authorization)
    except SupportConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="support identity provider is not configured",
        ) from exc
    except SupportAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="support identity is not authorized",
        ) from exc


@router.get("/ready")
def ready(session: Annotated[Session, Depends(get_db_session)]) -> dict[str, str]:
    settings = get_settings()
    runtime_environment = settings.environment.strip().casefold()
    session.execute(text("select 1")).scalar_one()
    migrations = tuple(
        str(value)
        for value in session.execute(
            text("select version_num from alembic_version order by version_num")
        ).scalars()
    )
    migration = migrations[0] if len(migrations) == 1 else None
    required_tables_ready = session.execute(
        text(
            "select to_regclass('commerce.wechat_orders') is not null "
            "and to_regclass('privacy.data_export_requests') is not null "
            "and to_regclass('product.position_rule_releases') is not null "
            "and to_regclass('product.position_release_windows') is not null "
            "and to_regclass('product.position_import_batches') is not null "
            "and to_regclass('product.position_snapshots') is not null "
            "and to_regclass('product.position_score_snapshot_batches') is not null "
            "and to_regclass('product.position_score_snapshots') is not null "
            "and to_regclass("
            "'platform.background_job_consumer_heartbeats'"
            ") is not null "
            "and to_regclass("
            "'platform.background_job_consumer_registry'"
            ") is not null "
            "and to_regclass("
            "'platform.background_job_type_registry'"
            ") is not null "
            "and to_regclass("
            "'platform.background_job_consumer_principal_registry'"
            ") is not null "
            "and to_regprocedure("
            "'platform.pulse_background_job_consumer_heartbeat(text,text,uuid)'"
            ") is not null "
            "and to_regclass("
            "'platform.commercial_data_publication_state'"
            ") is not null "
            "and to_regclass("
            "'platform.commercial_data_publication_transitions'"
            ") is not null "
            "and to_regclass('product.knowledge_map_releases') is not null "
            "and to_regclass('product.knowledge_map_nodes') is not null "
            "and to_regclass('messaging.coach_notification_deliveries') is not null "
            "and to_regclass('messaging.daily_opening_impressions') is not null "
            "and to_regclass('messaging.daily_opening_preference_versions') is not null"
        )
    ).scalar_one()
    if migration not in settings.compatible_database_revisions or not required_tables_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database schema is not at the required release",
        )
    if not _background_job_registry_exact(session, registrations=JOB_REGISTRY):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="background job registry does not match the application release",
        )

    checked_at = datetime.now(UTC)
    position_lifecycle = QualificationRepository().release_lifecycle(
        session,
        exam_code="guokao",
        now=checked_at,
    )
    if (
        position_lifecycle.phase not in {"open", "upcoming", "gap", "closed"}
        or position_lifecycle.release_id is None
        or position_lifecycle.rule_set_version is None
        or position_lifecycle.position_data_version is None
        or position_lifecycle.content_digest is None
        or position_lifecycle.window is None
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"position rule release authority is not ready (phase={position_lifecycle.phase})"
            ),
        )
    position_content_ready = (
        session.execute(
            text(
                """
            select
              release.content_digest
                = product.compute_position_release_content_digest(
                    release.release_id
                  ) as digest_matches,
              score_batch.score_evidence_count = (
                select count(*)::integer
                from product.position_score_snapshots score
                where score.position_data_version
                      = release.position_data_version
              ) as score_count_matches
            from product.position_rule_releases release
            join product.position_score_snapshot_batches score_batch
              on score_batch.position_data_version
                   = release.position_data_version
            where release.release_id = cast(:release_id as uuid)
            """
            ),
            {"release_id": position_lifecycle.release_id},
        )
        .mappings()
        .one_or_none()
    )
    if (
        position_content_ready is None
        or position_content_ready["digest_matches"] is not True
        or position_content_ready["score_count_matches"] is not True
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="position release content snapshot is not ready",
        )
    knowledge_release = (
        session.execute(
            text(
                """
            with active_releases as (
              select
                release.*,
                count(*) over () as active_release_count
              from product.knowledge_map_releases release
              where release.exam_scope = 'guokao'
                and release.track = 'xingce'
                and release.status = 'active'
            ), current_release as (
              select release.*
              from active_releases release
              where release.active_release_count = 1
                and release.activated_at is not null
                and release.retired_at is null
                and release.fresh_until > statement_timestamp()
                and release.node_count > 0
                and release.question_binding_count > 0
                and release.question_binding_fingerprint ~ '^[0-9a-f]{64}$'
                and release.release_version not like 'knowledge-map@legacy-pre-%'
                and release.source_data_version not like 'legacy:%'
                and release.vocab_version not like 'legacy:%'
                and release.generator_version not like 'legacy:%'
                and release.node_count = (
                  select count(*)::integer
                  from product.knowledge_map_nodes node
                  where node.release_id = release.release_id
                )
            ), binding_readiness as (
              select
                release.release_id,
                manifest.binding_count,
                manifest.binding_fingerprint
              from current_release release
              cross join lateral
                product.knowledge_map_question_binding_manifest(
                  release.release_id
                ) manifest
            ), expected_canonical_vocabulary(
              canonical_code,
              raw_module_name
            ) as (
              values
                ('political_theory', '政治理论'),
                ('general_knowledge', '常识判断'),
                ('verbal_comprehension', '言语理解与表达'),
                ('quantitative_relations', '数量关系'),
                ('judgment_reasoning', '判断推理'),
                ('data_analysis', '资料分析')
            ), canonical_vocabulary as (
              select
                release.release_id,
                alias.canonical_code,
                alias.raw_module_name
              from current_release release
              join product.vocab_module_alias_map alias
                on alias.version = release.vocab_version
               and alias.action = 'canonical'
              join expected_canonical_vocabulary expected
                on expected.canonical_code = alias.canonical_code
               and expected.raw_module_name = alias.raw_module_name
            ), vocabulary_readiness as (
              select
                release_id,
                count(*) as canonical_row_count,
                count(distinct canonical_code) as canonical_code_count
              from canonical_vocabulary
              group by release_id
            ), actionable_modules as (
              select distinct
                vocabulary.release_id,
                vocabulary.canonical_code
              from canonical_vocabulary vocabulary
              join product.knowledge_map_nodes node
                on node.release_id = vocabulary.release_id
               and node.module_name = vocabulary.raw_module_name
               and node.status = 'published'
               and node.support_status = 'text_supported'
               and node.node_type in ('micro_skill', 'knowledge_point')
               and node.tag_type is not null
               and coalesce(node.near5_occurrence_count, 0) > 0
              join product.knowledge_map_question_bindings binding
                on binding.release_id = node.release_id
               and binding.node_id = node.node_id
               and binding.module_code = vocabulary.canonical_code
               and binding.canonical_module = vocabulary.raw_module_name
               and binding.delivery_eligible
            )
            select release.release_version, release.source_data_version
            from current_release release
            join binding_readiness binding
              on binding.release_id = release.release_id
             and binding.binding_count = release.question_binding_count
             and binding.binding_fingerprint =
                 release.question_binding_fingerprint
            join vocabulary_readiness vocabulary
              on vocabulary.release_id = release.release_id
             and vocabulary.canonical_row_count = 6
             and vocabulary.canonical_code_count = 6
            where (
              select count(*)
              from actionable_modules module
              where module.release_id = release.release_id
            ) = 6
            """
            )
        )
        .mappings()
        .one_or_none()
    )
    if knowledge_release is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="knowledge map release is not ready",
        )
    job_health = _job_queue_health(
        session,
        now=checked_at,
        registrations=JOB_REGISTRY,
    )
    unready_job_types = _unready_job_types(
        registrations=JOB_REGISTRY,
        health=job_health,
    )
    if runtime_environment in _LOCAL_READINESS_ENVIRONMENTS:
        unready_job_types = tuple(
            job_type
            for job_type in unready_job_types
            if job_health[job_type]["queued_count"] > 0
            or job_health[job_type]["running_count"] > 0
        )
    if _active_unregistered_job_types(session) or unready_job_types:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="background job consumers are not ready",
        )
    if runtime_environment == "production" and settings.coach_notifications_enabled:
        try:
            coach_template = CoachTemplateRelease.from_json(
                settings.wechat_coach_template_release_json
            )
        except CoachTemplateConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="coach notification template release is not ready",
            ) from exc
        if (
            coach_template is None
            or not settings.wechat_mini_app_secret.strip()
            or settings.wechat_coach_miniprogram_state != "formal"
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="coach notification template release is not ready",
            )
    if runtime_environment not in _LOCAL_READINESS_ENVIRONMENTS:
        required_capabilities = (
            ocr_readiness(session, settings=settings, at=checked_at),
            cause_agent_readiness(session, settings=settings, at=checked_at),
        )
        unavailable_capabilities = tuple(
            capability for capability in required_capabilities if not capability.ready
        )
        if unavailable_capabilities:
            reasons = ", ".join(
                f"{capability.capability}={capability.reason}"
                for capability in unavailable_capabilities
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"required AI capabilities are not ready: {reasons}",
            )
        endpoint = endpoint_fingerprint(settings.ai_provider_base_url)
        requirements = (
            (
                settings.ai_companion_provider_dpa_confirmed,
                settings.ai_companion_provider_release_id,
                "companion_answer",
                ("learner_question", "learning_evidence"),
            ),
            (
                settings.ai_companion_provider_dpa_confirmed,
                settings.ai_companion_provider_release_id,
                "external_question_answer_generate",
                ("recognized_question",),
            ),
            (
                settings.ai_xingce_cause_provider_dpa_confirmed,
                settings.ai_xingce_cause_provider_release_id,
                "cause_candidate_generate",
                ("learning_evidence", "learner_answer"),
            ),
            (
                settings.ai_community_candidate_provider_dpa_confirmed,
                settings.ai_community_candidate_provider_release_id,
                "community_candidate_generate",
                ("community_content",),
            ),
            (
                settings.ai_essay_review_provider_dpa_confirmed,
                settings.ai_essay_rubric_provider_release_id,
                "rubric_generator",
                ("public_exam_material",),
            ),
            (
                settings.ai_essay_review_provider_dpa_confirmed,
                settings.ai_essay_rubric_provider_release_id,
                "rubric_critic",
                ("public_exam_material",),
            ),
            (
                settings.ai_essay_review_provider_dpa_confirmed,
                settings.ai_essay_review_provider_release_id,
                "grounded_review_generator",
                ("essay_answer", "public_exam_material", "grading_rubric"),
            ),
            (
                settings.ai_essay_review_provider_dpa_confirmed,
                settings.ai_essay_review_provider_release_id,
                "grounded_review_repair",
                ("essay_answer", "public_exam_material", "grading_rubric"),
            ),
        )
        try:
            processor_releases = ExternalProcessingRepository()
            for approved, provider_release, purpose, data_categories in requirements:
                if not approved:
                    continue
                processor_releases.require_active_release(
                    session,
                    declared_provider_release=provider_release,
                    provider_endpoint_fingerprint=endpoint,
                    purpose=purpose,
                    data_categories=data_categories,
                    at=checked_at,
                )
        except (ExternalProcessingAuthorizationError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="external AI processor release is not ready",
            ) from exc
    return {
        "status": "ready",
        "migration": str(migration),
        "positionRuleRelease": position_lifecycle.rule_set_version,
        "positionDataVersion": position_lifecycle.position_data_version,
        "positionReleaseContentDigest": position_lifecycle.content_digest,
        "positionReleasePhase": position_lifecycle.phase,
        "positionWindowKind": position_lifecycle.window.kind,
        "positionWindowOpensAt": position_lifecycle.window.opens_at.isoformat(),
        "positionWindowClosesAt": position_lifecycle.window.closes_at.isoformat(),
        "positionWindowSourceSha256": (position_lifecycle.window.official_source_sha256),
        "knowledgeMapRelease": str(knowledge_release["release_version"]),
        "knowledgeMapSourceDataVersion": str(knowledge_release["source_data_version"]),
        "jobLeaseContract": JOB_LEASE_CONTRACT_VERSION,
        "time": checked_at.isoformat(),
    }


@router.get("/internal/metrics", response_class=PlainTextResponse)
def metrics(
    session: Annotated[Session, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    settings = get_settings()
    expected = f"Bearer {settings.operations_metrics_token}"
    if not settings.operations_metrics_token or authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    row = (
        session.execute(
            text(
                """
            select
              (select count(*) from commerce.wechat_orders where state = 'payment_uncertain') as uncertain_orders,
              (select count(*) from commerce.wechat_orders where state = 'refund_processing' and updated_at < now() - interval '15 minutes') as stale_refunds,
              (select count(*) from platform.background_jobs where status = 'queued') as queued_jobs,
              (select count(*) from platform.background_jobs
                where status in ('queued', 'running')
                  and not exists (
                    select 1
                    from platform.background_job_type_registry registry
                    where registry.job_type =
                          platform.background_jobs.job_type
                  )) as unregistered_active_jobs,
              (select count(*) from platform.background_jobs
                where status = 'failed'
                  and completed_at >= now() - interval '15 minutes'
                  and coalesce(result ->> 'businessOutcome', '') <> 'terminated'
              ) as failed_jobs_recent,
              (select count(*) from platform.background_jobs
                where status = 'failed'
                  and completed_at >= now() - interval '15 minutes'
                  and result ->> 'businessOutcome' = 'terminated'
              ) as business_terminated_recent,
              (select count(*) from ai_audit.model_call_ledger
                where status = 'running'
                  and started_at < now() - interval '15 minutes') as stale_model_calls,
              (select count(*) from commerce.feature_usage_reservations
                where status = 'reserved'
                  and updated_at < now() - interval '15 minutes') as stale_usage_reservations,
              (select count(*) from privacy.account_deletion_requests where state = 'failed') as failed_deletions,
              (select count(*) from privacy.data_export_requests where state = 'failed') as failed_exports,
              (select count(*) from messaging.coach_notification_deliveries
                where state = 'outcome_unknown') as coach_unknown,
              (select count(*) from messaging.coach_notification_deliveries
                where state = 'platform_rejected'
                  and terminal_at >= now() - interval '1 hour') as coach_rejected_hour,
              (select count(*) from messaging.wechat_subscription_grants
                where state = 'available'
                  and template_id = :coach_template_id) as coach_available_grants,
              (select count(*) from messaging.coach_evaluation_cursors cursor
                join learner.users learner on learner.user_id = cursor.user_id
                join lateral (
                  select enabled from messaging.coach_preference_versions preference
                  where preference.user_id = cursor.user_id
                  order by version_no desc limit 1
                ) current on true
                where learner.is_active = true
                  and learner.account_status = 'active'
                  and current.enabled = true) as coach_enabled_users,
              (select count(*) from messaging.coach_evaluation_cursors cursor
                join learner.users learner on learner.user_id = cursor.user_id
                join lateral (
                  select enabled from messaging.coach_preference_versions preference
                  where preference.user_id = cursor.user_id
                  order by version_no desc limit 1
                ) current on true
                where learner.is_active = true
                  and learner.account_status = 'active'
                  and current.enabled = true
                  and not exists (
                    select 1 from messaging.wechat_subscription_grants grant_row
                    where grant_row.user_id = cursor.user_id
                      and grant_row.state = 'available'
                      and grant_row.template_id = :coach_template_id
                  )) as coach_enabled_without_grant,
              (select count(*) from platform.background_jobs
                where job_type = 'coach_notification_send_v1'
                  and status = 'running'
                  and claimed_at < now() - make_interval(
                    secs => :coach_stale_claim_seconds
                  )) as coach_stale_claims,
              (select count(*)
                 from platform.commercial_data_publication_state
              ) as data_publication_rows,
              (select count(*)
                 from platform.commercial_data_publication_state publication
                where publication.state = 'active'
              ) as data_publication_active_rows,
              (select count(*)
                 from platform.commercial_data_publication_state publication
                where publication.state = 'active'
                  and publication.publication_digest = encode(
                    public.digest(
                      convert_to(
                        '{"activeReceiptSha256":"'
                        || publication.active_receipt_sha256
                        || '","requestDigest":"'
                        || publication.request_digest
                        || '","schemaVersion":'
                        || '"commercial-data-publication@v1"}',
                        'UTF8'
                      ),
                      'sha256'
                    ),
                    'hex'
                  )
                  and publication.expected_database_revision = (
                    select version_num
                    from public.alembic_version
                  )
              ) as data_publication_exact_rows,
              (select coalesce(
                 greatest(
                   extract(epoch from (
                     statement_timestamp() - state_changed_at
                   )),
                   0
                 ),
                 0
               )
                 from platform.commercial_data_publication_state
              ) as data_publication_state_age_seconds,
              (select count(*)
                 from platform.commercial_data_publication_transitions
              ) as data_publication_transitions
            """
            ),
            {
                "coach_stale_claim_seconds": settings.coach_notification_stale_claim_seconds,
                "coach_template_id": _coach_template_id_for_metrics(settings),
            },
        )
        .mappings()
        .one()
    )
    values = {
        "anan_database_ready": 1,
        "anan_wechat_payment_uncertain_orders": int(row["uncertain_orders"]),
        "anan_wechat_stale_refunds": int(row["stale_refunds"]),
        "anan_background_jobs_queued": int(row["queued_jobs"]),
        "anan_background_jobs_unregistered_active": int(row["unregistered_active_jobs"]),
        "anan_background_jobs_failed_recent": int(row["failed_jobs_recent"]),
        "anan_background_jobs_business_terminated_recent": int(row["business_terminated_recent"]),
        "anan_background_job_registry_exact": int(
            _background_job_registry_exact(session, registrations=JOB_REGISTRY)
        ),
        "anan_ai_model_calls_running_stale": int(row["stale_model_calls"]),
        "anan_ai_usage_reservations_open_stale": int(row["stale_usage_reservations"]),
        "anan_privacy_deletions_failed": int(row["failed_deletions"]),
        "anan_privacy_exports_failed": int(row["failed_exports"]),
        "anan_coach_notification_outcome_unknown": int(row["coach_unknown"]),
        "anan_coach_notification_rejected_last_hour": int(row["coach_rejected_hour"]),
        "anan_coach_notification_available_grants": int(row["coach_available_grants"]),
        "anan_coach_notification_enabled_users": int(row["coach_enabled_users"]),
        "anan_coach_notification_enabled_without_grant": int(row["coach_enabled_without_grant"]),
        "anan_coach_notification_stale_claims": int(row["coach_stale_claims"]),
        "anan_commercial_data_publication_active": int(
            row["data_publication_rows"] == 1 and row["data_publication_active_rows"] == 1
        ),
        "anan_commercial_data_publication_exact": int(
            row["data_publication_rows"] == 1 and row["data_publication_exact_rows"] == 1
        ),
        "anan_commercial_data_publication_state_age_seconds": int(
            row["data_publication_state_age_seconds"] or 0
        ),
        "anan_commercial_data_publication_transitions_total": int(
            row["data_publication_transitions"]
        ),
    }
    ocr_capability = ocr_readiness(session, settings=settings)
    cause_capability = cause_agent_readiness(session, settings=settings)
    values["anan_recognition_capability_ready"] = int(ocr_capability.ready)
    values["anan_xingce_cause_capability_ready"] = int(cause_capability.ready)
    health = _job_queue_health(
        session,
        now=datetime.now(UTC),
        registrations=JOB_REGISTRY,
    )
    job_values = (
        tuple(
            (
                f'anan_job_queue_oldest_age_seconds{{job_type="{registration.job_type}"}}',
                int(health[registration.job_type]["queue_age_seconds"]),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_running_oldest_age_seconds{{job_type="{registration.job_type}"}}',
                int(health[registration.job_type]["running_age_seconds"]),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_queue_readiness_age_seconds{{job_type="{registration.job_type}"}}',
                int(registration.queue_readiness_age.total_seconds()),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_running_readiness_age_seconds{{job_type="{registration.job_type}"}}',
                int(registration.running_readiness_age.total_seconds()),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_queued{{job_type="{registration.job_type}"}}',
                int(health[registration.job_type]["queued_count"]),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_running{{job_type="{registration.job_type}"}}',
                int(health[registration.job_type]["running_count"]),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_consumer_enabled{{job_type="{registration.job_type}"}}',
                int(job_registration_enabled(registration)),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_consumer_deployed{{job_type="{registration.job_type}"}}',
                int(registration.deployment_state == "deployed"),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_consumer_heartbeat_age_seconds{{job_type="{registration.job_type}"}}',
                int(health[registration.job_type]["heartbeat_age_seconds"]),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_consumer_heartbeat_ttl_seconds{{job_type="{registration.job_type}"}}',
                int(JOB_CONSUMER_HEARTBEAT_TTL.total_seconds()),
            )
            for registration in JOB_REGISTRY
        )
        + tuple(
            (
                f'anan_job_consumer_heartbeat_healthy{{job_type="{registration.job_type}"}}',
                int(health[registration.job_type]["heartbeat_healthy"]),
            )
            for registration in JOB_REGISTRY
        )
    )
    return (
        "\n".join(
            (
                *(f"{key} {value}" for key, value in values.items()),
                *(f"{key} {value}" for key, value in job_values),
            )
        )
        + "\n"
    )


def _job_queue_health(
    session: Session,
    *,
    now: datetime,
    registrations: tuple[JobRegistration, ...],
) -> dict[str, dict[str, float]]:
    health = {
        registration.job_type: {
            "queue_age_seconds": 0.0,
            "running_age_seconds": 0.0,
            "queued_count": 0.0,
            "running_count": 0.0,
            "heartbeat_age_seconds": -1.0,
            "heartbeat_healthy": 0.0,
        }
        for registration in registrations
    }
    background_types = tuple(
        registration.job_type
        for registration in registrations
        if registration.queue_name == "platform.background_jobs"
    )
    if background_types:
        rows = (
            session.execute(
                text(
                    """
                    select
                      job_type,
                      coalesce(greatest(extract(epoch from (
                        :now - min(run_after) filter (
                          where status = 'queued' and run_after <= :now
                        )
                      )), 0), 0) as queue_age_seconds,
                      coalesce(greatest(extract(epoch from (
                        :now - min(claimed_at) filter (
                          where status = 'running'
                        )
                      )), 0), 0) as running_age_seconds,
                      count(*) filter (
                        where status = 'queued'
                      ) as queued_count,
                      count(*) filter (
                        where status = 'running'
                      ) as running_count
                    from platform.background_jobs
                    where job_type = any(cast(:job_types as text[]))
                      and status in ('queued', 'running')
                    group by job_type
                    """
                ),
                {"now": now, "job_types": list(background_types)},
            )
            .mappings()
            .all()
        )
        for row in rows:
            health[str(row["job_type"])] = {
                "queue_age_seconds": float(row["queue_age_seconds"]),
                "running_age_seconds": float(row["running_age_seconds"]),
                "queued_count": float(row["queued_count"]),
                "running_count": float(row["running_count"]),
                "heartbeat_age_seconds": health[str(row["job_type"])]["heartbeat_age_seconds"],
                "heartbeat_healthy": health[str(row["job_type"])]["heartbeat_healthy"],
            }
    if any(
        registration.queue_name == "messaging.ilink_object_deletion_queue"
        for registration in registrations
    ):
        row = (
            session.execute(
                text(
                    """
                    select
                      coalesce(greatest(extract(epoch from (
                        :now - min(updated_at) filter (
                          where state in ('pending', 'failed')
                        )
                      )), 0), 0) as queue_age_seconds,
                      coalesce(greatest(extract(epoch from (
                        :now - min(claimed_at) filter (
                          where state = 'deleting'
                        )
                      )), 0), 0) as running_age_seconds,
                      count(*) filter (
                        where state in ('pending', 'failed')
                      ) as queued_count,
                      count(*) filter (
                        where state = 'deleting'
                      ) as running_count
                    from messaging.ilink_object_deletion_queue
                    """
                ),
                {"now": now},
            )
            .mappings()
            .one()
        )
        health["ilink_object_deletion_v1"] = {
            "queue_age_seconds": float(row["queue_age_seconds"]),
            "running_age_seconds": float(row["running_age_seconds"]),
            "queued_count": float(row["queued_count"]),
            "running_count": float(row["running_count"]),
            "heartbeat_age_seconds": health["ilink_object_deletion_v1"]["heartbeat_age_seconds"],
            "heartbeat_healthy": health["ilink_object_deletion_v1"]["heartbeat_healthy"],
        }
    consumer_identities = {
        (registration.consumer_unit, registration.consumer_entrypoint)
        for registration in registrations
        if registration.deployment_state == "deployed"
    }
    if consumer_identities:
        heartbeat_rows = (
            session.execute(
                text(
                    """
                    select
                      consumer_unit,
                      consumer_entrypoint,
                      extract(epoch from (
                        statement_timestamp() - last_heartbeat_at
                      )) as heartbeat_age_seconds
                    from platform.background_job_consumer_heartbeats
                    where consumer_unit = any(cast(:consumer_units as text[]))
                    """
                ),
                {
                    "consumer_units": sorted({identity[0] for identity in consumer_identities}),
                },
            )
            .mappings()
            .all()
        )
        heartbeat_ages = {
            (str(row["consumer_unit"]), str(row["consumer_entrypoint"])): float(
                row["heartbeat_age_seconds"]
            )
            for row in heartbeat_rows
        }
        ttl_seconds = JOB_CONSUMER_HEARTBEAT_TTL.total_seconds()
        for registration in registrations:
            if registration.deployment_state != "deployed":
                continue
            identity = (
                registration.consumer_unit,
                registration.consumer_entrypoint,
            )
            heartbeat_age = heartbeat_ages.get(identity)
            if heartbeat_age is None:
                continue
            health[registration.job_type]["heartbeat_age_seconds"] = heartbeat_age
            health[registration.job_type]["heartbeat_healthy"] = float(
                0 <= heartbeat_age <= ttl_seconds
            )
    return health


def _unready_job_types(
    *,
    registrations: tuple[JobRegistration, ...],
    health: dict[str, dict[str, float]],
) -> tuple[str, ...]:
    unready: list[str] = []
    for registration in registrations:
        state = health[registration.job_type]
        if job_registration_enabled(registration):
            if (
                state["heartbeat_healthy"] != 1
                or state["queue_age_seconds"] > registration.queue_readiness_age.total_seconds()
                or state["running_age_seconds"] > registration.running_readiness_age.total_seconds()
            ):
                unready.append(registration.job_type)
        elif state["queued_count"] > 0 or state["running_count"] > 0:
            unready.append(registration.job_type)
    return tuple(unready)


def _active_unregistered_job_types(session: Session) -> tuple[str, ...]:
    return tuple(
        str(job_type)
        for job_type in session.execute(
            text(
                """
                select distinct job.job_type
                from platform.background_jobs job
                where job.status in ('queued', 'running')
                  and not exists (
                    select 1
                    from platform.background_job_type_registry registry
                    where registry.job_type = job.job_type
                  )
                order by job.job_type
                """
            )
        ).scalars()
    )


def _background_job_registry_exact(
    session: Session,
    *,
    registrations: tuple[JobRegistration, ...],
) -> bool:
    expected_consumers = {
        (
            registration.consumer_unit,
            registration.consumer_entrypoint,
            registration.queue_name,
        )
        for registration in registrations
        if registration.deployment_state == "deployed"
    }
    actual_consumers = {
        (
            str(row["consumer_unit"]),
            str(row["consumer_entrypoint"]),
            str(row["queue_name"]),
        )
        for row in session.execute(
            text(
                """
                select consumer_unit, consumer_entrypoint, queue_name
                from platform.background_job_consumer_registry
                """
            )
        ).mappings()
    }
    expected = {
        (
            registration.job_type,
            registration.queue_name,
            registration.producer,
            (registration.consumer_unit if registration.deployment_state == "deployed" else None),
            registration.consumer_entrypoint,
            registration.recovery_owner,
            registration.deployment_state,
            registration.enabled_setting,
            registration.disabled_reason,
        )
        for registration in registrations
        if registration.queue_name == "platform.background_jobs"
    }
    actual = {
        (
            str(row["job_type"]),
            str(row["queue_name"]),
            str(row["producer"]),
            str(row["consumer_unit"]) if row["consumer_unit"] is not None else None,
            (str(row["consumer_entrypoint"]) if row["consumer_entrypoint"] is not None else None),
            str(row["recovery_owner"]),
            str(row["deployment_state"]),
            str(row["enabled_setting"]) if row["enabled_setting"] is not None else None,
            str(row["disabled_reason"]) if row["disabled_reason"] is not None else None,
        )
        for row in session.execute(
            text(
                """
                select job_type, queue_name, producer, consumer_unit,
                       consumer_entrypoint, recovery_owner,
                       deployment_state, enabled_setting, disabled_reason
                from platform.background_job_type_registry
                """
            )
        ).mappings()
    }
    return (
        actual_consumers == expected_consumers
        and actual == expected
        and _heartbeat_principal_registry_exact(session)
    )


def _heartbeat_principal_registry_exact(session: Session) -> bool:
    expected = {
        (
            binding.database_principal,
            binding.group_role,
            binding.consumer_unit,
            binding.consumer_entrypoint,
        )
        for binding in JOB_CONSUMER_PRINCIPALS
    }
    role_rows = (
        session.execute(
            text(
                """
                select
                  registry.database_principal,
                  registry.group_role,
                  registry.consumer_unit,
                  registry.consumer_entrypoint,
                  login.rolcanlogin as login_can_login,
                  login.rolinherit as login_inherit,
                  login.rolsuper as login_super,
                  login.rolcreatedb as login_createdb,
                  login.rolcreaterole as login_create_role,
                  login.rolreplication as login_replication,
                  login.rolbypassrls as login_bypass_rls,
                  heartbeat_group.rolcanlogin as group_can_login,
                  heartbeat_group.rolsuper as group_super,
                  heartbeat_group.rolcreatedb as group_createdb,
                  heartbeat_group.rolcreaterole as group_create_role,
                  heartbeat_group.rolreplication as group_replication,
                  heartbeat_group.rolbypassrls as group_bypass_rls,
                  coalesce(
                    (
                      select array_agg(granted.rolname::text order by
                        granted.rolname)
                      from pg_auth_members membership
                      join pg_roles granted
                        on granted.oid = membership.roleid
                      where membership.member = login.oid
                    ),
                    array[]::text[]
                  ) as login_memberships,
                  coalesce(
                    (
                      select array_agg(granted.rolname::text order by
                        granted.rolname)
                      from pg_auth_members membership
                      join pg_roles granted
                        on granted.oid = membership.roleid
                      where membership.member = heartbeat_group.oid
                    ),
                    array[]::text[]
                  ) as group_memberships,
                  has_function_privilege(
                    login.rolname,
                    'platform.pulse_background_job_consumer_heartbeat'
                    '(text,text,uuid)',
                    'EXECUTE'
                  ) as can_execute,
                  exists (
                    select 1
                    from pg_class relation
                    join pg_namespace namespace
                      on namespace.oid = relation.relnamespace
                    cross join unnest(array[
                      'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                      'REFERENCES', 'TRIGGER'
                    ]::text[]) protected_privilege(privilege_name)
                    where relation.relkind in ('r', 'p', 'v', 'm', 'f')
                      and namespace.nspname <> 'information_schema'
                      and namespace.nspname !~ '^pg_'
                      and has_table_privilege(
                        login.rolname,
                        relation.oid,
                        protected_privilege.privilege_name
                      )
                  ) as has_any_table_privilege,
                  exists (
                    select 1
                    from pg_class sequence
                    join pg_namespace namespace
                      on namespace.oid = sequence.relnamespace
                    cross join unnest(array[
                      'USAGE', 'SELECT', 'UPDATE'
                    ]::text[]) sequence_privilege(privilege_name)
                    where sequence.relkind = 'S'
                      and namespace.nspname <> 'information_schema'
                      and namespace.nspname !~ '^pg_'
                      and has_sequence_privilege(
                        login.rolname,
                        sequence.oid,
                        sequence_privilege.privilege_name
                      )
                  ) as has_any_sequence_privilege,
                  exists (
                    select 1
                    from pg_namespace namespace
                    where namespace.nspname <> 'information_schema'
                      and namespace.nspname !~ '^pg_'
                      and has_schema_privilege(
                        login.rolname,
                        namespace.oid,
                        'CREATE'
                      )
                  ) as has_schema_create,
                  exists (
                    select 1
                    from pg_proc candidate
                    cross join lateral aclexplode(
                      coalesce(
                        candidate.proacl,
                        acldefault('f', candidate.proowner)
                      )
                    ) function_acl
                    where function_acl.grantee in (
                      login.oid,
                      heartbeat_group.oid
                    )
                      and function_acl.privilege_type = 'EXECUTE'
                      and candidate.oid <>
                        'platform.pulse_background_job_consumer_heartbeat'
                        '(text,text,uuid)'::regprocedure
                  ) as has_other_direct_function_execute
                from
                  platform.background_job_consumer_principal_registry
                  registry
                left join pg_roles login
                  on login.rolname = registry.database_principal
                left join pg_roles heartbeat_group
                  on heartbeat_group.rolname = registry.group_role
                """
            )
        )
        .mappings()
        .all()
    )
    actual = {
        (
            str(row["database_principal"]),
            str(row["group_role"]),
            str(row["consumer_unit"]),
            str(row["consumer_entrypoint"]),
        )
        for row in role_rows
    }
    if actual != expected or len(role_rows) != len(expected):
        return False
    for row in role_rows:
        if (
            row["login_can_login"] is not True
            or row["login_inherit"] is not True
            or row["login_super"] is not False
            or row["login_createdb"] is not False
            or row["login_create_role"] is not False
            or row["login_replication"] is not False
            or row["login_bypass_rls"] is not False
            or row["group_can_login"] is not False
            or row["group_super"] is not False
            or row["group_createdb"] is not False
            or row["group_create_role"] is not False
            or row["group_replication"] is not False
            or row["group_bypass_rls"] is not False
            or tuple(row["login_memberships"]) != (row["group_role"],)
            or tuple(row["group_memberships"])
            or row["can_execute"] is not True
            or row["has_any_table_privilege"] is not False
            or row["has_any_sequence_privilege"] is not False
            or row["has_schema_create"] is not False
            or row["has_other_direct_function_execute"] is not False
        ):
            return False

    untrusted = (
        session.execute(
            text(
                """
            with untrusted(role_name) as (
              select unnest(cast(:untrusted_roles as text[]))
            ),
            heartbeat_groups(role_name) as (
              select unnest(cast(:heartbeat_groups as text[]))
            )
            select
              exists (
                select 1
                from untrusted
                join pg_roles role
                  on role.rolname = untrusted.role_name
                cross join heartbeat_groups
                join pg_roles heartbeat_group
                  on heartbeat_group.rolname =
                     heartbeat_groups.role_name
                where untrusted.role_name <>
                      heartbeat_groups.role_name
                  and not (
                    untrusted.role_name =
                      'gongkao_worker_heartbeat_login'
                    and heartbeat_groups.role_name =
                      'gongkao_worker_heartbeat'
                  )
                  and pg_has_role(
                  role.oid,
                  heartbeat_group.oid,
                  'member'
                )
              ) as has_forbidden_membership,
              exists (
                select 1
                from untrusted
                join pg_roles role
                  on role.rolname = untrusted.role_name
                where has_function_privilege(
                  role.rolname,
                  'platform.pulse_background_job_consumer_heartbeat'
                  '(text,text,uuid)',
                  'EXECUTE'
                )
              ) as has_forbidden_execute
            """
            ),
            {
                "untrusted_roles": [
                    "gongkao_api",
                    "gongkao_worker_heartbeat",
                    "gongkao_worker_heartbeat_login",
                ],
                "heartbeat_groups": [
                    "gongkao_worker_heartbeat",
                    *(binding.group_role for binding in JOB_CONSUMER_PRINCIPALS),
                ],
            },
        )
        .mappings()
        .one()
    )
    return (
        untrusted["has_forbidden_membership"] is False
        and untrusted["has_forbidden_execute"] is False
    )


def _coach_template_id_for_metrics(settings: Settings) -> str:
    try:
        release = CoachTemplateRelease.from_json(settings.wechat_coach_template_release_json)
    except CoachTemplateConfigurationError:
        return "__invalid_coach_template__"
    return release.template_id if release is not None else "__missing_coach_template__"


@router.get(
    "/internal/support/commerce/orders/{out_trade_no}",
    response_model=SupportOrderView,
)
def support_commerce_order(
    out_trade_no: str,
    session: Annotated[Session, Depends(get_db_session)],
    principal: Annotated[SupportPrincipal, Depends(_support_principal)],
    ticket_ref: Annotated[str, Header(alias="X-Support-Ticket")],
    access_reason: Annotated[str, Header(alias="X-Support-Reason")],
) -> SupportOrderView:
    try:
        return read_order_for_support(
            session,
            principal=principal,
            out_trade_no=out_trade_no,
            ticket_ref=ticket_ref,
            access_reason=access_reason,
            occurred_at=datetime.now(UTC),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except SupportOrderNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
