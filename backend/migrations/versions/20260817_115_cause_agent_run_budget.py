"""Align cause-agent run counts with the bounded route budget.

Revision ID: 20260817_115
Revises: 20260817_114
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260817_115"
down_revision: str | Sequence[str] | None = "20260817_114"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT_NAME = "agent_run_versions_model_call_count_check"


def _replace_model_call_count_constraint(*, maximum: int) -> None:
    op.drop_constraint(
        _CONSTRAINT_NAME,
        "agent_run_versions",
        schema="ai_audit",
        type_="check",
    )
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "agent_run_versions",
        f"model_call_count between 0 and {maximum}",
        schema="ai_audit",
    )


def upgrade() -> None:
    _replace_model_call_count_constraint(maximum=6)


def downgrade() -> None:
    bind = op.get_bind()
    has_routed_run = bind.execute(
        text(
            """
            select exists (
              select 1
              from ai_audit.agent_run_versions
              where model_call_count > 2
            )
            """
        )
    ).scalar_one()
    if has_routed_run:
        raise RuntimeError(
            "cause-agent routed run counts must be retired before migration downgrade"
        )
    _replace_model_call_count_constraint(maximum=2)
