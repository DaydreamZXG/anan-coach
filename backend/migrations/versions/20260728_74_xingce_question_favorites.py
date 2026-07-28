"""Add account-scoped Xingce question favorites and exact-replay receipts.

Revision ID: 20260728_74
Revises: 20260728_73
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260728_74"
down_revision: str | Sequence[str] | None = "20260728_73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UP_STATEMENTS = (
    """
    create table if not exists learning.xingce_question_favorite_states (
      user_id uuid not null references learner.users(user_id),
      question_id text not null references product.questions(question_id),
      saved_content_version_id text,
      source_session_id uuid,
      favorited boolean not null,
      revision bigint not null check (revision >= 1),
      favorited_at timestamptz,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      primary key (user_id, question_id),
      constraint xingce_question_favorite_state_shape_check check (
        updated_at >= created_at
        and (
          (
            favorited
            and saved_content_version_id is not null
            and source_session_id is not null
            and favorited_at is not null
          )
          or (
            not favorited
            and saved_content_version_id is null
            and source_session_id is null
            and favorited_at is null
          )
        )
      )
    )
    """,
    """
    create index if not exists learning_xingce_favorites_page_idx
      on learning.xingce_question_favorite_states (
        user_id, favorited_at desc, question_id
      )
      where favorited
    """,
    """
    create table if not exists learning.xingce_question_favorite_commands (
      user_id uuid not null references learner.users(user_id),
      command_id text not null,
      question_id text not null references product.questions(question_id),
      request_fingerprint text not null check (length(request_fingerprint) = 64),
      response_payload jsonb not null check (
        jsonb_typeof(response_payload) = 'object'
      ),
      created_at timestamptz not null,
      primary key (user_id, command_id),
      constraint xingce_question_favorite_command_shape_check check (
        char_length(command_id) between 8 and 160
      )
    )
    """,
    """
    create index if not exists learning_xingce_favorite_commands_created_idx
      on learning.xingce_question_favorite_commands (
        user_id, created_at, command_id
      )
    """,
    """
    do $$
    begin
      if not exists (
        select 1
        from pg_constraint
        where conname = 'xingce_question_favorite_saved_content_fk'
          and conrelid =
              'learning.xingce_question_favorite_states'::regclass
      ) then
        alter table learning.xingce_question_favorite_states
          add constraint xingce_question_favorite_saved_content_fk
          foreign key (question_id, saved_content_version_id)
          references product.question_content_versions(
            question_id, content_version_id
          );
      end if;
    end
    $$
    """,
    """
    do $$
    begin
      if not exists (
        select 1
        from pg_constraint
        where conname = 'xingce_question_favorite_source_session_fk'
          and conrelid =
              'learning.xingce_question_favorite_states'::regclass
      ) then
        alter table learning.xingce_question_favorite_states
          add constraint xingce_question_favorite_source_session_fk
          foreign key (source_session_id, user_id)
          references learning.xingce_task_sessions(session_id, user_id);
      end if;
    end
    $$
    """,
    """
    create or replace function learning.enforce_xingce_question_favorite_transition()
    returns trigger
    language plpgsql
    as $$
    begin
      if tg_op = 'INSERT' then
        if new.revision <> 1 then
          raise exception using
            errcode = '23514',
            message = 'a new Xingce favorite state must start at revision 1';
        end if;
        return new;
      end if;

      if new.user_id <> old.user_id
         or new.question_id <> old.question_id
         or new.created_at <> old.created_at
         or new.revision <> old.revision + 1
         or new.favorited = old.favorited
         or new.updated_at < old.updated_at then
        raise exception using
          errcode = '23514',
          message = 'invalid Xingce favorite state transition';
      end if;
      return new;
    end
    $$
    """,
    """
    drop trigger if exists xingce_question_favorite_state_transition
      on learning.xingce_question_favorite_states
    """,
    """
    create trigger xingce_question_favorite_state_transition
      before insert or update
      on learning.xingce_question_favorite_states
      for each row execute function
        learning.enforce_xingce_question_favorite_transition()
    """,
    """
    drop trigger if exists xingce_question_favorite_state_delete_guard
      on learning.xingce_question_favorite_states
    """,
    """
    create trigger xingce_question_favorite_state_delete_guard
      before delete
      on learning.xingce_question_favorite_states
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    drop trigger if exists xingce_question_favorite_commands_append_only
      on learning.xingce_question_favorite_commands
    """,
    """
    create trigger xingce_question_favorite_commands_append_only
      before update or delete
      on learning.xingce_question_favorite_commands
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
)


def _grant_runtime_access() -> None:
    bind = op.get_bind()
    role_exists = bind.execute(
        text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
    ).scalar_one()
    if not role_exists:
        return
    bind.exec_driver_sql(
        "grant select, insert, update, delete on "
        "learning.xingce_question_favorite_states to gongkao_api"
    )
    bind.exec_driver_sql(
        "grant select, insert, update, delete on "
        "learning.xingce_question_favorite_commands to gongkao_api"
    )


def upgrade() -> None:
    for statement in _UP_STATEMENTS:
        op.execute(statement)
    _grant_runtime_access()


def downgrade() -> None:
    has_facts = op.get_bind().execute(
        text(
            """
            select
              exists (
                select 1
                from learning.xingce_question_favorite_states
              )
              or exists (
                select 1
                from learning.xingce_question_favorite_commands
              )
            """
        )
    ).scalar_one()
    if has_facts:
        raise RuntimeError(
            "XINGCE-QUESTION-FAVORITE@v1 facts exist; "
            "use a forward compatibility migration"
        )
    op.execute(
        "drop table if exists learning.xingce_question_favorite_commands"
    )
    op.execute("drop table if exists learning.xingce_question_favorite_states")
    op.execute(
        "drop function if exists "
        "learning.enforce_xingce_question_favorite_transition()"
    )
