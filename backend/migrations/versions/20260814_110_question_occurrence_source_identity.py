"""Adopt restored question-occurrence source identity into schema authority.

Revision ID: 20260814_110
Revises: 20260813_109
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260814_110"
down_revision: str | Sequence[str] | None = "20260813_109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UPGRADE_STATEMENTS = (
    "alter table product.question_occurrences add column if not exists source_paper_row_id text",
    "alter table product.question_occurrences add column if not exists source_paper_key text",
    "alter table product.question_occurrences "
    "add column if not exists source_occurrence_question_key text",
    "alter table product.question_occurrences "
    "add column if not exists source_occurrence_paper_key text",
    "alter table product.question_occurrences add column if not exists cls text",
    "alter table product.question_occurrences add column if not exists occurrence_answer text",
    "alter table product.question_occurrences "
    "add column if not exists occurrence_total_count integer",
    "alter table product.question_occurrences add column if not exists occurrence_easy_wrong text",
    "alter table product.question_occurrences "
    "add column if not exists occurrence_most_wrong_answer jsonb",
    "create index if not exists question_occurrences_source_question_identity_idx "
    "on product.question_occurrences(source_dataset_id, source_occurrence_question_key) "
    "where source_occurrence_question_key is not null",
    "create index if not exists question_occurrences_source_paper_identity_idx "
    "on product.question_occurrences(source_dataset_id, source_paper_key) "
    "where source_paper_key is not null",
)


def upgrade() -> None:
    # The server recovery already added these columns. IF NOT EXISTS makes the
    # formal migration safe there and makes a fresh database reproduce it.
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "question occurrence source identity is forward-only and may already "
        "be referenced by published evidence"
    )
