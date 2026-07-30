"""Add audited business windows to position-rule releases.

Revision ID: 20260730_78
Revises: 20260730_77
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260730_78"
down_revision: str | Sequence[str] | None = "20260730_77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UP_STATEMENTS = (
    """
    create table product.position_release_windows (
      window_id uuid primary key,
      release_id uuid not null
        references product.position_rule_releases(release_id)
        on delete cascade,
      window_kind text not null,
      sequence_no smallint not null,
      opens_at timestamptz not null,
      closes_at timestamptz not null,
      official_source_url text not null,
      official_source_title text not null,
      official_source_published_at timestamptz not null,
      official_source_received_at timestamptz not null,
      official_source_sha256 text not null,
      official_source_receipt text not null,
      created_at timestamptz not null default transaction_timestamp(),
      constraint position_release_windows_kind_check
        check (window_kind in ('primary', 'supplemental')),
      constraint position_release_windows_sequence_check
        check (
          (window_kind = 'primary' and sequence_no = 1)
          or (window_kind = 'supplemental' and sequence_no >= 1)
        ),
      constraint position_release_windows_time_check
        check (opens_at < closes_at),
      constraint position_release_windows_source_url_check
        check (
          official_source_url ~ '^https?://[^[:space:]]+$'
          and char_length(official_source_url) <= 2048
        ),
      constraint position_release_windows_source_title_check
        check (char_length(btrim(official_source_title)) between 1 and 500),
      constraint position_release_windows_source_time_check
        check (official_source_published_at <= official_source_received_at),
      constraint position_release_windows_source_sha256_check
        check (official_source_sha256 ~ '^[0-9a-f]{64}$'),
      constraint position_release_windows_source_receipt_check
        check (char_length(btrim(official_source_receipt)) between 1 and 500),
      constraint position_release_windows_kind_sequence_key
        unique (release_id, window_kind, sequence_no)
    )
    """,
    """
    create unique index product_position_release_windows_one_primary_idx
      on product.position_release_windows (release_id)
      where window_kind = 'primary'
    """,
    """
    create index product_position_release_windows_phase_idx
      on product.position_release_windows (release_id, opens_at, closes_at)
      include (window_kind, sequence_no)
    """,
    """
    create or replace function product.guard_position_release_window_mutation()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    declare
      target_release_id uuid;
      release_status text;
      prior_release_status text;
    begin
      target_release_id := case when tg_op = 'DELETE' then old.release_id else new.release_id end;
      select status
        into release_status
        from product.position_rule_releases
       where release_id = target_release_id
       for key share;

      if release_status is distinct from 'draft' then
        raise exception using
          errcode = '23514',
          message = 'position release windows are mutable only while the release is draft';
      end if;
      if tg_op = 'UPDATE' and old.release_id <> new.release_id then
        select status
          into prior_release_status
          from product.position_rule_releases
         where release_id = old.release_id
         for key share;
        if prior_release_status is distinct from 'draft' then
          raise exception using
            errcode = '23514',
            message = 'position release windows are mutable only while the release is draft';
        end if;
      end if;
      if tg_op = 'DELETE' then
        return old;
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function product.guard_position_release_window_mutation()
      from public
    """,
    """
    create trigger position_release_windows_draft_only
      before insert or update or delete
      on product.position_release_windows
      for each row execute function product.guard_position_release_window_mutation()
    """,
    """
    create or replace function product.validate_position_release_activation()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    declare
      window_count integer;
      primary_count integer;
      supplemental_count integer;
      latest_close timestamptz;
      latest_source_received timestamptz;
      has_overlap boolean;
      has_invalid_order boolean;
      supplemental_sequences integer[];
    begin
      if new.status <> 'active' then
        return new;
      end if;

      select
        count(*)::integer,
        count(*) filter (where window_kind = 'primary')::integer,
        count(*) filter (where window_kind = 'supplemental')::integer,
        max(closes_at),
        max(official_source_received_at)
      into
        window_count,
        primary_count,
        supplemental_count,
        latest_close,
        latest_source_received
      from product.position_release_windows
      where release_id = new.release_id;

      if window_count = 0 or primary_count <> 1 then
        raise exception using
          errcode = '23514',
          message = 'an active position release requires exactly one audited primary window';
      end if;

      select exists (
        select 1
        from product.position_release_windows left_window
        join product.position_release_windows right_window
          on right_window.release_id = left_window.release_id
         and right_window.window_id <> left_window.window_id
         and tstzrange(right_window.opens_at, right_window.closes_at, '[)')
             && tstzrange(left_window.opens_at, left_window.closes_at, '[)')
        where left_window.release_id = new.release_id
      ) into has_overlap;
      if has_overlap then
        raise exception using
          errcode = '23514',
          message = 'position release business windows cannot overlap';
      end if;

      select exists (
        select 1
        from product.position_release_windows primary_window
        join product.position_release_windows supplemental_window
          on supplemental_window.release_id = primary_window.release_id
         and supplemental_window.window_kind = 'supplemental'
        where primary_window.release_id = new.release_id
          and primary_window.window_kind = 'primary'
          and supplemental_window.opens_at < primary_window.closes_at
      ) into has_invalid_order;
      if has_invalid_order then
        raise exception using
          errcode = '23514',
          message = 'supplemental windows must start after the primary window closes';
      end if;

      select coalesce(
          array_agg(sequence_no order by sequence_no)::integer[],
          array[]::integer[]
        )
        into supplemental_sequences
        from product.position_release_windows
       where release_id = new.release_id
         and window_kind = 'supplemental';
      if supplemental_sequences
          <> coalesce(
            (
              select array_agg(value)
              from generate_series(1, supplemental_count) value
            ),
            array[]::integer[]
          ) then
        raise exception using
          errcode = '23514',
          message = 'supplemental window sequence numbers must be contiguous';
      end if;

      if new.activated_at is null or new.activated_at >= latest_close then
        raise exception using
          errcode = '23514',
          message = 'a position release cannot be activated after all business windows close';
      end if;
      if latest_source_received > new.activated_at then
        raise exception using
          errcode = '23514',
          message = 'official source evidence must be received before release activation';
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function product.validate_position_release_activation()
      from public
    """,
    """
    create trigger position_rule_releases_validate_activation
      before insert or update of status, activated_at
      on product.position_rule_releases
      for each row execute function product.validate_position_release_activation()
    """,
    """
    create or replace function learner.validate_position_goal_business_window()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, learner, product
    as $$
    declare
      matching_window_count integer;
    begin
      if new.status <> 'confirmed' then
        return new;
      end if;

      select count(*)::integer
        into matching_window_count
        from product.positions position
        join product.position_rule_releases release
          on release.exam_code = 'guokao'
         and release.exam_year = position.year
         and release.position_data_version = new.position_data_version
         and release.status = 'active'
        join product.position_release_windows business_window
          on business_window.release_id = release.release_id
         and business_window.opens_at <= new.created_at
         and new.created_at < business_window.closes_at
       where position.position_id = new.position_id
         and business_window.closes_at = new.target_score_valid_until;

      if matching_window_count <> 1 then
        raise exception using
          errcode = '23514',
          message = 'confirmed position goals require one open audited release window and its exact close time';
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function learner.validate_position_goal_business_window()
      from public
    """,
    """
    create trigger position_goal_versions_validate_business_window
      before insert or update of status, position_id, position_data_version,
        target_score_valid_until, created_at
      on learner.position_goal_versions
      for each row execute function learner.validate_position_goal_business_window()
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
        "revoke insert, update, delete, truncate, references, trigger "
        "on product.position_release_windows from gongkao_api"
    )
    bind.exec_driver_sql("grant select on product.position_release_windows to gongkao_api")


def upgrade() -> None:
    for statement in _UP_STATEMENTS:
        op.execute(statement)
    _grant_runtime_access()


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260730_78 is forward-only: official source and position "
        "business-window evidence must be preserved; publish a forward migration"
    )
