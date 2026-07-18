"""Bind essay OCR answers to confirmed handwriting evidence.

Revision ID: 20260716_32
Revises: 20260716_31
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_32"
down_revision: str | Sequence[str] | None = "20260716_31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1 from learning.essay_answer_versions
             where source_type = 'ocr_confirmed'
          ) then
            raise exception
              'legacy OCR essay answers lack immutable recognition evidence; '
              'resolve them before applying 20260716_32';
          end if;
        end;
        $$
        """
    )
    op.add_column(
        "essay_answer_versions",
        sa.Column("source_artifact_version_id", sa.Uuid(), nullable=True),
        schema="learning",
    )
    op.add_column(
        "essay_answer_versions",
        sa.Column("recognition_revision_id", sa.Uuid(), nullable=True),
        schema="learning",
    )
    op.create_check_constraint(
        "essay_answer_source_evidence_check",
        "essay_answer_versions",
        "(source_type = 'typed' and source_artifact_version_id is null "
        "and recognition_revision_id is null) or "
        "(source_type = 'ocr_confirmed' and source_artifact_version_id is not null "
        "and recognition_revision_id is not null)",
        schema="learning",
    )
    op.create_foreign_key(
        "essay_answer_recognition_evidence_fk",
        "essay_answer_versions",
        "recognition_revisions",
        ["recognition_revision_id", "user_id", "source_artifact_version_id"],
        ["recognition_revision_id", "owner_user_id", "artifact_version_id"],
        source_schema="learning",
        referent_schema="learning",
    )
    op.create_index(
        "learning_essay_answer_recognition_idx",
        "essay_answer_versions",
        ["recognition_revision_id"],
        schema="learning",
        postgresql_where=sa.text("recognition_revision_id is not null"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_handwriting_answers(bind):
        raise RuntimeError(
            "Handwriting-backed essay answers exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    op.drop_index(
        "learning_essay_answer_recognition_idx",
        table_name="essay_answer_versions",
        schema="learning",
    )
    op.drop_constraint(
        "essay_answer_recognition_evidence_fk",
        "essay_answer_versions",
        schema="learning",
        type_="foreignkey",
    )
    op.drop_constraint(
        "essay_answer_source_evidence_check",
        "essay_answer_versions",
        schema="learning",
        type_="check",
    )
    op.drop_column(
        "essay_answer_versions", "recognition_revision_id", schema="learning"
    )
    op.drop_column(
        "essay_answer_versions", "source_artifact_version_id", schema="learning"
    )


def _has_handwriting_answers(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "select exists (select 1 from learning.essay_answer_versions "
                "where recognition_revision_id is not null)"
            )
        ).scalar_one()
    )
