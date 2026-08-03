"""Allow TokenRhythm essay candidates without rewriting historical Grok facts.

Revision ID: 20260802_93
Revises: 20260802_92
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260802_93"
down_revision: str | Sequence[str] | None = "20260802_92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONSTRAINT_NAME = "essay_review_checkpoint_phase_check"


def upgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        "essay_review_checkpoints",
        schema="ai_audit",
        type_="check",
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "essay_review_checkpoints",
        "phase ~ '^(initial|repair|candidate:(gemini|grok|tokenrhythm|hunyuan)|adjudication)$'",
        schema="ai_audit",
    )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260802_93 is forward-only: TokenRhythm checkpoints are "
        "immutable review evidence and must not be invalidated"
    )
