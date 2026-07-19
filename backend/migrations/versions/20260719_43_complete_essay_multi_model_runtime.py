"""Complete formal multi-model grading failure and replay contracts.

Revision ID: 20260719_43
Revises: 20260719_42
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260719_43"
down_revision = "20260719_42"
branch_labels = None
depends_on = None


_TEST_STATEMENTS = (
    """
    alter table ai_audit.essay_grading_runs
      add column failure_code text null,
      add column failure_payload jsonb null
    """,
    """
    alter table ai_audit.essay_grading_runs
      drop constraint essay_grading_run_final_score_check
    """,
    """
    alter table ai_audit.essay_grading_runs
      add constraint essay_grading_run_failure_payload_check check (
        (failure_code is null and failure_payload is null)
        or (failure_code is not null and char_length(failure_code) > 0
          and jsonb_typeof(failure_payload) = 'object')
      ),
      add constraint essay_grading_run_final_score_check check (
        (failure_code is not null and
          num_nonnulls(selected_candidate_id, final_score_lower,
            final_score_recommended, final_score_upper, final_score_maximum) = 0)
        or (failure_code is null and state = 'unable_to_score'
          and valid_candidate_count < 2
          and num_nonnulls(selected_candidate_id, final_score_lower,
            final_score_recommended, final_score_upper, final_score_maximum) = 0)
        or (failure_code is null and state <> 'unable_to_score'
          and valid_candidate_count >= 2
          and num_nonnulls(selected_candidate_id, final_score_lower,
            final_score_recommended, final_score_upper, final_score_maximum) = 5)
      )
    """,
    """
    alter table ai_audit.essay_review_checkpoints
      drop constraint essay_review_checkpoint_phase_check
    """,
    """
    alter table ai_audit.essay_review_checkpoints
      add constraint essay_review_checkpoint_phase_check check (
        phase ~ '^(initial|repair|candidate:(gemini|grok|hunyuan)|adjudication)$'
      )
    """,
)


def upgrade() -> None:
    op.add_column(
        "essay_grading_runs",
        sa.Column("failure_code", sa.Text(), nullable=True),
        schema="ai_audit",
    )
    op.add_column(
        "essay_grading_runs",
        sa.Column("failure_payload", JSONB, nullable=True),
        schema="ai_audit",
    )
    op.drop_constraint(
        "essay_grading_run_final_score_check",
        "essay_grading_runs",
        schema="ai_audit",
        type_="check",
    )
    op.create_check_constraint(
        "essay_grading_run_failure_payload_check",
        "essay_grading_runs",
        "(failure_code is null and failure_payload is null) or "
        "(failure_code is not null and char_length(failure_code) > 0 "
        "and jsonb_typeof(failure_payload) = 'object')",
        schema="ai_audit",
    )
    op.create_check_constraint(
        "essay_grading_run_final_score_check",
        "essay_grading_runs",
        "(failure_code is not null and num_nonnulls(selected_candidate_id, "
        "final_score_lower, final_score_recommended, final_score_upper, "
        "final_score_maximum) = 0) or (failure_code is null and "
        "state = 'unable_to_score' and valid_candidate_count < 2 and "
        "num_nonnulls(selected_candidate_id, final_score_lower, "
        "final_score_recommended, final_score_upper, final_score_maximum) = 0) "
        "or (failure_code is null and state <> 'unable_to_score' and "
        "valid_candidate_count >= 2 and num_nonnulls(selected_candidate_id, "
        "final_score_lower, final_score_recommended, final_score_upper, "
        "final_score_maximum) = 5)",
        schema="ai_audit",
    )
    op.drop_constraint(
        "essay_review_checkpoint_phase_check",
        "essay_review_checkpoints",
        schema="ai_audit",
        type_="check",
    )
    op.create_check_constraint(
        "essay_review_checkpoint_phase_check",
        "essay_review_checkpoints",
        "phase ~ '^(initial|repair|candidate:(gemini|grok|hunyuan)|adjudication)$'",
        schema="ai_audit",
    )


def downgrade() -> None:
    op.drop_constraint(
        "essay_review_checkpoint_phase_check",
        "essay_review_checkpoints",
        schema="ai_audit",
        type_="check",
    )
    op.create_check_constraint(
        "essay_review_checkpoint_phase_check",
        "essay_review_checkpoints",
        "phase in ('initial', 'repair')",
        schema="ai_audit",
    )
    op.drop_constraint(
        "essay_grading_run_final_score_check",
        "essay_grading_runs",
        schema="ai_audit",
        type_="check",
    )
    op.drop_constraint(
        "essay_grading_run_failure_payload_check",
        "essay_grading_runs",
        schema="ai_audit",
        type_="check",
    )
    op.create_check_constraint(
        "essay_grading_run_final_score_check",
        "essay_grading_runs",
        "(state = 'unable_to_score' and valid_candidate_count < 2 and "
        "num_nonnulls(selected_candidate_id, final_score_lower, "
        "final_score_recommended, final_score_upper, final_score_maximum) = 0) "
        "or (state <> 'unable_to_score' and valid_candidate_count >= 2 and "
        "num_nonnulls(selected_candidate_id, final_score_lower, "
        "final_score_recommended, final_score_upper, final_score_maximum) = 5)",
        schema="ai_audit",
    )
    op.drop_column("essay_grading_runs", "failure_payload", schema="ai_audit")
    op.drop_column("essay_grading_runs", "failure_code", schema="ai_audit")
