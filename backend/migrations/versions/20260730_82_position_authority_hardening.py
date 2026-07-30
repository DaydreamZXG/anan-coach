"""Harden immutable position authority and current learner projections.

Revision ID: 20260730_82
Revises: 20260730_81
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260730_82"
down_revision: str | Sequence[str] | None = "20260730_81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


POSITION_AUTHORITY_TABLES = (
    "product.position_systems",
    "product.positions",
    "product.position_score_histories",
    "product.position_score_links",
    "product.position_entry_score_evidence",
    "product.position_entry_score_links",
    "product.system_score_histories",
    "product.position_tags",
    "product.position_rule_releases",
    "product.position_release_windows",
    "product.position_requirements",
    "product.position_rule_compilations",
    "product.position_import_batches",
    "product.position_snapshots",
    "product.position_score_snapshot_batches",
    "product.position_score_snapshots",
    "product.position_release_version_identities",
)

CURRENT_PROJECTION_VIEWS = (
    "learner.current_profile_position_projections",
    "learner.current_primary_position_goals",
)


_STATEMENTS = (
    """
    create table if not exists product.position_release_version_identities (
      position_data_version text primary key,
      exam_code text not null,
      exam_year integer not null,
      first_release_id uuid not null
        references product.position_rule_releases(release_id),
      bound_at timestamptz not null default transaction_timestamp(),
      constraint position_release_version_identities_version_check
        check (char_length(btrim(position_data_version)) between 1 and 200),
      constraint position_release_version_identities_exam_code_check
        check (char_length(btrim(exam_code)) between 1 and 80),
      constraint position_release_version_identities_exam_year_check
        check (exam_year between 2000 and 2100)
    )
    """,
    """
    do $$
    begin
      if exists (
        select release.position_data_version
        from product.position_rule_releases release
        where release.status in ('active', 'retired')
          and release.content_digest is not null
        group by release.position_data_version
        having count(
          distinct (
            release.exam_code
            || chr(31)
            || release.exam_year::text
          )
        ) > 1
      ) then
        raise exception using
          errcode = '23514',
          message =
            'a frozen position data version is already reused across exam identities';
      end if;
    end
    $$
    """,
    """
    insert into product.position_release_version_identities (
      position_data_version,
      exam_code,
      exam_year,
      first_release_id,
      bound_at
    )
    select distinct on (release.position_data_version)
      release.position_data_version,
      release.exam_code,
      release.exam_year,
      release.release_id,
      coalesce(release.content_frozen_at, release.created_at)
    from product.position_rule_releases release
    where release.status in ('active', 'retired')
      and release.content_digest is not null
    order by
      release.position_data_version,
      release.content_frozen_at nulls last,
      release.created_at,
      release.release_id
    on conflict (position_data_version) do nothing
    """,
    """
    create or replace function product.enforce_position_release_version_identity()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    declare
      bound_exam_code text;
      bound_exam_year integer;
      import_position_count integer;
      snapshot_position_count integer;
      mismatched_snapshot_exists boolean;
    begin
      if new.status not in ('active', 'retired')
         or new.content_digest is null then
        return new;
      end if;

      select import_batch.position_count
        into import_position_count
        from product.position_import_batches import_batch
       where import_batch.position_data_version = new.position_data_version;
      if found then
        select
          count(*)::integer,
          coalesce(bool_or(snapshot.year <> new.exam_year), false)
        into snapshot_position_count, mismatched_snapshot_exists
        from product.position_snapshots snapshot
        where snapshot.position_data_version = new.position_data_version;
        if snapshot_position_count <> import_position_count then
          raise exception using
            errcode = '23514',
            message =
              'position release snapshot count does not match its immutable import batch';
        end if;
        if mismatched_snapshot_exists then
          raise exception using
            errcode = '23514',
            message =
              'a position data version cannot contain snapshots from another exam year';
        end if;
      end if;

      insert into product.position_release_version_identities (
        position_data_version,
        exam_code,
        exam_year,
        first_release_id,
        bound_at
      ) values (
        new.position_data_version,
        new.exam_code,
        new.exam_year,
        new.release_id,
        transaction_timestamp()
      )
      on conflict (position_data_version) do nothing;

      select identity.exam_code, identity.exam_year
        into bound_exam_code, bound_exam_year
        from product.position_release_version_identities identity
       where identity.position_data_version = new.position_data_version
       for update;
      if bound_exam_code is distinct from new.exam_code
         or bound_exam_year is distinct from new.exam_year then
        raise exception using
          errcode = '23514',
          message =
            'a frozen position data version cannot be reused across exam identities';
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function product.enforce_position_release_version_identity()
      from public
    """,
    """
    drop trigger if exists zz_position_rule_releases_version_identity
      on product.position_rule_releases
    """,
    """
    create trigger zz_position_rule_releases_version_identity
      before insert or update of
        status, exam_code, exam_year, position_data_version
      on product.position_rule_releases
      for each row execute function
        product.enforce_position_release_version_identity()
    """,
    """
    create or replace function product.position_version_is_frozen(
      target_position_data_version text
    )
    returns boolean
    language sql
    stable
    security definer
    set search_path = pg_catalog, product
    as $$
      select exists (
        select 1
        from product.position_rule_releases release
        where release.position_data_version = target_position_data_version
          and release.status in ('active', 'retired')
          and release.content_digest is not null
      )
    $$
    """,
    """
    revoke all on function product.position_version_is_frozen(text)
      from public
    """,
    """
    create or replace function product.guard_position_snapshot_immutability()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    declare
      target_version text;
    begin
      if tg_op in ('UPDATE', 'DELETE') then
        raise exception using
          errcode = '23514',
          message = 'position import batches and snapshots are immutable';
      end if;
      target_version := new.position_data_version;
      if product.position_version_is_frozen(target_version) then
        raise exception using
          errcode = '23514',
          message =
            'a frozen position release cannot accept additional import snapshots';
      end if;
      return new;
    end
    $$
    """,
    """
    drop trigger if exists position_import_batches_immutable
      on product.position_import_batches
    """,
    """
    create trigger position_import_batches_immutable
      before insert or update or delete
      on product.position_import_batches
      for each row execute function product.guard_position_snapshot_immutability()
    """,
    """
    drop trigger if exists position_snapshots_immutable
      on product.position_snapshots
    """,
    """
    create trigger position_snapshots_immutable
      before insert or update or delete
      on product.position_snapshots
      for each row execute function product.guard_position_snapshot_immutability()
    """,
    """
    alter table learner.profiles
      add column if not exists target_position_data_version text
    """,
    """
    create or replace function product.position_projection_is_current(
      target_position_id uuid,
      target_position_data_version text,
      target_exam_code text,
      checked_at timestamptz
    )
    returns boolean
    language sql
    stable
    security definer
    set search_path = pg_catalog, product
    as $$
      select
        target_position_id is not null
        and nullif(btrim(target_position_data_version), '') is not null
        and nullif(btrim(target_exam_code), '') is not null
        and exists (
          select 1
          from product.position_rule_releases release
          join product.position_release_windows business_window
            on business_window.release_id = release.release_id
           and business_window.opens_at <= checked_at
           and checked_at < business_window.closes_at
          left join product.position_import_batches import_batch
            on import_batch.position_data_version =
                 release.position_data_version
          left join product.position_snapshots position_snapshot
            on position_snapshot.position_id = target_position_id
           and position_snapshot.position_data_version =
                 release.position_data_version
          left join product.positions legacy_position
            on legacy_position.position_id = target_position_id
           and import_batch.position_data_version is null
          where release.exam_code = target_exam_code
            and release.position_data_version =
                  target_position_data_version
            and release.status = 'active'
            and release.content_digest is not null
            and coalesce(position_snapshot.year, legacy_position.year) =
                  release.exam_year
        )
    $$
    """,
    """
    revoke all on function product.position_projection_is_current(
      uuid, text, text, timestamptz
    ) from public
    """,
    """
    with candidates as (
      select
        profile.user_id,
        release.position_data_version,
        row_number() over (
          partition by profile.user_id
          order by release.activated_at desc, release.release_id
        ) as candidate_rank
      from learner.profiles profile
      join product.position_rule_releases release
        on release.exam_code = profile.target_exam
       and release.status = 'active'
       and release.content_digest is not null
      join product.position_release_windows business_window
        on business_window.release_id = release.release_id
       and business_window.opens_at <= statement_timestamp()
       and statement_timestamp() < business_window.closes_at
      left join product.position_import_batches import_batch
        on import_batch.position_data_version =
             release.position_data_version
      left join product.position_snapshots position_snapshot
        on position_snapshot.position_id = profile.target_position_id
       and position_snapshot.position_data_version =
             release.position_data_version
      left join product.positions legacy_position
        on legacy_position.position_id = profile.target_position_id
       and import_batch.position_data_version is null
      where profile.target_position_id is not null
        and profile.target_position_data_version is null
        and coalesce(position_snapshot.year, legacy_position.year) =
              release.exam_year
    )
    update learner.profiles profile
       set target_position_data_version = candidate.position_data_version,
           updated_at = statement_timestamp()
      from candidates candidate
     where candidate.user_id = profile.user_id
       and candidate.candidate_rank = 1
    """,
    """
    create or replace function learner.guard_profile_position_projection()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, learner, product
    as $$
    begin
      if new.target_position_id is null then
        if new.target_position_data_version is not null
           or new.target_score is not null then
          raise exception using
            errcode = '23514',
            message =
              'a profile target score and position version require a target position';
        end if;
        return new;
      end if;
      if nullif(btrim(new.target_position_data_version), '') is null then
        raise exception using
          errcode = '23514',
          message = 'a profile target position requires its position data version';
      end if;
      if not product.position_projection_is_current(
        new.target_position_id,
        new.target_position_data_version,
        new.target_exam,
        statement_timestamp()
      ) then
        raise exception using
          errcode = '23514',
          message =
            'a profile target position must belong to the current open release';
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function learner.guard_profile_position_projection()
      from public
    """,
    """
    drop trigger if exists profiles_position_projection_guard
      on learner.profiles
    """,
    """
    create trigger profiles_position_projection_guard
      before insert or update of
        target_exam,
        target_position_id,
        target_score,
        target_position_data_version
      on learner.profiles
      for each row execute function learner.guard_profile_position_projection()
    """,
    """
    create or replace view learner.current_profile_position_projections
    with (security_barrier = true)
    as
    select
      profile.user_id,
      profile.target_position_id,
      profile.target_score,
      profile.target_position_data_version
    from learner.profiles profile
    where product.position_projection_is_current(
      profile.target_position_id,
      profile.target_position_data_version,
      profile.target_exam,
      statement_timestamp()
    )
    """,
    """
    create or replace view learner.current_primary_position_goals
    with (security_barrier = true)
    as
    select goal.*
    from learner.position_goal_versions goal
    join product.position_rule_releases release
      on release.exam_code = 'guokao'
     and release.position_data_version = goal.position_data_version
     and release.status = 'active'
     and release.content_digest is not null
    join product.position_release_windows business_window
      on business_window.release_id = release.release_id
     and business_window.opens_at <= statement_timestamp()
     and statement_timestamp() < business_window.closes_at
     and business_window.closes_at = goal.target_score_valid_until
    left join product.position_import_batches import_batch
      on import_batch.position_data_version = release.position_data_version
    left join product.position_snapshots position_snapshot
      on position_snapshot.position_id = goal.position_id
     and position_snapshot.position_data_version =
           release.position_data_version
    left join product.positions legacy_position
      on legacy_position.position_id = goal.position_id
     and import_batch.position_data_version is null
    where goal.role = 'primary'
      and goal.status = 'confirmed'
      and coalesce(position_snapshot.year, legacy_position.year) =
            release.exam_year
    """,
)


def _runtime_role_exists(bind: Connection | None = None) -> bool:
    connection = bind or op.get_bind()
    return bool(
        connection.execute(
            text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
        ).scalar_one()
    )


def harden_runtime_position_authority(bind: Connection | None = None) -> None:
    connection = bind or op.get_bind()
    if not _runtime_role_exists(connection):
        return
    authority_tables = ", ".join(POSITION_AUTHORITY_TABLES)
    projection_views = ", ".join(CURRENT_PROJECTION_VIEWS)
    connection.exec_driver_sql(
        "revoke insert, update, delete, truncate, references, trigger "
        f"on {authority_tables} from gongkao_api"
    )
    connection.exec_driver_sql(f"grant select on {authority_tables} to gongkao_api")
    connection.exec_driver_sql(f"revoke all privileges on {projection_views} from gongkao_api")
    connection.exec_driver_sql(f"grant select on {projection_views} to gongkao_api")
    connection.exec_driver_sql(
        "grant execute on function product.position_projection_is_current("
        "uuid, text, text, timestamptz) to gongkao_api"
    )


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)
    harden_runtime_position_authority()


def downgrade() -> None:
    raise RuntimeError("position authority hardening is forward-only; publish a forward migration")
