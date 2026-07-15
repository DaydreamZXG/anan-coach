"""Add explicit account state, session revocation reasons, and account audit events.

Revision ID: 20260712_07
Revises: 20260712_06
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_07"
down_revision: str | Sequence[str] | None = "20260712_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("account_status", sa.Text(), nullable=False, server_default="active"),
        schema="learner",
    )
    op.add_column(
        "users",
        sa.Column("account_status_changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="learner",
    )
    op.create_check_constraint(
        "users_account_status_check",
        "users",
        "account_status in ('active', 'recovery_required', 'deletion_pending', 'disabled')",
        schema="learner",
    )
    op.execute(
        "update learner.users set account_status = 'disabled', "
        "account_status_changed_at = now() where is_active = false"
    )
    op.add_column(
        "app_sessions",
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        schema="learner",
    )
    op.create_check_constraint(
        "app_sessions_revoked_reason_check",
        "app_sessions",
        "revoked_reason is null or revoked_reason in "
        "('user_logout', 'user_logout_all', 'user_revoked_device', "
        "'account_recovery', 'security', 'expired_cleanup')",
        schema="learner",
    )
    op.create_table(
        "account_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "event_type in ('account_recovered', 'session_revoked', 'all_sessions_revoked', "
            "'account_recovery_required', 'account_deletion_requested', 'account_disabled')",
            name="account_events_type_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        schema="learner",
    )
    op.create_index(
        "learner_account_events_user_idx",
        "account_events",
        ["user_id", "created_at"],
        schema="learner",
    )


def downgrade() -> None:
    op.drop_index("learner_account_events_user_idx", table_name="account_events", schema="learner")
    op.drop_table("account_events", schema="learner")
    op.drop_constraint(
        "app_sessions_revoked_reason_check",
        "app_sessions",
        schema="learner",
        type_="check",
    )
    op.drop_column("app_sessions", "revoked_reason", schema="learner")
    op.drop_constraint("users_account_status_check", "users", schema="learner", type_="check")
    op.drop_column("users", "account_status_changed_at", schema="learner")
    op.drop_column("users", "account_status", schema="learner")
