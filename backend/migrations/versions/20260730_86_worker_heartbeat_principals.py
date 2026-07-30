"""Bind every worker heartbeat to one immutable database principal.

Revision ID: 20260730_86
Revises: 20260730_85
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260730_86"
down_revision: str | Sequence[str] | None = "20260730_85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_HEARTBEAT_GROUP_ROLE = "gongkao_worker_heartbeat"
LEGACY_HEARTBEAT_LOGIN_ROLE = "gongkao_worker_heartbeat_login"
HEARTBEAT_FUNCTION_SIGNATURE = "platform.pulse_background_job_consumer_heartbeat(text,text,uuid)"

PRINCIPAL_BINDINGS = (
    (
        "gongkao_worker_heartbeat_data_rights_login",
        "gongkao_worker_heartbeat_data_rights",
        "gongkao-dazi-data-rights-worker",
        "app.workers.data_rights",
    ),
    (
        "gongkao_worker_heartbeat_cause_agent_login",
        "gongkao_worker_heartbeat_cause_agent",
        "gongkao-dazi-cause-agent-worker",
        "app.workers.xingce_cause_agent",
    ),
    (
        "gongkao_worker_heartbeat_external_learning_login",
        "gongkao_worker_heartbeat_external_learning",
        "gongkao-dazi-external-learning-worker",
        "app.workers.external_learning",
    ),
    (
        "gongkao_worker_heartbeat_essay_review_login",
        "gongkao_worker_heartbeat_essay_review",
        "gongkao-dazi-essay-review-worker",
        "app.workers.essay_reviews",
    ),
    (
        "gongkao_worker_heartbeat_coach_notification_login",
        "gongkao_worker_heartbeat_coach_notification",
        "gongkao-dazi-coach-notification-worker",
        "app.workers.coach_notifications",
    ),
    (
        "gongkao_worker_heartbeat_ilink_login",
        "gongkao_worker_heartbeat_ilink",
        "gongkao-dazi-ilink-worker",
        "app.workers.ilink_channel",
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


def _is_member(
    bind: Connection,
    *,
    member: str,
    role_name: str,
) -> bool:
    return bool(
        bind.execute(
            text("select pg_has_role(:member, :role_name, 'member')"),
            {"member": member, "role_name": role_name},
        ).scalar_one()
    )


def _direct_memberships(
    bind: Connection,
    *,
    member: str,
) -> set[str]:
    return {
        str(role_name)
        for role_name in bind.execute(
            text(
                """
                select granted.rolname
                from pg_auth_members membership
                join pg_roles member
                  on member.oid = membership.member
                join pg_roles granted
                  on granted.oid = membership.roleid
                where member.rolname = :member
                """
            ),
            {"member": member},
        ).scalars()
    }


def require_heartbeat_principal_roles(bind: Connection) -> None:
    elevated_fields = (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
    )
    group_roles = tuple(binding[1] for binding in PRINCIPAL_BINDINGS)
    required_roles = {role_name for binding in PRINCIPAL_BINDINGS for role_name in binding[:2]}
    missing_roles = sorted(
        role_name for role_name in required_roles if _role_row(bind, role_name) is None
    )
    if missing_roles:
        raise RuntimeError(
            "migration 20260730_86 requires all six pre-provisioned "
            "worker-specific heartbeat LOGIN/NOLOGIN role pairs; run the "
            "reviewed DBA provisioning SQL before retrying; missing: " + ", ".join(missing_roles)
        )
    for database_principal, group_role, _, _ in PRINCIPAL_BINDINGS:
        group = _role_row(bind, group_role)
        login = _role_row(bind, database_principal)
        assert group is not None
        assert login is not None
        if bool(group["rolcanlogin"]) or any(bool(group[field]) for field in elevated_fields):
            raise RuntimeError(f"{group_role} must be a non-elevated NOLOGIN group role")
        if (
            not bool(login["rolcanlogin"])
            or not bool(login["rolinherit"])
            or any(bool(login[field]) for field in elevated_fields)
        ):
            raise RuntimeError(f"{database_principal} must be a non-elevated INHERIT LOGIN role")
        if not _is_member(
            bind,
            member=database_principal,
            role_name=group_role,
        ):
            raise RuntimeError(f"{database_principal} must be a member of {group_role}")
        if _direct_memberships(bind, member=database_principal) != {group_role}:
            raise RuntimeError(f"{database_principal} must inherit only {group_role}")
        if _direct_memberships(bind, member=group_role):
            raise RuntimeError(f"{group_role} must not inherit another database role")
        forbidden_groups = (
            LEGACY_HEARTBEAT_GROUP_ROLE,
            *(candidate for candidate in group_roles if candidate != group_role),
        )
        for forbidden_group in forbidden_groups:
            if _is_member(
                bind,
                member=database_principal,
                role_name=forbidden_group,
            ):
                raise RuntimeError(f"{database_principal} must not inherit {forbidden_group}")

    forbidden_memberships = {
        "gongkao_api": (
            LEGACY_HEARTBEAT_GROUP_ROLE,
            *group_roles,
        ),
        LEGACY_HEARTBEAT_LOGIN_ROLE: group_roles,
        LEGACY_HEARTBEAT_GROUP_ROLE: group_roles,
    }
    for untrusted_role, forbidden_groups in forbidden_memberships.items():
        if _role_row(bind, untrusted_role) is None:
            continue
        for group_role in forbidden_groups:
            if _is_member(
                bind,
                member=untrusted_role,
                role_name=group_role,
            ):
                raise RuntimeError(f"{untrusted_role} must not inherit {group_role}")


def _create_principal_registry(bind: Connection) -> None:
    bind.exec_driver_sql(
        """
        create table platform.background_job_consumer_principal_registry (
          database_principal text primary key,
          group_role text not null unique,
          consumer_unit text not null,
          consumer_entrypoint text not null,
          registered_at timestamptz not null default statement_timestamp(),
          constraint background_job_consumer_principal_identity_key
            unique (consumer_unit, consumer_entrypoint),
          constraint background_job_consumer_principal_login_check
            check (
              database_principal ~
              '^gongkao_worker_heartbeat_[a-z0-9_]+_login$'
            ),
          constraint background_job_consumer_principal_group_check
            check (
              group_role ~ '^gongkao_worker_heartbeat_[a-z0-9_]+$'
            ),
          constraint background_job_consumer_principal_consumer_fk
            foreign key (consumer_unit, consumer_entrypoint)
            references platform.background_job_consumer_registry (
              consumer_unit, consumer_entrypoint
            )
        )
        """
    )
    for (
        database_principal,
        group_role,
        consumer_unit,
        consumer_entrypoint,
    ) in PRINCIPAL_BINDINGS:
        bind.execute(
            text(
                """
                insert into
                  platform.background_job_consumer_principal_registry (
                    database_principal,
                    group_role,
                    consumer_unit,
                    consumer_entrypoint
                  ) values (
                    :database_principal,
                    :group_role,
                    :consumer_unit,
                    :consumer_entrypoint
                  )
                """
            ),
            {
                "database_principal": database_principal,
                "group_role": group_role,
                "consumer_unit": consumer_unit,
                "consumer_entrypoint": consumer_entrypoint,
            },
        )


def _assert_principal_registry_is_exact(bind: Connection) -> None:
    actual = {
        (
            str(row["database_principal"]),
            str(row["group_role"]),
            str(row["consumer_unit"]),
            str(row["consumer_entrypoint"]),
        )
        for row in bind.execute(
            text(
                """
                select database_principal, group_role,
                       consumer_unit, consumer_entrypoint
                from platform.background_job_consumer_principal_registry
                """
            )
        ).mappings()
    }
    if actual != set(PRINCIPAL_BINDINGS):
        raise RuntimeError(
            "migration 20260730_86 found worker heartbeat principal drift; "
            "publish an audited forward repair"
        )


def _install_immutable_principal_guards(bind: Connection) -> None:
    bind.exec_driver_sql(
        """
        create trigger background_job_consumer_principal_immutable_row
        before update or delete
          on platform.background_job_consumer_principal_registry
        for each row execute function
          platform.reject_background_job_registry_mutation()
        """
    )
    bind.exec_driver_sql(
        """
        create trigger background_job_consumer_principal_immutable_truncate
        before truncate
          on platform.background_job_consumer_principal_registry
        for each statement execute function
          platform.reject_background_job_registry_mutation()
        """
    )


def _bind_heartbeat_function_to_session_user(bind: Connection) -> None:
    bind.exec_driver_sql(
        """
        create or replace function
          platform.pulse_background_job_consumer_heartbeat(
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
          bound_consumer_unit text;
          bound_consumer_entrypoint text;
          pulse_at timestamptz := statement_timestamp();
        begin
          select registry.consumer_unit, registry.consumer_entrypoint
            into bound_consumer_unit, bound_consumer_entrypoint
            from platform.background_job_consumer_principal_registry registry
           where registry.database_principal = session_user::text;

          if not found then
            raise exception using
              errcode = '42501',
              message = 'session principal is not authorized to pulse heartbeats';
          end if;
          if requested_consumer_unit is distinct from bound_consumer_unit
             or requested_consumer_entrypoint is distinct from
                bound_consumer_entrypoint then
            raise exception using
              errcode = '42501',
              message = 'session principal cannot pulse another consumer';
          end if;
          if requested_process_instance_id is null then
            raise exception using
              errcode = '23502',
              message = 'heartbeat process instance is required';
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
              bound_consumer_unit,
              bound_consumer_entrypoint,
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
    heartbeat_tables = (
        "platform.background_jobs, "
        "platform.background_job_consumer_heartbeats, "
        "platform.background_job_consumer_registry, "
        "platform.background_job_type_registry, "
        "platform.background_job_consumer_principal_registry"
    )
    bind.exec_driver_sql(f"revoke all privileges on {heartbeat_tables} from public")
    function_roles = (
        LEGACY_HEARTBEAT_GROUP_ROLE,
        LEGACY_HEARTBEAT_LOGIN_ROLE,
        *(binding[0] for binding in PRINCIPAL_BINDINGS),
        *(binding[1] for binding in PRINCIPAL_BINDINGS),
    )
    for role_name in function_roles:
        bind.exec_driver_sql(f"revoke all privileges on {heartbeat_tables} from {role_name}")
        bind.exec_driver_sql(f"revoke create on schema platform from {role_name}")
        bind.exec_driver_sql(
            f"revoke all privileges on function {HEARTBEAT_FUNCTION_SIGNATURE} from {role_name}"
        )

    bind.exec_driver_sql(
        f"revoke all privileges on function {HEARTBEAT_FUNCTION_SIGNATURE} from public"
    )
    for _, group_role, _, _ in PRINCIPAL_BINDINGS:
        bind.exec_driver_sql(f"revoke create on schema platform from {group_role}")
        bind.exec_driver_sql(f"grant usage on schema platform to {group_role}")
        bind.exec_driver_sql(
            f"grant execute on function {HEARTBEAT_FUNCTION_SIGNATURE} to {group_role}"
        )

    if _role_row(bind, "gongkao_api") is not None:
        bind.exec_driver_sql(
            "revoke all privileges on "
            "platform.background_job_consumer_principal_registry "
            "from gongkao_api"
        )
        bind.exec_driver_sql(
            "grant select on platform.background_job_consumer_principal_registry to gongkao_api"
        )
        bind.exec_driver_sql(
            f"revoke all privileges on function {HEARTBEAT_FUNCTION_SIGNATURE} from gongkao_api"
        )


def upgrade() -> None:
    bind = op.get_bind()
    require_heartbeat_principal_roles(bind)
    _create_principal_registry(bind)
    _assert_principal_registry_is_exact(bind)
    _install_immutable_principal_guards(bind)
    _bind_heartbeat_function_to_session_user(bind)
    _harden_acl(bind)


def downgrade() -> None:
    raise RuntimeError(
        "worker-specific heartbeat principals are forward-only; publish a forward migration"
    )
