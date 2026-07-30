"""Add the fail-closed commercial data publication authority.

Revision ID: 20260730_81
Revises: 20260730_80

The migration intentionally creates no current-state row. The first adoption
must happen while traffic and every worker are stopped, and the publisher must
claim the empty authority with an exact, signed baseline receipt. An empty
authority is therefore fail-closed instead of pretending that unsigned legacy
data is active.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260730_81"
down_revision: str | Sequence[str] | None = "20260730_80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    create table if not exists platform.commercial_data_publication_state (
      singleton_key boolean primary key default true,
      publication_id uuid not null,
      state text not null,
      request_digest text not null,
      request_payload jsonb not null,
      publication_digest text,
      previous_receipt_sha256 text not null,
      active_receipt_sha256 text not null,
      expected_database_revision text not null,
      compatible_application_commits jsonb not null,
      publication_started_at timestamptz not null,
      state_changed_at timestamptz not null,
      activated_at timestamptz,
      constraint commercial_data_publication_state_singleton_check
        check (singleton_key),
      constraint commercial_data_publication_state_state_check
        check (state in ('publishing', 'active')),
      constraint commercial_data_publication_state_request_digest_check
        check (request_digest ~ '^[0-9a-f]{64}$'),
      constraint commercial_data_publication_state_publication_digest_check
        check (
          publication_digest is null
          or publication_digest ~ '^[0-9a-f]{64}$'
        ),
      constraint commercial_data_publication_state_previous_receipt_check
        check (previous_receipt_sha256 ~ '^[0-9a-f]{64}$'),
      constraint commercial_data_publication_state_active_receipt_check
        check (active_receipt_sha256 ~ '^[0-9a-f]{64}$'),
      constraint commercial_data_publication_state_revision_check
        check (
          char_length(btrim(expected_database_revision)) between 1 and 200
        ),
      constraint commercial_data_publication_state_request_schema_check
        check (
          request_payload ->> 'schemaVersion'
            = 'commercial-data-publication-request@v1'
          and request_payload ->> 'expectedDatabaseRevision'
            = expected_database_revision
          and compatible_application_commits = jsonb_build_array(
            request_payload ->> 'candidateApplicationCommit',
            request_payload ->> 'rollbackApplicationCommit'
          )
        ),
      constraint commercial_data_publication_state_commits_check
        check (
          jsonb_typeof(compatible_application_commits) = 'array'
          and jsonb_array_length(compatible_application_commits) = 2
          and compatible_application_commits ->> 0
            ~ '^[0-9a-f]{40,64}$'
          and compatible_application_commits ->> 1
            ~ '^[0-9a-f]{40,64}$'
          and compatible_application_commits ->> 0
            <> compatible_application_commits ->> 1
        ),
      constraint commercial_data_publication_state_time_check
        check (
          state_changed_at >= publication_started_at
          and (activated_at is null or activated_at >= publication_started_at)
        ),
      constraint commercial_data_publication_state_active_contract_check
        check (
          (
            state = 'publishing'
            and activated_at is null
          )
          or (
            state = 'active'
            and activated_at is not null
            and publication_digest is not null
          )
        )
    )
    """,
    """
    create table if not exists platform.commercial_data_publication_transitions (
      transition_id bigint generated always as identity primary key,
      publication_id uuid not null,
      event_kind text not null,
      from_state text,
      to_state text not null,
      request_digest text not null,
      request_payload jsonb not null,
      publication_digest text,
      previous_receipt_sha256 text not null,
      active_receipt_sha256 text not null,
      expected_database_revision text not null,
      compatible_application_commits jsonb not null,
      occurred_at timestamptz not null default transaction_timestamp(),
      constraint commercial_data_publication_transitions_event_check
        check (
          event_kind in (
            'publication_started',
            'publication_resumed',
            'receipt_recorded',
            'receipt_replaced',
            'publication_activated'
          )
        ),
      constraint commercial_data_publication_transitions_from_state_check
        check (from_state is null or from_state in ('publishing', 'active')),
      constraint commercial_data_publication_transitions_to_state_check
        check (to_state in ('publishing', 'active')),
      constraint commercial_data_publication_transitions_request_digest_check
        check (request_digest ~ '^[0-9a-f]{64}$'),
      constraint commercial_data_publication_transitions_publication_digest_check
        check (
          publication_digest is null
          or publication_digest ~ '^[0-9a-f]{64}$'
        ),
      constraint commercial_data_publication_transitions_receipts_check
        check (
          previous_receipt_sha256 ~ '^[0-9a-f]{64}$'
          and active_receipt_sha256 ~ '^[0-9a-f]{64}$'
        ),
      constraint commercial_data_publication_transitions_revision_check
        check (
          char_length(btrim(expected_database_revision)) between 1 and 200
        ),
      constraint commercial_data_publication_transitions_request_schema_check
        check (
          request_payload ->> 'schemaVersion'
            = 'commercial-data-publication-request@v1'
          and request_payload ->> 'expectedDatabaseRevision'
            = expected_database_revision
          and compatible_application_commits = jsonb_build_array(
            request_payload ->> 'candidateApplicationCommit',
            request_payload ->> 'rollbackApplicationCommit'
          )
        ),
      constraint commercial_data_publication_transitions_commits_check
        check (
          jsonb_typeof(compatible_application_commits) = 'array'
          and jsonb_array_length(compatible_application_commits) = 2
          and compatible_application_commits ->> 0
            ~ '^[0-9a-f]{40,64}$'
          and compatible_application_commits ->> 1
            ~ '^[0-9a-f]{40,64}$'
          and compatible_application_commits ->> 0
            <> compatible_application_commits ->> 1
        )
    )
    """,
    """
    create index if not exists commercial_data_publication_transitions_lookup_idx
      on platform.commercial_data_publication_transitions (
        publication_id,
        transition_id
      )
    """,
    """
    create or replace function platform.guard_commercial_data_publication_state()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, platform
    as $$
    begin
      if current_setting(
        'gongkao.commercial_data_publication_writer',
        true
      ) is distinct from 'publisher-v1' then
        raise exception using
          errcode = '42501',
          message =
            'commercial data publication state is publisher-owned';
      end if;
      if tg_op in ('DELETE', 'TRUNCATE') then
        raise exception using
          errcode = '42501',
          message =
            'commercial data publication state cannot be removed';
      end if;
      if tg_op = 'UPDATE' and new.singleton_key is distinct from old.singleton_key then
        raise exception using
          errcode = '23514',
          message = 'commercial data publication singleton identity is immutable';
      end if;
      if tg_op = 'INSERT' then
        return new;
      end if;
      return new;
    end
    $$
    """,
    """
    create or replace function platform.guard_commercial_data_publication_transition()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, platform
    as $$
    begin
      if tg_op <> 'INSERT' then
        raise exception using
          errcode = '42501',
          message = 'commercial data publication transitions are append-only';
      end if;
      if current_setting(
        'gongkao.commercial_data_publication_writer',
        true
      ) is distinct from 'publisher-v1' then
        raise exception using
          errcode = '42501',
          message =
            'commercial data publication transitions are publisher-owned';
      end if;
      return new;
    end
    $$
    """,
    """
    drop trigger if exists commercial_data_publication_state_write_guard
      on platform.commercial_data_publication_state
    """,
    """
    create trigger commercial_data_publication_state_write_guard
      before insert or update or delete
      on platform.commercial_data_publication_state
      for each row execute function
        platform.guard_commercial_data_publication_state()
    """,
    """
    drop trigger if exists commercial_data_publication_state_truncate_guard
      on platform.commercial_data_publication_state
    """,
    """
    create trigger commercial_data_publication_state_truncate_guard
      before truncate
      on platform.commercial_data_publication_state
      for each statement execute function
        platform.guard_commercial_data_publication_state()
    """,
    """
    drop trigger if exists commercial_data_publication_transition_write_guard
      on platform.commercial_data_publication_transitions
    """,
    """
    create trigger commercial_data_publication_transition_write_guard
      before insert or update or delete
      on platform.commercial_data_publication_transitions
      for each row execute function
        platform.guard_commercial_data_publication_transition()
    """,
    """
    drop trigger if exists commercial_data_publication_transition_truncate_guard
      on platform.commercial_data_publication_transitions
    """,
    """
    create trigger commercial_data_publication_transition_truncate_guard
      before truncate
      on platform.commercial_data_publication_transitions
      for each statement execute function
        platform.guard_commercial_data_publication_transition()
    """,
    """
    revoke all on platform.commercial_data_publication_state from public
    """,
    """
    revoke all on platform.commercial_data_publication_transitions from public
    """,
    """
    revoke all on sequence
      platform.commercial_data_publication_transitions_transition_id_seq
      from public
    """,
    """
    revoke all on function
      platform.guard_commercial_data_publication_state()
      from public
    """,
    """
    revoke all on function
      platform.guard_commercial_data_publication_transition()
      from public
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)
    if _runtime_role_exists():
        op.execute("grant select on platform.commercial_data_publication_state to gongkao_api")
        op.execute(
            "grant select on platform.commercial_data_publication_transitions to gongkao_api"
        )


def downgrade() -> None:
    raise RuntimeError(
        "commercial data publication authority is forward-only; "
        "publish a forward compatibility migration"
    )


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')"))
        .scalar_one()
    )
