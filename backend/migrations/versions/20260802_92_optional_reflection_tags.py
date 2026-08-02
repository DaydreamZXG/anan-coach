"""Allow evidence-first Xingce reflection without manual cause tags.

Revision ID: 20260802_92
Revises: 20260801_91
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260802_92"
down_revision: str | Sequence[str] | None = "20260801_91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONSTRAINT_NAME = "xingce_reflection_versions_payload_check"


def upgrade() -> None:
    op.execute(
        f"""
        alter table learning.xingce_reflection_versions
          drop constraint {CONSTRAINT_NAME},
          add constraint {CONSTRAINT_NAME} check (
            jsonb_typeof(payload) = 'object'
            and jsonb_typeof(payload->'rawText') = 'string'
            and length(payload->>'rawText') <= 4000
            and jsonb_typeof(payload->'tags') = 'array'
          )
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260802_92 is forward-only: empty-tag reflections are "
        "truthful immutable learning evidence and must not be invalidated"
    )
