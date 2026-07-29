"""Allow tag-only Xingce reflection while preserving truthful user text.

Revision ID: 20260729_76
Revises: 20260729_75
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260729_76"
down_revision: str | Sequence[str] | None = "20260729_75"
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
            and jsonb_array_length(payload->'tags') > 0
          )
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        alter table learning.xingce_reflection_versions
          drop constraint {CONSTRAINT_NAME},
          add constraint {CONSTRAINT_NAME} check (
            jsonb_typeof(payload) = 'object'
            and length(trim(payload->>'rawText')) > 0
            and jsonb_typeof(payload->'tags') = 'array'
            and jsonb_array_length(payload->'tags') > 0
          )
        """
    )
