"""Align mock trajectory idempotency scope and name conflict constraints.

Trajectory replay lookup is scoped by ``(user_id, command_id)``. The original
database constraint made ``command_id`` globally unique, so two users could not
legitimately reuse the same client-generated idempotency key. This migration
aligns storage with the service contract and gives both trajectory conflict
constraints stable names that the repository can translate without masking
unrelated integrity failures.

Revision ID: 20260727_64
Revises: 20260727_63
Create Date: 2026-07-27
"""

from typing import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260727_64"
down_revision: str | Sequence[str] | None = "20260727_63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    alter table learner.mock_exam_trajectory_events
      drop constraint mock_exam_trajectory_events_command_id_key
    """,
    """
    alter table learner.mock_exam_trajectory_events
      add constraint mock_exam_trajectory_user_command_key
      unique (user_id, command_id)
    """,
    """
    alter table learner.mock_exam_trajectory_events
      rename constraint mock_exam_trajectory_events_session_id_event_seq_key
      to mock_exam_trajectory_session_seq_key
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_cross_user_command_reuse(bind):
        raise RuntimeError(
            "Mock trajectory command IDs are reused across users; "
            "a global command_id constraint cannot be restored safely."
        )
    op.execute(
        """
        alter table learner.mock_exam_trajectory_events
          rename constraint mock_exam_trajectory_session_seq_key
          to mock_exam_trajectory_events_session_id_event_seq_key
        """
    )
    op.execute(
        """
        alter table learner.mock_exam_trajectory_events
          drop constraint mock_exam_trajectory_user_command_key
        """
    )
    op.execute(
        """
        alter table learner.mock_exam_trajectory_events
          add constraint mock_exam_trajectory_events_command_id_key
          unique (command_id)
        """
    )


def _has_cross_user_command_reuse(bind: Connection) -> bool:
    return bool(
        bind.execute(
            text(
                """
                select exists (
                  select 1
                  from learner.mock_exam_trajectory_events
                  group by command_id
                  having count(distinct user_id) > 1
                )
                """
            )
        ).scalar_one()
    )
