"""Add canonical immutable essay learning artifacts.

Revision ID: 20260713_12
Revises: 20260713_11
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260713_12"
down_revision: str | Sequence[str] | None = "20260713_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_unique_constraint(
        "essay_answer_artifact_owner_task_key",
        "essay_answer_versions",
        ["answer_version_id", "user_id", "task_version_id"],
        schema="learning",
    )
    op.create_unique_constraint(
        "essay_review_artifact_binding_key",
        "essay_review_versions",
        [
            "review_version_id",
            "user_id",
            "task_version_id",
            "material_bundle_version_id",
            "rubric_version_id",
        ],
        schema="learning",
    )
    op.create_table(
        "essay_learning_artifact_versions",
        sa.Column("artifact_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("material_bundle_version_id", sa.Uuid(), nullable=False),
        sa.Column("rubric_version_id", sa.Uuid(), nullable=True),
        sa.Column("review_version_id", sa.Uuid(), nullable=True),
        sa.Column("answer_version_id", sa.Uuid(), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_artifact_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "artifact_type in ('material_annotation', 'point_extraction', "
            "'outline', 'review_acknowledgement', 'reflection')",
            name="essay_learning_artifact_type_check",
        ),
        sa.CheckConstraint("version_no >= 1", name="essay_learning_artifact_version_check"),
        sa.CheckConstraint("length(scope_key) = 64", name="essay_learning_artifact_scope_sha_check"),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 and length(input_fingerprint) = 64",
            name="essay_learning_artifact_hashes_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object' and payload->>'schemaType' = artifact_type",
            name="essay_learning_artifact_payload_check",
        ),
        sa.CheckConstraint(
            "(artifact_type = 'material_annotation' and rubric_version_id is null "
            "and review_version_id is null and answer_version_id is null) or "
            "(artifact_type in ('point_extraction', 'outline') "
            "and rubric_version_id is not null and review_version_id is null "
            "and answer_version_id is null) or "
            "(artifact_type = 'review_acknowledgement' "
            "and rubric_version_id is not null and review_version_id is not null "
            "and answer_version_id is null) or "
            "(artifact_type = 'reflection' and rubric_version_id is not null "
            "and answer_version_id is not null)",
            name="essay_learning_artifact_binding_shape_check",
        ),
        sa.CheckConstraint(
            "supersedes_artifact_version_id is null or "
            "supersedes_artifact_version_id <> artifact_version_id",
            name="essay_learning_artifact_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            ["task_version_id"],
            ["product.essay_task_versions.task_version_id"],
            name="essay_learning_artifact_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["material_bundle_version_id", "task_version_id"],
            [
                "product.essay_material_bundle_versions.material_bundle_version_id",
                "product.essay_material_bundle_versions.task_version_id",
            ],
            name="essay_learning_artifact_material_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["rubric_version_id", "task_version_id"],
            [
                "product.essay_rubric_versions.rubric_version_id",
                "product.essay_rubric_versions.task_version_id",
            ],
            name="essay_learning_artifact_rubric_task_fk",
        ),
        sa.ForeignKeyConstraint(
            [
                "review_version_id",
                "user_id",
                "task_version_id",
                "material_bundle_version_id",
                "rubric_version_id",
            ],
            [
                "learning.essay_review_versions.review_version_id",
                "learning.essay_review_versions.user_id",
                "learning.essay_review_versions.task_version_id",
                "learning.essay_review_versions.material_bundle_version_id",
                "learning.essay_review_versions.rubric_version_id",
            ],
            name="essay_learning_artifact_review_binding_fk",
        ),
        sa.ForeignKeyConstraint(
            ["answer_version_id", "user_id", "task_version_id"],
            [
                "learning.essay_answer_versions.answer_version_id",
                "learning.essay_answer_versions.user_id",
                "learning.essay_answer_versions.task_version_id",
            ],
            name="essay_learning_artifact_answer_owner_task_fk",
        ),
        sa.PrimaryKeyConstraint("artifact_version_id"),
        sa.UniqueConstraint("command_id", name="essay_learning_artifact_command_key"),
        sa.UniqueConstraint(
            "user_id",
            "scope_key",
            "version_no",
            name="essay_learning_artifact_scope_version_key",
        ),
        sa.UniqueConstraint(
            "artifact_version_id",
            "user_id",
            "scope_key",
            name="essay_learning_artifact_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_essay_artifact_one_successor_idx",
        "essay_learning_artifact_versions",
        ["supersedes_artifact_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_artifact_version_id is not null"),
    )
    op.create_index(
        "learning_essay_artifact_current_task_idx",
        "essay_learning_artifact_versions",
        ["user_id", "task_version_id", "artifact_type", "created_at"],
        schema="learning",
    )
    op.execute(
        """
        create function learning.validate_essay_learning_artifact_chain()
        returns trigger language plpgsql as $$
        declare
          previous learning.essay_learning_artifact_versions%rowtype;
        begin
          if new.version_no = 1 then
            if new.supersedes_artifact_version_id is not null then
              raise exception 'first artifact version cannot supersede another version';
            end if;
            if exists (
              select 1 from learning.essay_learning_artifact_versions item
              where item.user_id = new.user_id and item.scope_key = new.scope_key
            ) then
              raise exception 'artifact scope already has a first version';
            end if;
          else
            if new.supersedes_artifact_version_id is null then
              raise exception 'artifact successor must reference its predecessor';
            end if;
            select * into previous
            from learning.essay_learning_artifact_versions item
            where item.artifact_version_id = new.supersedes_artifact_version_id
            for share;
            if not found
               or previous.user_id <> new.user_id
               or previous.scope_key <> new.scope_key
               or previous.artifact_type <> new.artifact_type
               or previous.task_version_id <> new.task_version_id
               or previous.material_bundle_version_id <> new.material_bundle_version_id
               or previous.rubric_version_id is distinct from new.rubric_version_id
               or previous.review_version_id is distinct from new.review_version_id
               or previous.answer_version_id is distinct from new.answer_version_id
               or new.version_no <> previous.version_no + 1 then
              raise exception 'artifact successor must be contiguous in one exact scope';
            end if;
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger essay_learning_artifact_validate_chain
        before insert on learning.essay_learning_artifact_versions
        for each row execute function learning.validate_essay_learning_artifact_chain()
        """
    )
    op.execute(
        """
        create trigger essay_learning_artifact_append_only
        before update or delete on learning.essay_learning_artifact_versions
        for each row execute function learning.reject_essay_intelligence_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "drop trigger essay_learning_artifact_append_only "
        "on learning.essay_learning_artifact_versions"
    )
    op.execute(
        "drop trigger essay_learning_artifact_validate_chain "
        "on learning.essay_learning_artifact_versions"
    )
    op.execute("drop function learning.validate_essay_learning_artifact_chain()")
    op.drop_index(
        "learning_essay_artifact_current_task_idx",
        table_name="essay_learning_artifact_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_essay_artifact_one_successor_idx",
        table_name="essay_learning_artifact_versions",
        schema="learning",
    )
    op.drop_table("essay_learning_artifact_versions", schema="learning")
    op.drop_constraint(
        "essay_review_artifact_binding_key",
        "essay_review_versions",
        schema="learning",
        type_="unique",
    )
    op.drop_constraint(
        "essay_answer_artifact_owner_task_key",
        "essay_answer_versions",
        schema="learning",
        type_="unique",
    )
