"""Add owner-isolated WeKnora private-knowledge projections.

Revision ID: 20260721_50
Revises: 20260721_49
"""

from alembic import op


revision = "20260721_50"
down_revision = "20260721_49"
branch_labels = None
depends_on = None


_STATEMENTS = (
    """
    create table learning.private_knowledge_projection_spaces (
      space_id uuid primary key,
      owner_user_id uuid not null references learner.users(user_id),
      provider_release text not null,
      deterministic_name text not null,
      remote_knowledge_base_id text,
      state text not null check (state in ('provisioning', 'ready', 'failed', 'deleted')),
      revision integer not null check (revision >= 1),
      last_error_code text,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      deleted_at timestamptz,
      unique (owner_user_id),
      unique (space_id, owner_user_id),
      unique (deterministic_name),
      unique (remote_knowledge_base_id),
      check ((state = 'deleted') = (deleted_at is not null)),
      check ((state = 'failed') = (last_error_code is not null))
    )
    """,
    """
    create table learning.private_knowledge_chunk_projections (
      projection_id uuid primary key,
      owner_user_id uuid not null,
      space_id uuid not null,
      source_id uuid not null,
      source_version_id uuid not null,
      chunk_id uuid not null,
      provider_release text not null,
      deterministic_title text not null,
      content_sha256 char(64) not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
      remote_knowledge_id text,
      state text not null check (
        state in ('queued', 'indexing', 'ready', 'deleting', 'deleted', 'failed')
      ),
      last_error_code text,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      ready_at timestamptz,
      deleted_at timestamptz,
      unique (owner_user_id, chunk_id),
      unique (deterministic_title),
      unique (remote_knowledge_id),
      foreign key (space_id, owner_user_id)
        references learning.private_knowledge_projection_spaces(space_id, owner_user_id),
      foreign key (source_version_id, owner_user_id)
        references learning.private_knowledge_source_versions(source_version_id, owner_user_id)
        on delete cascade,
      foreign key (chunk_id, owner_user_id)
        references learning.private_knowledge_chunks(chunk_id, owner_user_id)
        on delete cascade,
      check ((state = 'ready') = (ready_at is not null)),
      check ((state = 'deleted') = (deleted_at is not null)),
      check ((state = 'failed') = (last_error_code is not null)),
      check (state in ('queued', 'failed') or remote_knowledge_id is not null)
    )
    """,
    """
    create index private_knowledge_chunk_projection_scope_idx
      on learning.private_knowledge_chunk_projections
      (owner_user_id, source_version_id, state)
    """,
    """
    create index private_knowledge_chunk_projection_remote_idx
      on learning.private_knowledge_chunk_projections
      (owner_user_id, remote_knowledge_id)
      where remote_knowledge_id is not null
    """,
    """
    create or replace function learning.require_private_projection_erasure()
    returns trigger language plpgsql as $$
    begin
      if exists (
        select 1
        from learning.private_knowledge_chunk_projections projection
        where projection.owner_user_id = old.owner_user_id
          and projection.source_version_id = old.source_version_id
          and projection.state <> 'deleted'
      ) then
        raise exception using errcode = '23514',
          message = 'private knowledge remote projection must be deleted first';
      end if;
      return old;
    end;
    $$
    """,
    """
    create trigger private_knowledge_projection_erasure_guard
      before delete on learning.private_knowledge_source_versions
      for each row execute function learning.require_private_projection_erasure()
    """,
)

_TEST_STATEMENTS = _STATEMENTS


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        """
        do $$ begin
          if exists (
            select 1 from learning.private_knowledge_chunk_projections
            where state <> 'deleted'
          ) then
            raise exception 'active WeKnora projections exist; use a forward repair migration';
          end if;
        end $$
        """
    )
    op.execute(
        "drop trigger private_knowledge_projection_erasure_guard "
        "on learning.private_knowledge_source_versions"
    )
    op.execute("drop function learning.require_private_projection_erasure()")
    op.execute("drop table learning.private_knowledge_chunk_projections")
    op.execute("drop table learning.private_knowledge_projection_spaces")
