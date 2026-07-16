"""Add private, versioned learning-media uploads and reflection bindings.

Revision ID: 20260716_26
Revises: 20260716_25
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_26"
down_revision: str | Sequence[str] | None = "20260716_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_records(bind):
        raise RuntimeError(
            "Learning-media records exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    op.execute(
        "drop trigger if exists learning_media_bindings_append_only "
        "on learning.learning_media_bindings"
    )
    op.execute(
        "drop trigger if exists learning_media_artifact_versions_append_only "
        "on learning.learning_media_artifact_versions"
    )
    op.execute(
        "drop trigger if exists learning_media_audit_events_append_only "
        "on learning.learning_media_audit_events"
    )
    op.execute(
        "drop trigger if exists learning_media_binding_entity_guard "
        "on learning.learning_media_bindings"
    )
    op.execute("drop function if exists learning.validate_learning_media_binding()")
    op.execute("drop function if exists learning.reject_learning_media_fact_mutation()")
    for table in (
        "learning_media_bindings",
        "learning_media_artifact_versions",
        "learning_media_audit_events",
        "learning_media_upload_sessions",
        "learning_media_commands",
    ):
        op.drop_table(table, schema="learning")


def _has_records(bind: Connection) -> bool:
    query = " union all ".join(
        f"select 1 from learning.{table}" for table in _TABLES
    )
    return bool(bind.execute(sa.text(f"select exists ({query})")).scalar_one())


_TABLES = (
    "learning_media_commands",
    "learning_media_upload_sessions",
    "learning_media_audit_events",
    "learning_media_artifact_versions",
    "learning_media_bindings",
)


_STATEMENTS = (
    """
    create table learning.learning_media_commands (
      user_id uuid not null references learner.users(user_id),
      command_id text not null,
      command_type text not null check (command_type in ('upload_start', 'upload_complete')),
      target_id uuid not null,
      request_fingerprint text not null check (length(request_fingerprint) = 64),
      created_at timestamptz not null,
      primary key (user_id, command_id),
      check (char_length(command_id) between 8 and 160)
    )
    """,
    """
    create table learning.learning_media_upload_sessions (
      upload_session_id uuid primary key,
      artifact_id uuid not null,
      artifact_version_id uuid not null,
      user_id uuid not null references learner.users(user_id),
      purpose text not null check (purpose = 'reflection_voice'),
      state text not null check (state in (
        'awaiting_upload', 'scanning', 'available', 'quarantined', 'failed', 'expired'
      )),
      revision bigint not null check (revision >= 1),
      original_filename text not null check (char_length(original_filename) between 1 and 255),
      mime_type text not null check (mime_type in ('audio/mpeg', 'audio/mp4', 'audio/aac')),
      byte_size bigint not null check (byte_size between 1 and 15728640),
      content_sha256 text not null check (length(content_sha256) = 64),
      duration_milliseconds integer not null check (duration_milliseconds between 1 and 180000),
      object_ref text not null check (object_ref like 'tos://%/private/learning-media/%'),
      upload_policy_version text not null,
      retention_policy_version text not null,
      scanner_receipt jsonb check (
        scanner_receipt is null or jsonb_typeof(scanner_receipt) = 'object'
      ),
      failure_code text,
      authorization_expires_at timestamptz not null,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      completed_at timestamptz,
      expires_at timestamptz not null,
      unique (artifact_version_id, artifact_id, user_id),
      unique (user_id, object_ref),
      check (expires_at > created_at),
      check (
        (state = 'available' and scanner_receipt is not null and completed_at is not null and failure_code is null)
        or (state in ('quarantined', 'failed') and failure_code is not null)
        or (state in ('awaiting_upload', 'scanning') and completed_at is null)
        or (state = 'expired' and failure_code = 'unbound_retention_expired')
      )
    )
    """,
    """
    create index learning_media_upload_owner_state_idx
      on learning.learning_media_upload_sessions(user_id, state, updated_at)
    """,
    """
    create index learning_media_upload_retention_due_idx
      on learning.learning_media_upload_sessions(expires_at, upload_session_id)
      where state in ('awaiting_upload', 'scanning', 'available')
    """,
    """
    create table learning.learning_media_audit_events (
      event_id uuid primary key,
      upload_session_id uuid not null references learning.learning_media_upload_sessions(upload_session_id),
      user_id uuid not null references learner.users(user_id),
      sequence_no bigint not null check (sequence_no >= 1),
      event_type text not null,
      from_state text,
      to_state text not null,
      evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
      occurred_at timestamptz not null,
      unique (upload_session_id, sequence_no)
    )
    """,
    """
    create table learning.learning_media_artifact_versions (
      artifact_version_id uuid primary key,
      artifact_id uuid not null,
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      purpose text not null check (purpose = 'reflection_voice'),
      state text not null check (state in ('available', 'deleted')),
      object_ref text,
      mime_type text not null,
      byte_size bigint not null check (byte_size > 0),
      content_sha256 text not null check (length(content_sha256) = 64),
      duration_milliseconds integer not null check (duration_milliseconds between 1 and 180000),
      scanner_receipt jsonb not null check (jsonb_typeof(scanner_receipt) = 'object'),
      retention_policy_version text not null,
      source_upload_session_id uuid not null,
      supersedes_artifact_version_id uuid,
      created_at timestamptz not null,
      unique (artifact_id, version_no),
      unique (artifact_version_id, artifact_id, user_id),
      foreign key (source_upload_session_id)
        references learning.learning_media_upload_sessions(upload_session_id),
      foreign key (supersedes_artifact_version_id, artifact_id, user_id)
        references learning.learning_media_artifact_versions(artifact_version_id, artifact_id, user_id),
      check (
        (version_no = 1 and supersedes_artifact_version_id is null and state = 'available' and object_ref is not null)
        or (version_no > 1 and supersedes_artifact_version_id is not null)
      ),
      check ((state = 'deleted') = (object_ref is null))
    )
    """,
    """
    create unique index learning_media_artifact_one_successor_idx
      on learning.learning_media_artifact_versions(supersedes_artifact_version_id)
      where supersedes_artifact_version_id is not null
    """,
    """
    create table learning.learning_media_bindings (
      binding_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      artifact_version_id uuid not null,
      artifact_id uuid not null,
      entity_type text not null check (entity_type in ('xingce_reflection', 'essay_reflection')),
      entity_version_id uuid not null,
      purpose text not null check (purpose = 'reflection_voice'),
      created_at timestamptz not null,
      unique (artifact_version_id),
      foreign key (artifact_version_id, artifact_id, user_id)
        references learning.learning_media_artifact_versions(artifact_version_id, artifact_id, user_id)
    )
    """,
    """
    create function learning.validate_learning_media_binding()
    returns trigger as $$
    begin
      if new.entity_type = 'xingce_reflection' then
        if not exists (
          select 1 from learning.xingce_reflection_versions reflection
          where reflection.reflection_version_id = new.entity_version_id
            and reflection.user_id = new.user_id
        ) then
          raise exception 'learning media xingce reflection binding is invalid';
        end if;
      elsif new.entity_type = 'essay_reflection' then
        if not exists (
          select 1 from learning.essay_learning_artifact_versions artifact
          where artifact.artifact_version_id = new.entity_version_id
            and artifact.user_id = new.user_id
            and artifact.artifact_type = 'reflection'
        ) then
          raise exception 'learning media essay reflection binding is invalid';
        end if;
      end if;
      return new;
    end;
    $$ language plpgsql
    """,
    """
    create constraint trigger learning_media_binding_entity_guard
      after insert on learning.learning_media_bindings
      deferrable initially deferred
      for each row execute function learning.validate_learning_media_binding()
    """,
    """
    create function learning.reject_learning_media_fact_mutation()
    returns trigger as $$
    begin
      raise exception 'learning media facts are append-only';
    end;
    $$ language plpgsql
    """,
    """
    create trigger learning_media_audit_events_append_only
      before update or delete on learning.learning_media_audit_events
      for each row execute function learning.reject_learning_media_fact_mutation()
    """,
    """
    create trigger learning_media_artifact_versions_append_only
      before update or delete on learning.learning_media_artifact_versions
      for each row execute function learning.reject_learning_media_fact_mutation()
    """,
    """
    create trigger learning_media_bindings_append_only
      before update or delete on learning.learning_media_bindings
      for each row execute function learning.reject_learning_media_fact_mutation()
    """,
)
