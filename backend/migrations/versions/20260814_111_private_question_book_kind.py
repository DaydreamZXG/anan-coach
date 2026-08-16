"""Admit private question books to the existing private-source lifecycle.

Revision ID: 20260814_111
Revises: 20260814_110
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260814_111"
down_revision: str | Sequence[str] | None = "20260814_110"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        do $$
        declare
          target_constraint text;
        begin
          select constraint_name into target_constraint
          from information_schema.check_constraints
          where constraint_schema = 'learning'
            and check_clause like '%document_kind%'
            and constraint_name in (
              select constraint_name
              from information_schema.constraint_column_usage
              where table_schema = 'learning'
                and table_name = 'private_knowledge_source_versions'
                and column_name = 'document_kind'
            )
          order by constraint_name
          limit 1;

          if target_constraint is not null then
            execute format(
              'alter table learning.private_knowledge_source_versions drop constraint %I',
              target_constraint
            );
          end if;

          alter table learning.private_knowledge_source_versions
            add constraint private_knowledge_document_kind_check
            check (document_kind in ('lecture', 'note', 'summary', 'question_book'));
        end $$
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "private question-book source identity is forward-only and may already "
        "be referenced by user deletion and export records"
    )

