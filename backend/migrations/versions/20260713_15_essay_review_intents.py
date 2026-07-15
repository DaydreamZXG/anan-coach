"""Add durable, versioned essay review intents.

Revision ID: 20260713_15
Revises: 20260713_14
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_15"
down_revision: str | Sequence[str] | None = "20260713_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "essay_review_intent_versions",
        sa.Column("intent_version_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("answer_version_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("material_bundle_version_id", sa.Uuid(), nullable=False),
        sa.Column("rubric_version_id", sa.Uuid(), nullable=False),
        sa.Column("review_scope", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("intent_fingerprint", sa.Text(), nullable=False),
        sa.Column("state_fingerprint", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("quota_feature", sa.Text(), nullable=True),
        sa.Column("quota_usage_date", sa.Date(), nullable=True),
        sa.Column("quota_used", sa.Integer(), nullable=True),
        sa.Column("quota_limit", sa.Integer(), nullable=True),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_intent_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "review_scope = 'grounded_full'",
            name="essay_review_intent_scope_check",
        ),
        sa.CheckConstraint(
            "version_no >= 1",
            name="essay_review_intent_version_check",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'waiting_quota', 'queued')",
            name="essay_review_intent_status_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64 and "
            "length(intent_fingerprint) = 64 and "
            "length(state_fingerprint) = 64",
            name="essay_review_intent_fingerprint_check",
        ),
        sa.CheckConstraint(
            "(status = 'pending' and "
            "num_nonnulls(job_id, quota_feature, quota_usage_date, quota_used, "
            "quota_limit, retry_after) = 0) or "
            "(status = 'waiting_quota' and job_id is null and "
            "quota_feature = 'essay_review' and "
            "num_nonnulls(quota_usage_date, quota_used, quota_limit, retry_after) = 4 "
            "and quota_limit >= 0 and quota_used >= quota_limit) or "
            "(status = 'queued' and job_id is not null and "
            "quota_feature = 'essay_review' and retry_after is null and "
            "num_nonnulls(quota_usage_date, quota_used, quota_limit) = 3 and "
            "quota_limit >= 1 and quota_used between 1 and quota_limit)",
            name="essay_review_intent_state_shape_check",
        ),
        sa.CheckConstraint(
            "retry_after is null or retry_after > created_at",
            name="essay_review_intent_retry_after_check",
        ),
        sa.CheckConstraint(
            "supersedes_intent_version_id is null or "
            "supersedes_intent_version_id <> intent_version_id",
            name="essay_review_intent_supersedes_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            ["attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_attempts.attempt_id",
                "learning.essay_attempts.user_id",
                "learning.essay_attempts.task_version_id",
            ],
            name="essay_review_intent_attempt_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["answer_version_id", "attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_answer_versions.answer_version_id",
                "learning.essay_answer_versions.attempt_id",
                "learning.essay_answer_versions.user_id",
                "learning.essay_answer_versions.task_version_id",
            ],
            name="essay_review_intent_answer_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["material_bundle_version_id", "task_version_id"],
            [
                "product.essay_material_bundle_versions.material_bundle_version_id",
                "product.essay_material_bundle_versions.task_version_id",
            ],
            name="essay_review_intent_material_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["rubric_version_id", "task_version_id", "material_bundle_version_id"],
            [
                "product.essay_rubric_versions.rubric_version_id",
                "product.essay_rubric_versions.task_version_id",
                "product.essay_rubric_versions.material_bundle_version_id",
            ],
            name="essay_review_intent_rubric_material_fk",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["platform.background_jobs.job_id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_intent_version_id"],
            ["learning.essay_review_intent_versions.intent_version_id"],
            name="essay_review_intent_supersedes_fk",
        ),
        sa.PrimaryKeyConstraint("intent_version_id"),
        sa.UniqueConstraint("command_id", name="essay_review_intent_command_key"),
        sa.UniqueConstraint(
            "intent_id",
            "version_no",
            name="essay_review_intent_version_key",
        ),
        sa.UniqueConstraint(
            "user_id",
            "answer_version_id",
            "review_scope",
            "version_no",
            name="essay_review_intent_answer_version_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_essay_review_intent_one_successor_idx",
        "essay_review_intent_versions",
        ["supersedes_intent_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_intent_version_id is not null"),
    )
    op.create_index(
        "learning_essay_review_intent_current_answer_idx",
        "essay_review_intent_versions",
        ["user_id", "answer_version_id", "review_scope", "version_no"],
        schema="learning",
    )
    op.create_index(
        "learning_essay_review_intent_job_idx",
        "essay_review_intent_versions",
        ["job_id"],
        schema="learning",
        postgresql_where=sa.text("job_id is not null"),
    )
    op.execute(
        """
        create function learning.enforce_essay_review_intent_insert()
        returns trigger language plpgsql as $$
        declare
          previous learning.essay_review_intent_versions%rowtype;
        begin
          if new.version_no = 1 then
            if new.supersedes_intent_version_id is not null or new.status <> 'pending' then
              raise exception using
                errcode = '23514',
                message = 'initial review intent version must be pending';
            end if;
            return new;
          end if;

          if new.supersedes_intent_version_id is null then
            raise exception using
              errcode = '23514',
              message = 'review intent successor must reference its prior version';
          end if;
          select * into previous
            from learning.essay_review_intent_versions
           where intent_version_id = new.supersedes_intent_version_id;
          if not found then
            raise exception using
              errcode = '23503',
              message = 'review intent prior version does not exist';
          end if;
          if row(
            new.intent_id, new.user_id, new.attempt_id, new.answer_version_id,
            new.task_version_id, new.material_bundle_version_id,
            new.rubric_version_id, new.review_scope, new.input_fingerprint,
            new.intent_fingerprint
          ) is distinct from row(
            previous.intent_id, previous.user_id, previous.attempt_id,
            previous.answer_version_id, previous.task_version_id,
            previous.material_bundle_version_id, previous.rubric_version_id,
            previous.review_scope, previous.input_fingerprint,
            previous.intent_fingerprint
          ) then
            raise exception using
              errcode = '23514',
              message = 'review intent identity is immutable across versions';
          end if;
          if new.version_no <> previous.version_no + 1 then
            raise exception using
              errcode = '23514',
              message = 'review intent version must increase by one';
          end if;
          if new.status not in ('waiting_quota', 'queued') then
            raise exception using
              errcode = '23514',
              message = 'review intent successor has an invalid state transition';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger essay_review_intent_validate_insert "
        "before insert on learning.essay_review_intent_versions "
        "for each row execute function learning.enforce_essay_review_intent_insert()"
    )
    op.execute(
        "create trigger essay_review_intent_versions_append_only "
        "before update or delete on learning.essay_review_intent_versions "
        "for each row execute function learning.reject_essay_intelligence_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "drop trigger essay_review_intent_versions_append_only "
        "on learning.essay_review_intent_versions"
    )
    op.execute(
        "drop trigger essay_review_intent_validate_insert "
        "on learning.essay_review_intent_versions"
    )
    op.execute("drop function learning.enforce_essay_review_intent_insert()")
    op.drop_index(
        "learning_essay_review_intent_job_idx",
        table_name="essay_review_intent_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_essay_review_intent_current_answer_idx",
        table_name="essay_review_intent_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_essay_review_intent_one_successor_idx",
        table_name="essay_review_intent_versions",
        schema="learning",
    )
    op.drop_table("essay_review_intent_versions", schema="learning")
