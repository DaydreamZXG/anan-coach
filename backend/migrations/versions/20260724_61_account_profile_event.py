"""Allow durable account profile update audit events.

Revision ID: 20260724_61
Revises: 20260724_60
Create Date: 2026-07-24
"""

from alembic import op


revision = "20260724_61"
down_revision = "20260724_60"
branch_labels = None
depends_on = None


BASE_EVENT_TYPES = (
    "'account_recovered', 'session_revoked', 'all_sessions_revoked', "
    "'account_recovery_required', 'account_deletion_requested', 'account_disabled'"
)


def _replace_event_type_constraint(allowed_event_types: str) -> None:
    op.execute(
        """
        alter table learner.account_events
          drop constraint if exists account_events_type_check
        """
    )
    op.execute(
        """
        alter table learner.account_events
          drop constraint if exists account_events_event_type_check
        """
    )
    op.create_check_constraint(
        "account_events_type_check",
        "account_events",
        f"event_type in ({allowed_event_types})",
        schema="learner",
    )


def upgrade() -> None:
    _replace_event_type_constraint(f"{BASE_EVENT_TYPES}, 'account_profile_updated'")


def downgrade() -> None:
    _replace_event_type_constraint(BASE_EVENT_TYPES)
