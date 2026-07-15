"""Add durable platform outbox and background jobs.

Revision ID: 20260712_02
Revises: 20260712_01
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_02"
down_revision: str | Sequence[str] | None = "20260712_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create schema if not exists platform")
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("deduplication_key", sa.Text(), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status in ('pending', 'publishing', 'published', 'failed')",
            name="outbox_events_status_check",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="outbox_events_attempt_count_check"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("deduplication_key", name="outbox_events_deduplication_key_key"),
        schema="platform",
    )
    op.create_index(
        "platform_outbox_dispatch_idx",
        "outbox_events",
        ["status", "available_at", "created_at"],
        schema="platform",
    )

    op.create_table(
        "background_jobs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("job_key", sa.Text(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("input_version", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'waiting_confirmation', 'succeeded', 'failed', 'cancelled')",
            name="background_jobs_status_check",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="background_jobs_attempt_count_check"),
        sa.CheckConstraint("max_attempts > 0", name="background_jobs_max_attempts_check"),
        sa.CheckConstraint("attempt_count <= max_attempts", name="background_jobs_attempt_limit_check"),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("job_key", name="background_jobs_job_key_key"),
        schema="platform",
    )
    op.create_index(
        "platform_background_jobs_claim_idx",
        "background_jobs",
        ["status", "run_after", "created_at"],
        schema="platform",
    )


def downgrade() -> None:
    op.drop_index(
        "platform_background_jobs_claim_idx",
        table_name="background_jobs",
        schema="platform",
    )
    op.drop_table("background_jobs", schema="platform")
    op.drop_index(
        "platform_outbox_dispatch_idx",
        table_name="outbox_events",
        schema="platform",
    )
    op.drop_table("outbox_events", schema="platform")
    op.execute("drop schema if exists platform")
