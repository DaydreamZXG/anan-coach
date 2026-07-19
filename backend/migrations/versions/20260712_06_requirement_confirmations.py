"""Add append-only learner confirmations for manual position requirements.

Revision ID: 20260712_06
Revises: 20260712_05
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_06"
down_revision: str | Sequence[str] | None = "20260712_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qualification_requirement_confirmations",
        sa.Column("confirmation_id", sa.Uuid(), nullable=False),
        sa.Column("change_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version", sa.BigInteger(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("supersedes_confirmation_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("profile_version >= 1", name="requirement_confirmations_profile_version_check"),
        sa.CheckConstraint(
            "response in ('satisfied', 'not_satisfied', 'uncertain')",
            name="requirement_confirmations_response_check",
        ),
        sa.CheckConstraint(
            "statement is null or char_length(statement) <= 500",
            name="requirement_confirmations_statement_length_check",
        ),
        sa.ForeignKeyConstraint(["change_id"], ["learner.qualification_profile_changes.change_id"]),
        sa.ForeignKeyConstraint(["position_id"], ["product.positions.position_id"]),
        sa.ForeignKeyConstraint(["requirement_id"], ["product.position_requirements.requirement_id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_confirmation_id"],
            ["learner.qualification_requirement_confirmations.confirmation_id"],
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("confirmation_id"),
        sa.UniqueConstraint("command_id", name="requirement_confirmations_command_id_key"),
        sa.UniqueConstraint(
            "user_id",
            "requirement_id",
            "profile_version",
            name="requirement_confirmations_user_requirement_version_key",
        ),
        schema="learner",
    )
    op.create_index(
        "learner_requirement_confirmations_current_idx",
        "qualification_requirement_confirmations",
        ["user_id", "position_id", "requirement_id", "profile_version"],
        schema="learner",
    )


def downgrade() -> None:
    op.drop_index(
        "learner_requirement_confirmations_current_idx",
        table_name="qualification_requirement_confirmations",
        schema="learner",
    )
    op.drop_table("qualification_requirement_confirmations", schema="learner")
