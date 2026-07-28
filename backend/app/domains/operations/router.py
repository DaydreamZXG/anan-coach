from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
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
    JOB_LEASE_CONTRACT_VERSION,
    JOB_REGISTRY,
    JobRegistration,
)


router = APIRouter(tags=["operations"])

POSITION_RELEASE_MAX_AGE = timedelta(days=365)


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
            "and to_regclass('product.knowledge_map_releases') is not null "
            "and to_regclass('product.knowledge_map_nodes') is not null "
            "and to_regclass('messaging.coach_notification_deliveries') is not null "
            "and to_regclass('messaging.daily_opening_impressions') is not null "
            "and to_regclass('messaging.daily_opening_preference_versions') is not null"
        )
    ).scalar_one()
    if (
        migration not in settings.compatible_database_revisions
        or not required_tables_ready
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database schema is not at the required release",
        )

    checked_at = datetime.now(UTC)
    release = (
        session.execute(
            text(
                """
            select rule_set_version, position_data_version
            from product.position_rule_releases
            where exam_code = 'guokao'
              and status = 'active'
              and activated_at between :minimum_activated_at and :checked_at
            order by exam_year desc, activated_at desc
            limit 1
            """
            ),
            {
                "minimum_activated_at": checked_at - POSITION_RELEASE_MAX_AGE,
                "checked_at": checked_at,
            },
        )
        .mappings()
        .one_or_none()
    )
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="position rule release is not ready",
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
        registrations=tuple(
            registration
            for registration in JOB_REGISTRY
            if _job_registration_enabled(registration)
        ),
    )
    stale_job_types = tuple(
        registration.job_type
        for registration in JOB_REGISTRY
        if _job_registration_enabled(registration)
        and (
            job_health[registration.job_type]["queue_age_seconds"]
            > registration.queue_readiness_age.total_seconds()
            or job_health[registration.job_type]["running_age_seconds"]
            > registration.running_readiness_age.total_seconds()
        )
    )
    if stale_job_types:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="background job consumers are not ready",
        )
    if (
        settings.environment.strip().casefold() == "production"
        and settings.coach_notifications_enabled
    ):
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
    if settings.environment.strip().casefold() == "production":
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
            if settings.recognition_processor_enabled:
                processor_releases.require_active_release(
                    session,
                    declared_provider_release=(settings.recognition_processor_release_id),
                    provider_endpoint_fingerprint=endpoint_fingerprint(
                        settings.recognition_processor_endpoint
                    ),
                    purpose="recognition_process",
                    data_categories=("uploaded_image",),
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
        "positionRuleRelease": str(release["rule_set_version"]),
        "positionDataVersion": str(release["position_data_version"]),
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
              (select count(*) from platform.background_jobs where status = 'failed') as failed_jobs,
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
                  )) as coach_stale_claims
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
        "anan_wechat_payment_uncertain_orders": int(row["uncertain_orders"]),
        "anan_wechat_stale_refunds": int(row["stale_refunds"]),
        "anan_background_jobs_queued": int(row["queued_jobs"]),
        "anan_background_jobs_failed": int(row["failed_jobs"]),
        "anan_privacy_deletions_failed": int(row["failed_deletions"]),
        "anan_privacy_exports_failed": int(row["failed_exports"]),
        "anan_coach_notification_outcome_unknown": int(row["coach_unknown"]),
        "anan_coach_notification_rejected_last_hour": int(row["coach_rejected_hour"]),
        "anan_coach_notification_available_grants": int(row["coach_available_grants"]),
        "anan_coach_notification_enabled_users": int(row["coach_enabled_users"]),
        "anan_coach_notification_enabled_without_grant": int(row["coach_enabled_without_grant"]),
        "anan_coach_notification_stale_claims": int(row["coach_stale_claims"]),
    }
    health = _job_queue_health(
        session,
        now=datetime.now(UTC),
        registrations=JOB_REGISTRY,
    )
    job_values = tuple(
        (
            f'anan_job_queue_oldest_age_seconds{{job_type="{registration.job_type}"}}',
            int(health[registration.job_type]["queue_age_seconds"]),
        )
        for registration in JOB_REGISTRY
    ) + tuple(
        (
            f'anan_job_running_oldest_age_seconds{{job_type="{registration.job_type}"}}',
            int(health[registration.job_type]["running_age_seconds"]),
        )
        for registration in JOB_REGISTRY
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


def _job_registration_enabled(registration: JobRegistration) -> bool:
    if registration.enabled_setting is None:
        return True
    return (
        os.getenv(registration.enabled_setting, "false").strip().casefold()
        == "true"
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
                      )), 0), 0) as running_age_seconds
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
                      )), 0), 0) as running_age_seconds
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
        }
    return health


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
