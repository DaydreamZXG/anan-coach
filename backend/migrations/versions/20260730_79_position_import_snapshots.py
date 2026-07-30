"""Keep immutable, release-bound position import snapshots.

Revision ID: 20260730_79
Revises: 20260730_78
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260730_79"
down_revision: str | Sequence[str] | None = "20260730_78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UP_STATEMENTS = (
    """
    alter table product.position_rule_releases
      add column if not exists content_digest text,
      add column if not exists content_frozen_at timestamptz
    """,
    """
    do $$
    begin
      if not exists (
        select 1
        from pg_catalog.pg_constraint
        where conname = 'position_rule_releases_content_digest_check'
          and conrelid = 'product.position_rule_releases'::regclass
      ) then
        alter table product.position_rule_releases
          add constraint position_rule_releases_content_digest_check
            check (
              content_digest is null
              or content_digest ~ '^[0-9a-f]{64}$'
            );
      end if;
      if not exists (
        select 1
        from pg_catalog.pg_constraint
        where conname = 'position_rule_releases_content_freeze_check'
          and conrelid = 'product.position_rule_releases'::regclass
      ) then
        alter table product.position_rule_releases
          add constraint position_rule_releases_content_freeze_check
            check (
              (content_digest is null and content_frozen_at is null)
              or (content_digest is not null and content_frozen_at is not null)
            );
      end if;
    end
    $$
    """,
    """
    create table if not exists product.position_import_batches (
      position_data_version text primary key,
      source_sha256 text not null,
      position_count integer not null,
      imported_at timestamptz not null default transaction_timestamp(),
      constraint position_import_batches_version_check
        check (char_length(btrim(position_data_version)) between 1 and 200),
      constraint position_import_batches_source_sha256_check
        check (source_sha256 ~ '^[0-9a-f]{64}$'),
      constraint position_import_batches_position_count_check
        check (position_count > 0)
    )
    """,
    """
    create table if not exists product.position_snapshots (
      snapshot_id uuid primary key default gen_random_uuid(),
      position_id uuid not null
        references product.positions(position_id),
      position_data_version text not null
        references product.position_import_batches(position_data_version),
      source_id text not null,
      year integer not null,
      position_code text not null,
      name text not null,
      department_full text not null,
      system_code text not null,
      region text not null,
      city text,
      paper_type text not null,
      headcount integer not null,
      education_min text,
      major_required text,
      political_req text,
      hukou_req text,
      service_min_yrs integer,
      has_extra_exam boolean not null default false,
      remark text,
      raw_json jsonb,
      source_fingerprint text not null,
      captured_at timestamptz not null default transaction_timestamp(),
      constraint position_snapshots_year_check
        check (year between 2000 and 2100),
      constraint position_snapshots_headcount_check
        check (headcount > 0),
      constraint position_snapshots_source_fingerprint_check
        check (source_fingerprint ~ '^[0-9a-f]{64}$'),
      constraint position_snapshots_position_version_key
        unique (position_id, position_data_version),
      constraint position_snapshots_source_version_key
        unique (source_id, position_data_version),
      constraint position_snapshots_catalog_identity_key
        unique (
          position_data_version, year, position_code, department_full
        )
    )
    """,
    """
    create index if not exists product_position_snapshots_release_lookup_idx
      on product.position_snapshots (
        position_data_version, year, region, system_code
      )
      include (
        position_id, position_code, name, department_full, city, headcount
      )
    """,
    """
    create table if not exists product.position_score_snapshot_batches (
      position_data_version text primary key,
      source_sha256 text not null,
      score_evidence_count integer not null,
      captured_at timestamptz not null default transaction_timestamp(),
      constraint position_score_snapshot_batches_version_check
        check (char_length(btrim(position_data_version)) between 1 and 200),
      constraint position_score_snapshot_batches_source_sha256_check
        check (source_sha256 ~ '^[0-9a-f]{64}$'),
      constraint position_score_snapshot_batches_count_check
        check (score_evidence_count >= 0)
    )
    """,
    """
    create table if not exists product.position_score_snapshots (
      score_snapshot_id uuid primary key default gen_random_uuid(),
      position_data_version text not null
        references product.position_score_snapshot_batches(position_data_version),
      position_id uuid not null
        references product.positions(position_id),
      evidence_kind text not null,
      evidence_key text not null,
      score_year integer not null,
      entry_score_min numeric(8, 3) not null,
      entry_score_max numeric(8, 3),
      source_fingerprint text not null,
      source_json jsonb not null,
      captured_at timestamptz not null default transaction_timestamp(),
      constraint position_score_snapshots_kind_check
        check (evidence_kind in ('official_entry', 'gwy_history')),
      constraint position_score_snapshots_year_check
        check (score_year between 2000 and 2100),
      constraint position_score_snapshots_score_check
        check (
          entry_score_min > 0
          and (entry_score_max is null or entry_score_max > 0)
        ),
      constraint position_score_snapshots_source_fingerprint_check
        check (source_fingerprint ~ '^[0-9a-f]{64}$'),
      constraint position_score_snapshots_evidence_key
        unique (
          position_data_version, position_id, evidence_kind, evidence_key
        )
    )
    """,
    """
    create index if not exists product_position_score_snapshots_release_idx
      on product.position_score_snapshots (
        position_data_version, position_id, score_year
      )
      include (entry_score_min, entry_score_max, evidence_kind)
    """,
    """
    create or replace function product.frozen_position_score_snapshot_exists(
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
    revoke all on function product.frozen_position_score_snapshot_exists(text)
      from public
    """,
    """
    create or replace function product.guard_frozen_position_score_snapshot()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    declare
      target_version text;
    begin
      if tg_op = 'DELETE' then
        target_version := old.position_data_version;
      else
        target_version := new.position_data_version;
      end if;
      if product.frozen_position_score_snapshot_exists(target_version) then
        raise exception using
          errcode = '23514',
          message = 'active or retired position score snapshots are immutable';
      end if;
      if tg_op = 'DELETE' then
        return old;
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function product.guard_frozen_position_score_snapshot()
      from public
    """,
    """
    create trigger position_score_snapshot_batches_frozen_release
      before insert or update or delete
      on product.position_score_snapshot_batches
      for each row execute function product.guard_frozen_position_score_snapshot()
    """,
    """
    create trigger position_score_snapshots_frozen_release
      before insert or update or delete
      on product.position_score_snapshots
      for each row execute function product.guard_frozen_position_score_snapshot()
    """,
    """
    create or replace function product.refresh_position_score_snapshots(
      target_position_data_version text
    )
    returns integer
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    declare
      captured_count integer;
      captured_sha256 text;
    begin
      if nullif(btrim(target_position_data_version), '') is null then
        raise exception using
          errcode = '23514',
          message = 'position data version is required for score snapshot capture';
      end if;
      perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
          'position-score-snapshot:' || target_position_data_version,
          0
        )
      );

      if product.frozen_position_score_snapshot_exists(
        target_position_data_version
      ) then
        select score_evidence_count
          into captured_count
          from product.position_score_snapshot_batches
         where position_data_version = target_position_data_version;
        if not found then
          raise exception using
            errcode = '23514',
            message = 'frozen position release is missing its score snapshot batch';
        end if;
        return captured_count;
      end if;

      insert into product.position_score_snapshot_batches (
        position_data_version,
        source_sha256,
        score_evidence_count,
        captured_at
      ) values (
        target_position_data_version,
        encode(
          public.digest(convert_to('[]', 'UTF8'), 'sha256'),
          'hex'
        ),
        0,
        transaction_timestamp()
      )
      on conflict (position_data_version) do update set
        source_sha256 = excluded.source_sha256,
        score_evidence_count = excluded.score_evidence_count,
        captured_at = excluded.captured_at;

      delete from product.position_score_snapshots
       where position_data_version = target_position_data_version;

      with effective_positions as (
        select
          snapshot.position_id,
          snapshot.position_code
        from product.position_snapshots snapshot
        where snapshot.position_data_version = target_position_data_version
        union all
        select
          position.position_id,
          position.position_code
        from product.positions position
        where not exists (
          select 1
          from product.position_import_batches import_batch
          where import_batch.position_data_version
                = target_position_data_version
        )
          and exists (
            select 1
            from product.position_rule_releases release
            where release.position_data_version
                  = target_position_data_version
              and release.exam_year = position.year
          )
      ),
      score_sources as (
        select
          target_position_data_version as position_data_version,
          position.position_id,
          'official_entry'::text as evidence_kind,
          concat_ws(
            ':',
            evidence.year::text,
            evidence.department_code,
            evidence.position_code
          ) as evidence_key,
          evidence.year as score_year,
          evidence.entry_score_min::numeric as entry_score_min,
          evidence.entry_score_min::numeric as entry_score_max,
          jsonb_build_object(
            'contractVersion', 'position-score-source@v1',
            'kind', 'official_entry',
            'year', evidence.year,
            'departmentCode', evidence.department_code,
            'positionCode', evidence.position_code,
            'agency', evidence.agency,
            'bureau', evidence.bureau,
            'positionTitle', evidence.position_title,
            'entryScoreMin', evidence.entry_score_min,
            'sourceName', evidence.source_name,
            'sourceUrl', evidence.source_url,
            'sourceFileSha256', evidence.source_file_sha256,
            'candidateCount', evidence.candidate_count,
            'rawJson', evidence.raw_json,
            'matchMethod', score_link.match_method,
            'confidence', score_link.confidence,
            'linkEvidence', score_link.evidence
          ) as source_json
        from effective_positions position
        join product.position_entry_score_links score_link
          on score_link.position_id = position.position_id
        join product.position_entry_score_evidence evidence
          on evidence.evidence_id = score_link.evidence_id
         and evidence.position_code = position.position_code
        union all
        select
          target_position_data_version,
          position.position_id,
          'gwy_history'::text,
          history.source_id,
          history.year,
          history.entry_score_min::numeric,
          history.entry_score_max::numeric,
          jsonb_build_object(
            'contractVersion', 'position-score-source@v1',
            'kind', 'gwy_history',
            'sourceId', history.source_id,
            'positionCode', history.position_code,
            'year', history.year,
            'entryScoreMin', history.entry_score_min,
            'entryScoreMax', history.entry_score_max,
            'admissionMin', history.admission_min,
            'admissionMax', history.admission_max,
            'signupCount', history.signup_count,
            'ratio', history.ratio,
            'dataSource', history.data_source,
            'rawJson', history.raw_json,
            'matchMethod', score_link.match_method,
            'confidence', score_link.confidence,
            'linkEvidence', score_link.evidence
          )
        from effective_positions position
        join product.position_score_links score_link
          on score_link.position_id = position.position_id
        join product.position_score_histories history
          on history.score_history_id = score_link.score_history_id
         and history.position_code = position.position_code
        where history.entry_score_min is not null
      ),
      fingerprinted as (
        select
          position_data_version,
          position_id,
          evidence_kind,
          evidence_key,
          score_year,
          entry_score_min,
          entry_score_max,
          source_json,
          encode(
            public.digest(
              convert_to(source_json::text, 'UTF8'),
              'sha256'
            ),
            'hex'
          ) as source_fingerprint
        from score_sources
      )
      insert into product.position_score_snapshots (
        position_data_version,
        position_id,
        evidence_kind,
        evidence_key,
        score_year,
        entry_score_min,
        entry_score_max,
        source_fingerprint,
        source_json,
        captured_at
      )
      select
        position_data_version,
        position_id,
        evidence_kind,
        evidence_key,
        score_year,
        entry_score_min,
        entry_score_max,
        source_fingerprint,
        source_json,
        transaction_timestamp()
      from fingerprinted
      order by position_id, evidence_kind, evidence_key;

      select
        count(*)::integer,
        encode(
          public.digest(
            convert_to(
              coalesce(
                jsonb_agg(
                  jsonb_build_object(
                    'positionId', score.position_id::text,
                    'kind', score.evidence_kind,
                    'key', score.evidence_key,
                    'year', score.score_year,
                    'sourceFingerprint', score.source_fingerprint
                  )
                  order by
                    score.position_id,
                    score.evidence_kind,
                    score.evidence_key
                ),
                '[]'::jsonb
              )::text,
              'UTF8'
            ),
            'sha256'
          ),
          'hex'
        )
        into captured_count, captured_sha256
        from product.position_score_snapshots score
       where score.position_data_version = target_position_data_version;

      update product.position_score_snapshot_batches
         set source_sha256 = captured_sha256,
             score_evidence_count = captured_count,
             captured_at = transaction_timestamp()
       where position_data_version = target_position_data_version;

      return captured_count;
    end
    $$
    """,
    """
    revoke all on function product.refresh_position_score_snapshots(text)
      from public
    """,
    """
    create or replace function product.guard_position_snapshot_immutability()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    begin
      raise exception using
        errcode = '23514',
        message = 'position import batches and snapshots are immutable';
    end
    $$
    """,
    """
    revoke all on function product.guard_position_snapshot_immutability()
      from public
    """,
    """
    create trigger position_import_batches_immutable
      before update or delete on product.position_import_batches
      for each row execute function product.guard_position_snapshot_immutability()
    """,
    """
    create trigger position_snapshots_immutable
      before update or delete on product.position_snapshots
      for each row execute function product.guard_position_snapshot_immutability()
    """,
    """
    do $$
    begin
      if exists (
        select 1
        from product.position_rule_releases release
        join product.positions position
          on position.year = release.exam_year
        where release.status = 'active'
          and nullif(btrim(position.raw_json ->> 'positionDataVersion'), '') is not null
          and position.raw_json ->> 'positionDataVersion'
                <> release.position_data_version
      ) then
        raise exception using
          errcode = '23514',
          message = 'active position release does not match the recoverable imported position version';
      end if;
    end
    $$
    """,
    """
    insert into product.position_import_batches (
      position_data_version,
      source_sha256,
      position_count,
      imported_at
    )
    select
      release.position_data_version,
      encode(
        public.digest(
          convert_to(
            string_agg(
              position.position_id::text
                || ':'
                || encode(
                  public.digest(
                    convert_to(
                      jsonb_build_array(
                        position.source_id,
                        position.year,
                        position.position_code,
                        position.name,
                        position.department_full,
                        position.system_code,
                        position.region,
                        position.city,
                        position.paper_type,
                        position.headcount,
                        position.education_min,
                        position.major_required,
                        position.political_req,
                        position.hukou_req,
                        position.service_min_yrs,
                        position.has_extra_exam,
                        position.remark,
                        position.raw_json
                      )::text,
                      'UTF8'
                    ),
                    'sha256'
                  ),
                  'hex'
                ),
              '' order by position.position_id
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ),
      count(*)::integer,
      transaction_timestamp()
    from product.position_rule_releases release
    join product.positions position
      on position.year = release.exam_year
    where release.status = 'active'
    group by release.position_data_version
    """,
    """
    insert into product.position_snapshots (
      position_id,
      position_data_version,
      source_id,
      year,
      position_code,
      name,
      department_full,
      system_code,
      region,
      city,
      paper_type,
      headcount,
      education_min,
      major_required,
      political_req,
      hukou_req,
      service_min_yrs,
      has_extra_exam,
      remark,
      raw_json,
      source_fingerprint,
      captured_at
    )
    select
      position.position_id,
      release.position_data_version,
      position.source_id,
      position.year,
      position.position_code,
      position.name,
      position.department_full,
      position.system_code,
      position.region,
      position.city,
      position.paper_type,
      position.headcount,
      position.education_min,
      position.major_required,
      position.political_req,
      position.hukou_req,
      position.service_min_yrs,
      position.has_extra_exam,
      position.remark,
      position.raw_json,
      encode(
        public.digest(
          convert_to(
            jsonb_build_array(
              position.source_id,
              position.year,
              position.position_code,
              position.name,
              position.department_full,
              position.system_code,
              position.region,
              position.city,
              position.paper_type,
              position.headcount,
              position.education_min,
              position.major_required,
              position.political_req,
              position.hukou_req,
              position.service_min_yrs,
              position.has_extra_exam,
              position.remark,
              position.raw_json
            )::text,
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ),
      transaction_timestamp()
    from product.position_rule_releases release
    join product.positions position
      on position.year = release.exam_year
    where release.status = 'active'
    """,
    """
    do $$
    declare
      release_version text;
    begin
      for release_version in
        select distinct position_data_version
        from product.position_rule_releases
        where status in ('active', 'retired')
      loop
        perform product.refresh_position_score_snapshots(release_version);
      end loop;
    end
    $$
    """,
    """
    create or replace function product.compute_position_release_content_digest(
      target_release_id uuid
    )
    returns text
    language plpgsql
    stable
    security definer
    set search_path = pg_catalog, product
    as $$
    declare
      authority jsonb;
    begin
      select jsonb_build_object(
        'contractVersion', 'position-release-content@v2',
        'release', jsonb_build_object(
          'releaseId', release.release_id::text,
          'examCode', release.exam_code,
          'examYear', release.exam_year,
          'ruleSetVersion', release.rule_set_version,
          'positionDataVersion', release.position_data_version
        ),
        'importBatch', (
          select to_jsonb(import_batch)
          from (
            select source_sha256, position_count
            from product.position_import_batches
            where position_data_version = release.position_data_version
          ) import_batch
        ),
        'positions', (
          select coalesce(
            jsonb_agg(
              jsonb_build_object(
                'positionId', position.position_id::text,
                'sourceFingerprint',
                case
                  when snapshot.snapshot_id is not null
                    then snapshot.source_fingerprint
                  else encode(
                    public.digest(
                      convert_to(
                        jsonb_build_array(
                          position.source_id,
                          position.year,
                          position.position_code,
                          position.name,
                          position.department_full,
                          position.system_code,
                          position.region,
                          position.city,
                          position.paper_type,
                          position.headcount,
                          position.education_min,
                          position.major_required,
                          position.political_req,
                          position.hukou_req,
                          position.service_min_yrs,
                          position.has_extra_exam,
                          position.remark,
                          position.raw_json
                        )::text,
                        'UTF8'
                      ),
                      'sha256'
                    ),
                    'hex'
                  )
                end
              )
              order by position.position_id
            ),
            '[]'::jsonb
          )
          from product.positions position
          left join product.position_import_batches import_batch
            on import_batch.position_data_version
                 = release.position_data_version
          left join product.position_snapshots snapshot
            on snapshot.position_id = position.position_id
           and snapshot.position_data_version = release.position_data_version
          where coalesce(snapshot.year, position.year) = release.exam_year
            and (
              import_batch.position_data_version is null
              or snapshot.snapshot_id is not null
            )
        ),
        'scoreBatch', (
          select to_jsonb(score_batch)
          from (
            select source_sha256, score_evidence_count
            from product.position_score_snapshot_batches
            where position_data_version = release.position_data_version
          ) score_batch
        ),
        'scoreEvidence', (
          select coalesce(
            jsonb_agg(
              jsonb_build_object(
                'positionId', score.position_id::text,
                'kind', score.evidence_kind,
                'key', score.evidence_key,
                'year', score.score_year,
                'sourceFingerprint', score.source_fingerprint
              )
              order by
                score.position_id,
                score.evidence_kind,
                score.evidence_key
            ),
            '[]'::jsonb
          )
          from product.position_score_snapshots score
          where score.position_data_version = release.position_data_version
            and exists (
              select 1
              from product.position_snapshots snapshot
              where snapshot.position_id = score.position_id
                and snapshot.position_data_version
                      = release.position_data_version
                and snapshot.year = release.exam_year
              union all
              select 1
              from product.positions legacy_position
              where legacy_position.position_id = score.position_id
                and legacy_position.year = release.exam_year
                and not exists (
                  select 1
                  from product.position_import_batches import_batch
                  where import_batch.position_data_version
                        = release.position_data_version
                )
            )
        ),
        'compilations', (
          select coalesce(
            jsonb_agg(
              jsonb_build_object(
                'positionId', compilation.position_id::text,
                'compilerVersion', compilation.compiler_version,
                'sourceFingerprint', compilation.source_fingerprint,
                'status', compilation.status,
                'requirementsCount', compilation.requirements_count,
                'manualReviewCount', compilation.manual_review_count,
                'warnings', compilation.warnings
              )
              order by compilation.position_id
            ),
            '[]'::jsonb
          )
          from product.position_rule_compilations compilation
          where compilation.rule_set_version = release.rule_set_version
            and compilation.position_data_version
                  = release.position_data_version
            and exists (
              select 1
              from product.position_snapshots snapshot
              where snapshot.position_id = compilation.position_id
                and snapshot.position_data_version
                      = release.position_data_version
                and snapshot.year = release.exam_year
              union all
              select 1
              from product.positions legacy_position
              where legacy_position.position_id = compilation.position_id
                and legacy_position.year = release.exam_year
                and not exists (
                  select 1
                  from product.position_import_batches import_batch
                  where import_batch.position_data_version
                        = release.position_data_version
                )
            )
        ),
        'requirements', (
          select coalesce(
            jsonb_agg(
              jsonb_build_object(
                'positionId', requirement.position_id::text,
                'factKey', requirement.fact_key,
                'operator', requirement.operator,
                'expected', requirement.expected,
                'hardCondition', requirement.hard_condition,
                'sourceText', requirement.source_text,
                'sourceReference', requirement.source_reference
              )
              order by
                requirement.position_id,
                requirement.fact_key,
                requirement.requirement_id
            ),
            '[]'::jsonb
          )
          from product.position_requirements requirement
          where requirement.rule_set_version = release.rule_set_version
            and requirement.position_data_version
                  = release.position_data_version
            and exists (
              select 1
              from product.position_snapshots snapshot
              where snapshot.position_id = requirement.position_id
                and snapshot.position_data_version
                      = release.position_data_version
                and snapshot.year = release.exam_year
              union all
              select 1
              from product.positions legacy_position
              where legacy_position.position_id = requirement.position_id
                and legacy_position.year = release.exam_year
                and not exists (
                  select 1
                  from product.position_import_batches import_batch
                  where import_batch.position_data_version
                        = release.position_data_version
                )
            )
        ),
        'businessWindows', (
          select coalesce(
            jsonb_agg(
              jsonb_build_object(
                'kind', business_window.window_kind,
                'sequenceNo', business_window.sequence_no,
                'opensAt', to_char(
                  business_window.opens_at at time zone 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'closesAt', to_char(
                  business_window.closes_at at time zone 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'officialSourceUrl', business_window.official_source_url,
                'officialSourceTitle', business_window.official_source_title,
                'officialSourcePublishedAt', to_char(
                  business_window.official_source_published_at
                    at time zone 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'officialSourceReceivedAt', to_char(
                  business_window.official_source_received_at
                    at time zone 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'officialSourceSha256',
                  business_window.official_source_sha256,
                'officialSourceReceipt',
                  business_window.official_source_receipt
              )
              order by
                business_window.opens_at,
                business_window.window_kind,
                business_window.sequence_no
            ),
            '[]'::jsonb
          )
          from product.position_release_windows business_window
          where business_window.release_id = release.release_id
        )
      )
        into authority
        from product.position_rule_releases release
       where release.release_id = target_release_id;

      if authority is null then
        raise exception using
          errcode = '23503',
          message = 'position release does not exist';
      end if;
      return encode(
        public.digest(convert_to(authority::text, 'UTF8'), 'sha256'),
        'hex'
      );
    end
    $$
    """,
    """
    revoke all on function product.compute_position_release_content_digest(uuid)
      from public
    """,
    """
    create or replace function product.freeze_position_release_content()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    begin
      if tg_op = 'DELETE' then
        if old.content_digest is not null then
          raise exception using
            errcode = '23514',
            message = 'frozen position releases are immutable';
        end if;
        return old;
      end if;

      if old.content_digest is not null and (
        new.release_id is distinct from old.release_id
        or new.exam_code is distinct from old.exam_code
        or new.exam_year is distinct from old.exam_year
        or new.rule_set_version is distinct from old.rule_set_version
        or new.position_data_version is distinct from old.position_data_version
        or new.activated_at is distinct from old.activated_at
        or new.content_digest is distinct from old.content_digest
        or new.content_frozen_at is distinct from old.content_frozen_at
        or new.status not in (old.status, 'retired')
      ) then
        raise exception using
          errcode = '23514',
          message = 'frozen position releases are immutable';
      end if;

      if new.status = 'active'
         and (old.status <> 'active' or old.content_digest is null) then
        if new.exam_code is distinct from old.exam_code
           or new.exam_year is distinct from old.exam_year
           or new.rule_set_version is distinct from old.rule_set_version
           or new.position_data_version is distinct from old.position_data_version then
          raise exception using
            errcode = '23514',
            message = 'position release identity cannot change during activation';
        end if;
        perform product.refresh_position_score_snapshots(
          old.position_data_version
        );
        new.content_digest :=
          product.compute_position_release_content_digest(old.release_id);
        new.content_frozen_at := transaction_timestamp();
      end if;

      if new.status = 'retired' and old.content_digest is null then
        perform product.refresh_position_score_snapshots(
          old.position_data_version
        );
        new.content_digest :=
          product.compute_position_release_content_digest(old.release_id);
        new.content_frozen_at := transaction_timestamp();
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function product.freeze_position_release_content()
      from public
    """,
    """
    create trigger position_rule_releases_freeze_content
      before update or delete on product.position_rule_releases
      for each row execute function product.freeze_position_release_content()
    """,
    """
    create or replace function product.frozen_position_release_exists(
      target_position_id uuid,
      target_rule_set_version text,
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
        where release.rule_set_version = target_rule_set_version
          and release.position_data_version = target_position_data_version
          and release.status in ('active', 'retired')
          and release.content_digest is not null
          and exists (
            select 1
            from product.position_snapshots snapshot
            where snapshot.position_id = target_position_id
              and snapshot.position_data_version
                    = release.position_data_version
              and snapshot.year = release.exam_year
            union all
            select 1
            from product.positions legacy_position
            where legacy_position.position_id = target_position_id
              and legacy_position.year = release.exam_year
              and not exists (
                select 1
                from product.position_import_batches import_batch
                where import_batch.position_data_version
                      = release.position_data_version
              )
          )
      )
    $$
    """,
    """
    revoke all on function product.frozen_position_release_exists(uuid, text, text)
      from public
    """,
    """
    create or replace function product.guard_frozen_position_rule_content()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    begin
      if tg_op <> 'INSERT'
         and product.frozen_position_release_exists(
           old.position_id,
           old.rule_set_version,
           old.position_data_version
         ) then
        raise exception using
          errcode = '23514',
          message = 'active or retired position release content is immutable';
      end if;
      if tg_op <> 'DELETE'
         and product.frozen_position_release_exists(
           new.position_id,
           new.rule_set_version,
           new.position_data_version
         ) then
        raise exception using
          errcode = '23514',
          message = 'active or retired position release content is immutable';
      end if;
      if tg_op = 'DELETE' then
        return old;
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function product.guard_frozen_position_rule_content()
      from public
    """,
    """
    create trigger position_rule_compilations_frozen_release
      before insert or update or delete
      on product.position_rule_compilations
      for each row execute function product.guard_frozen_position_rule_content()
    """,
    """
    create trigger position_requirements_frozen_release
      before insert or update or delete
      on product.position_requirements
      for each row execute function product.guard_frozen_position_rule_content()
    """,
    """
    create or replace function product.guard_frozen_legacy_position()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    begin
      if exists (
        select 1
        from product.position_rule_releases release
        where release.exam_year = old.year
          and release.status in ('active', 'retired')
          and release.content_digest is not null
          and not exists (
            select 1
            from product.position_import_batches import_batch
            where import_batch.position_data_version
                  = release.position_data_version
          )
      ) then
        raise exception using
          errcode = '23514',
          message = 'legacy position rows referenced by a frozen release are immutable';
      end if;
      if tg_op = 'DELETE' then
        return old;
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function product.guard_frozen_legacy_position()
      from public
    """,
    """
    create trigger positions_frozen_legacy_release
      before update or delete on product.positions
      for each row execute function product.guard_frozen_legacy_position()
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
        from product.position_rule_releases release
        join product.position_release_windows business_window
          on business_window.release_id = release.release_id
         and business_window.opens_at <= new.created_at
         and new.created_at < business_window.closes_at
        left join product.position_import_batches import_batch
          on import_batch.position_data_version = release.position_data_version
        left join product.position_snapshots position_snapshot
          on position_snapshot.position_id = new.position_id
         and position_snapshot.position_data_version
               = release.position_data_version
        left join product.positions legacy_position
          on legacy_position.position_id = new.position_id
         and import_batch.position_data_version is null
       where release.exam_code = 'guokao'
         and release.position_data_version = new.position_data_version
         and release.status = 'active'
         and coalesce(position_snapshot.year, legacy_position.year)
               = release.exam_year
         and business_window.closes_at = new.target_score_valid_until;

      if matching_window_count <> 1 then
        raise exception using
          errcode = '23514',
          message = 'confirmed position goals require one open audited release window and its exact position snapshot';
      end if;
      return new;
    end
    $$
    """,
)


def _grant_runtime_access() -> None:
    bind = op.get_bind()
    role_exists = bind.execute(
        text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
    ).scalar_one()
    if not role_exists:
        return
    tables = (
        "product.position_import_batches",
        "product.position_snapshots",
        "product.position_score_snapshot_batches",
        "product.position_score_snapshots",
    )
    table_list = ", ".join(tables)
    bind.exec_driver_sql(
        "revoke insert, update, delete, truncate, references, trigger "
        f"on {table_list} from gongkao_api"
    )
    bind.exec_driver_sql(f"grant select on {table_list} to gongkao_api")


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _UP_STATEMENTS:
        connection.execute(text(statement))
    _grant_runtime_access()


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260730_79 is forward-only: immutable position import and "
        "score snapshots are release authority; publish a forward migration"
    )
