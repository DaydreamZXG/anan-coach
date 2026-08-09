"""Let a learner choose balanced, Xingce, or essay focus for today.

Revision ID: 20260809_104
Revises: 20260809_103
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260809_104"
down_revision: str | Sequence[str] | None = "20260809_103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "daily_capacity_versions",
        sa.Column(
            "preferred_track",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'balanced'"),
        ),
        schema="learning",
    )
    op.execute(
        "update learning.daily_capacity_versions "
        "set preferred_track = 'xingce' "
        "where preferred_xingce_module is not null"
    )
    op.create_check_constraint(
        "daily_capacity_preferred_track_check",
        "daily_capacity_versions",
        "preferred_track in ('balanced', 'xingce', 'essay')",
        schema="learning",
    )
    op.create_check_constraint(
        "daily_capacity_track_focus_consistency_check",
        "daily_capacity_versions",
        "(preferred_track = 'xingce' and preferred_xingce_module is not null) "
        "or (preferred_track in ('balanced', 'essay') "
        "and preferred_xingce_module is null and xingce_only = false)",
        schema="learning",
    )
    op.alter_column(
        "daily_capacity_versions",
        "preferred_track",
        server_default=None,
        schema="learning",
    )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260809_104 is forward-only: learner-owned daily track focus "
        "must remain readable"
    )
