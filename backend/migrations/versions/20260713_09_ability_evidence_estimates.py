"""Add immutable score evidence and versioned ability estimates.

Revision ID: 20260713_09
Revises: 20260712_08
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260713_09"
down_revision: str | Sequence[str] | None = "20260712_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create schema learning")
    op.create_table(
        "score_evidence",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("exam_code", sa.Text(), nullable=False),
        sa.Column("exam_year", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("reported_context", sa.Text(), nullable=True),
        sa.Column("xingce_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("shenlun_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("total_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("timed_status", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_evidence_id", sa.Uuid(), nullable=True),
        sa.Column(
            "command_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("exam_year between 2000 and 2100", name="score_evidence_exam_year_check"),
        sa.CheckConstraint(
            "source_type in ('self_report', 'placement_test', 'controlled_mock', "
            "'external_mock', 'official_exam')",
            name="score_evidence_source_type_check",
        ),
        sa.CheckConstraint(
            "reported_context is null or reported_context in "
            "('official_exam', 'full_mock', 'historical_estimate')",
            name="score_evidence_reported_context_check",
        ),
        sa.CheckConstraint(
            "(source_type = 'self_report' and reported_context is not null) or "
            "(source_type <> 'self_report' and reported_context is null)",
            name="score_evidence_context_source_check",
        ),
        sa.CheckConstraint(
            "timed_status in ('timed', 'untimed', 'unknown')",
            name="score_evidence_timed_status_check",
        ),
        sa.CheckConstraint(
            "verification_status in ('user_confirmed', 'system_verified')",
            name="score_evidence_verification_status_check",
        ),
        sa.CheckConstraint(
            "xingce_score is not null or shenlun_score is not null",
            name="score_evidence_component_required_check",
        ),
        sa.CheckConstraint(
            "xingce_score is null or xingce_score between 0 and 100",
            name="score_evidence_xingce_range_check",
        ),
        sa.CheckConstraint(
            "shenlun_score is null or shenlun_score between 0 and 100",
            name="score_evidence_shenlun_range_check",
        ),
        sa.CheckConstraint(
            "(num_nonnulls(xingce_score, shenlun_score) = 1 and total_score is null) "
            "or (num_nonnulls(xingce_score, shenlun_score) = 2 "
            "and total_score is not null "
            "and total_score = xingce_score + shenlun_score)",
            name="score_evidence_total_consistency_check",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_evidence_id", "user_id", "exam_code", "exam_year"],
            [
                "learning.score_evidence.evidence_id",
                "learning.score_evidence.user_id",
                "learning.score_evidence.exam_code",
                "learning.score_evidence.exam_year",
            ],
            name="score_evidence_supersedes_same_owner_exam_fk",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("evidence_id"),
        sa.UniqueConstraint("command_id", name="score_evidence_command_id_key"),
        sa.UniqueConstraint(
            "evidence_id",
            "user_id",
            "exam_code",
            "exam_year",
            name="score_evidence_owner_exam_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_score_evidence_user_exam_idx",
        "score_evidence",
        ["user_id", "exam_code", "exam_year", "occurred_at"],
        schema="learning",
    )
    op.create_index(
        "learning_score_evidence_one_successor_idx",
        "score_evidence",
        ["supersedes_evidence_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_evidence_id is not null"),
    )

    op.create_table(
        "ability_estimate_versions",
        sa.Column("estimate_version_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("exam_code", sa.Text(), nullable=False),
        sa.Column("exam_year", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("xingce_lower", sa.Numeric(5, 2), nullable=True),
        sa.Column("xingce_recommended", sa.Numeric(5, 2), nullable=True),
        sa.Column("xingce_upper", sa.Numeric(5, 2), nullable=True),
        sa.Column("shenlun_lower", sa.Numeric(5, 2), nullable=True),
        sa.Column("shenlun_recommended", sa.Numeric(5, 2), nullable=True),
        sa.Column("shenlun_upper", sa.Numeric(5, 2), nullable=True),
        sa.Column("total_lower", sa.Numeric(6, 2), nullable=True),
        sa.Column("total_recommended", sa.Numeric(6, 2), nullable=True),
        sa.Column("total_upper", sa.Numeric(6, 2), nullable=True),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("source_evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_estimate_version_id", sa.Uuid(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("exam_year between 2000 and 2100", name="ability_estimates_exam_year_check"),
        sa.CheckConstraint(
            "status in ('partial', 'complete')",
            name="ability_estimates_status_check",
        ),
        sa.CheckConstraint(
            "confidence in ('low', 'medium', 'high')",
            name="ability_estimates_confidence_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="ability_estimates_fingerprint_check",
        ),
        sa.CheckConstraint(
            "valid_until > generated_at",
            name="ability_estimates_validity_check",
        ),
        sa.CheckConstraint(
            _range_check("xingce", 100),
            name="ability_estimates_xingce_range_check",
        ),
        sa.CheckConstraint(
            _range_check("shenlun", 100),
            name="ability_estimates_shenlun_range_check",
        ),
        sa.CheckConstraint(
            _range_check("total", 200),
            name="ability_estimates_total_range_check",
        ),
        sa.CheckConstraint(
            "(status = 'complete' and xingce_recommended is not null "
            "and shenlun_recommended is not null and total_recommended is not null) "
            "or (status = 'partial' and total_recommended is null "
            "and ((xingce_recommended is null) <> (shenlun_recommended is null)))",
            name="ability_estimates_status_components_check",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_estimate_version_id",
                "user_id",
                "exam_code",
                "exam_year",
            ],
            [
                "learning.ability_estimate_versions.estimate_version_id",
                "learning.ability_estimate_versions.user_id",
                "learning.ability_estimate_versions.exam_code",
                "learning.ability_estimate_versions.exam_year",
            ],
            name="ability_estimates_supersedes_same_owner_exam_fk",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("estimate_version_id"),
        sa.UniqueConstraint(
            "user_id",
            "exam_code",
            "exam_year",
            "input_fingerprint",
            name="ability_estimates_input_fingerprint_key",
        ),
        sa.UniqueConstraint(
            "estimate_version_id",
            "user_id",
            "exam_code",
            "exam_year",
            name="ability_estimates_owner_exam_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_ability_estimates_latest_idx",
        "ability_estimate_versions",
        ["user_id", "exam_code", "exam_year", "generated_at"],
        schema="learning",
    )
    op.create_index(
        "learning_ability_estimates_one_successor_idx",
        "ability_estimate_versions",
        ["supersedes_estimate_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_estimate_version_id is not null"),
    )


def downgrade() -> None:
    op.drop_index(
        "learning_ability_estimates_one_successor_idx",
        table_name="ability_estimate_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_ability_estimates_latest_idx",
        table_name="ability_estimate_versions",
        schema="learning",
    )
    op.drop_table("ability_estimate_versions", schema="learning")
    op.drop_index(
        "learning_score_evidence_one_successor_idx",
        table_name="score_evidence",
        schema="learning",
    )
    op.drop_index(
        "learning_score_evidence_user_exam_idx",
        table_name="score_evidence",
        schema="learning",
    )
    op.drop_table("score_evidence", schema="learning")
    op.execute("drop schema if exists learning")


def _range_check(prefix: str, maximum: int) -> str:
    lower = f"{prefix}_lower"
    recommended = f"{prefix}_recommended"
    upper = f"{prefix}_upper"
    return (
        f"num_nonnulls({lower}, {recommended}, {upper}) = 0 or "
        f"(num_nonnulls({lower}, {recommended}, {upper}) = 3 "
        f"and {lower} between 0 and {maximum} and {lower} <= {recommended} "
        f"and {recommended} <= {upper} and {upper} <= {maximum})"
    )
