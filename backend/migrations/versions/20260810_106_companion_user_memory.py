"""Add explicit user-managed long-term memory for Companion.

Revision ID: 20260810_106
Revises: 20260810_105
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260810_106"
down_revision: str | Sequence[str] | None = "20260810_105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        create table platform.companion_memory_settings (
          user_id uuid primary key references learner.users(user_id) on delete cascade,
          long_term_enabled boolean not null default true,
          updated_at timestamptz not null
        )
        """
    )
    op.execute(
        """
        create table platform.companion_memory_items (
          memory_id uuid primary key,
          user_id uuid not null references learner.users(user_id) on delete cascade,
          source_command_fingerprint char(64) not null check (
            source_command_fingerprint ~ '^[0-9a-f]{64}$'
          ),
          request_fingerprint char(64) not null check (
            request_fingerprint ~ '^[0-9a-f]{64}$'
          ),
          category text not null check (
            category in (
              'explanation_preference', 'study_constraint', 'personal_note'
            )
          ),
          content text not null check (char_length(content) between 1 and 240),
          source text not null default 'explicit_user' check (source = 'explicit_user'),
          revision integer not null default 1 check (revision >= 1),
          created_at timestamptz not null,
          updated_at timestamptz not null,
          unique (user_id, source_command_fingerprint),
          check (updated_at >= created_at)
        )
        """
    )
    op.execute(
        """
        create index companion_memory_items_user_updated_idx
          on platform.companion_memory_items (user_id, updated_at desc)
        """
    )
    op.execute(
        """
        create function platform.erase_companion_user_memory_on_account_disable()
        returns trigger language plpgsql as $$
        begin
          if new.account_status = 'disabled'
             and old.account_status is distinct from new.account_status then
            delete from platform.companion_memory_items where user_id = new.user_id;
            delete from platform.companion_memory_settings where user_id = new.user_id;
          end if;
          return new;
        end
        $$
        """
    )
    op.execute(
        """
        create trigger erase_companion_user_memory_on_account_disable
        after update of account_status on learner.users
        for each row execute function
          platform.erase_companion_user_memory_on_account_disable()
        """
    )
    if _runtime_role_exists():
        op.execute(
            "grant select, insert, update, delete on "
            "platform.companion_memory_settings, "
            "platform.companion_memory_items to gongkao_api"
        )


def downgrade() -> None:
    op.execute(
        "drop trigger if exists erase_companion_user_memory_on_account_disable "
        "on learner.users"
    )
    op.execute(
        "drop function if exists "
        "platform.erase_companion_user_memory_on_account_disable()"
    )
    op.execute("drop table if exists platform.companion_memory_items")
    op.execute("drop table if exists platform.companion_memory_settings")
