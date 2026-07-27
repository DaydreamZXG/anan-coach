"""Version knowledge-map publication and retain every historical release.

Existing knowledge-map nodes are assigned to one deterministic retired legacy
release per observed ``(exam_scope, track)`` pair. They are deliberately not
marked active or fresh because their source and generator lineage predate the
release contract.

Revision ID: 20260727_65
Revises: 20260727_64
Create Date: 2026-07-27
"""

from typing import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260727_65"
down_revision: str | Sequence[str] | None = "20260727_64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_RELEASE_VERSION_PREFIX = "knowledge-map@legacy-pre-20260727-65"


_STATEMENTS = (
    """
    create table product.knowledge_map_releases (
      release_id uuid primary key,
      release_version text not null unique check (
        char_length(btrim(release_version)) > 0
      ),
      exam_scope text not null check (char_length(btrim(exam_scope)) > 0),
      track text not null check (track in ('xingce', 'shenlun')),
      source_data_version text not null check (
        char_length(btrim(source_data_version)) > 0
      ),
      vocab_version text not null check (char_length(btrim(vocab_version)) > 0),
      generator_version text not null check (
        char_length(btrim(generator_version)) > 0
      ),
      source_fingerprint text not null check (length(source_fingerprint) = 64),
      generated_at timestamptz not null,
      fresh_until timestamptz not null,
      status text not null check (status in ('draft', 'active', 'retired')),
      node_count integer not null default 0 check (node_count >= 0),
      activated_at timestamptz,
      retired_at timestamptz,
      supersedes_release_id uuid references product.knowledge_map_releases(release_id),
      unique (release_id, exam_scope, track),
      check (
        supersedes_release_id is null or supersedes_release_id <> release_id
      ),
      check (status = 'retired' or fresh_until > generated_at),
      check (
        (
          status = 'draft'
          and activated_at is null
          and retired_at is null
        )
        or (
          status = 'active'
          and node_count > 0
          and activated_at is not null
          and retired_at is null
          and fresh_until > generated_at
        )
        or (
          status = 'retired'
          and retired_at is not null
        )
      )
    )
    """,
    """
    create unique index product_knowledge_map_one_active_release_idx
      on product.knowledge_map_releases(exam_scope, track)
      where status = 'active'
    """,
    """
    create index product_knowledge_map_release_freshness_idx
      on product.knowledge_map_releases(
        exam_scope,
        track,
        status,
        fresh_until desc
      )
    """,
    f"""
    insert into product.knowledge_map_releases (
      release_id,
      release_version,
      exam_scope,
      track,
      source_data_version,
      vocab_version,
      generator_version,
      source_fingerprint,
      generated_at,
      fresh_until,
      status,
      node_count,
      activated_at,
      retired_at,
      supersedes_release_id
    )
    select
      md5(
        'knowledge-map-legacy|' || node.exam_scope || '|' || node.track
      )::uuid,
      '{LEGACY_RELEASE_VERSION_PREFIX}:'
        || node.exam_scope || ':' || node.track,
      node.exam_scope,
      node.track,
      'legacy:source-version-unknown',
      'legacy:vocab-version-unknown',
      'legacy:generator-version-unknown',
      encode(
        digest(
          convert_to(
            coalesce(
              string_agg(
                concat_ws(
                  E'\\x1f',
                  node.node_id,
                  node.exam_scope,
                  node.track,
                  node.module_name,
                  node.node_type,
                  coalesce(node.tag_type, ''),
                  node.tag_value,
                  node.display_path,
                  coalesce(node.parent_node_id, ''),
                  node.status,
                  node.evidence::text
                ),
                E'\\x1e'
                order by node.node_id
              ),
              ''
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ),
      min(node.created_at),
      min(node.created_at),
      'retired',
      count(*)::integer,
      null,
      transaction_timestamp(),
      null
    from product.knowledge_map_nodes node
    group by node.exam_scope, node.track
    """,
    """
    alter table product.knowledge_map_nodes
      add column release_id uuid
    """,
    """
    update product.knowledge_map_nodes
    set release_id = md5(
      'knowledge-map-legacy|' || exam_scope || '|' || track
    )::uuid
    where release_id is null
    """,
    """
    alter table product.knowledge_map_nodes
      alter column release_id set not null
    """,
    """
    alter table product.knowledge_map_nodes
      drop constraint knowledge_map_nodes_pkey
    """,
    """
    do $migration$
    declare
      legacy_business_key text;
    begin
      select constraint_row.conname
      into legacy_business_key
      from (
        select
          constraint_def.conname,
          array_agg(attribute_def.attname::text order by key_column.ordinality)
            as column_names
        from pg_constraint constraint_def
        cross join lateral unnest(constraint_def.conkey)
          with ordinality as key_column(attnum, ordinality)
        join pg_attribute attribute_def
          on attribute_def.attrelid = constraint_def.conrelid
         and attribute_def.attnum = key_column.attnum
        where constraint_def.conrelid =
              'product.knowledge_map_nodes'::regclass
          and constraint_def.contype = 'u'
        group by constraint_def.conname
      ) constraint_row
      where constraint_row.column_names = array[
        'exam_scope',
        'track',
        'module_name',
        'node_type',
        'tag_type',
        'tag_value'
      ]::text[];

      if legacy_business_key is null then
        raise exception using
          errcode = '42704',
          message = 'legacy knowledge-map business key was not found';
      end if;

      execute format(
        'alter table product.knowledge_map_nodes drop constraint %I',
        legacy_business_key
      );
    end
    $migration$
    """,
    """
    alter table product.knowledge_map_nodes
      add constraint knowledge_map_nodes_pkey
      primary key (release_id, node_id)
    """,
    """
    alter table product.knowledge_map_nodes
      add constraint product_knowledge_map_nodes_release_business_key
      unique (
        release_id,
        exam_scope,
        track,
        module_name,
        node_type,
        tag_type,
        tag_value
      )
    """,
    """
    alter table product.knowledge_map_nodes
      add constraint product_knowledge_map_nodes_release_scope_fkey
      foreign key (release_id, exam_scope, track)
      references product.knowledge_map_releases(release_id, exam_scope, track)
    """,
    """
    alter table product.knowledge_map_nodes
      add constraint product_knowledge_map_nodes_parent_fkey
      foreign key (release_id, parent_node_id)
      references product.knowledge_map_nodes(release_id, node_id)
      deferrable initially deferred
    """,
    """
    drop index product.product_knowledge_map_nodes_rank_idx
    """,
    """
    create index product_knowledge_map_nodes_rank_idx
      on product.knowledge_map_nodes(
        release_id,
        status,
        support_status,
        near5_weight desc nulls last,
        near5_occurrence_count desc nulls last
      )
    """,
    """
    drop index product.product_knowledge_map_nodes_tag_idx
    """,
    """
    create index product_knowledge_map_nodes_tag_idx
      on product.knowledge_map_nodes(
        release_id,
        module_name,
        tag_type,
        tag_value
      )
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_versioned_release_data(bind):
        raise RuntimeError(
            "Knowledge-map releases beyond the fixed legacy release exist; "
            "destructive downgrade is forbidden. Repair with a forward migration."
        )

    op.execute(
        "alter table product.knowledge_map_nodes "
        "drop constraint product_knowledge_map_nodes_parent_fkey"
    )
    op.execute(
        "alter table product.knowledge_map_nodes "
        "drop constraint product_knowledge_map_nodes_release_scope_fkey"
    )
    op.execute(
        "alter table product.knowledge_map_nodes "
        "drop constraint product_knowledge_map_nodes_release_business_key"
    )
    op.execute(
        "alter table product.knowledge_map_nodes "
        "drop constraint knowledge_map_nodes_pkey"
    )
    op.execute(
        "alter table product.knowledge_map_nodes "
        "add constraint knowledge_map_nodes_pkey primary key (node_id)"
    )
    op.execute(
        """
        alter table product.knowledge_map_nodes
          add constraint knowledge_map_nodes_exam_scope_track_module_key
          unique (
            exam_scope,
            track,
            module_name,
            node_type,
            tag_type,
            tag_value
          )
        """
    )
    op.execute("drop index product.product_knowledge_map_nodes_rank_idx")
    op.execute(
        """
        create index product_knowledge_map_nodes_rank_idx
          on product.knowledge_map_nodes(
            exam_scope,
            track,
            status,
            support_status,
            near5_weight desc nulls last,
            near5_occurrence_count desc nulls last
          )
        """
    )
    op.execute("drop index product.product_knowledge_map_nodes_tag_idx")
    op.execute(
        """
        create index product_knowledge_map_nodes_tag_idx
          on product.knowledge_map_nodes(
            exam_scope,
            track,
            module_name,
            tag_type,
            tag_value
          )
        """
    )
    op.drop_column("knowledge_map_nodes", "release_id", schema="product")
    op.execute("drop table product.knowledge_map_releases")


def _has_versioned_release_data(bind: Connection) -> bool:
    return bool(
        bind.execute(
            text(
                """
                select exists (
                  select 1
                  from product.knowledge_map_releases
                  where release_version <>
                        :legacy_prefix || ':' || exam_scope || ':' || track
                     or release_id <> md5(
                          'knowledge-map-legacy|' || exam_scope || '|' || track
                        )::uuid
                     or source_data_version <> 'legacy:source-version-unknown'
                     or vocab_version <> 'legacy:vocab-version-unknown'
                     or generator_version <> 'legacy:generator-version-unknown'
                     or status <> 'retired'
                )
                """
            ),
            {"legacy_prefix": LEGACY_RELEASE_VERSION_PREFIX},
        ).scalar_one()
    )
