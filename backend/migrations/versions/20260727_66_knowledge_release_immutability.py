"""Freeze published knowledge-map releases and their governed dependencies.

Revision ID: 20260727_66
Revises: 20260727_65
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260727_66"
down_revision: str | Sequence[str] | None = "20260727_65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FIXED_LEGACY_RELEASE_PREFIX = "knowledge-map@legacy-pre-20260727-65:"


_STATEMENTS = (
    """
    create function product.guard_referenced_vocab_version_mutation()
    returns trigger
    language plpgsql
    as $$
    declare
      affected_versions text[];
    begin
      affected_versions := case tg_op
        when 'INSERT' then array[new.version]
        when 'DELETE' then array[old.version]
        else array[old.version, new.version]
      end;

      if exists (
        select 1
        from product.knowledge_map_releases release
        where release.vocab_version = any(affected_versions)
      ) then
        raise exception using
          errcode = '23514',
          message = 'referenced knowledge vocabulary versions are immutable';
      end if;

      if tg_op = 'DELETE' then
        return old;
      end if;
      return new;
    end
    $$
    """,
    """
    create trigger vocab_module_alias_map_referenced_version_immutable
      before insert or update or delete
      on product.vocab_module_alias_map
      for each row
      execute function product.guard_referenced_vocab_version_mutation()
    """,
    """
    create trigger vocab_facet_rules_referenced_version_immutable
      before insert or update or delete
      on product.vocab_facet_rules
      for each row
      execute function product.guard_referenced_vocab_version_mutation()
    """,
    """
    create function product.guard_published_knowledge_map_node_mutation()
    returns trigger
    language plpgsql
    as $$
    declare
      source_release_id uuid;
      target_release_id uuid;
    begin
      source_release_id := case
        when tg_op in ('UPDATE', 'DELETE') then old.release_id
        else null
      end;
      target_release_id := case
        when tg_op in ('INSERT', 'UPDATE') then new.release_id
        else null
      end;

      if exists (
        select 1
        from product.knowledge_map_releases release
        where release.release_id in (source_release_id, target_release_id)
          and release.status in ('active', 'retired')
      ) then
        raise exception using
          errcode = '23514',
          message = 'active or retired knowledge-map nodes are immutable';
      end if;

      if tg_op = 'DELETE' then
        return old;
      end if;
      return new;
    end
    $$
    """,
    """
    create trigger knowledge_map_nodes_published_release_immutable
      before insert or update or delete
      on product.knowledge_map_nodes
      for each row
      execute function product.guard_published_knowledge_map_node_mutation()
    """,
    """
    create function product.guard_knowledge_map_release_lifecycle()
    returns trigger
    language plpgsql
    as $$
    declare
      actual_node_count integer;
      invalid_node_count integer;
    begin
      if tg_op = 'INSERT' then
        if new.status <> 'draft' then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map releases must be created as draft';
        end if;
        return new;
      end if;

      if tg_op = 'DELETE' then
        if old.status in ('active', 'retired') then
          raise exception using
            errcode = '23514',
            message = 'active or retired knowledge-map releases are immutable';
        end if;
        return old;
      end if;

      if old.status = 'draft' then
        if new.status = 'draft' then
          return new;
        end if;
        if new.status <> 'active' then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map releases must transition from draft to active';
        end if;
        if (
          new.release_id,
          new.release_version,
          new.exam_scope,
          new.track,
          new.source_data_version,
          new.vocab_version,
          new.generator_version,
          new.source_fingerprint,
          new.generated_at,
          new.fresh_until,
          new.supersedes_release_id
        ) is distinct from (
          old.release_id,
          old.release_version,
          old.exam_scope,
          old.track,
          old.source_data_version,
          old.vocab_version,
          old.generator_version,
          old.source_fingerprint,
          old.generated_at,
          old.fresh_until,
          old.supersedes_release_id
        ) then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map release identity changed during activation';
        end if;
        if new.activated_at is null
          or new.activated_at < new.generated_at
          or new.activated_at >= new.fresh_until then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map release activation time is outside its validity window';
        end if;

        select
          count(*)::integer,
          count(*) filter (
            where node.status is distinct from 'published'
              or node.exam_scope is distinct from new.exam_scope
              or node.track is distinct from new.track
          )::integer
        into actual_node_count, invalid_node_count
        from product.knowledge_map_nodes node
        where node.release_id = old.release_id;

        if actual_node_count <= 0 or new.node_count <> actual_node_count then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map release node count does not match frozen content';
        end if;
        if invalid_node_count > 0 then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map release contains unpublished or out-of-scope nodes';
        end if;
        return new;
      end if;

      if old.status = 'active' then
        if new.status <> 'retired' then
          raise exception using
            errcode = '23514',
            message = 'active knowledge-map releases may only transition to retired';
        end if;
        if (
          new.release_id,
          new.release_version,
          new.exam_scope,
          new.track,
          new.source_data_version,
          new.vocab_version,
          new.generator_version,
          new.source_fingerprint,
          new.generated_at,
          new.fresh_until,
          new.node_count,
          new.activated_at,
          new.supersedes_release_id
        ) is distinct from (
          old.release_id,
          old.release_version,
          old.exam_scope,
          old.track,
          old.source_data_version,
          old.vocab_version,
          old.generator_version,
          old.source_fingerprint,
          old.generated_at,
          old.fresh_until,
          old.node_count,
          old.activated_at,
          old.supersedes_release_id
        ) or new.retired_at is null
          or new.retired_at < old.activated_at then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map active-to-retired transition is invalid';
        end if;
        return new;
      end if;

      raise exception using
        errcode = '23514',
        message = 'retired knowledge-map releases are immutable';
    end
    $$
    """,
    """
    create trigger knowledge_map_releases_lifecycle_guard
      before insert or update or delete
      on product.knowledge_map_releases
      for each row
      execute function product.guard_knowledge_map_release_lifecycle()
    """,
    """
    create function product.guard_released_method_card_mutation()
    returns trigger
    language plpgsql
    as $$
    declare
      affected_method_card_ids text[];
    begin
      affected_method_card_ids := case tg_op
        when 'INSERT' then array[new.id]
        when 'DELETE' then array[old.id]
        else array[old.id, new.id]
      end;

      if exists (
        select 1
        from product.knowledge_map_nodes node
        join product.knowledge_map_releases release
          on release.release_id = node.release_id
        where release.status in ('active', 'retired')
          and node.method_card_ids ?| affected_method_card_ids
      ) then
        raise exception using
          errcode = '23514',
          message = 'method cards referenced by published knowledge releases are immutable';
      end if;
      if tg_op = 'DELETE' then
        return old;
      end if;
      return new;
    end
    $$
    """,
    """
    create trigger method_cards_released_content_immutable
      before insert or update or delete
      on product.method_cards
      for each row
      execute function product.guard_released_method_card_mutation()
    """,
)


_DOWN_STATEMENTS = (
    "drop trigger method_cards_released_content_immutable on product.method_cards",
    "drop function product.guard_released_method_card_mutation()",
    "drop trigger knowledge_map_releases_lifecycle_guard on product.knowledge_map_releases",
    "drop function product.guard_knowledge_map_release_lifecycle()",
    "drop trigger knowledge_map_nodes_published_release_immutable on product.knowledge_map_nodes",
    "drop function product.guard_published_knowledge_map_node_mutation()",
    "drop trigger vocab_facet_rules_referenced_version_immutable on product.vocab_facet_rules",
    "drop trigger vocab_module_alias_map_referenced_version_immutable on product.vocab_module_alias_map",
    "drop function product.guard_referenced_vocab_version_mutation()",
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def _assert_safe_downgrade() -> None:
    connection = op.get_bind()
    unsafe_release_exists = connection.execute(
        text(
            """
            select exists (
              select 1
              from product.knowledge_map_releases release
              where not (
                release.release_version =
                  :fixed_legacy_prefix || release.exam_scope || ':' || release.track
                and release.release_id = md5(
                  'knowledge-map-legacy|' || release.exam_scope || '|' || release.track
                )::uuid
                and release.source_data_version = 'legacy:source-version-unknown'
                and release.vocab_version = 'legacy:vocab-version-unknown'
                and release.generator_version = 'legacy:generator-version-unknown'
                and release.status = 'retired'
                and release.node_count > 0
                and release.activated_at is null
                and release.retired_at is not null
                and release.supersedes_release_id is null
              )
            )
            """
        ),
        {"fixed_legacy_prefix": _FIXED_LEGACY_RELEASE_PREFIX},
    ).scalar_one()
    if unsafe_release_exists:
        raise RuntimeError(
            "knowledge release immutability cannot be downgraded after real "
            "release content exists; apply a forward migration instead"
        )


def downgrade() -> None:
    _assert_safe_downgrade()
    for statement in _DOWN_STATEMENTS:
        op.execute(statement)
