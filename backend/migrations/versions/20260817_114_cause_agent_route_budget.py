"""Allow the bounded multi-route cause-agent budget.

Revision ID: 20260817_114
Revises: 20260815_113
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260817_114"
down_revision: str | Sequence[str] | None = "20260815_113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT_NAME = "agent_definition_releases_budget_snapshot_check"
_CAUSE_DEFINITION_RELEASE = "xingce-cause-agent-2026-08-17.1"

_HISTORICAL_BUDGET_CONSTRAINT = """
jsonb_typeof(budget_snapshot) = 'object'
and (budget_snapshot->>'maxModelCalls')::integer = 2
and (budget_snapshot->>'maxToolCalls')::integer = 6
and (budget_snapshot->>'maxFollowupRounds')::integer = 1
and (budget_snapshot->>'maxConsecutiveNoProgress')::integer = 2
"""

_ROUTED_BUDGET_CONSTRAINT = f"""
jsonb_typeof(budget_snapshot) = 'object'
and (
  (
    definition_release_id = '{_CAUSE_DEFINITION_RELEASE}'
    and (budget_snapshot->>'maxModelCalls')::integer = 6
  )
  or
  (
    definition_release_id <> '{_CAUSE_DEFINITION_RELEASE}'
    and (budget_snapshot->>'maxModelCalls')::integer = 2
  )
)
and (budget_snapshot->>'maxToolCalls')::integer = 6
and (budget_snapshot->>'maxFollowupRounds')::integer = 1
and (budget_snapshot->>'maxConsecutiveNoProgress')::integer = 2
"""


def _replace_budget_constraint(check_condition: str) -> None:
    op.drop_constraint(
        _CONSTRAINT_NAME,
        "agent_definition_releases",
        schema="ai_audit",
        type_="check",
    )
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "agent_definition_releases",
        check_condition,
        schema="ai_audit",
    )


def upgrade() -> None:
    _replace_budget_constraint(_ROUTED_BUDGET_CONSTRAINT)


def downgrade() -> None:
    bind = op.get_bind()
    has_six_call_definition = bind.execute(
        text(
            """
            select exists (
              select 1
              from ai_audit.agent_definition_releases
              where (budget_snapshot->>'maxModelCalls')::integer = 6
            )
            """
        )
    ).scalar_one()
    if has_six_call_definition:
        raise RuntimeError(
            "cause-agent six-call definitions must be retired before migration downgrade"
        )
    _replace_budget_constraint(_HISTORICAL_BUDGET_CONSTRAINT)
