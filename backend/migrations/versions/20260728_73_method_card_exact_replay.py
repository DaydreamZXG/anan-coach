"""Allow exact method-card replay without weakening immutable releases.

Revision ID: 20260728_73
Revises: 20260728_72
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260728_73"
down_revision: str | Sequence[str] | None = "20260728_72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EXACT_REPLAY_GUARD = """
create or replace function product.guard_released_method_card_mutation()
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

  if not exists (
    select 1
    from product.knowledge_map_nodes node
    join product.knowledge_map_releases release
      on release.release_id = node.release_id
    where release.status in ('active', 'retired')
      and node.method_card_ids ?| affected_method_card_ids
  ) then
    if tg_op = 'DELETE' then
      return old;
    end if;
    return new;
  end if;

  if tg_op = 'INSERT' and exists (
    select 1
    from product.method_cards existing
    where existing.id = new.id
      and (
        existing.exam_scope,
        existing.track,
        existing.module_name,
        existing.tag_path,
        existing.title,
        existing.recognition,
        existing.applicability,
        existing.method_steps,
        existing.error_signatures,
        existing.fix_action,
        existing.time_budget_sec,
        existing.accuracy_rule,
        existing.pitfall,
        existing.author_note,
        existing.validation,
        existing.priority,
        existing.status
      ) is not distinct from (
        new.exam_scope,
        new.track,
        new.module_name,
        new.tag_path,
        new.title,
        new.recognition,
        new.applicability,
        new.method_steps,
        new.error_signatures,
        new.fix_action,
        new.time_budget_sec,
        new.accuracy_rule,
        new.pitfall,
        new.author_note,
        new.validation,
        new.priority,
        new.status
      )
  ) then
    return new;
  end if;

  raise exception using
    errcode = '23514',
    message = 'method cards referenced by published knowledge releases are immutable';
end
$$
"""


_LEGACY_GUARD = """
create or replace function product.guard_released_method_card_mutation()
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
"""


def upgrade() -> None:
    op.execute(_EXACT_REPLAY_GUARD)


def downgrade() -> None:
    op.execute(_LEGACY_GUARD)
