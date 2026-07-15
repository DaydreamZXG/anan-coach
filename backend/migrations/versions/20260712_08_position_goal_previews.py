"""Add expiring, single-use primary position goal previews.

Revision ID: 20260712_08
Revises: 20260712_07
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_08"
down_revision: str | Sequence[str] | None = "20260712_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "position_goal_previews",
        sa.Column("preview_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("eligibility_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("from_goal_version_id", sa.Uuid(), nullable=True),
        sa.Column("learner_profile_version", sa.BigInteger(), nullable=False),
        sa.Column("position_data_version", sa.Text(), nullable=False),
        sa.Column("target_score", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_impact", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by_command_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "learner_profile_version >= 1",
            name="position_goal_previews_profile_version_check",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="position_goal_previews_expiry_check",
        ),
        sa.CheckConstraint(
            "(used_at is null and used_by_command_id is null) or "
            "(used_at is not null and used_by_command_id is not null)",
            name="position_goal_previews_usage_check",
        ),
        sa.ForeignKeyConstraint(
            ["eligibility_snapshot_id"],
            ["learner.eligibility_snapshots.snapshot_id"],
        ),
        sa.ForeignKeyConstraint(
            ["from_goal_version_id"],
            ["learner.position_goal_versions.goal_version_id"],
        ),
        sa.ForeignKeyConstraint(["position_id"], ["product.positions.position_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("preview_id"),
        sa.UniqueConstraint("request_id", name="position_goal_previews_request_id_key"),
        sa.UniqueConstraint(
            "used_by_command_id",
            name="position_goal_previews_used_command_key",
        ),
        schema="learner",
    )
    op.create_index(
        "learner_position_goal_previews_active_idx",
        "position_goal_previews",
        ["user_id", "expires_at"],
        schema="learner",
        postgresql_where=sa.text("used_at is null"),
    )


def downgrade() -> None:
    op.drop_index(
        "learner_position_goal_previews_active_idx",
        table_name="position_goal_previews",
        schema="learner",
    )
    op.drop_table("position_goal_previews", schema="learner")
