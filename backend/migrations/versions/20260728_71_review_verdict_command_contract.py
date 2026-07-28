"""Expand Xingce commands for review-verdict receipts.

Revision ID: 20260728_71
Revises: 20260728_70
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260728_71"
down_revision: str | Sequence[str] | None = "20260728_70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EXISTING_COMMAND_TYPES = (
    "session_start",
    "checkpoint_save",
    "attempt_submit",
    "reflection_submit",
    "cause_followup",
    "cause_decision",
)
_COMMAND_TYPES = (*_EXISTING_COMMAND_TYPES, "review_verdict")


def _shape_constraint(command_types: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{command_type}'" for command_type in command_types)
    return f"""
    alter table learning.xingce_commands
      add constraint xingce_commands_wave1_shape_check check (
        char_length(command_id) between 8 and 260
        and length(request_fingerprint) = 64
        and jsonb_typeof(response_payload) = 'object'
        and command_type in ({allowed})
      )
    """


_DROP_SHAPE_CONSTRAINTS = (
    "alter table learning.xingce_commands "
    "drop constraint if exists xingce_commands_command_type_check",
    "alter table learning.xingce_commands "
    "drop constraint if exists xingce_commands_stage2_shape_check",
    "alter table learning.xingce_commands "
    "drop constraint if exists xingce_commands_stage3_ai_shape_check",
    "alter table learning.xingce_commands "
    "drop constraint if exists xingce_commands_wave1_shape_check",
)


def _replace_shape_constraint(command_types: tuple[str, ...]) -> None:
    for statement in _DROP_SHAPE_CONSTRAINTS:
        op.execute(statement)
    op.execute(_shape_constraint(command_types))


def upgrade() -> None:
    _replace_shape_constraint(_COMMAND_TYPES)


def downgrade() -> None:
    has_review_verdict_facts = (
        op.get_bind()
        .execute(
            text(
                """
            select exists (
              select 1
              from learning.xingce_commands
              where command_type = 'review_verdict'
            )
            """
            )
        )
        .scalar_one()
    )
    if has_review_verdict_facts:
        raise RuntimeError(
            "BACKEND-COMMAND@v1 review-verdict receipts exist; "
            "use a forward compatibility migration"
        )
    _replace_shape_constraint(_EXISTING_COMMAND_TYPES)
