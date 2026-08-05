"""Add knowledge_nodes AI mapping to product.method_cards.

Method cards carry a ``tag_path`` describing the teaching approach (e.g.
``判断推理/逻辑判断/因果推理``) while the knowledge tree nodes describe the
exam-point taxonomy (e.g. ``行测/判断推理/削弱题型``). Tag-path matching alone
only covers ~9% of cards against knowledge tree nodes, so the refresh
generator now also attaches cards through their AI-mapped ``knowledge_nodes``
(display_path array in each card's YAML, e.g.
``["行测/判断推理/削弱题型", ...]``).

This migration only touches ``product.method_cards``:
  * adds a ``knowledge_nodes`` jsonb column (JSON array of display path
    strings), ``NOT NULL DEFAULT '[]'::jsonb`` so existing cards without an AI
    mapping and the guard's exact-replay comparison share one normalized shape,
  * recreates the released-card mutation guard so an exact replay of a card
    referenced by an active/retired release also covers ``knowledge_nodes``.
    Downgrade restores migration 73's 17-field exact-replay guard shape after
    dropping the column.

The immutable ``product.knowledge_map_nodes`` table is not touched; release
guard and frozen-release integrity still hold.

Revision ID: 20260806_98
Revises: 20260805_97
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260806_98"
down_revision: str | Sequence[str] | None = "20260805_97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CARD_FIELDS = (
    "exam_scope",
    "track",
    "module_name",
    "tag_path",
    "title",
    "recognition",
    "applicability",
    "method_steps",
    "error_signatures",
    "fix_action",
    "time_budget_sec",
    "accuracy_rule",
    "pitfall",
    "author_note",
    "validation",
    "priority",
    "status",
    "knowledge_nodes",
)

# The pre-98 field list (migration 73's exact-replay guard shape). Restored on
# downgrade because dropping the knowledge_nodes column must also drop it from
# the exact-replay comparison.
_PRE_98_CARD_FIELDS = tuple(field for field in _CARD_FIELDS if field != "knowledge_nodes")


def _field_list(prefix: str, fields: tuple[str, ...] = _CARD_FIELDS) -> str:
    return ",\n".join(f"{prefix}{field}" for field in fields)


_EXACT_REPLAY_GUARD = f"""
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
        {_field_list('existing.')}
      ) is not distinct from (
        {_field_list('new.')}
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


_LEGACY_GUARD = f"""
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
        {_field_list('existing.', _PRE_98_CARD_FIELDS)}
      ) is not distinct from (
        {_field_list('new.', _PRE_98_CARD_FIELDS)}
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


def upgrade() -> None:
    op.execute(
        """
        alter table product.method_cards
          add column knowledge_nodes jsonb not null default '[]'::jsonb
        """
    )
    op.execute(_EXACT_REPLAY_GUARD)


def downgrade() -> None:
    op.execute(
        """
        alter table product.method_cards
          drop column knowledge_nodes
        """
    )
    op.execute(_LEGACY_GUARD)
