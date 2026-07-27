"""Add account-bound OpeniLink Hub bot authorizations.

Revision ID: 20260727_69
Revises: 20260727_68
"""

from alembic import op


revision = "20260727_69"
down_revision = "20260727_68"
branch_labels = None
depends_on = None


_STATEMENTS = (
    """
    create table messaging.ilink_hub_bot_links (
      authorization_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      state text not null check (
        state in ('waiting_scan', 'scanned', 'connected', 'reauth_required', 'revoked')
      ),
      encrypted_bind_session_id text,
      encrypted_qr_content text,
      encryption_key_id text not null,
      hub_bot_id text,
      expires_at timestamptz,
      revision integer not null check (revision >= 1),
      last_command_id text not null,
      failure_code text,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      connected_at timestamptz,
      revoked_at timestamptz,
      unique (user_id),
      unique (hub_bot_id),
      check (
        (
          state in ('waiting_scan', 'scanned')
          and encrypted_bind_session_id is not null
          and encrypted_qr_content is not null
          and expires_at is not null
          and hub_bot_id is null
          and connected_at is null
          and revoked_at is null
        )
        or
        (
          state = 'connected'
          and encrypted_bind_session_id is null
          and encrypted_qr_content is null
          and expires_at is null
          and hub_bot_id is not null
          and connected_at is not null
          and revoked_at is null
        )
        or
        (
          state = 'reauth_required'
          and encrypted_bind_session_id is null
          and encrypted_qr_content is null
          and expires_at is null
          and hub_bot_id is not null
          and connected_at is not null
          and failure_code is not null
          and revoked_at is null
        )
        or
        (
          state = 'revoked'
          and encrypted_bind_session_id is null
          and encrypted_qr_content is null
          and expires_at is null
          and hub_bot_id is null
          and revoked_at is not null
        )
      )
    )
    """,
    """
    create index ilink_hub_bot_links_state_idx
      on messaging.ilink_hub_bot_links (state, updated_at)
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
    """,
)


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .exec_driver_sql("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
        .scalar_one()
    )


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.get_bind().exec_driver_sql(statement.replace("%", "%%"))
    if _runtime_role_exists():
        op.get_bind().exec_driver_sql(
            "grant select, insert, update, delete on messaging.ilink_hub_bot_links to gongkao_api"
        )


def downgrade() -> None:
    op.execute("drop table if exists messaging.ilink_hub_bot_links")
    op.execute(
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
        """
    )
