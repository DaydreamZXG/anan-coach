"""Enforce planning evidence integrity and explicit objective closure readiness.

Revision ID: 20260713_16
Revises: 20260713_15
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_16"
down_revision: str | Sequence[str] | None = "20260713_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Unknown objective closure counts are NULL. Zero is reserved for a canonical
    # objective closure reader that has positively observed no due work.
    op.alter_column(
        "daily_plan_versions",
        "due_reflection_count",
        existing_type=sa.Integer(),
        nullable=True,
        schema="learning",
    )
    op.alter_column(
        "daily_plan_versions",
        "due_verification_count",
        existing_type=sa.Integer(),
        nullable=True,
        schema="learning",
    )

    # A review intent repeats the full pinned content tuple. Make that tuple a
    # first-class attempt identity so a mismatched material or rubric fails at
    # the database boundary, including during migration of historical rows.
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1
            from learning.essay_review_intent_versions intent
            join learning.essay_attempts attempt
              on attempt.attempt_id = intent.attempt_id
             and attempt.user_id = intent.user_id
             and attempt.task_version_id = intent.task_version_id
            where row(
              intent.material_bundle_version_id,
              intent.rubric_version_id
            ) is distinct from row(
              attempt.material_bundle_version_id,
              attempt.rubric_version_id
            )
          ) then
            raise exception using
              errcode = '23514',
              message = 'essay review intent history contains an invalid attempt content tuple';
          end if;
        end;
        $$
        """
    )
    op.create_unique_constraint(
        "essay_attempt_content_identity_key",
        "essay_attempts",
        [
            "attempt_id",
            "user_id",
            "task_version_id",
            "material_bundle_version_id",
            "rubric_version_id",
        ],
        schema="learning",
    )
    op.drop_constraint(
        "essay_review_intent_attempt_owner_task_fk",
        "essay_review_intent_versions",
        schema="learning",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "essay_review_intent_attempt_content_fk",
        "essay_review_intent_versions",
        "essay_attempts",
        [
            "attempt_id",
            "user_id",
            "task_version_id",
            "material_bundle_version_id",
            "rubric_version_id",
        ],
        [
            "attempt_id",
            "user_id",
            "task_version_id",
            "material_bundle_version_id",
            "rubric_version_id",
        ],
        source_schema="learning",
        referent_schema="learning",
    )


def downgrade() -> None:
    op.drop_constraint(
        "essay_review_intent_attempt_content_fk",
        "essay_review_intent_versions",
        schema="learning",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "essay_review_intent_attempt_owner_task_fk",
        "essay_review_intent_versions",
        "essay_attempts",
        ["attempt_id", "user_id", "task_version_id"],
        ["attempt_id", "user_id", "task_version_id"],
        source_schema="learning",
        referent_schema="learning",
    )
    op.drop_constraint(
        "essay_attempt_content_identity_key",
        "essay_attempts",
        schema="learning",
        type_="unique",
    )

    # Downgrade intentionally fails closed when v16 rows contain unknown counts;
    # callers must first migrate them to genuine canonical values.
    op.alter_column(
        "daily_plan_versions",
        "due_verification_count",
        existing_type=sa.Integer(),
        nullable=False,
        schema="learning",
    )
    op.alter_column(
        "daily_plan_versions",
        "due_reflection_count",
        existing_type=sa.Integer(),
        nullable=False,
        schema="learning",
    )
