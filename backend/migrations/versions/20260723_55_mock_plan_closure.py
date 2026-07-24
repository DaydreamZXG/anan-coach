"""Bind weekly mock sessions to planning tasks.

Revision ID: 20260723_55
Revises: 20260723_54
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision = "20260723_55"
down_revision = "20260723_54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_bound_sessions(bind):
        raise RuntimeError(
            "Mock exam sessions are bound to planning tasks; "
            "a destructive downgrade is forbidden."
        )
    op.execute("drop index if exists learner.mock_exam_source_plan_task_unique_idx")
    op.drop_column(
        "mock_exam_sessions",
        "source_plan_task_id",
        schema="learner",
    )


def _has_bound_sessions(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                select exists (
                  select 1
                  from learner.mock_exam_sessions
                  where source_plan_task_id is not null
                )
                """
            )
        ).scalar_one()
    )


_STATEMENTS = (
    """
    alter table learner.mock_exam_sessions
      add column source_plan_task_id uuid
        references learning.daily_plan_tasks(task_id)
    """,
    """
    create unique index mock_exam_source_plan_task_unique_idx
      on learner.mock_exam_sessions(source_plan_task_id)
      where source_plan_task_id is not null
    """,
)


_TEST_STATEMENTS = _STATEMENTS
