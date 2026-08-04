"""Allow a learner to override today's Xingce focus without rewriting study preferences.

Revision ID: 20260804_95
Revises: 20260802_94
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_95"
down_revision: str | Sequence[str] | None = "20260802_94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_capacity_versions",
        sa.Column("preferred_xingce_module", sa.Text(), nullable=True),
        schema="learning",
    )
    op.add_column(
        "daily_capacity_versions",
        sa.Column(
            "xingce_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="learning",
    )
    op.create_check_constraint(
        "daily_capacity_xingce_focus_check",
        "daily_capacity_versions",
        "preferred_xingce_module is null or preferred_xingce_module in "
        "('言语理解与表达', '数量关系', '判断推理', '资料分析', '常识判断', '政治理论')",
        schema="learning",
    )
    op.create_check_constraint(
        "daily_capacity_xingce_only_requires_focus_check",
        "daily_capacity_versions",
        "xingce_only = false or preferred_xingce_module is not null",
        schema="learning",
    )
    op.alter_column(
        "daily_capacity_versions",
        "xingce_only",
        server_default=None,
        schema="learning",
    )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260804_95 is forward-only: persisted learner-owned daily focus "
        "overrides must remain readable"
    )
