"""Persist independent essay candidates and blind adjudication.

Revision ID: 20260719_42
Revises: 20260719_41
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260719_42"
down_revision = "20260719_41"
branch_labels = None
depends_on = None


_TEST_STATEMENTS = (
    """
    create table ai_audit.essay_grading_runs (
      grading_run_id uuid primary key,
      intended_review_version_id uuid not null unique,
      job_id uuid not null unique references platform.background_jobs(job_id),
      user_id uuid not null references learner.users(user_id),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      plan_version text not null,
      comparison_policy_version text not null,
      state text not null check (state in (
        'consistent', 'minor_disagreement', 'major_disagreement', 'unable_to_score'
      )),
      candidate_count smallint not null check (candidate_count between 2 and 3),
      valid_candidate_count smallint not null check (
        valid_candidate_count between 0 and candidate_count
      ),
      comparison_fingerprint text not null check (length(comparison_fingerprint) = 64),
      comparison_payload jsonb not null check (jsonb_typeof(comparison_payload) = 'object'),
      selected_candidate_id uuid null,
      final_score_lower numeric(6, 2) null,
      final_score_recommended numeric(6, 2) null,
      final_score_upper numeric(6, 2) null,
      final_score_maximum numeric(6, 2) null,
      created_at timestamptz not null default now(),
      constraint essay_grading_run_final_score_check check (
        (state = 'unable_to_score' and valid_candidate_count < 2
          and num_nonnulls(selected_candidate_id, final_score_lower,
            final_score_recommended, final_score_upper, final_score_maximum) = 0)
        or (state <> 'unable_to_score' and valid_candidate_count >= 2
          and num_nonnulls(selected_candidate_id, final_score_lower,
            final_score_recommended, final_score_upper, final_score_maximum) = 5)
      ),
      check (final_score_lower is null or (
        final_score_lower >= 0 and final_score_lower <= final_score_recommended
        and final_score_recommended <= final_score_upper
        and final_score_upper <= final_score_maximum and final_score_maximum <= 100
      ))
    )
    """,
    """
    create table ai_audit.essay_grading_candidates (
      candidate_id uuid primary key,
      grading_run_id uuid not null references ai_audit.essay_grading_runs(grading_run_id),
      slot_id text not null,
      provider_release_id text not null,
      model text not null,
      prompt_version text not null,
      schema_version text not null,
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      output_fingerprint text null check (
        output_fingerprint is null or length(output_fingerprint) = 64
      ),
      status text not null check (status in ('valid', 'invalid', 'provider_failed')),
      payload jsonb null check (payload is null or jsonb_typeof(payload) = 'object'),
      model_call_id uuid null references ai_audit.model_call_ledger(call_id),
      error_code text null,
      created_at timestamptz not null default now(),
      unique (grading_run_id, slot_id),
      unique (grading_run_id, provider_release_id, model),
      unique (grading_run_id, model_call_id),
      unique (grading_run_id, candidate_id),
      check (
        (status = 'valid' and payload is not null and model_call_id is not null
          and output_fingerprint is not null and error_code is null)
        or (status <> 'valid' and payload is null and error_code is not null)
      )
    )
    """,
    """
    alter table ai_audit.essay_grading_runs
      add constraint essay_grading_run_selected_candidate_fk
      foreign key (grading_run_id, selected_candidate_id)
      references ai_audit.essay_grading_candidates(grading_run_id, candidate_id)
      deferrable initially deferred
    """,
    """
    create table ai_audit.essay_grading_adjudications (
      adjudication_id uuid primary key,
      grading_run_id uuid not null unique
        references ai_audit.essay_grading_runs(grading_run_id),
      contract_version text not null,
      provider_release_id text not null,
      model text not null,
      prompt_version text not null,
      schema_version text not null,
      model_call_id uuid not null references ai_audit.model_call_ledger(call_id),
      blind_request jsonb not null check (jsonb_typeof(blind_request) = 'object'),
      decision jsonb not null check (jsonb_typeof(decision) = 'object'),
      selected_candidate_id uuid not null,
      output_fingerprint text not null check (length(output_fingerprint) = 64),
      created_at timestamptz not null default now(),
      foreign key (grading_run_id, selected_candidate_id)
        references ai_audit.essay_grading_candidates(grading_run_id, candidate_id)
    )
    """,
    """
    create table ai_audit.essay_review_grading_bindings (
      review_version_id uuid primary key
        references learning.essay_review_versions(review_version_id),
      grading_run_id uuid not null unique
        references ai_audit.essay_grading_runs(grading_run_id),
      created_at timestamptz not null default now()
    )
    """,
    """
    create trigger essay_grading_runs_append_only before update or delete
    on ai_audit.essay_grading_runs for each row execute function
    ai_audit.reject_append_only_mutation()
    """,
    """
    create trigger essay_grading_candidates_append_only before update or delete
    on ai_audit.essay_grading_candidates for each row execute function
    ai_audit.reject_append_only_mutation()
    """,
    """
    create trigger essay_grading_adjudications_append_only before update or delete
    on ai_audit.essay_grading_adjudications for each row execute function
    ai_audit.reject_append_only_mutation()
    """,
    """
    create trigger essay_review_grading_bindings_append_only before update or delete
    on ai_audit.essay_review_grading_bindings for each row execute function
    ai_audit.reject_append_only_mutation()
    """,
)


def upgrade() -> None:
    op.create_table(
        "essay_grading_runs",
        sa.Column("grading_run_id", sa.Uuid(), nullable=False),
        sa.Column("intended_review_version_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.Text(), nullable=False),
        sa.Column("comparison_policy_version", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("candidate_count", sa.SmallInteger(), nullable=False),
        sa.Column("valid_candidate_count", sa.SmallInteger(), nullable=False),
        sa.Column("comparison_fingerprint", sa.Text(), nullable=False),
        sa.Column("comparison_payload", JSONB, nullable=False),
        sa.Column("selected_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("final_score_lower", sa.Numeric(6, 2), nullable=True),
        sa.Column("final_score_recommended", sa.Numeric(6, 2), nullable=True),
        sa.Column("final_score_upper", sa.Numeric(6, 2), nullable=True),
        sa.Column("final_score_maximum", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "state in ('consistent', 'minor_disagreement', "
            "'major_disagreement', 'unable_to_score')",
            name="essay_grading_run_state_check",
        ),
        sa.CheckConstraint(
            "candidate_count between 2 and 3 and valid_candidate_count between 0 and candidate_count",
            name="essay_grading_run_candidate_count_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64 and length(comparison_fingerprint) = 64",
            name="essay_grading_run_fingerprint_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(comparison_payload) = 'object'",
            name="essay_grading_run_comparison_payload_check",
        ),
        sa.CheckConstraint(
            "(state = 'unable_to_score' and valid_candidate_count < 2 "
            "and num_nonnulls(selected_candidate_id, final_score_lower, "
            "final_score_recommended, final_score_upper, final_score_maximum) = 0) "
            "or (state <> 'unable_to_score' and valid_candidate_count >= 2 "
            "and num_nonnulls(selected_candidate_id, final_score_lower, "
            "final_score_recommended, final_score_upper, final_score_maximum) = 5)",
            name="essay_grading_run_final_score_check",
        ),
        sa.CheckConstraint(
            "final_score_lower is null or (final_score_lower >= 0 "
            "and final_score_lower <= final_score_recommended "
            "and final_score_recommended <= final_score_upper "
            "and final_score_upper <= final_score_maximum "
            "and final_score_maximum <= 100)",
            name="essay_grading_run_score_range_check",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["platform.background_jobs.job_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("grading_run_id"),
        sa.UniqueConstraint(
            "intended_review_version_id",
            name="essay_grading_run_review_key",
        ),
        sa.UniqueConstraint("job_id", name="essay_grading_run_job_key"),
        schema="ai_audit",
    )
    op.create_table(
        "essay_grading_candidates",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("grading_run_id", sa.Uuid(), nullable=False),
        sa.Column("slot_id", sa.Text(), nullable=False),
        sa.Column("provider_release_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("output_fingerprint", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status in ('valid', 'invalid', 'provider_failed')",
            name="essay_grading_candidate_status_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64 and "
            "(output_fingerprint is null or length(output_fingerprint) = 64)",
            name="essay_grading_candidate_fingerprint_check",
        ),
        sa.CheckConstraint(
            "(status = 'valid' and payload is not null and model_call_id is not null "
            "and output_fingerprint is not null and error_code is null) "
            "or (status <> 'valid' and payload is null and error_code is not null)",
            name="essay_grading_candidate_payload_check",
        ),
        sa.CheckConstraint(
            "payload is null or jsonb_typeof(payload) = 'object'",
            name="essay_grading_candidate_payload_type_check",
        ),
        sa.ForeignKeyConstraint(
            ["grading_run_id"],
            ["ai_audit.essay_grading_runs.grading_run_id"],
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"],
            ["ai_audit.model_call_ledger.call_id"],
        ),
        sa.PrimaryKeyConstraint("candidate_id"),
        sa.UniqueConstraint(
            "grading_run_id", "slot_id", name="essay_grading_candidate_slot_key"
        ),
        sa.UniqueConstraint(
            "grading_run_id",
            "provider_release_id",
            "model",
            name="essay_grading_candidate_release_key",
        ),
        sa.UniqueConstraint(
            "grading_run_id",
            "model_call_id",
            name="essay_grading_candidate_model_call_key",
        ),
        sa.UniqueConstraint(
            "grading_run_id",
            "candidate_id",
            name="essay_grading_candidate_run_identity_key",
        ),
        schema="ai_audit",
    )
    op.create_foreign_key(
        "essay_grading_run_selected_candidate_fk",
        "essay_grading_runs",
        "essay_grading_candidates",
        ["grading_run_id", "selected_candidate_id"],
        ["grading_run_id", "candidate_id"],
        source_schema="ai_audit",
        referent_schema="ai_audit",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "essay_grading_adjudications",
        sa.Column("adjudication_id", sa.Uuid(), nullable=False),
        sa.Column("grading_run_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("provider_release_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("model_call_id", sa.Uuid(), nullable=False),
        sa.Column("blind_request", JSONB, nullable=False),
        sa.Column("decision", JSONB, nullable=False),
        sa.Column("selected_candidate_id", sa.Uuid(), nullable=False),
        sa.Column("output_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blind_request) = 'object' and jsonb_typeof(decision) = 'object'",
            name="essay_grading_adjudication_payload_check",
        ),
        sa.CheckConstraint(
            "length(output_fingerprint) = 64",
            name="essay_grading_adjudication_output_sha_check",
        ),
        sa.ForeignKeyConstraint(
            ["grading_run_id"],
            ["ai_audit.essay_grading_runs.grading_run_id"],
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"], ["ai_audit.model_call_ledger.call_id"]
        ),
        sa.ForeignKeyConstraint(
            ["grading_run_id", "selected_candidate_id"],
            [
                "ai_audit.essay_grading_candidates.grading_run_id",
                "ai_audit.essay_grading_candidates.candidate_id",
            ],
            name="essay_grading_adjudication_selected_candidate_fk",
        ),
        sa.PrimaryKeyConstraint("adjudication_id"),
        sa.UniqueConstraint("grading_run_id", name="essay_grading_adjudication_run_key"),
        schema="ai_audit",
    )
    op.create_table(
        "essay_review_grading_bindings",
        sa.Column("review_version_id", sa.Uuid(), nullable=False),
        sa.Column("grading_run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["review_version_id"],
            ["learning.essay_review_versions.review_version_id"],
        ),
        sa.ForeignKeyConstraint(
            ["grading_run_id"],
            ["ai_audit.essay_grading_runs.grading_run_id"],
        ),
        sa.PrimaryKeyConstraint("review_version_id"),
        sa.UniqueConstraint("grading_run_id", name="essay_review_grading_run_key"),
        schema="ai_audit",
    )
    for table in (
        "essay_grading_runs",
        "essay_grading_candidates",
        "essay_grading_adjudications",
        "essay_review_grading_bindings",
    ):
        op.execute(
            f"create trigger {table}_append_only before update or delete "
            f"on ai_audit.{table} for each row execute function "
            "ai_audit.reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table in (
        "essay_review_grading_bindings",
        "essay_grading_adjudications",
        "essay_grading_candidates",
        "essay_grading_runs",
    ):
        op.execute(f"drop trigger {table}_append_only on ai_audit.{table}")
    op.drop_table("essay_review_grading_bindings", schema="ai_audit")
    op.drop_table("essay_grading_adjudications", schema="ai_audit")
    op.drop_constraint(
        "essay_grading_run_selected_candidate_fk",
        "essay_grading_runs",
        schema="ai_audit",
        type_="foreignkey",
    )
    op.drop_table("essay_grading_candidates", schema="ai_audit")
    op.drop_table("essay_grading_runs", schema="ai_audit")
