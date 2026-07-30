"""Harden background-job registration and consumer heartbeat authority.

Revision ID: 20260730_84
Revises: 20260730_83
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260730_84"
down_revision: str | Sequence[str] | None = "20260730_83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


HEARTBEAT_GROUP_ROLE = "gongkao_worker_heartbeat"
HEARTBEAT_LOGIN_ROLE = "gongkao_worker_heartbeat_login"
HEARTBEAT_FUNCTION_SIGNATURE = "platform.pulse_background_job_consumer_heartbeat(text,text,uuid)"

CONSUMER_IDENTITIES = (
    (
        "gongkao-dazi-data-rights-worker",
        "app.workers.data_rights",
        "platform.background_jobs",
    ),
    (
        "gongkao-dazi-cause-agent-worker",
        "app.workers.xingce_cause_agent",
        "platform.background_jobs",
    ),
    (
        "gongkao-dazi-external-learning-worker",
        "app.workers.external_learning",
        "platform.background_jobs",
    ),
    (
        "gongkao-dazi-essay-review-worker",
        "app.workers.essay_reviews",
        "platform.background_jobs",
    ),
    (
        "gongkao-dazi-coach-notification-worker",
        "app.workers.coach_notifications",
        "platform.background_jobs",
    ),
    (
        "gongkao-dazi-ilink-worker",
        "app.workers.ilink_channel",
        "messaging.ilink_object_deletion_queue",
    ),
)

BACKGROUND_JOB_TYPES = (
    (
        "personal_data_export_v1",
        "app.domains.data_rights.repository.DataRightsRepository",
        "gongkao-dazi-data-rights-worker",
        "app.workers.data_rights",
        "app.domains.data_rights.worker.DataRightsWorker",
        "deployed",
        None,
        None,
    ),
    (
        "account_erasure_v1",
        "app.domains.data_rights.repository.DataRightsRepository",
        "gongkao-dazi-data-rights-worker",
        "app.workers.data_rights",
        "app.domains.data_rights.worker.DataRightsWorker",
        "deployed",
        None,
        None,
    ),
    (
        "xingce_cause_agent_v1",
        "app.domains.xingce_learning.cause_agent.service.CauseAgentWorkflow",
        "gongkao-dazi-cause-agent-worker",
        "app.workers.xingce_cause_agent",
        "app.domains.xingce_learning.cause_agent.worker.CauseAgentWorker",
        "deployed",
        None,
        None,
    ),
    (
        "learning_media_scan_v1",
        "app.domains.learning_media.service.LearningMediaService",
        "gongkao-dazi-external-learning-worker",
        "app.workers.external_learning",
        "app.domains.learning_media.worker.LearningMediaWorker",
        "deployed",
        None,
        None,
    ),
    (
        "learning_media_retention_cleanup_v1",
        "app.domains.learning_media.service.LearningMediaService",
        "gongkao-dazi-external-learning-worker",
        "app.workers.external_learning",
        "app.domains.learning_media.worker.LearningMediaWorker",
        "deployed",
        None,
        None,
    ),
    (
        "offline_packet_render_v1",
        "app.domains.learning_packages.service.LearningPackageService",
        "gongkao-dazi-external-learning-worker",
        "app.workers.external_learning",
        "app.domains.learning_packages.worker.PacketRenderWorker",
        "deployed",
        None,
        None,
    ),
    (
        "external_question_answer_v1",
        "app.domains.external_ingestion.answering.service.ExternalAIAnswerService",
        "gongkao-dazi-external-learning-worker",
        "app.workers.external_learning",
        "app.domains.external_ingestion.answering.worker.ExternalAIAnswerWorker",
        "deployed",
        None,
        None,
    ),
    (
        "private_knowledge_source_delete@v1",
        "app.domains.private_knowledge.repository.PrivateKnowledgeRepository",
        "gongkao-dazi-external-learning-worker",
        "app.workers.external_learning",
        "app.domains.private_knowledge.worker.PrivateKnowledgeDeletionWorker",
        "deployed",
        None,
        None,
    ),
    (
        "private_knowledge_weknora_index@v1",
        "app.domains.private_knowledge.projection.PrivateKnowledgeProjectionGateway",
        "gongkao-dazi-external-learning-worker",
        "app.workers.external_learning",
        "app.domains.private_knowledge.worker.PrivateKnowledgeDeletionWorker",
        "deployed",
        None,
        None,
    ),
    (
        "essay_grounded_review_v2",
        "app.domains.essay_intelligence.workflow_service.EssayReviewWorkflowService",
        "gongkao-dazi-essay-review-worker",
        "app.workers.essay_reviews",
        "app.domains.essay_intelligence.review_worker.GroundedEssayReviewWorker",
        "deployed",
        "ESSAY_REVIEW_WORKER_ENABLED",
        None,
    ),
    (
        "coach_notification_send_v1",
        "app.domains.coach_notifications.evaluator.CoachNotificationEvaluator",
        "gongkao-dazi-coach-notification-worker",
        "app.workers.coach_notifications",
        "app.domains.coach_notifications.worker.CoachNotificationWorker",
        "deployed",
        "COACH_NOTIFICATIONS_ENABLED",
        None,
    ),
    (
        "community_method_card_candidate_v2",
        "app.workers.community_method_cards.CommunityMethodCardJobScheduler",
        None,
        None,
        "app.workers.community_method_cards.CommunityMethodCardWorker",
        "disabled",
        None,
        "No approved production scheduler or deployed consumer entrypoint",
    ),
    (
        "community_method_card_review_v2",
        "app.workers.community_method_cards.CommunityMethodCardJobScheduler",
        None,
        None,
        "app.workers.community_method_cards.CommunityMethodCardWorker",
        "disabled",
        None,
        "No approved production scheduler or deployed consumer entrypoint",
    ),
)


def _role_row(bind: Connection, role_name: str):
    return (
        bind.execute(
            text(
                """
                select rolname, rolcanlogin, rolinherit, rolsuper,
                       rolcreatedb, rolcreaterole, rolreplication,
                       rolbypassrls
                from pg_roles
                where rolname = :role_name
                """
            ),
            {"role_name": role_name},
        )
        .mappings()
        .one_or_none()
    )


def require_heartbeat_roles(bind: Connection) -> None:
    group = _role_row(bind, HEARTBEAT_GROUP_ROLE)
    login = _role_row(bind, HEARTBEAT_LOGIN_ROLE)
    if group is None or login is None:
        raise RuntimeError(
            "migration 20260730_84 requires pre-provisioned "
            "gongkao_worker_heartbeat NOLOGIN and "
            "gongkao_worker_heartbeat_login LOGIN roles; run the reviewed DBA "
            "provisioning SQL before retrying"
        )
    elevated_fields = (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
    )
    if bool(group["rolcanlogin"]) or any(bool(group[field]) for field in elevated_fields):
        raise RuntimeError("gongkao_worker_heartbeat must be a non-elevated NOLOGIN group role")
    if (
        not bool(login["rolcanlogin"])
        or not bool(login["rolinherit"])
        or any(bool(login[field]) for field in elevated_fields)
    ):
        raise RuntimeError(
            "gongkao_worker_heartbeat_login must be a non-elevated INHERIT LOGIN role"
        )
    membership = bind.execute(
        text("select pg_has_role(:member, :role_name, 'member')"),
        {
            "member": HEARTBEAT_LOGIN_ROLE,
            "role_name": HEARTBEAT_GROUP_ROLE,
        },
    ).scalar_one()
    if not membership:
        raise RuntimeError(
            "gongkao_worker_heartbeat_login must be a member of gongkao_worker_heartbeat"
        )
    api_role = _role_row(bind, "gongkao_api")
    if (
        api_role is not None
        and bind.execute(
            text("select pg_has_role('gongkao_api', :role_name, 'member')"),
            {"role_name": HEARTBEAT_GROUP_ROLE},
        ).scalar_one()
    ):
        raise RuntimeError("gongkao_api must not be a member of gongkao_worker_heartbeat")


def _create_registries(bind: Connection) -> None:
    bind.exec_driver_sql(
        """
        create table platform.background_job_consumer_registry (
          consumer_unit text primary key,
          consumer_entrypoint text not null,
          queue_name text not null,
          registered_at timestamptz not null default statement_timestamp(),
          constraint background_job_consumer_registry_identity_key
            unique (consumer_unit, consumer_entrypoint),
          constraint background_job_consumer_registry_unit_check
            check (consumer_unit ~ '^gongkao-dazi-[a-z0-9-]+$'),
          constraint background_job_consumer_registry_entrypoint_check
            check (consumer_entrypoint ~ '^app[.]workers[.][a-z0-9_]+$'),
          constraint background_job_consumer_registry_queue_check
            check (queue_name in (
              'platform.background_jobs',
              'messaging.ilink_object_deletion_queue'
            ))
        )
        """
    )
    bind.exec_driver_sql(
        """
        create table platform.background_job_type_registry (
          job_type text primary key,
          queue_name text not null default 'platform.background_jobs',
          producer text not null,
          consumer_unit text,
          consumer_entrypoint text,
          recovery_owner text not null,
          deployment_state text not null,
          enabled_setting text,
          disabled_reason text,
          registered_at timestamptz not null default statement_timestamp(),
          constraint background_job_type_registry_queue_check
            check (queue_name = 'platform.background_jobs'),
          constraint background_job_type_registry_state_check
            check (deployment_state in ('deployed', 'disabled')),
          constraint background_job_type_registry_consumer_fk
            foreign key (consumer_unit, consumer_entrypoint)
            references platform.background_job_consumer_registry (
              consumer_unit, consumer_entrypoint
            ),
          constraint background_job_type_registry_contract_check check (
            (
              deployment_state = 'deployed'
              and consumer_unit is not null
              and consumer_entrypoint is not null
              and disabled_reason is null
            )
            or
            (
              deployment_state = 'disabled'
              and consumer_unit is null
              and consumer_entrypoint is null
              and enabled_setting is null
              and nullif(btrim(disabled_reason), '') is not null
            )
          )
        )
        """
    )
    for consumer_unit, consumer_entrypoint, queue_name in CONSUMER_IDENTITIES:
        bind.execute(
            text(
                """
                insert into platform.background_job_consumer_registry (
                  consumer_unit, consumer_entrypoint, queue_name
                ) values (
                  :consumer_unit, :consumer_entrypoint, :queue_name
                )
                """
            ),
            {
                "consumer_unit": consumer_unit,
                "consumer_entrypoint": consumer_entrypoint,
                "queue_name": queue_name,
            },
        )
    for (
        job_type,
        producer,
        consumer_unit,
        consumer_entrypoint,
        recovery_owner,
        deployment_state,
        enabled_setting,
        disabled_reason,
    ) in BACKGROUND_JOB_TYPES:
        bind.execute(
            text(
                """
                insert into platform.background_job_type_registry (
                  job_type, producer, consumer_unit, consumer_entrypoint,
                  recovery_owner, deployment_state, enabled_setting,
                  disabled_reason
                ) values (
                  :job_type, :producer, :consumer_unit, :consumer_entrypoint,
                  :recovery_owner, :deployment_state, :enabled_setting,
                  :disabled_reason
                )
                """
            ),
            {
                "job_type": job_type,
                "producer": producer,
                "consumer_unit": consumer_unit,
                "consumer_entrypoint": consumer_entrypoint,
                "recovery_owner": recovery_owner,
                "deployment_state": deployment_state,
                "enabled_setting": enabled_setting,
                "disabled_reason": disabled_reason,
            },
        )


def _assert_registry_rows_are_exact(bind: Connection) -> None:
    expected_consumers = set(CONSUMER_IDENTITIES)
    actual_consumers = {
        (
            str(row["consumer_unit"]),
            str(row["consumer_entrypoint"]),
            str(row["queue_name"]),
        )
        for row in bind.execute(
            text(
                """
                select consumer_unit, consumer_entrypoint, queue_name
                from platform.background_job_consumer_registry
                """
            )
        ).mappings()
    }
    if actual_consumers != expected_consumers:
        raise RuntimeError(
            "migration 20260730_84 found consumer registry drift; publish an audited forward repair"
        )
    expected_jobs = {
        (
            job_type,
            "platform.background_jobs",
            producer,
            consumer_unit,
            consumer_entrypoint,
            recovery_owner,
            deployment_state,
            enabled_setting,
            disabled_reason,
        )
        for (
            job_type,
            producer,
            consumer_unit,
            consumer_entrypoint,
            recovery_owner,
            deployment_state,
            enabled_setting,
            disabled_reason,
        ) in BACKGROUND_JOB_TYPES
    }
    actual_jobs = {
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
        for row in bind.execute(
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
    if actual_jobs != expected_jobs:
        raise RuntimeError(
            "migration 20260730_84 found job type registry drift; publish an audited forward repair"
        )


def _assert_legacy_rows_are_compatible(bind: Connection) -> None:
    unknown_job_types = tuple(
        str(value)
        for value in bind.execute(
            text(
                """
                select distinct job.job_type
                from platform.background_jobs job
                left join platform.background_job_type_registry registry
                  on registry.job_type = job.job_type
                where registry.job_type is null
                order by job.job_type
                """
            )
        ).scalars()
    )
    if unknown_job_types:
        raise RuntimeError(
            "migration 20260730_84 refuses unknown legacy background job "
            f"types without an audited forward registration: {', '.join(unknown_job_types)}"
        )
    invalid_heartbeats = tuple(
        str(value)
        for value in bind.execute(
            text(
                """
                select heartbeat.consumer_unit
                from platform.background_job_consumer_heartbeats heartbeat
                left join platform.background_job_consumer_registry registry
                  on registry.consumer_unit = heartbeat.consumer_unit
                 and registry.consumer_entrypoint =
                     heartbeat.consumer_entrypoint
                where registry.consumer_unit is null
                   or heartbeat.started_at > statement_timestamp()
                   or heartbeat.last_heartbeat_at > statement_timestamp()
                order by heartbeat.consumer_unit
                """
            )
        ).scalars()
    )
    if invalid_heartbeats:
        raise RuntimeError(
            "migration 20260730_84 refuses unknown or future-dated legacy "
            "consumer heartbeats without an audited cleanup: "
            f"{', '.join(invalid_heartbeats)}"
        )


def _install_immutable_registry_guards(bind: Connection) -> None:
    bind.exec_driver_sql(
        """
        create function platform.reject_background_job_registry_mutation()
        returns trigger
        language plpgsql
        set search_path = pg_catalog
        as $$
        begin
          raise exception using
            errcode = '23514',
            message = format(
              '%s is append-only; publish a forward registry migration',
              tg_table_name
            );
        end;
        $$
        """
    )
    for table_name in (
        "background_job_consumer_registry",
        "background_job_type_registry",
    ):
        bind.exec_driver_sql(
            f"""
            create trigger {table_name}_immutable_row
            before update or delete on platform.{table_name}
            for each row execute function
              platform.reject_background_job_registry_mutation()
            """
        )
        bind.exec_driver_sql(
            f"""
            create trigger {table_name}_immutable_truncate
            before truncate on platform.{table_name}
            for each statement execute function
              platform.reject_background_job_registry_mutation()
            """
        )


def _install_job_type_foreign_key(bind: Connection) -> None:
    bind.exec_driver_sql(
        """
        alter table platform.background_jobs
          add constraint background_jobs_registered_job_type_fk
          foreign key (job_type)
          references platform.background_job_type_registry (job_type)
          not valid
        """
    )
    bind.exec_driver_sql(
        """
        alter table platform.background_jobs
          validate constraint background_jobs_registered_job_type_fk
        """
    )


def _install_heartbeat_authority(bind: Connection) -> None:
    bind.exec_driver_sql(
        """
        create function platform.reject_future_background_job_heartbeat()
        returns trigger
        language plpgsql
        set search_path = pg_catalog
        as $$
        begin
          if new.started_at > statement_timestamp()
             or new.last_heartbeat_at > statement_timestamp() then
            raise exception using
              errcode = '22007',
              message = 'background consumer heartbeat cannot be future-dated';
          end if;
          return new;
        end;
        $$
        """
    )
    bind.exec_driver_sql(
        """
        create trigger background_job_consumer_heartbeats_reject_future
        before insert or update
          on platform.background_job_consumer_heartbeats
        for each row execute function
          platform.reject_future_background_job_heartbeat()
        """
    )
    bind.exec_driver_sql(
        """
        create function platform.pulse_background_job_consumer_heartbeat(
          requested_consumer_unit text,
          requested_consumer_entrypoint text,
          requested_process_instance_id uuid
        )
        returns table (
          consumer_unit text,
          consumer_entrypoint text,
          process_instance_id uuid,
          started_at timestamptz,
          last_heartbeat_at timestamptz
        )
        language plpgsql
        security definer
        set search_path = pg_catalog
        as $$
        declare
          pulse_at timestamptz := statement_timestamp();
        begin
          if requested_process_instance_id is null
             or not exists (
               select 1
               from platform.background_job_consumer_registry registry
               where registry.consumer_unit = requested_consumer_unit
                 and registry.consumer_entrypoint =
                     requested_consumer_entrypoint
             ) then
            raise exception using
              errcode = '23503',
              message = 'consumer heartbeat identity is not registered';
          end if;

          return query
          insert into platform.background_job_consumer_heartbeats
            as current_heartbeat (
              consumer_unit,
              consumer_entrypoint,
              process_instance_id,
              started_at,
              last_heartbeat_at
            ) values (
              requested_consumer_unit,
              requested_consumer_entrypoint,
              requested_process_instance_id,
              pulse_at,
              pulse_at
            )
          on conflict on constraint
            background_job_consumer_heartbeats_pkey do update
          set consumer_entrypoint = excluded.consumer_entrypoint,
              process_instance_id = excluded.process_instance_id,
              started_at = case
                when current_heartbeat.process_instance_id =
                     excluded.process_instance_id
                  then current_heartbeat.started_at
                else pulse_at
              end,
              last_heartbeat_at = pulse_at
          returning
            current_heartbeat.consumer_unit,
            current_heartbeat.consumer_entrypoint,
            current_heartbeat.process_instance_id,
            current_heartbeat.started_at,
            current_heartbeat.last_heartbeat_at;
        end;
        $$
        """
    )


def _harden_acl(bind: Connection) -> None:
    tables = (
        "platform.background_job_consumer_heartbeats, "
        "platform.background_job_consumer_registry, "
        "platform.background_job_type_registry"
    )
    bind.exec_driver_sql(f"revoke all privileges on {tables} from public")
    bind.exec_driver_sql(f"revoke all privileges on {tables} from {HEARTBEAT_GROUP_ROLE}")
    bind.exec_driver_sql(f"revoke create on schema platform from {HEARTBEAT_GROUP_ROLE}")
    bind.exec_driver_sql(f"grant usage on schema platform to {HEARTBEAT_GROUP_ROLE}")
    bind.exec_driver_sql(
        f"revoke all privileges on function {HEARTBEAT_FUNCTION_SIGNATURE} from public"
    )
    bind.exec_driver_sql(
        f"grant execute on function {HEARTBEAT_FUNCTION_SIGNATURE} to {HEARTBEAT_GROUP_ROLE}"
    )
    if _role_row(bind, "gongkao_api") is not None:
        bind.exec_driver_sql(f"revoke all privileges on {tables} from gongkao_api")
        bind.exec_driver_sql(f"grant select on {tables} to gongkao_api")
        bind.exec_driver_sql(
            f"revoke all privileges on function {HEARTBEAT_FUNCTION_SIGNATURE} from gongkao_api"
        )


def upgrade() -> None:
    bind = op.get_bind()
    require_heartbeat_roles(bind)
    _create_registries(bind)
    _assert_registry_rows_are_exact(bind)
    _assert_legacy_rows_are_compatible(bind)
    _install_immutable_registry_guards(bind)
    _install_job_type_foreign_key(bind)
    _install_heartbeat_authority(bind)
    _harden_acl(bind)


def downgrade() -> None:
    raise RuntimeError(
        "background job and consumer heartbeat authority is forward-only; "
        "publish a forward migration"
    )
