"""Add append-only, source-grounded essay intelligence v2 records.

Revision ID: 20260713_10
Revises: 20260713_09
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260713_10"
down_revision: str | Sequence[str] | None = "20260713_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "essay_task_versions",
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("task_key", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Text(), nullable=True),
        sa.Column("question_type", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.Text(), nullable=False),
        sa.Column("word_limit", sa.Integer(), nullable=False),
        sa.Column("maximum_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("supersedes_task_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version_no >= 1", name="essay_task_version_no_check"),
        sa.CheckConstraint("char_length(task_key) > 0", name="essay_task_key_check"),
        sa.CheckConstraint("char_length(prompt) > 0", name="essay_task_prompt_check"),
        sa.CheckConstraint(
            "length(prompt_sha256) = 64",
            name="essay_task_prompt_sha_check",
        ),
        sa.CheckConstraint(
            "question_type in ('summary', 'comprehensive_analysis', "
            "'countermeasure', 'implementation', 'composition')",
            name="essay_task_question_type_check",
        ),
        sa.CheckConstraint("word_limit between 1 and 5000", name="essay_task_word_limit_check"),
        sa.CheckConstraint(
            "maximum_score > 0 and maximum_score <= 100",
            name="essay_task_maximum_score_check",
        ),
        sa.CheckConstraint(
            "state in ('draft', 'published')",
            name="essay_task_state_check",
        ),
        sa.CheckConstraint(
            "supersedes_task_version_id is null "
            "or supersedes_task_version_id <> task_version_id",
            name="essay_task_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["product.questions.question_id"],
            name="essay_task_question_fk",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_task_version_id", "task_key"],
            [
                "product.essay_task_versions.task_version_id",
                "product.essay_task_versions.task_key",
            ],
            name="essay_task_supersedes_same_key_fk",
        ),
        sa.PrimaryKeyConstraint("task_version_id"),
        sa.UniqueConstraint("task_key", "version_no", name="essay_task_key_version_key"),
        sa.UniqueConstraint(
            "task_version_id",
            "task_key",
            name="essay_task_identity_key",
        ),
        schema="product",
    )
    op.create_index(
        "product_essay_task_one_successor_idx",
        "essay_task_versions",
        ["supersedes_task_version_id"],
        unique=True,
        schema="product",
        postgresql_where=sa.text("supersedes_task_version_id is not null"),
    )

    op.create_table(
        "essay_material_bundle_versions",
        sa.Column("material_bundle_version_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("task_key", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("completeness", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("supersedes_material_bundle_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version_no >= 1", name="essay_material_version_no_check"),
        sa.CheckConstraint(
            "completeness in ('complete', 'incomplete')",
            name="essay_material_completeness_check",
        ),
        sa.CheckConstraint(
            "source_kind in ('own_bank', 'official', 'external_confirmed')",
            name="essay_material_source_kind_check",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="essay_material_content_sha_check",
        ),
        sa.CheckConstraint(
            "supersedes_material_bundle_version_id is null "
            "or supersedes_material_bundle_version_id "
            "<> material_bundle_version_id",
            name="essay_material_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(
            ["task_version_id", "task_key"],
            [
                "product.essay_task_versions.task_version_id",
                "product.essay_task_versions.task_key",
            ],
            name="essay_material_task_fk",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_material_bundle_version_id",
                "task_version_id",
                "task_key",
            ],
            [
                "product.essay_material_bundle_versions.material_bundle_version_id",
                "product.essay_material_bundle_versions.task_version_id",
                "product.essay_material_bundle_versions.task_key",
            ],
            name="essay_material_supersedes_same_task_fk",
        ),
        sa.PrimaryKeyConstraint("material_bundle_version_id"),
        sa.UniqueConstraint(
            "task_version_id",
            "version_no",
            name="essay_material_task_version_key",
        ),
        sa.UniqueConstraint(
            "material_bundle_version_id",
            "task_version_id",
            "task_key",
            name="essay_material_identity_key",
        ),
        sa.UniqueConstraint(
            "material_bundle_version_id",
            "task_version_id",
            name="essay_material_task_identity_key",
        ),
        schema="product",
    )
    op.create_index(
        "product_essay_material_one_successor_idx",
        "essay_material_bundle_versions",
        ["supersedes_material_bundle_version_id"],
        unique=True,
        schema="product",
        postgresql_where=sa.text("supersedes_material_bundle_version_id is not null"),
    )

    op.create_table(
        "essay_material_segments",
        sa.Column("material_segment_id", sa.Uuid(), nullable=False),
        sa.Column("material_bundle_version_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Text(), nullable=False),
        sa.Column("segment_id", sa.Text(), nullable=False),
        sa.Column("material_order", sa.Integer(), nullable=False),
        sa.Column("segment_order", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.Text(), nullable=False),
        sa.Column("source_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("char_length(material_id) > 0", name="essay_segment_material_id_check"),
        sa.CheckConstraint("char_length(segment_id) > 0", name="essay_segment_id_check"),
        sa.CheckConstraint("material_order >= 1", name="essay_segment_material_order_check"),
        sa.CheckConstraint("segment_order >= 1", name="essay_segment_order_check"),
        sa.CheckConstraint("char_length(text) > 0", name="essay_segment_text_check"),
        sa.CheckConstraint("length(text_sha256) = 64", name="essay_segment_text_sha_check"),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="essay_segment_source_metadata_check",
        ),
        sa.ForeignKeyConstraint(
            ["material_bundle_version_id"],
            ["product.essay_material_bundle_versions.material_bundle_version_id"],
            name="essay_segment_bundle_fk",
        ),
        sa.PrimaryKeyConstraint("material_segment_id"),
        sa.UniqueConstraint(
            "material_bundle_version_id",
            "segment_id",
            name="essay_segment_bundle_segment_key",
        ),
        sa.UniqueConstraint(
            "material_bundle_version_id",
            "material_id",
            "material_order",
            "segment_order",
            name="essay_segment_bundle_position_key",
        ),
        schema="product",
    )

    op.create_table(
        "essay_rubric_versions",
        sa.Column("rubric_version_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("task_key", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("total_maximum", sa.Numeric(6, 2), nullable=False),
        sa.Column("criteria", JSONB, nullable=False),
        sa.Column("points", JSONB, nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("supersedes_rubric_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version_no >= 1", name="essay_rubric_version_no_check"),
        sa.CheckConstraint(
            "source_kind in ('official', 'system_compiled')",
            name="essay_rubric_source_kind_check",
        ),
        sa.CheckConstraint(
            "verification_status in ('verified', 'unverified')",
            name="essay_rubric_verification_check",
        ),
        sa.CheckConstraint(
            "source_kind <> 'system_compiled' or verification_status = 'unverified'",
            name="essay_rubric_system_unverified_check",
        ),
        sa.CheckConstraint(
            "state in ('draft', 'published')",
            name="essay_rubric_state_check",
        ),
        sa.CheckConstraint(
            "total_maximum > 0 and total_maximum <= 100",
            name="essay_rubric_total_maximum_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(criteria) = 'array' and jsonb_array_length(criteria) > 0",
            name="essay_rubric_criteria_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(points) = 'array' and jsonb_array_length(points) > 0",
            name="essay_rubric_points_check",
        ),
        sa.CheckConstraint("length(content_sha256) = 64", name="essay_rubric_content_sha_check"),
        sa.CheckConstraint(
            "supersedes_rubric_version_id is null "
            "or supersedes_rubric_version_id <> rubric_version_id",
            name="essay_rubric_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(
            ["task_version_id", "task_key"],
            [
                "product.essay_task_versions.task_version_id",
                "product.essay_task_versions.task_key",
            ],
            name="essay_rubric_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_rubric_version_id", "task_version_id", "task_key"],
            [
                "product.essay_rubric_versions.rubric_version_id",
                "product.essay_rubric_versions.task_version_id",
                "product.essay_rubric_versions.task_key",
            ],
            name="essay_rubric_supersedes_same_task_fk",
        ),
        sa.PrimaryKeyConstraint("rubric_version_id"),
        sa.UniqueConstraint(
            "task_version_id",
            "version_no",
            name="essay_rubric_task_version_key",
        ),
        sa.UniqueConstraint(
            "rubric_version_id",
            "task_version_id",
            "task_key",
            name="essay_rubric_identity_key",
        ),
        sa.UniqueConstraint(
            "rubric_version_id",
            "task_version_id",
            name="essay_rubric_task_identity_key",
        ),
        schema="product",
    )
    op.create_index(
        "product_essay_rubric_one_successor_idx",
        "essay_rubric_versions",
        ["supersedes_rubric_version_id"],
        unique=True,
        schema="product",
        postgresql_where=sa.text("supersedes_rubric_version_id is not null"),
    )

    op.create_table(
        "essay_attempts",
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("task_key", sa.Text(), nullable=False),
        sa.Column("material_bundle_version_id", sa.Uuid(), nullable=False),
        sa.Column("rubric_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_context", sa.Text(), nullable=False),
        sa.Column("timed_status", sa.Text(), nullable=False),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=True),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source_context in ('in_program', 'paper', 'external')",
            name="essay_attempt_source_context_check",
        ),
        sa.CheckConstraint(
            "timed_status in ('timed', 'untimed', 'unknown')",
            name="essay_attempt_timed_status_check",
        ),
        sa.CheckConstraint(
            "elapsed_seconds is null or elapsed_seconds >= 1",
            name="essay_attempt_elapsed_check",
        ),
        sa.CheckConstraint(
            "timed_status <> 'timed' or elapsed_seconds is not null",
            name="essay_attempt_timed_elapsed_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="essay_attempt_input_fingerprint_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            ["task_version_id", "task_key"],
            [
                "product.essay_task_versions.task_version_id",
                "product.essay_task_versions.task_key",
            ],
            name="essay_attempt_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["material_bundle_version_id", "task_version_id", "task_key"],
            [
                "product.essay_material_bundle_versions.material_bundle_version_id",
                "product.essay_material_bundle_versions.task_version_id",
                "product.essay_material_bundle_versions.task_key",
            ],
            name="essay_attempt_material_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["rubric_version_id", "task_version_id", "task_key"],
            [
                "product.essay_rubric_versions.rubric_version_id",
                "product.essay_rubric_versions.task_version_id",
                "product.essay_rubric_versions.task_key",
            ],
            name="essay_attempt_rubric_task_fk",
        ),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint("command_id", name="essay_attempt_command_key"),
        sa.UniqueConstraint(
            "user_id",
            "input_fingerprint",
            name="essay_attempt_user_input_key",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            "user_id",
            "task_version_id",
            name="essay_attempt_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_essay_attempt_user_task_idx",
        "essay_attempts",
        ["user_id", "task_version_id", "submitted_at"],
        schema="learning",
    )

    op.create_table(
        "essay_answer_versions",
        sa.Column("answer_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("answer_sha256", sa.Text(), nullable=False),
        sa.Column("thinking_payload", JSONB, nullable=True),
        sa.Column("thinking_sha256", sa.Text(), nullable=True),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_answer_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version_no >= 1", name="essay_answer_version_no_check"),
        sa.CheckConstraint(
            "source_type in ('typed', 'ocr_confirmed')",
            name="essay_answer_source_type_check",
        ),
        sa.CheckConstraint("char_length(answer_text) > 0", name="essay_answer_text_check"),
        sa.CheckConstraint("length(answer_sha256) = 64", name="essay_answer_sha_check"),
        sa.CheckConstraint(
            "(thinking_payload is null and thinking_sha256 is null) or "
            "(thinking_payload is not null and thinking_sha256 is not null "
            "and length(thinking_sha256) = 64)",
            name="essay_answer_thinking_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="essay_answer_input_fingerprint_check",
        ),
        sa.CheckConstraint(
            "thinking_payload is null or jsonb_typeof(thinking_payload) = 'object'",
            name="essay_answer_thinking_payload_check",
        ),
        sa.CheckConstraint(
            "supersedes_answer_version_id is null "
            "or supersedes_answer_version_id <> answer_version_id",
            name="essay_answer_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_attempts.attempt_id",
                "learning.essay_attempts.user_id",
                "learning.essay_attempts.task_version_id",
            ],
            name="essay_answer_attempt_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_answer_version_id",
                "attempt_id",
                "user_id",
                "task_version_id",
            ],
            [
                "learning.essay_answer_versions.answer_version_id",
                "learning.essay_answer_versions.attempt_id",
                "learning.essay_answer_versions.user_id",
                "learning.essay_answer_versions.task_version_id",
            ],
            name="essay_answer_supersedes_same_owner_task_fk",
        ),
        sa.PrimaryKeyConstraint("answer_version_id"),
        sa.UniqueConstraint("command_id", name="essay_answer_command_key"),
        sa.UniqueConstraint(
            "attempt_id",
            "version_no",
            name="essay_answer_attempt_version_key",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            "input_fingerprint",
            name="essay_answer_attempt_input_key",
        ),
        sa.UniqueConstraint(
            "answer_version_id",
            "attempt_id",
            "user_id",
            "task_version_id",
            name="essay_answer_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_essay_answer_one_successor_idx",
        "essay_answer_versions",
        ["supersedes_answer_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_answer_version_id is not null"),
    )

    op.create_table(
        "essay_review_versions",
        sa.Column("review_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("answer_version_id", sa.Uuid(), nullable=False),
        sa.Column("material_bundle_version_id", sa.Uuid(), nullable=False),
        sa.Column("rubric_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("review_scope", sa.Text(), nullable=False),
        sa.Column("thinking_evidence_available", sa.Boolean(), nullable=False),
        sa.Column("readiness", JSONB, nullable=False),
        sa.Column("score_lower", sa.Numeric(6, 2), nullable=True),
        sa.Column("score_recommended", sa.Numeric(6, 2), nullable=True),
        sa.Column("score_upper", sa.Numeric(6, 2), nullable=True),
        sa.Column("score_maximum", sa.Numeric(6, 2), nullable=True),
        sa.Column("score_confidence", sa.Text(), nullable=True),
        sa.Column("dimensions", JSONB, nullable=False),
        sa.Column("point_coverage", JSONB, nullable=False),
        sa.Column("claim_evidence", JSONB, nullable=False),
        sa.Column("findings", JSONB, nullable=False),
        sa.Column("rewrite_plan", JSONB, nullable=True),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("provider_schema_version", sa.Text(), nullable=False),
        sa.Column("rubric_contract_version", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("output_fingerprint", sa.Text(), nullable=True),
        sa.Column("supersedes_review_version_id", sa.Uuid(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('completed', 'blocked', 'failed')",
            name="essay_review_status_check",
        ),
        sa.CheckConstraint(
            "review_scope = 'grounded_full'",
            name="essay_review_scope_check",
        ),
        sa.CheckConstraint(
            "score_confidence is null or score_confidence in ('low', 'medium')",
            name="essay_review_confidence_check",
        ),
        sa.CheckConstraint(
            "(status = 'completed' and num_nonnulls(score_lower, score_recommended, "
            "score_upper, score_maximum, score_confidence, provider, model, "
            "output_fingerprint, rewrite_plan) = 9) or "
            "(status <> 'completed' and num_nonnulls(score_lower, score_recommended, "
            "score_upper, score_maximum, score_confidence, output_fingerprint, "
            "rewrite_plan) = 0)",
            name="essay_review_completion_fields_check",
        ),
        sa.CheckConstraint(
            "score_lower is null or (score_lower >= 0 "
            "and score_lower <= score_recommended "
            "and score_recommended <= score_upper "
            "and score_upper <= score_maximum and score_maximum <= 100)",
            name="essay_review_score_range_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(readiness) = 'object'",
            name="essay_review_readiness_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dimensions) = 'array' "
            "and jsonb_typeof(point_coverage) = 'array' "
            "and jsonb_typeof(claim_evidence) = 'array' "
            "and jsonb_typeof(findings) = 'array'",
            name="essay_review_arrays_check",
        ),
        sa.CheckConstraint(
            "rewrite_plan is null or jsonb_typeof(rewrite_plan) = 'object'",
            name="essay_review_rewrite_plan_check",
        ),
        sa.CheckConstraint(
            "(status = 'completed' "
            "and jsonb_array_length(dimensions) > 0 "
            "and jsonb_array_length(point_coverage) > 0 "
            "and jsonb_array_length(findings) > 0) "
            "or (status <> 'completed' "
            "and jsonb_array_length(dimensions) = 0 "
            "and jsonb_array_length(point_coverage) = 0 "
            "and jsonb_array_length(claim_evidence) = 0 "
            "and jsonb_array_length(findings) = 0)",
            name="essay_review_status_payload_check",
        ),
        sa.CheckConstraint("length(input_fingerprint) = 64", name="essay_review_input_sha_check"),
        sa.CheckConstraint(
            "output_fingerprint is null or length(output_fingerprint) = 64",
            name="essay_review_output_sha_check",
        ),
        sa.CheckConstraint("valid_until > generated_at", name="essay_review_validity_check"),
        sa.CheckConstraint(
            "supersedes_review_version_id is null "
            "or supersedes_review_version_id <> review_version_id",
            name="essay_review_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_attempts.attempt_id",
                "learning.essay_attempts.user_id",
                "learning.essay_attempts.task_version_id",
            ],
            name="essay_review_attempt_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["answer_version_id", "attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_answer_versions.answer_version_id",
                "learning.essay_answer_versions.attempt_id",
                "learning.essay_answer_versions.user_id",
                "learning.essay_answer_versions.task_version_id",
            ],
            name="essay_review_answer_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_review_version_id",
                "attempt_id",
                "user_id",
                "task_version_id",
            ],
            [
                "learning.essay_review_versions.review_version_id",
                "learning.essay_review_versions.attempt_id",
                "learning.essay_review_versions.user_id",
                "learning.essay_review_versions.task_version_id",
            ],
            name="essay_review_supersedes_same_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["material_bundle_version_id", "task_version_id"],
            [
                "product.essay_material_bundle_versions.material_bundle_version_id",
                "product.essay_material_bundle_versions.task_version_id",
            ],
            name="essay_review_material_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["rubric_version_id", "task_version_id"],
            [
                "product.essay_rubric_versions.rubric_version_id",
                "product.essay_rubric_versions.task_version_id",
            ],
            name="essay_review_rubric_task_fk",
        ),
        sa.PrimaryKeyConstraint("review_version_id"),
        sa.UniqueConstraint("command_id", name="essay_review_command_key"),
        sa.UniqueConstraint(
            "attempt_id",
            "input_fingerprint",
            name="essay_review_attempt_input_key",
        ),
        sa.UniqueConstraint(
            "review_version_id",
            "attempt_id",
            "user_id",
            "task_version_id",
            name="essay_review_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_essay_review_one_successor_idx",
        "essay_review_versions",
        ["supersedes_review_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_review_version_id is not null"),
    )
    op.create_index(
        "learning_essay_review_user_task_idx",
        "essay_review_versions",
        ["user_id", "task_version_id", "generated_at"],
        schema="learning",
    )

    op.create_table(
        "essay_rewrite_versions",
        sa.Column("rewrite_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_version_id", sa.Uuid(), nullable=False),
        sa.Column("base_review_version_id", sa.Uuid(), nullable=False),
        sa.Column("base_answer_version_id", sa.Uuid(), nullable=False),
        sa.Column("rewritten_answer_version_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_payload", JSONB, nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("output_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_rewrite_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "base_answer_version_id <> rewritten_answer_version_id",
            name="essay_rewrite_distinct_answers_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(analysis_payload) = 'object'",
            name="essay_rewrite_analysis_check",
        ),
        sa.CheckConstraint("length(input_fingerprint) = 64", name="essay_rewrite_input_sha_check"),
        sa.CheckConstraint("length(output_fingerprint) = 64", name="essay_rewrite_output_sha_check"),
        sa.CheckConstraint(
            "supersedes_rewrite_version_id is null "
            "or supersedes_rewrite_version_id <> rewrite_version_id",
            name="essay_rewrite_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_attempts.attempt_id",
                "learning.essay_attempts.user_id",
                "learning.essay_attempts.task_version_id",
            ],
            name="essay_rewrite_attempt_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["base_review_version_id", "attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_review_versions.review_version_id",
                "learning.essay_review_versions.attempt_id",
                "learning.essay_review_versions.user_id",
                "learning.essay_review_versions.task_version_id",
            ],
            name="essay_rewrite_review_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["base_answer_version_id", "attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_answer_versions.answer_version_id",
                "learning.essay_answer_versions.attempt_id",
                "learning.essay_answer_versions.user_id",
                "learning.essay_answer_versions.task_version_id",
            ],
            name="essay_rewrite_base_answer_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["rewritten_answer_version_id", "attempt_id", "user_id", "task_version_id"],
            [
                "learning.essay_answer_versions.answer_version_id",
                "learning.essay_answer_versions.attempt_id",
                "learning.essay_answer_versions.user_id",
                "learning.essay_answer_versions.task_version_id",
            ],
            name="essay_rewrite_new_answer_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_rewrite_version_id",
                "attempt_id",
                "user_id",
                "task_version_id",
            ],
            [
                "learning.essay_rewrite_versions.rewrite_version_id",
                "learning.essay_rewrite_versions.attempt_id",
                "learning.essay_rewrite_versions.user_id",
                "learning.essay_rewrite_versions.task_version_id",
            ],
            name="essay_rewrite_supersedes_same_owner_task_fk",
        ),
        sa.PrimaryKeyConstraint("rewrite_version_id"),
        sa.UniqueConstraint("command_id", name="essay_rewrite_command_key"),
        sa.UniqueConstraint(
            "attempt_id",
            "input_fingerprint",
            name="essay_rewrite_attempt_input_key",
        ),
        sa.UniqueConstraint(
            "rewrite_version_id",
            "attempt_id",
            "user_id",
            "task_version_id",
            name="essay_rewrite_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_essay_rewrite_one_successor_idx",
        "essay_rewrite_versions",
        ["supersedes_rewrite_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_rewrite_version_id is not null"),
    )

    op.create_table(
        "essay_transfer_verification_versions",
        sa.Column("verification_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_review_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("source_task_version_id", sa.Uuid(), nullable=False),
        sa.Column("transfer_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("transfer_task_version_id", sa.Uuid(), nullable=False),
        sa.Column("transfer_answer_version_id", sa.Uuid(), nullable=False),
        sa.Column("fresh_material_bundle_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("tested_criterion_codes", JSONB, nullable=False),
        sa.Column("evidence_payload", JSONB, nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("output_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_verification_version_id", sa.Uuid(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('verified', 'not_verified', 'inconclusive')",
            name="essay_transfer_status_check",
        ),
        sa.CheckConstraint(
            "source_attempt_id <> transfer_attempt_id "
            "and source_task_version_id <> transfer_task_version_id",
            name="essay_transfer_fresh_task_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tested_criterion_codes) = 'array' "
            "and jsonb_array_length(tested_criterion_codes) > 0",
            name="essay_transfer_criteria_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_payload) = 'object'",
            name="essay_transfer_evidence_check",
        ),
        sa.CheckConstraint("length(input_fingerprint) = 64", name="essay_transfer_input_sha_check"),
        sa.CheckConstraint("length(output_fingerprint) = 64", name="essay_transfer_output_sha_check"),
        sa.CheckConstraint("valid_until > generated_at", name="essay_transfer_validity_check"),
        sa.CheckConstraint(
            "supersedes_verification_version_id is null "
            "or supersedes_verification_version_id <> verification_version_id",
            name="essay_transfer_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            [
                "source_review_version_id",
                "source_attempt_id",
                "user_id",
                "source_task_version_id",
            ],
            [
                "learning.essay_review_versions.review_version_id",
                "learning.essay_review_versions.attempt_id",
                "learning.essay_review_versions.user_id",
                "learning.essay_review_versions.task_version_id",
            ],
            name="essay_transfer_source_review_owner_fk",
        ),
        sa.ForeignKeyConstraint(
            ["transfer_attempt_id", "user_id", "transfer_task_version_id"],
            [
                "learning.essay_attempts.attempt_id",
                "learning.essay_attempts.user_id",
                "learning.essay_attempts.task_version_id",
            ],
            name="essay_transfer_attempt_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            [
                "transfer_answer_version_id",
                "transfer_attempt_id",
                "user_id",
                "transfer_task_version_id",
            ],
            [
                "learning.essay_answer_versions.answer_version_id",
                "learning.essay_answer_versions.attempt_id",
                "learning.essay_answer_versions.user_id",
                "learning.essay_answer_versions.task_version_id",
            ],
            name="essay_transfer_answer_owner_task_fk",
        ),
        sa.ForeignKeyConstraint(
            ["fresh_material_bundle_version_id", "transfer_task_version_id"],
            [
                "product.essay_material_bundle_versions.material_bundle_version_id",
                "product.essay_material_bundle_versions.task_version_id",
            ],
            name="essay_transfer_material_task_fk",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_verification_version_id",
                "user_id",
                "source_review_version_id",
                "transfer_attempt_id",
            ],
            [
                "learning.essay_transfer_verification_versions.verification_version_id",
                "learning.essay_transfer_verification_versions.user_id",
                "learning.essay_transfer_verification_versions.source_review_version_id",
                "learning.essay_transfer_verification_versions.transfer_attempt_id",
            ],
            name="essay_transfer_supersedes_same_scope_fk",
        ),
        sa.PrimaryKeyConstraint("verification_version_id"),
        sa.UniqueConstraint("command_id", name="essay_transfer_command_key"),
        sa.UniqueConstraint(
            "user_id",
            "input_fingerprint",
            name="essay_transfer_user_input_key",
        ),
        sa.UniqueConstraint(
            "verification_version_id",
            "user_id",
            "source_review_version_id",
            "transfer_attempt_id",
            name="essay_transfer_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_essay_transfer_one_successor_idx",
        "essay_transfer_verification_versions",
        ["supersedes_verification_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_verification_version_id is not null"),
    )

    op.execute(
        """
        create function learning.reject_essay_intelligence_mutation()
        returns trigger
        language plpgsql
        as $$
        begin
          raise exception 'essay intelligence v2 records are append-only';
        end;
        $$
        """
    )
    for schema, table in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            create trigger {table}_append_only
            before update or delete on {schema}.{table}
            for each row execute function learning.reject_essay_intelligence_mutation()
            """
        )


def downgrade() -> None:
    op.drop_index(
        "learning_essay_transfer_one_successor_idx",
        table_name="essay_transfer_verification_versions",
        schema="learning",
    )
    op.drop_table("essay_transfer_verification_versions", schema="learning")
    op.drop_index(
        "learning_essay_rewrite_one_successor_idx",
        table_name="essay_rewrite_versions",
        schema="learning",
    )
    op.drop_table("essay_rewrite_versions", schema="learning")
    op.drop_index(
        "learning_essay_review_user_task_idx",
        table_name="essay_review_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_essay_review_one_successor_idx",
        table_name="essay_review_versions",
        schema="learning",
    )
    op.drop_table("essay_review_versions", schema="learning")
    op.drop_index(
        "learning_essay_answer_one_successor_idx",
        table_name="essay_answer_versions",
        schema="learning",
    )
    op.drop_table("essay_answer_versions", schema="learning")
    op.drop_index(
        "learning_essay_attempt_user_task_idx",
        table_name="essay_attempts",
        schema="learning",
    )
    op.drop_table("essay_attempts", schema="learning")
    op.drop_index(
        "product_essay_rubric_one_successor_idx",
        table_name="essay_rubric_versions",
        schema="product",
    )
    op.drop_table("essay_rubric_versions", schema="product")
    op.drop_table("essay_material_segments", schema="product")
    op.drop_index(
        "product_essay_material_one_successor_idx",
        table_name="essay_material_bundle_versions",
        schema="product",
    )
    op.drop_table("essay_material_bundle_versions", schema="product")
    op.drop_index(
        "product_essay_task_one_successor_idx",
        table_name="essay_task_versions",
        schema="product",
    )
    op.drop_table("essay_task_versions", schema="product")
    op.execute("drop function learning.reject_essay_intelligence_mutation()")


_IMMUTABLE_TABLES = (
    ("product", "essay_task_versions"),
    ("product", "essay_material_bundle_versions"),
    ("product", "essay_material_segments"),
    ("product", "essay_rubric_versions"),
    ("learning", "essay_attempts"),
    ("learning", "essay_answer_versions"),
    ("learning", "essay_review_versions"),
    ("learning", "essay_rewrite_versions"),
    ("learning", "essay_transfer_verification_versions"),
)
