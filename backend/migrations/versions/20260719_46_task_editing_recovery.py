"""Add single-device task editing leases and conflict decisions.

Revision ID: 20260719_46
Revises: 20260719_45
"""

from alembic import op


revision = "20260719_46"
down_revision = "20260719_45"
branch_labels = None
depends_on = None


_TEST_STATEMENTS = (
    """
    create function learning.reject_task_edit_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      raise exception using errcode = '23514',
        message = 'task editing facts are append-only';
    end
    $$
    """,
    """
    create table learning.task_edit_leases (
      resource_type text not null check (
        resource_type in ('xingce_task_session', 'mock_exam_session')
      ),
      resource_id uuid not null,
      user_id uuid not null references learner.users(user_id),
      lease_id uuid not null unique,
      generation bigint not null check (generation >= 1),
      device_id text not null check (
        char_length(device_id) between 24 and 120
        and device_id ~ '^[A-Za-z0-9._:-]+$'
      ),
      device_label text not null check (char_length(device_label) between 2 and 120),
      reconciliation_state text not null check (
        reconciliation_state in ('clean', 'choice_required')
      ),
      local_base_revision bigint check (local_base_revision >= 1),
      server_revision bigint not null check (server_revision >= 1),
      acquired_at timestamptz not null,
      last_seen_at timestamptz not null,
      taken_over_at timestamptz,
      primary key (user_id, resource_type, resource_id),
      check (
        (reconciliation_state = 'clean')
        or (local_base_revision is not null)
      )
    )
    """,
    """
    create index task_edit_lease_device_idx
      on learning.task_edit_leases(user_id, device_id, last_seen_at desc)
    """,
    """
    create table learning.task_edit_events (
      event_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      resource_type text not null check (
        resource_type in ('xingce_task_session', 'mock_exam_session')
      ),
      resource_id uuid not null,
      lease_id uuid not null,
      generation bigint not null check (generation >= 1),
      device_id text not null,
      device_label text not null,
      event_type text not null check (
        event_type in (
          'acquired', 'reconnected', 'takeover_requested', 'taken_over',
          'conflict_detected', 'conflict_resolved', 'write_authorized'
        )
      ),
      resource_revision bigint not null check (resource_revision >= 1),
      local_base_revision bigint check (local_base_revision >= 1),
      resolution text check (resolution in ('use_server', 'use_this_device')),
      metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
      created_at timestamptz not null
    )
    """,
    """
    create index task_edit_event_resource_idx
      on learning.task_edit_events(user_id, resource_type, resource_id, created_at desc)
    """,
    """
    create trigger task_edit_events_append_only
      before update or delete on learning.task_edit_events
      for each row execute function learning.reject_task_edit_fact_mutation()
    """,
)


def upgrade() -> None:
    for statement in _TEST_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "drop trigger if exists task_edit_events_append_only on learning.task_edit_events"
    )
    op.execute("drop table if exists learning.task_edit_events")
    op.execute("drop table if exists learning.task_edit_leases")
    op.execute("drop function if exists learning.reject_task_edit_fact_mutation()")
