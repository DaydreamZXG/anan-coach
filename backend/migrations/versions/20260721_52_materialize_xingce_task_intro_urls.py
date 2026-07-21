"""Materialize xingce task intro URLs with plan task identifiers.

Revision ID: 20260721_52
Revises: 20260721_51
Create Date: 2026-07-21
"""

from alembic import op


revision = "20260721_52"
down_revision = "20260721_51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing planning v2 rows are append-only. The application materializes
    # legacy xingce intro URLs at the read-model boundary instead of rewriting
    # historical task rows.
    op.execute("select 1")


def downgrade() -> None:
    op.execute("select 1")
