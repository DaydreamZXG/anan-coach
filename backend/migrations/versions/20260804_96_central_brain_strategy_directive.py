"""Add the central-brain weekly strategy directive inference and per-day suggestion guard.

The central brain (中控大脑) decision agent reads the full learning state and
infers a weekly strategy directive. Its output lands as a new inference type
``strategy_directive`` on the existing append-only strategy candidate table
(producer_type=agent), then flows through the existing user-confirmation flow.
This migration:

1. widens the ``inference_candidate_versions`` inference_type check to include
   ``strategy_directive``;
2. adds ``learning_brain_agent_suggestions`` — the per-day idempotency guard
   for the passive daily-first-entry trigger, so a day never recomputes/writes
   the strategy candidate twice.

Revision ID: 20260804_96
Revises: 20260804_95
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_96"
down_revision: str | Sequence[str] | None = "20260804_95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "inference_candidate_versions_inference_type_check",
        "inference_candidate_versions",
        schema="learning",
    )
    op.create_check_constraint(
        "inference_candidate_versions_inference_type_check",
        "inference_candidate_versions",
        "inference_type in ('error_pattern', 'module_interpretation', "
        "'strategy_recommendation', 'strategy_directive', 'planning_explanation')",
        schema="learning",
    )
    op.create_table(
        "learning_brain_agent_suggestions",
        sa.Column("suggestion_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("trigger_kind", sa.Text(), nullable=False),
        sa.Column("profile_version_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_version_id", sa.Uuid(), nullable=True),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trigger_kind in ('daily_first_entry', 'cause_confirmed', 'on_demand')",
            name="learning_brain_agent_suggestion_trigger_check",
        ),
        sa.CheckConstraint(
            "char_length(input_fingerprint) = 64",
            name="learning_brain_agent_suggestion_fingerprint_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["learner.users.user_id"],
            name="learning_brain_agent_suggestion_user_fk",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_version_id"],
            ["learning.inference_candidate_versions.candidate_version_id"],
            name="learning_brain_agent_suggestion_candidate_fk",
        ),
        sa.PrimaryKeyConstraint(
            "suggestion_id",
            name="learning_brain_agent_suggestions_pkey",
        ),
        sa.UniqueConstraint(
            "command_id",
            name="learning_brain_agent_suggestions_command_key",
        ),
        sa.UniqueConstraint(
            "user_id",
            "local_date",
            name="learning_brain_agent_suggestions_user_date_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_brain_agent_suggestions_user_date_idx",
        "learning_brain_agent_suggestions",
        ["user_id", "local_date"],
        schema="learning",
    )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260804_96 is forward-only: the central-brain strategy "
        "directive inference and its per-day idempotency guard must remain "
        "readable once written"
    )
