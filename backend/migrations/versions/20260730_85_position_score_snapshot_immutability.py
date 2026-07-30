"""Close OLD/NEW version escapes in frozen position score snapshots.

Revision ID: 20260730_85
Revises: 20260730_84
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260730_85"
down_revision: str | Sequence[str] | None = "20260730_84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    create or replace function product.guard_frozen_position_score_snapshot()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, product
    as $$
    begin
      if tg_op in ('UPDATE', 'DELETE')
         and product.frozen_position_score_snapshot_exists(
           old.position_data_version
         ) then
        raise exception using
          errcode = '23514',
          message = 'active or retired position score snapshots are immutable';
      end if;
      if tg_op in ('INSERT', 'UPDATE')
         and product.frozen_position_score_snapshot_exists(
           new.position_data_version
         ) then
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
    drop trigger if exists position_score_snapshot_batches_frozen_release
      on product.position_score_snapshot_batches
    """,
    """
    create trigger position_score_snapshot_batches_frozen_release
      before insert or update or delete
      on product.position_score_snapshot_batches
      for each row execute function
        product.guard_frozen_position_score_snapshot()
    """,
    """
    drop trigger if exists position_score_snapshots_frozen_release
      on product.position_score_snapshots
    """,
    """
    create trigger position_score_snapshots_frozen_release
      before insert or update or delete
      on product.position_score_snapshots
      for each row execute function
        product.guard_frozen_position_score_snapshot()
    """,
)


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _STATEMENTS:
        connection.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260730_85 is forward-only: frozen score immutability cannot be weakened"
    )
