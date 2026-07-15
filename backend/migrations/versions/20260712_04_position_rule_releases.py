"""Add server-owned position rule and data release registry.

Revision ID: 20260712_04
Revises: 20260712_03
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_04"
down_revision: str | Sequence[str] | None = "20260712_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "position_rule_releases",
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("exam_code", sa.Text(), nullable=False),
        sa.Column("exam_year", sa.Integer(), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("position_data_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("exam_year between 2000 and 2100", name="position_rule_releases_exam_year_check"),
        sa.CheckConstraint(
            "status in ('draft', 'active', 'retired')",
            name="position_rule_releases_status_check",
        ),
        sa.CheckConstraint(
            "(status = 'active' and activated_at is not null) or status <> 'active'",
            name="position_rule_releases_activation_check",
        ),
        sa.PrimaryKeyConstraint("release_id"),
        sa.UniqueConstraint(
            "exam_code",
            "exam_year",
            "rule_set_version",
            "position_data_version",
            name="position_rule_releases_version_key",
        ),
        schema="product",
    )
    op.create_index(
        "product_position_rule_releases_one_active_idx",
        "position_rule_releases",
        ["exam_code", "exam_year"],
        unique=True,
        schema="product",
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "product_position_rule_releases_one_active_idx",
        table_name="position_rule_releases",
        schema="product",
    )
    op.drop_table("position_rule_releases", schema="product")
