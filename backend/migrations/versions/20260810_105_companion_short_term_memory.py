"""Add page-context-scoped short-term memory for Companion.

Revision ID: 20260810_105
Revises: 20260809_104
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260810_105"
down_revision: str | Sequence[str] | None = "20260809_104"
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
        create table platform.companion_short_term_memory_turns (
          turn_id uuid primary key,
          user_id uuid not null references learner.users(user_id) on delete cascade,
          context_scope_fingerprint char(64) not null check (
            context_scope_fingerprint ~ '^[0-9a-f]{64}$'
          ),
          source_message_fingerprint char(64) not null check (
            source_message_fingerprint ~ '^[0-9a-f]{64}$'
          ),
          user_text text not null check (char_length(user_text) between 1 and 800),
          assistant_text text not null check (
            char_length(assistant_text) between 1 and 2000
          ),
          created_at timestamptz not null,
          expires_at timestamptz not null,
          unique (
            user_id, context_scope_fingerprint, source_message_fingerprint
          ),
          check (expires_at > created_at)
        )
        """
    )
    op.execute(
        """
        create index companion_short_term_memory_scope_created_idx
          on platform.companion_short_term_memory_turns (
            user_id, context_scope_fingerprint, created_at desc
          )
        """
    )
    op.execute(
        """
        create index companion_short_term_memory_expiry_idx
          on platform.companion_short_term_memory_turns (expires_at)
        """
    )
    op.execute(
        """
        create function platform.erase_companion_short_term_memory_on_account_disable()
        returns trigger language plpgsql as $$
        begin
          if new.account_status = 'disabled'
             and old.account_status is distinct from new.account_status then
            delete from platform.companion_short_term_memory_turns
            where user_id = new.user_id;
          end if;
          return new;
        end
        $$
        """
    )
    op.execute(
        """
        create trigger erase_companion_short_term_memory_on_account_disable
        after update of account_status on learner.users
        for each row execute function
          platform.erase_companion_short_term_memory_on_account_disable()
        """
    )
    if _runtime_role_exists():
        op.execute(
            "grant select, insert, update, delete on "
            "platform.companion_short_term_memory_turns to gongkao_api"
        )


def downgrade() -> None:
    op.execute(
        "drop trigger if exists erase_companion_short_term_memory_on_account_disable "
        "on learner.users"
    )
    op.execute(
        "drop function if exists "
        "platform.erase_companion_short_term_memory_on_account_disable()"
    )
    op.execute("drop table if exists platform.companion_short_term_memory_turns")
