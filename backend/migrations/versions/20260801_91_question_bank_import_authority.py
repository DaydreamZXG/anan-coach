"""Bring question-bank import fields into the Alembic authority.

The staging importer has long used a stable external source identifier and a
source correct-rate value. They existed in the live staging database but were
never declared by the repository migration chain, making a fresh database
unable to replay the importer. Both additions are nullable so existing
canonical questions remain unchanged.

Revision ID: 20260801_91
Revises: 20260801_90
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260801_91"
down_revision: str | Sequence[str] | None = "20260801_90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if not exists (
            select 1
            from information_schema.columns
            where table_schema = 'product'
              and table_name = 'questions'
              and column_name = 'representative_source_question_id'
          ) then
            alter table product.questions
              add column representative_source_question_id text;
          end if;
          if not exists (
            select 1
            from information_schema.columns
            where table_schema = 'product'
              and table_name = 'questions'
              and column_name = 'rate'
          ) then
            alter table product.questions add column rate text;
          end if;
        end
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260801_91 is forward-only: imported source identity must "
        "not be discarded after qbank finalization"
    )
