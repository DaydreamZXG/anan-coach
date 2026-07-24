"""Add durable learner avatar style.

Revision ID: 20260723_54
Revises: 20260721_53
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op


revision = "20260723_54"
down_revision = "20260721_53"
branch_labels = None
depends_on = None


AVATAR_STYLES = "'ocean', 'aurora', 'sunrise', 'violet', 'graphite', 'mint'"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "avatar_style",
            sa.Text(),
            nullable=False,
            server_default="ocean",
        ),
        schema="learner",
    )
    op.create_check_constraint(
        "users_avatar_style_check",
        "users",
        f"avatar_style in ({AVATAR_STYLES})",
        schema="learner",
    )


def downgrade() -> None:
    op.drop_constraint(
        "users_avatar_style_check",
        "users",
        schema="learner",
        type_="check",
    )
    op.drop_column("users", "avatar_style", schema="learner")
