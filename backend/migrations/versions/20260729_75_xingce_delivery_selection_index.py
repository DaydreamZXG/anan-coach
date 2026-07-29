"""Speed up account-scoped Xingce delivery candidate selection.

Revision ID: 20260729_75
Revises: 20260728_74
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260729_75"
down_revision: str | Sequence[str] | None = "20260728_74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEX_NAME = "product_knowledge_map_binding_baseline_selection_idx"


def upgrade() -> None:
    op.execute(
        f"""
        create index if not exists {INDEX_NAME}
          on product.knowledge_map_question_bindings (
            release_id, question_id
          )
          include (
            content_version_id,
            canonical_module,
            visual_required,
            shared_context_required,
            latest_year,
            occurrence_count
          )
          where delivery_eligible
        """
    )


def downgrade() -> None:
    op.execute(f"drop index if exists product.{INDEX_NAME}")
