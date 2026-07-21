"""Add the account-bound iLink photo-question channel.

Revision ID: 20260721_49
Revises: 20260721_48
"""

from alembic import op


revision = "20260721_49"
down_revision = "20260721_48"
branch_labels = None
depends_on = None


_STATEMENTS = (
    "create schema if not exists messaging",
    """
    create table messaging.ilink_channel_links (
      link_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      state text not null check (state in ('pending', 'bound', 'revoked')),
      bot_account_id text,
      sender_fingerprint char(64),
      encrypted_sender_id text,
      encrypted_binding_code text,
      encryption_key_id text not null,
      binding_code_hash char(64),
      code_expires_at timestamptz,
      code_consumed_at timestamptz,
      revision integer not null check (revision >= 1),
      last_command_id text not null,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      revoked_at timestamptz,
      unique (user_id),
      check (
        (state = 'pending' and encrypted_binding_code is not null
          and binding_code_hash is not null and code_expires_at is not null
          and code_consumed_at is null and sender_fingerprint is null
          and encrypted_sender_id is null)
        or
        (state = 'bound' and bot_account_id is not null
          and sender_fingerprint is not null and encrypted_sender_id is not null
          and encrypted_binding_code is null and binding_code_hash is null
          and code_expires_at is null and code_consumed_at is not null)
        or
        (state = 'revoked' and encrypted_binding_code is null
          and binding_code_hash is null and code_expires_at is null
          and sender_fingerprint is null and encrypted_sender_id is null
          and revoked_at is not null)
      )
    )
    """,
    """
    create unique index ilink_channel_links_active_sender_uk
      on messaging.ilink_channel_links (bot_account_id, sender_fingerprint)
      where state = 'bound'
    """,
    """
    create table messaging.ilink_photo_queries (
      query_id uuid primary key,
      link_id uuid not null references messaging.ilink_channel_links(link_id)
        on delete cascade,
      user_id uuid not null references learner.users(user_id),
      bot_account_id text not null,
      source_message_id text not null,
      source_fingerprint char(64),
      state text not null check (
        state in ('received', 'processing', 'answered', 'failed', 'deleted')
      ),
      answer_kind text not null check (
        answer_kind in ('none', 'authorized_exact', 'similar_reference',
          'ai_candidate', 'ambiguous', 'material_required')
      ),
      track text check (track is null or track in ('xingce', 'essay')),
      encrypted_context_token text,
      encryption_key_id text not null,
      transient_object_ref text,
      mime_type text,
      recognized_payload jsonb,
      answer_payload jsonb,
      content_version_id text,
      authorization_snapshot_ref text,
      model_provenance jsonb,
      failure_code text,
      reply_client_id text,
      reply_state text not null default 'not_sent' check (
        reply_state in ('not_sent', 'sending', 'sent', 'uncertain')
      ),
      version integer not null check (version >= 1),
      created_at timestamptz not null,
      updated_at timestamptz not null,
      answered_at timestamptz,
      deleted_at timestamptz,
      unique (bot_account_id, source_message_id),
      check (state <> 'answered' or answer_kind <> 'none'),
      check (state <> 'failed' or failure_code is not null),
      check (state <> 'deleted' or deleted_at is not null)
    )
    """,
    """
    create index ilink_photo_queries_user_created_idx
      on messaging.ilink_photo_queries (user_id, created_at desc)
    """,
    """
    create table messaging.ilink_photo_collections (
      collection_id uuid primary key,
      query_id uuid not null references messaging.ilink_photo_queries(query_id)
        on delete cascade,
      user_id uuid not null references learner.users(user_id),
      state text not null check (
        state in ('collected', 'queued_for_reattempt', 'deleted')
      ),
      command_id text not null,
      collected_at timestamptz not null,
      reattempt_requested_at timestamptz,
      deleted_at timestamptz,
      updated_at timestamptz not null,
      unique (user_id, query_id),
      check ((state = 'queued_for_reattempt') = (reattempt_requested_at is not null)),
      check ((state = 'deleted') = (deleted_at is not null))
    )
    """,
    """
    create table messaging.ilink_command_receipts (
      command_id text primary key,
      command_type text not null,
      user_id uuid references learner.users(user_id),
      request_fingerprint char(64) not null,
      result_payload jsonb not null,
      created_at timestamptz not null
    )
    """,
    """
    create table messaging.ilink_worker_cursors (
      bot_account_id text primary key,
      encrypted_updates_buf text not null,
      encryption_key_id text not null,
      revision integer not null check (revision >= 1),
      updated_at timestamptz not null
    )
    """,
    """
    create table messaging.ilink_object_deletion_queue (
      object_ref text primary key,
      state text not null default 'pending' check (
        state in ('pending', 'deleting', 'failed')
      ),
      attempt_count integer not null default 0 check (attempt_count >= 0),
      failure_code text,
      created_at timestamptz not null,
      updated_at timestamptz not null
    )
    """,
    """
    create table messaging.ilink_channel_events (
      event_id uuid primary key,
      aggregate_type text not null check (
        aggregate_type in ('link', 'query', 'collection', 'worker')
      ),
      aggregate_id text not null,
      user_id uuid references learner.users(user_id),
      event_type text not null,
      from_state text,
      to_state text,
      correlation_fingerprint char(64),
      details jsonb not null default '{}'::jsonb,
      created_at timestamptz not null
    )
    """,
    """
    create index ilink_channel_events_aggregate_idx
      on messaging.ilink_channel_events (aggregate_type, aggregate_id, created_at)
    """,
    """
    create or replace function messaging.erase_ilink_on_account_disable()
    returns trigger language plpgsql as $$
    begin
      if new.account_status = 'disabled'
         and old.account_status is distinct from new.account_status then
        insert into messaging.ilink_object_deletion_queue (
          object_ref, state, attempt_count, created_at, updated_at
        )
        select transient_object_ref, 'pending', 0, now(), now()
        from messaging.ilink_photo_queries
        where user_id = new.user_id and transient_object_ref is not null
        on conflict (object_ref) do nothing;
        delete from messaging.ilink_command_receipts where user_id = new.user_id;
        delete from messaging.ilink_channel_events where user_id = new.user_id;
        delete from messaging.ilink_photo_collections where user_id = new.user_id;
        delete from messaging.ilink_photo_queries where user_id = new.user_id;
        delete from messaging.ilink_channel_links where user_id = new.user_id;
      end if;
      return new;
    end
    $$
    """,
    """
    create trigger learner_users_erase_ilink_trigger
      after update of account_status on learner.users
      for each row execute function messaging.erase_ilink_on_account_disable()
    """,
)


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .exec_driver_sql(
            "select exists (select 1 from pg_roles where rolname = 'gongkao_api')"
        )
        .scalar_one()
    )


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.get_bind().exec_driver_sql(statement.replace("%", "%%"))
    if _runtime_role_exists():
        op.get_bind().exec_driver_sql("grant usage on schema messaging to gongkao_api")
        op.get_bind().exec_driver_sql(
            "grant select, insert, update, delete on all tables in schema messaging to gongkao_api"
        )


def downgrade() -> None:
    op.execute("drop trigger if exists learner_users_erase_ilink_trigger on learner.users")
    op.execute("drop function if exists messaging.erase_ilink_on_account_disable()")
    for table in (
        "ilink_channel_events",
        "ilink_object_deletion_queue",
        "ilink_worker_cursors",
        "ilink_command_receipts",
        "ilink_photo_collections",
        "ilink_photo_queries",
        "ilink_channel_links",
    ):
        op.execute(f"drop table if exists messaging.{table}")
