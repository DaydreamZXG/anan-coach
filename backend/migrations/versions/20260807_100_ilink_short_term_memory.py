"""Add expiring account-scoped short-term memory for the WeChat channel.

Revision ID: 20260807_100
Revises: 20260807_99
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260807_100"
down_revision: str | Sequence[str] | None = "20260807_99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ERASE_FUNCTION_WITH_MEMORY = """
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
    delete from messaging.ilink_short_term_memory_turns where user_id = new.user_id;
    delete from messaging.ilink_hub_bot_links where user_id = new.user_id;
    delete from messaging.ilink_command_receipts where user_id = new.user_id;
    delete from messaging.ilink_channel_events where user_id = new.user_id;
    delete from messaging.ilink_photo_collections where user_id = new.user_id;
    delete from messaging.ilink_photo_queries where user_id = new.user_id;
    delete from messaging.ilink_channel_links where user_id = new.user_id;
  end if;
  return new;
end
$$
"""

_ERASE_FUNCTION_WITHOUT_MEMORY = """
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
    delete from messaging.ilink_hub_bot_links where user_id = new.user_id;
    delete from messaging.ilink_command_receipts where user_id = new.user_id;
    delete from messaging.ilink_channel_events where user_id = new.user_id;
    delete from messaging.ilink_photo_collections where user_id = new.user_id;
    delete from messaging.ilink_photo_queries where user_id = new.user_id;
    delete from messaging.ilink_channel_links where user_id = new.user_id;
  end if;
  return new;
end
$$
"""


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .exec_driver_sql(
            "select exists (select 1 from pg_roles where rolname = 'gongkao_api')"
        )
        .scalar_one()
    )


def upgrade() -> None:
    op.execute(
        """
        create table messaging.ilink_short_term_memory_turns (
          turn_id uuid primary key,
          user_id uuid not null references learner.users(user_id) on delete cascade,
          source_message_fingerprint char(64) not null,
          user_text text not null check (
            char_length(user_text) between 1 and 800
          ),
          assistant_text text not null check (
            char_length(assistant_text) between 1 and 2000
          ),
          created_at timestamptz not null,
          expires_at timestamptz not null,
          unique (user_id, source_message_fingerprint),
          check (expires_at > created_at)
        )
        """
    )
    op.execute(
        """
        create index ilink_short_term_memory_user_created_idx
          on messaging.ilink_short_term_memory_turns (user_id, created_at desc)
        """
    )
    op.execute(
        """
        create index ilink_short_term_memory_expiry_idx
          on messaging.ilink_short_term_memory_turns (expires_at)
        """
    )
    op.execute(_ERASE_FUNCTION_WITH_MEMORY)
    if _runtime_role_exists():
        op.execute(
            "grant select, insert, update, delete on "
            "messaging.ilink_short_term_memory_turns to gongkao_api"
        )


def downgrade() -> None:
    op.execute(_ERASE_FUNCTION_WITHOUT_MEMORY)
    op.execute("drop table if exists messaging.ilink_short_term_memory_turns")
