"""Freeze release-scoped question-to-node bindings and delivery facts.

Revision ID: 20260727_68
Revises: 20260727_67
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260727_68"
down_revision: str | Sequence[str] | None = "20260727_67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    alter table product.knowledge_map_releases
      add column question_binding_count integer not null default 0,
      add column question_binding_fingerprint text
    """,
    """
    alter table product.knowledge_map_releases
      add constraint product_knowledge_map_release_binding_manifest_check
      check (
        (
          question_binding_count = 0
          and question_binding_fingerprint is null
        )
        or (
          question_binding_count > 0
          and question_binding_fingerprint ~ '^[0-9a-f]{64}$'
        )
      )
    """,
    """
    create table product.knowledge_map_question_bindings (
      release_id uuid not null,
      node_id text not null,
      question_id text not null,
      content_version_id text not null
        check (content_version_id ~ '^[0-9a-f]{64}$'),
      module_code text not null check (char_length(btrim(module_code)) > 0),
      canonical_module text not null
        check (char_length(btrim(canonical_module)) > 0),
      binding_kind text not null
        check (binding_kind in ('direct', 'collapsed')),
      source_tag_type text not null
        check (char_length(btrim(source_tag_type)) > 0),
      source_tag_value text not null
        check (char_length(btrim(source_tag_value)) > 0),
      delivery_eligible boolean not null,
      visual_required boolean not null,
      shared_context_required boolean not null,
      latest_year integer,
      occurrence_count integer not null check (occurrence_count >= 0),
      latest_recent_exam_year integer,
      recent_occurrence_count integer not null
        check (recent_occurrence_count >= 0),
      latest_old_exam_year integer,
      old_occurrence_count integer not null
        check (old_occurrence_count >= 0),
      binding_fingerprint text not null
        check (binding_fingerprint ~ '^[0-9a-f]{64}$'),
      created_at timestamptz not null default transaction_timestamp(),
      primary key (release_id, node_id, question_id),
      foreign key (release_id, node_id)
        references product.knowledge_map_nodes(release_id, node_id)
        on delete restrict,
      foreign key (question_id, content_version_id)
        references product.question_content_versions(question_id, content_version_id)
        on delete restrict,
      check (
        (recent_occurrence_count = 0 and latest_recent_exam_year is null)
        or (
          recent_occurrence_count > 0
          and latest_recent_exam_year is not null
        )
      ),
      check (
        (old_occurrence_count = 0 and latest_old_exam_year is null)
        or (
          old_occurrence_count > 0
          and latest_old_exam_year is not null
        )
      )
    )
    """,
    """
    create index product_knowledge_map_binding_module_selection_idx
      on product.knowledge_map_question_bindings (
        release_id,
        module_code,
        delivery_eligible,
        node_id,
        latest_year desc,
        occurrence_count desc,
        question_id
      )
    """,
    """
    create index product_knowledge_map_binding_question_lookup_idx
      on product.knowledge_map_question_bindings (
        release_id,
        question_id,
        node_id
      )
    """,
    """
    create function product.knowledge_map_question_binding_row_fingerprint(
      binding product.knowledge_map_question_bindings
    )
    returns text
    language sql
    immutable
    strict
    as $$
      select encode(
        digest(
          convert_to(
            jsonb_build_object(
              'release_id', binding.release_id,
              'node_id', binding.node_id,
              'question_id', binding.question_id,
              'content_version_id', binding.content_version_id,
              'module_code', binding.module_code,
              'canonical_module', binding.canonical_module,
              'binding_kind', binding.binding_kind,
              'source_tag_type', binding.source_tag_type,
              'source_tag_value', binding.source_tag_value,
              'delivery_eligible', binding.delivery_eligible,
              'visual_required', binding.visual_required,
              'shared_context_required', binding.shared_context_required,
              'latest_year', binding.latest_year,
              'occurrence_count', binding.occurrence_count,
              'latest_recent_exam_year', binding.latest_recent_exam_year,
              'recent_occurrence_count', binding.recent_occurrence_count,
              'latest_old_exam_year', binding.latest_old_exam_year,
              'old_occurrence_count', binding.old_occurrence_count
            )::text,
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      )
    $$
    """,
    """
    create function product.knowledge_map_question_binding_authority_is_valid(
      binding product.knowledge_map_question_bindings
    )
    returns boolean
    language sql
    stable
    strict
    security definer
    set search_path = pg_catalog, product
    as $$
      select exists (
        select 1
        from product.knowledge_map_releases release
        join product.knowledge_map_nodes node
          on node.release_id = release.release_id
         and node.node_id = binding.node_id
        join product.question_content_versions content
          on content.question_id = binding.question_id
         and content.content_version_id = binding.content_version_id
        join product.question_content_heads content_head
          on content_head.question_id = content.question_id
         and content_head.content_version_id = content.content_version_id
        join product.questions question
          on question.question_id = content.question_id
        join product.vocab_module_alias_map canonical
          on canonical.version = release.vocab_version
         and canonical.action = 'canonical'
         and canonical.raw_module_name = binding.canonical_module
         and canonical.canonical_code = binding.module_code
        join product.vocab_module_alias_map source_module
          on source_module.version = release.vocab_version
         and source_module.action in ('canonical', 'map')
         and source_module.raw_module_name =
             content.snapshot_payload->>'module_name'
         and source_module.canonical_code = binding.module_code
        where release.release_id = binding.release_id
          and release.status = 'draft'
          and node.exam_scope = release.exam_scope
          and node.track = release.track
          and node.module_name = binding.canonical_module
          and node.status = 'published'
          and content.track = release.track
          and content.snapshot_payload->>'question_id' = binding.question_id
          and question.module_name =
              content.snapshot_payload->>'module_name'
          and question.visual_required is not distinct from
              binding.visual_required
          and question.shared_context_required is not distinct from
              binding.shared_context_required
          and question.latest_year is not distinct from binding.latest_year
          and coalesce(question.occurrence_count, 0) =
              binding.occurrence_count
          and exists (
            select 1
            from product.question_tags question_tag
            where question_tag.question_id = binding.question_id
              and question_tag.tag_type = binding.source_tag_type
              and question_tag.tag_value = binding.source_tag_value
          )
          and (
            (
              binding.binding_kind = 'direct'
              and node.tag_type = binding.source_tag_type
              and node.tag_value = binding.source_tag_value
            )
            or (
              binding.binding_kind = 'collapsed'
              and exists (
                select 1
                from jsonb_array_elements(
                  case
                    when jsonb_typeof(node.evidence->'collapsed_from') = 'array'
                      then node.evidence->'collapsed_from'
                    else '[]'::jsonb
                  end
                ) collapsed
                where collapsed->>'node_type' = binding.source_tag_type
                  and collapsed->>'tag_value' = binding.source_tag_value
              )
            )
          )
      )
    $$
    """,
    """
    create function product.knowledge_map_question_binding_manifest(
      target_release_id uuid
    )
    returns table (
      binding_count integer,
      binding_fingerprint text
    )
    language sql
    stable
    strict
    as $$
      select
        count(*)::integer,
        encode(
          digest(
            convert_to(
              coalesce(
                string_agg(
                  concat_ws(
                    E'\\x1f',
                    binding.node_id,
                    binding.question_id,
                    binding.binding_fingerprint
                  ),
                  E'\\x1e'
                  order by binding.node_id, binding.question_id
                ),
                ''
              ),
              'UTF8'
            ),
            'sha256'
          ),
          'hex'
        )
      from product.knowledge_map_question_bindings binding
      where binding.release_id = target_release_id
    $$
    """,
    """
    create function product.guard_knowledge_map_question_binding_mutation()
    returns trigger
    language plpgsql
    as $$
    declare
      source_release_id uuid;
      target_release_id uuid;
      expected_fingerprint text;
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
          message = 'active or retired knowledge-map question bindings are immutable';
      end if;

      if tg_op in ('INSERT', 'UPDATE') then
        expected_fingerprint :=
          product.knowledge_map_question_binding_row_fingerprint(new);
        if new.binding_fingerprint is distinct from expected_fingerprint then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map question binding fingerprint is invalid';
        end if;
        if not product.knowledge_map_question_binding_authority_is_valid(new) then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map question binding authority is invalid';
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
    create trigger knowledge_map_question_bindings_immutable
      before insert or update or delete
      on product.knowledge_map_question_bindings
      for each row
      execute function product.guard_knowledge_map_question_binding_mutation()
    """,
    """
    drop trigger knowledge_map_releases_lifecycle_guard
      on product.knowledge_map_releases
    """,
    """
    create or replace function product.guard_knowledge_map_release_lifecycle()
    returns trigger
    language plpgsql
    as $$
    declare
      actual_node_count integer;
      invalid_node_count integer;
      actual_binding_count integer;
      actual_binding_fingerprint text;
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
          to_jsonb(new)
            - array[
                'status',
                'node_count',
                'activated_at',
                'question_binding_count',
                'question_binding_fingerprint'
              ]
        ) is distinct from (
          to_jsonb(old)
            - array[
                'status',
                'node_count',
                'activated_at',
                'question_binding_count',
                'question_binding_fingerprint'
              ]
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
            message = 'knowledge-map release activation freshness is invalid';
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

        select manifest.binding_count, manifest.binding_fingerprint
        into actual_binding_count, actual_binding_fingerprint
        from product.knowledge_map_question_binding_manifest(old.release_id) manifest;

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
        if actual_binding_count <= 0
           or new.question_binding_count <> actual_binding_count
           or new.question_binding_fingerprint is distinct from
              actual_binding_fingerprint then
          raise exception using
            errcode = '23514',
            message = 'knowledge-map release question binding manifest is incomplete';
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
          to_jsonb(new) - array['status', 'retired_at']
        ) is distinct from (
          to_jsonb(old) - array['status', 'retired_at']
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
)


_RESTORE_V66_LIFECYCLE = """
create or replace function product.guard_knowledge_map_release_lifecycle()
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
"""


_DOWN_STATEMENTS = (
    "drop trigger knowledge_map_releases_lifecycle_guard on product.knowledge_map_releases",
    _RESTORE_V66_LIFECYCLE,
    """
    create trigger knowledge_map_releases_lifecycle_guard
      before insert or update or delete
      on product.knowledge_map_releases
      for each row
      execute function product.guard_knowledge_map_release_lifecycle()
    """,
    """
    drop trigger knowledge_map_question_bindings_immutable
      on product.knowledge_map_question_bindings
    """,
    "drop function product.guard_knowledge_map_question_binding_mutation()",
    """
    drop function product.knowledge_map_question_binding_authority_is_valid(
      product.knowledge_map_question_bindings
    )
    """,
    """
    drop function product.knowledge_map_question_binding_manifest(uuid)
    """,
    """
    drop function product.knowledge_map_question_binding_row_fingerprint(
      product.knowledge_map_question_bindings
    )
    """,
    "drop table product.knowledge_map_question_bindings",
    """
    alter table product.knowledge_map_releases
      drop constraint product_knowledge_map_release_binding_manifest_check,
      drop column question_binding_fingerprint,
      drop column question_binding_count
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bool(
        bind.execute(
            text(
                """
                select exists (
                  select 1
                  from product.knowledge_map_question_bindings
                )
                """
            )
        ).scalar_one()
    ):
        raise RuntimeError(
            "Knowledge-map question bindings exist; destructive downgrade is "
            "forbidden. Repair with a forward migration."
        )
    for statement in _DOWN_STATEMENTS:
        op.execute(statement)
