"""Add immutable, versioned dual-track planning records.

Revision ID: 20260713_11
Revises: 20260713_10
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260713_11"
down_revision: str | Sequence[str] | None = "20260713_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "weekly_study_preference_versions",
        sa.Column("preference_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("weekday_minutes", sa.Integer(), nullable=False),
        sa.Column("weekend_minutes", sa.Integer(), nullable=False),
        sa.Column("long_block_minutes", sa.Integer(), nullable=False),
        sa.Column("fragment_minutes", sa.Integer(), nullable=False),
        sa.Column("regular_rest_weekday", sa.SmallInteger(), nullable=False),
        sa.Column("regular_mock_weekday", sa.SmallInteger(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_preference_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version_no >= 1", name="weekly_preference_version_no_check"),
        sa.CheckConstraint(
            "char_length(timezone) between 1 and 80",
            name="weekly_preference_timezone_check",
        ),
        sa.CheckConstraint(
            "weekday_minutes between 0 and 1440",
            name="weekly_preference_weekday_minutes_check",
        ),
        sa.CheckConstraint(
            "weekend_minutes between 0 and 1440",
            name="weekly_preference_weekend_minutes_check",
        ),
        sa.CheckConstraint(
            "long_block_minutes between 0 and 1440",
            name="weekly_preference_long_block_check",
        ),
        sa.CheckConstraint(
            "fragment_minutes between 0 and 1440",
            name="weekly_preference_fragment_check",
        ),
        sa.CheckConstraint(
            "regular_rest_weekday between 1 and 7",
            name="weekly_preference_rest_weekday_check",
        ),
        sa.CheckConstraint(
            "regular_mock_weekday between 1 and 7",
            name="weekly_preference_mock_weekday_check",
        ),
        sa.CheckConstraint(
            "regular_rest_weekday <> regular_mock_weekday",
            name="weekly_preference_distinct_special_days_check",
        ),
        sa.CheckConstraint(
            "source_type in ('system_default', 'user_confirmed', 'user_revised')",
            name="weekly_preference_source_check",
        ),
        sa.CheckConstraint(
            "(source_type = 'system_default' and confirmed = false) or "
            "(source_type in ('user_confirmed', 'user_revised') and confirmed = true)",
            name="weekly_preference_confirmation_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="weekly_preference_input_sha_check",
        ),
        sa.CheckConstraint(
            "supersedes_preference_version_id is null "
            "or supersedes_preference_version_id <> preference_version_id",
            name="weekly_preference_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_preference_version_id", "user_id"],
            [
                "learning.weekly_study_preference_versions.preference_version_id",
                "learning.weekly_study_preference_versions.user_id",
            ],
            name="weekly_preference_supersedes_same_owner_fk",
        ),
        sa.PrimaryKeyConstraint("preference_version_id"),
        sa.UniqueConstraint("command_id", name="weekly_preference_command_key"),
        sa.UniqueConstraint(
            "user_id",
            "version_no",
            name="weekly_preference_user_version_key",
        ),
        sa.UniqueConstraint(
            "preference_version_id",
            "user_id",
            name="weekly_preference_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_weekly_preference_current_idx",
        "weekly_study_preference_versions",
        ["user_id", "version_no"],
        schema="learning",
    )
    op.create_index(
        "learning_weekly_preference_input_idx",
        "weekly_study_preference_versions",
        ["user_id", "input_fingerprint", "version_no"],
        schema="learning",
    )
    op.create_index(
        "learning_weekly_preference_one_successor_idx",
        "weekly_study_preference_versions",
        ["supersedes_preference_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_preference_version_id is not null"),
    )

    op.create_table(
        "daily_capacity_versions",
        sa.Column("capacity_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("available_minutes", sa.Integer(), nullable=False),
        sa.Column("energy_level", sa.SmallInteger(), nullable=False),
        sa.Column("stress_level", sa.SmallInteger(), nullable=False),
        sa.Column("confidence_level", sa.SmallInteger(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("weekly_preference_version_id", sa.Uuid(), nullable=True),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_capacity_version_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_no >= 1", name="daily_capacity_version_no_check"),
        sa.CheckConstraint(
            "available_minutes between 0 and 1440",
            name="daily_capacity_minutes_check",
        ),
        sa.CheckConstraint(
            "energy_level between 1 and 5",
            name="daily_capacity_energy_check",
        ),
        sa.CheckConstraint(
            "stress_level between 1 and 5",
            name="daily_capacity_stress_check",
        ),
        sa.CheckConstraint(
            "confidence_level between 1 and 5",
            name="daily_capacity_confidence_check",
        ),
        sa.CheckConstraint(
            "source_type in ('weekly_preference', 'user_override')",
            name="daily_capacity_source_check",
        ),
        sa.CheckConstraint(
            "(source_type = 'weekly_preference' and weekly_preference_version_id is not null "
            "and confirmed = false) or "
            "(source_type = 'user_override' and weekly_preference_version_id is not null "
            "and confirmed = true)",
            name="daily_capacity_source_preference_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="daily_capacity_input_sha_check",
        ),
        sa.CheckConstraint(
            "supersedes_capacity_version_id is null "
            "or supersedes_capacity_version_id <> capacity_version_id",
            name="daily_capacity_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            ["weekly_preference_version_id", "user_id"],
            [
                "learning.weekly_study_preference_versions.preference_version_id",
                "learning.weekly_study_preference_versions.user_id",
            ],
            name="daily_capacity_preference_owner_fk",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_capacity_version_id", "user_id", "local_date"],
            [
                "learning.daily_capacity_versions.capacity_version_id",
                "learning.daily_capacity_versions.user_id",
                "learning.daily_capacity_versions.local_date",
            ],
            name="daily_capacity_supersedes_same_owner_date_fk",
        ),
        sa.PrimaryKeyConstraint("capacity_version_id"),
        sa.UniqueConstraint("command_id", name="daily_capacity_command_key"),
        sa.UniqueConstraint(
            "user_id",
            "local_date",
            "version_no",
            name="daily_capacity_owner_date_version_key",
        ),
        sa.UniqueConstraint(
            "capacity_version_id",
            "user_id",
            "local_date",
            name="daily_capacity_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_daily_capacity_current_idx",
        "daily_capacity_versions",
        ["user_id", "local_date", "version_no"],
        schema="learning",
    )
    op.create_index(
        "learning_daily_capacity_input_idx",
        "daily_capacity_versions",
        ["user_id", "local_date", "input_fingerprint", "version_no"],
        schema="learning",
    )
    op.create_index(
        "learning_daily_capacity_one_successor_idx",
        "daily_capacity_versions",
        ["supersedes_capacity_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_capacity_version_id is not null"),
    )

    op.create_table(
        "daily_plan_versions",
        sa.Column("plan_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("capacity_version_id", sa.Uuid(), nullable=False),
        sa.Column("weekly_preference_version_id", sa.Uuid(), nullable=False),
        sa.Column("day_mode", sa.Text(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("goal_gap", JSONB, nullable=False),
        sa.Column("scheduled_minutes", sa.Integer(), nullable=False),
        sa.Column("due_reflection_count", sa.Integer(), nullable=False),
        sa.Column("due_verification_count", sa.Integer(), nullable=False),
        sa.Column("track_explanation", JSONB, nullable=False),
        sa.Column("input_versions", JSONB, nullable=False),
        sa.Column("evidence_refs", JSONB, nullable=False),
        sa.Column("invalidation_conditions", JSONB, nullable=False),
        sa.Column("replan_reason_codes", JSONB, nullable=False),
        sa.Column("plan_basis", sa.Text(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_plan_version_id", sa.Uuid(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_no >= 1", name="daily_plan_version_no_check"),
        sa.CheckConstraint(
            "day_mode in ('normal', 'mock', 'recovery', 'unavailable')",
            name="daily_plan_day_mode_check",
        ),
        sa.CheckConstraint("char_length(headline) > 0", name="daily_plan_headline_check"),
        sa.CheckConstraint("char_length(rationale) > 0", name="daily_plan_rationale_check"),
        sa.CheckConstraint(
            "jsonb_typeof(goal_gap) = 'object'",
            name="daily_plan_goal_gap_check",
        ),
        sa.CheckConstraint(
            "scheduled_minutes between 0 and 1440",
            name="daily_plan_scheduled_minutes_check",
        ),
        sa.CheckConstraint(
            "due_reflection_count >= 0",
            name="daily_plan_reflection_count_check",
        ),
        sa.CheckConstraint(
            "due_verification_count >= 0",
            name="daily_plan_verification_count_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(track_explanation) = 'object'",
            name="daily_plan_track_explanation_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_versions) = 'object'",
            name="daily_plan_input_versions_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' "
            "and jsonb_array_length(evidence_refs) > 0",
            name="daily_plan_evidence_refs_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(invalidation_conditions) = 'array' "
            "and jsonb_array_length(invalidation_conditions) > 0",
            name="daily_plan_invalidation_conditions_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(replan_reason_codes) = 'array'",
            name="daily_plan_replan_reasons_check",
        ),
        sa.CheckConstraint(
            "plan_basis = 'evidence_priority_v2'",
            name="daily_plan_basis_check",
        ),
        sa.CheckConstraint(
            "char_length(contract_version) > 0",
            name="daily_plan_contract_version_check",
        ),
        sa.CheckConstraint(
            "char_length(algorithm_version) > 0",
            name="daily_plan_algorithm_version_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="daily_plan_input_sha_check",
        ),
        sa.CheckConstraint(
            "valid_until > generated_at",
            name="daily_plan_validity_check",
        ),
        sa.CheckConstraint(
            "supersedes_plan_version_id is null "
            "or supersedes_plan_version_id <> plan_version_id",
            name="daily_plan_no_self_supersede_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(
            ["capacity_version_id", "user_id", "local_date"],
            [
                "learning.daily_capacity_versions.capacity_version_id",
                "learning.daily_capacity_versions.user_id",
                "learning.daily_capacity_versions.local_date",
            ],
            name="daily_plan_capacity_owner_date_fk",
        ),
        sa.ForeignKeyConstraint(
            ["weekly_preference_version_id", "user_id"],
            [
                "learning.weekly_study_preference_versions.preference_version_id",
                "learning.weekly_study_preference_versions.user_id",
            ],
            name="daily_plan_preference_owner_fk",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_plan_version_id", "user_id", "local_date"],
            [
                "learning.daily_plan_versions.plan_version_id",
                "learning.daily_plan_versions.user_id",
                "learning.daily_plan_versions.local_date",
            ],
            name="daily_plan_supersedes_same_owner_date_fk",
        ),
        sa.PrimaryKeyConstraint("plan_version_id"),
        sa.UniqueConstraint("command_id", name="daily_plan_command_key"),
        sa.UniqueConstraint(
            "user_id",
            "local_date",
            "version_no",
            name="daily_plan_owner_date_version_key",
        ),
        sa.UniqueConstraint(
            "plan_version_id",
            "user_id",
            "local_date",
            name="daily_plan_owner_date_identity_key",
        ),
        sa.UniqueConstraint(
            "plan_version_id",
            "user_id",
            "local_date",
            "version_no",
            name="daily_plan_full_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_daily_plan_current_idx",
        "daily_plan_versions",
        ["user_id", "local_date", "version_no"],
        schema="learning",
    )
    op.create_index(
        "learning_daily_plan_input_idx",
        "daily_plan_versions",
        ["user_id", "local_date", "input_fingerprint", "version_no"],
        schema="learning",
    )
    op.create_index(
        "learning_daily_plan_one_successor_idx",
        "daily_plan_versions",
        ["supersedes_plan_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_plan_version_id is not null"),
    )

    op.create_table(
        "daily_plan_tasks",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("plan_version_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("plan_version_no", sa.Integer(), nullable=False),
        sa.Column("candidate_key", sa.Text(), nullable=False),
        sa.Column("work_item_key", sa.Text(), nullable=False),
        sa.Column("task_order", sa.Integer(), nullable=False),
        sa.Column("track", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("start_url", sa.Text(), nullable=False),
        sa.Column("priority_score", sa.Numeric(6, 5), nullable=False),
        sa.Column("goal_gain", sa.Numeric(6, 5), nullable=False),
        sa.Column("ability_gain", sa.Numeric(6, 5), nullable=False),
        sa.Column("verification_urgency", sa.Numeric(6, 5), nullable=False),
        sa.Column("time_fit", sa.Numeric(6, 5), nullable=False),
        sa.Column("load_penalty", sa.Numeric(6, 5), nullable=False),
        sa.Column("track_balance_bonus", sa.Numeric(6, 5), nullable=False),
        sa.Column("evidence_refs", JSONB, nullable=False),
        sa.Column("completion_contract", JSONB, nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("plan_version_no >= 1", name="daily_plan_task_version_no_check"),
        sa.CheckConstraint("task_order >= 1", name="daily_plan_task_order_check"),
        sa.CheckConstraint(
            "char_length(candidate_key) between 1 and 240",
            name="daily_plan_task_candidate_key_check",
        ),
        sa.CheckConstraint(
            "length(work_item_key) = 64",
            name="daily_plan_task_work_item_sha_check",
        ),
        sa.CheckConstraint(
            "track in ('xingce', 'essay', 'recovery', 'mock')",
            name="daily_plan_task_track_check",
        ),
        sa.CheckConstraint(
            "stage in ('objective_baseline', 'objective_practice', "
            "'objective_reflection', 'objective_verification', "
            "'essay_material_reading', 'essay_point_extraction', "
            "'essay_outlining', 'essay_drafting', 'essay_review', 'essay_rewrite', "
            "'essay_transfer', 'recovery_review', 'weekly_mock')",
            name="daily_plan_task_stage_check",
        ),
        sa.CheckConstraint(
            "(track = 'xingce' and stage in ('objective_baseline', "
            "'objective_practice', 'objective_reflection', 'objective_verification')) or "
            "(track = 'essay' and stage in ('essay_material_reading', "
            "'essay_point_extraction', 'essay_outlining', 'essay_drafting', "
            "'essay_review', 'essay_rewrite', 'essay_transfer')) or "
            "(track = 'recovery' and stage = 'recovery_review') or "
            "(track = 'mock' and stage = 'weekly_mock')",
            name="daily_plan_task_track_stage_check",
        ),
        sa.CheckConstraint(
            "kind in ('practice', 'reflection', 'verification', 'mock_exam', 'essay')",
            name="daily_plan_task_kind_check",
        ),
        sa.CheckConstraint(
            "(track = 'xingce' and kind in ('practice', 'reflection', 'verification')) or "
            "(track = 'essay' and kind = 'essay') or "
            "(track = 'recovery' and kind in ('reflection', 'verification')) or "
            "(track = 'mock' and kind = 'mock_exam')",
            name="daily_plan_task_track_kind_check",
        ),
        sa.CheckConstraint("char_length(title) > 0", name="daily_plan_task_title_check"),
        sa.CheckConstraint("char_length(reason) > 0", name="daily_plan_task_reason_check"),
        sa.CheckConstraint(
            "estimated_minutes between 1 and 1440",
            name="daily_plan_task_minutes_check",
        ),
        sa.CheckConstraint(
            "left(start_url, 1) = '/'",
            name="daily_plan_task_start_url_check",
        ),
        sa.CheckConstraint(
            "priority_score between 0 and 1 and goal_gain between 0 and 1 "
            "and ability_gain between 0 and 1 and verification_urgency between 0 and 1 "
            "and time_fit between 0 and 1 and load_penalty between 0 and 1 "
            "and track_balance_bonus between 0 and 1",
            name="daily_plan_task_priority_range_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' "
            "and jsonb_array_length(evidence_refs) > 0",
            name="daily_plan_task_evidence_refs_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(completion_contract) = 'object'",
            name="daily_plan_task_completion_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="daily_plan_task_input_sha_check",
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id", "user_id", "local_date", "plan_version_no"],
            [
                "learning.daily_plan_versions.plan_version_id",
                "learning.daily_plan_versions.user_id",
                "learning.daily_plan_versions.local_date",
                "learning.daily_plan_versions.version_no",
            ],
            name="daily_plan_task_same_owner_date_version_fk",
        ),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint(
            "plan_version_id",
            "task_order",
            name="daily_plan_task_order_key",
        ),
        sa.UniqueConstraint(
            "plan_version_id",
            "input_fingerprint",
            name="daily_plan_task_input_key",
        ),
        sa.UniqueConstraint(
            "plan_version_id",
            "candidate_key",
            name="daily_plan_task_candidate_key",
        ),
        sa.UniqueConstraint(
            "plan_version_id",
            "work_item_key",
            name="daily_plan_task_work_item_key",
        ),
        sa.UniqueConstraint(
            "task_id",
            "user_id",
            "local_date",
            "plan_version_no",
            name="daily_plan_task_identity_key",
        ),
        sa.UniqueConstraint(
            "task_id",
            "plan_version_id",
            "user_id",
            "local_date",
            "plan_version_no",
            "candidate_key",
            "work_item_key",
            name="daily_plan_task_execution_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_daily_plan_task_track_idx",
        "daily_plan_tasks",
        ["user_id", "local_date", "track", "stage"],
        schema="learning",
    )

    op.create_table(
        "task_execution_versions",
        sa.Column("execution_version_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("plan_version_id", sa.Uuid(), nullable=False),
        sa.Column("plan_version_no", sa.Integer(), nullable=False),
        sa.Column("candidate_key", sa.Text(), nullable=False),
        sa.Column("work_item_key", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("elapsed_minutes", sa.Integer(), nullable=False),
        sa.Column("evidence_refs", JSONB, nullable=False),
        sa.Column("abandonment_reason", sa.Text(), nullable=True),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("supersedes_execution_version_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_execution_version_no", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("plan_version_no >= 1", name="task_execution_plan_version_check"),
        sa.CheckConstraint("version_no >= 1", name="task_execution_version_no_check"),
        sa.CheckConstraint(
            "char_length(command_id) between 1 and 240",
            name="task_execution_command_check",
        ),
        sa.CheckConstraint(
            "char_length(candidate_key) between 1 and 240",
            name="task_execution_candidate_key_check",
        ),
        sa.CheckConstraint(
            "length(work_item_key) = 64",
            name="task_execution_work_item_sha_check",
        ),
        sa.CheckConstraint(
            "status in ('in_progress', 'complete', 'abandoned')",
            name="task_execution_status_check",
        ),
        sa.CheckConstraint(
            "elapsed_minutes between 0 and 1440",
            name="task_execution_elapsed_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'",
            name="task_execution_evidence_refs_check",
        ),
        sa.CheckConstraint(
            "(status = 'complete' and jsonb_array_length(evidence_refs) > 0 "
            "and abandonment_reason is null) or "
            "(status = 'abandoned' and char_length(trim(abandonment_reason)) > 0) or "
            "(status = 'in_progress' and abandonment_reason is null)",
            name="task_execution_terminal_evidence_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="task_execution_input_sha_check",
        ),
        sa.CheckConstraint(
            "supersedes_execution_version_id is null "
            "or supersedes_execution_version_id <> execution_version_id",
            name="task_execution_no_self_supersede_check",
        ),
        sa.CheckConstraint(
            "(version_no = 1 and supersedes_execution_version_id is null "
            "and supersedes_execution_version_no is null) or "
            "(version_no > 1 and supersedes_execution_version_id is not null "
            "and supersedes_execution_version_no = version_no - 1)",
            name="task_execution_contiguous_version_check",
        ),
        sa.ForeignKeyConstraint(
            [
                "task_id",
                "plan_version_id",
                "user_id",
                "local_date",
                "plan_version_no",
                "candidate_key",
                "work_item_key",
            ],
            [
                "learning.daily_plan_tasks.task_id",
                "learning.daily_plan_tasks.plan_version_id",
                "learning.daily_plan_tasks.user_id",
                "learning.daily_plan_tasks.local_date",
                "learning.daily_plan_tasks.plan_version_no",
                "learning.daily_plan_tasks.candidate_key",
                "learning.daily_plan_tasks.work_item_key",
            ],
            name="task_execution_same_owner_task_date_version_fk",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_execution_version_id",
                "user_id",
                "work_item_key",
                "local_date",
                "supersedes_execution_version_no",
            ],
            [
                "learning.task_execution_versions.execution_version_id",
                "learning.task_execution_versions.user_id",
                "learning.task_execution_versions.work_item_key",
                "learning.task_execution_versions.local_date",
                "learning.task_execution_versions.version_no",
            ],
            name="task_execution_supersedes_same_owner_task_date_fk",
        ),
        sa.PrimaryKeyConstraint("execution_version_id"),
        sa.UniqueConstraint(
            "user_id",
            "command_id",
            name="task_execution_owner_command_key",
        ),
        sa.UniqueConstraint(
            "user_id",
            "local_date",
            "work_item_key",
            "version_no",
            name="task_execution_owner_date_work_item_version_key",
        ),
        sa.UniqueConstraint(
            "execution_version_id",
            "user_id",
            "work_item_key",
            "local_date",
            "version_no",
            name="task_execution_identity_key",
        ),
        schema="learning",
    )
    op.create_index(
        "learning_task_execution_current_idx",
        "task_execution_versions",
        ["user_id", "local_date", "work_item_key", "version_no"],
        schema="learning",
    )
    op.create_index(
        "learning_task_execution_terminal_candidate_idx",
        "task_execution_versions",
        ["user_id", "local_date", "work_item_key", "status"],
        schema="learning",
    )
    op.create_index(
        "learning_task_execution_one_successor_idx",
        "task_execution_versions",
        ["supersedes_execution_version_id"],
        unique=True,
        schema="learning",
        postgresql_where=sa.text("supersedes_execution_version_id is not null"),
    )

    op.execute(
        """
        create function learning.reject_planning_v2_mutation()
        returns trigger
        language plpgsql
        as $$
        begin
          raise exception 'planning v2 records are append-only';
        end;
        $$
        """
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            create trigger {table}_append_only
            before update or delete on learning.{table}
            for each row execute function learning.reject_planning_v2_mutation()
            """
        )


def downgrade() -> None:
    op.drop_index(
        "learning_task_execution_one_successor_idx",
        table_name="task_execution_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_task_execution_terminal_candidate_idx",
        table_name="task_execution_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_task_execution_current_idx",
        table_name="task_execution_versions",
        schema="learning",
    )
    op.drop_table("task_execution_versions", schema="learning")
    op.drop_index(
        "learning_daily_plan_task_track_idx",
        table_name="daily_plan_tasks",
        schema="learning",
    )
    op.drop_table("daily_plan_tasks", schema="learning")
    op.drop_index(
        "learning_daily_plan_input_idx",
        table_name="daily_plan_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_daily_plan_one_successor_idx",
        table_name="daily_plan_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_daily_plan_current_idx",
        table_name="daily_plan_versions",
        schema="learning",
    )
    op.drop_table("daily_plan_versions", schema="learning")
    op.drop_index(
        "learning_daily_capacity_input_idx",
        table_name="daily_capacity_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_daily_capacity_one_successor_idx",
        table_name="daily_capacity_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_daily_capacity_current_idx",
        table_name="daily_capacity_versions",
        schema="learning",
    )
    op.drop_table("daily_capacity_versions", schema="learning")
    op.drop_index(
        "learning_weekly_preference_input_idx",
        table_name="weekly_study_preference_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_weekly_preference_one_successor_idx",
        table_name="weekly_study_preference_versions",
        schema="learning",
    )
    op.drop_index(
        "learning_weekly_preference_current_idx",
        table_name="weekly_study_preference_versions",
        schema="learning",
    )
    op.drop_table("weekly_study_preference_versions", schema="learning")
    op.execute("drop function learning.reject_planning_v2_mutation()")


_IMMUTABLE_TABLES = (
    "weekly_study_preference_versions",
    "daily_capacity_versions",
    "daily_plan_versions",
    "daily_plan_tasks",
    "task_execution_versions",
)
