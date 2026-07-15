"""Add versioned qualification facts, eligibility snapshots, and position goals.

Revision ID: 20260712_03
Revises: 20260712_02
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_03"
down_revision: str | Sequence[str] | None = "20260712_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FACT_KEYS = (
    "'education', 'degree', 'major', 'fresh_graduate', 'political_status', "
    "'grassroots_years', 'service_project', 'hukou', 'age', 'certifications'"
)


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("profile_version", sa.BigInteger(), nullable=False, server_default="1"),
        schema="learner",
    )
    op.create_check_constraint(
        "profiles_profile_version_check",
        "profiles",
        "profile_version >= 1",
        schema="learner",
    )

    op.create_table(
        "position_requirements",
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("position_data_version", sa.Text(), nullable=False),
        sa.Column("fact_key", sa.Text(), nullable=False),
        sa.Column("operator", sa.Text(), nullable=False),
        sa.Column("expected", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("hard_condition", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"fact_key in ({FACT_KEYS})", name="position_requirements_fact_key_check"),
        sa.CheckConstraint(
            "operator in ('equals', 'one_of', 'at_least', 'contains_any', 'manual_review')",
            name="position_requirements_operator_check",
        ),
        sa.CheckConstraint(
            "(operator = 'manual_review' and expected is null) or "
            "(operator <> 'manual_review' and expected is not null)",
            name="position_requirements_expected_check",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["product.positions.position_id"]),
        sa.PrimaryKeyConstraint("requirement_id"),
        sa.UniqueConstraint(
            "position_id",
            "rule_set_version",
            "position_data_version",
            "fact_key",
            name="position_requirements_version_fact_key_key",
        ),
        schema="product",
    )
    op.create_index(
        "product_position_requirements_lookup_idx",
        "position_requirements",
        ["position_id", "rule_set_version", "position_data_version"],
        schema="product",
    )

    op.create_table(
        "qualification_profile_changes",
        sa.Column("change_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("from_profile_version", sa.BigInteger(), nullable=False),
        sa.Column("to_profile_version", sa.BigInteger(), nullable=False),
        sa.Column("command_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "from_profile_version >= 1 and to_profile_version = from_profile_version + 1",
            name="qualification_profile_changes_version_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("change_id"),
        sa.UniqueConstraint("command_id", name="qualification_profile_changes_command_id_key"),
        schema="learner",
    )

    op.create_table(
        "qualification_facts",
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("change_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version", sa.BigInteger(), nullable=False),
        sa.Column("fact_key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_fact_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("profile_version >= 1", name="qualification_facts_profile_version_check"),
        sa.CheckConstraint(f"fact_key in ({FACT_KEYS})", name="qualification_facts_fact_key_check"),
        sa.CheckConstraint(
            "verification_status in ('reported', 'confirmed', 'conflicting')",
            name="qualification_facts_verification_status_check",
        ),
        sa.ForeignKeyConstraint(["change_id"], ["learner.qualification_profile_changes.change_id"]),
        sa.ForeignKeyConstraint(["supersedes_fact_id"], ["learner.qualification_facts.fact_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("fact_id"),
        sa.UniqueConstraint(
            "user_id", "fact_key", "profile_version", name="qualification_facts_user_key_version_key"
        ),
        schema="learner",
    )
    op.create_index(
        "learner_qualification_facts_current_idx",
        "qualification_facts",
        ["user_id", "fact_key", "profile_version"],
        schema="learner",
    )

    op.create_table(
        "qualification_evidence",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type in ('learner_report', 'document', 'official_import', 'operator')",
            name="qualification_evidence_source_type_check",
        ),
        sa.ForeignKeyConstraint(["fact_id"], ["learner.qualification_facts.fact_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evidence_id"),
        schema="learner",
    )
    op.create_index(
        "learner_qualification_evidence_fact_idx",
        "qualification_evidence",
        ["fact_id"],
        schema="learner",
    )

    op.create_table(
        "eligibility_snapshots",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("learner_profile_version", sa.BigInteger(), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("position_data_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("missing_fact_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("learner_profile_version >= 1", name="eligibility_snapshots_profile_version_check"),
        sa.CheckConstraint(
            "status in ('eligible', 'needs_confirmation', 'ineligible')",
            name="eligibility_snapshots_status_check",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["product.positions.position_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "user_id",
            "position_id",
            "learner_profile_version",
            "rule_set_version",
            "position_data_version",
            name="eligibility_snapshots_input_versions_key",
        ),
        schema="learner",
    )
    op.create_index(
        "learner_eligibility_snapshots_history_idx",
        "eligibility_snapshots",
        ["user_id", "position_id", "evaluated_at"],
        schema="learner",
    )

    op.create_table(
        "position_goal_versions",
        sa.Column("goal_version_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("target_score_lower", sa.Numeric(6, 2), nullable=False),
        sa.Column("target_score_recommended", sa.Numeric(6, 2), nullable=False),
        sa.Column("target_score_upper", sa.Numeric(6, 2), nullable=False),
        sa.Column("target_score_evidence_level", sa.Text(), nullable=False),
        sa.Column("target_score_sample_count", sa.Integer(), nullable=False),
        sa.Column("target_score_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("learner_profile_version", sa.BigInteger(), nullable=False),
        sa.Column("eligibility_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("position_data_version", sa.Text(), nullable=False),
        sa.Column("supersedes_goal_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("role in ('primary', 'balanced', 'backup')", name="position_goal_versions_role_check"),
        sa.CheckConstraint("status in ('candidate', 'confirmed', 'retired')", name="position_goal_versions_status_check"),
        sa.CheckConstraint(
            "target_score_lower between 0 and 300 and "
            "target_score_lower <= target_score_recommended and "
            "target_score_recommended <= target_score_upper and "
            "target_score_upper <= 300",
            name="position_goal_versions_target_score_order_check",
        ),
        sa.CheckConstraint(
            "target_score_evidence_level in ('direct', 'strong', 'limited', 'unavailable')",
            name="position_goal_versions_evidence_level_check",
        ),
        sa.CheckConstraint(
            "(target_score_evidence_level = 'unavailable' and target_score_sample_count = 0) or "
            "(target_score_evidence_level <> 'unavailable' and target_score_sample_count > 0)",
            name="position_goal_versions_sample_evidence_check",
        ),
        sa.CheckConstraint("learner_profile_version >= 1", name="position_goal_versions_profile_version_check"),
        sa.ForeignKeyConstraint(["eligibility_snapshot_id"], ["learner.eligibility_snapshots.snapshot_id"]),
        sa.ForeignKeyConstraint(["position_id"], ["product.positions.position_id"]),
        sa.ForeignKeyConstraint(["supersedes_goal_version_id"], ["learner.position_goal_versions.goal_version_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("goal_version_id"),
        schema="learner",
    )
    op.create_index(
        "learner_position_goal_versions_history_idx",
        "position_goal_versions",
        ["user_id", "created_at"],
        schema="learner",
    )
    op.create_index(
        "learner_position_goal_versions_one_primary_idx",
        "position_goal_versions",
        ["user_id"],
        unique=True,
        schema="learner",
        postgresql_where=sa.text("role = 'primary' and status = 'confirmed'"),
    )

    op.create_table(
        "position_goal_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_goal_version_id", sa.Uuid(), nullable=True),
        sa.Column("to_goal_version_id", sa.Uuid(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("command_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_impact", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "event_type in ('goal_selected', 'goal_changed', 'goal_retired')",
            name="position_goal_events_event_type_check",
        ),
        sa.ForeignKeyConstraint(["from_goal_version_id"], ["learner.position_goal_versions.goal_version_id"]),
        sa.ForeignKeyConstraint(["to_goal_version_id"], ["learner.position_goal_versions.goal_version_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("command_id", name="position_goal_events_command_id_key"),
        schema="learner",
    )
    op.create_index(
        "learner_position_goal_events_user_idx",
        "position_goal_events",
        ["user_id", "created_at"],
        schema="learner",
    )


def downgrade() -> None:
    op.drop_index("learner_position_goal_events_user_idx", table_name="position_goal_events", schema="learner")
    op.drop_table("position_goal_events", schema="learner")
    op.drop_index("learner_position_goal_versions_one_primary_idx", table_name="position_goal_versions", schema="learner")
    op.drop_index("learner_position_goal_versions_history_idx", table_name="position_goal_versions", schema="learner")
    op.drop_table("position_goal_versions", schema="learner")
    op.drop_index("learner_eligibility_snapshots_history_idx", table_name="eligibility_snapshots", schema="learner")
    op.drop_table("eligibility_snapshots", schema="learner")
    op.drop_index("learner_qualification_evidence_fact_idx", table_name="qualification_evidence", schema="learner")
    op.drop_table("qualification_evidence", schema="learner")
    op.drop_index("learner_qualification_facts_current_idx", table_name="qualification_facts", schema="learner")
    op.drop_table("qualification_facts", schema="learner")
    op.drop_table("qualification_profile_changes", schema="learner")
    op.drop_index("product_position_requirements_lookup_idx", table_name="position_requirements", schema="product")
    op.drop_table("position_requirements", schema="product")
    op.drop_constraint("profiles_profile_version_check", "profiles", schema="learner", type_="check")
    op.drop_column("profiles", "profile_version", schema="learner")
