"""Add the JOB-LEASE@v1 database compatibility window.

Revision ID: 20260728_72
Revises: 20260728_71
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260728_72"
down_revision: str | Sequence[str] | None = "20260728_71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UP_STATEMENTS = (
    """
    alter table platform.background_jobs
      add column if not exists lease_expires_at timestamptz
    """,
    """
    alter table platform.background_jobs
      drop constraint if exists background_jobs_lease_fencing_check
    """,
    """
    alter table platform.background_jobs
      add constraint background_jobs_lease_fencing_check check (
        lease_expires_at is null
        or (
          claim_token is not null
          and claimed_at is not null
          and lease_expires_at > claimed_at
        )
      )
    """,
    """
    create index if not exists platform_background_jobs_lease_recovery_idx
      on platform.background_jobs (
        status, lease_expires_at, run_after, created_at
      )
      where status in ('queued', 'running')
    """,
    """
    alter table messaging.ilink_object_deletion_queue
      add column if not exists claim_token uuid,
      add column if not exists claimed_at timestamptz,
      add column if not exists lease_expires_at timestamptz
    """,
    """
    alter table messaging.ilink_object_deletion_queue
      drop constraint if exists ilink_object_deletion_lease_fencing_check
    """,
    """
    alter table messaging.ilink_object_deletion_queue
      add constraint ilink_object_deletion_lease_fencing_check check (
        lease_expires_at is null
        or (
          claim_token is not null
          and claimed_at is not null
          and lease_expires_at > claimed_at
        )
      )
    """,
    """
    create index if not exists ilink_object_deletion_lease_recovery_idx
      on messaging.ilink_object_deletion_queue (
        state, lease_expires_at, updated_at
      )
      where state in ('pending', 'deleting', 'failed')
    """,
)


def upgrade() -> None:
    for statement in _UP_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    connection = op.get_bind()
    has_background_lease_facts = connection.execute(
        text(
            """
            select exists (
              select 1
              from platform.background_jobs
              where lease_expires_at is not null
            )
            """
        )
    ).scalar_one()
    has_ilink_claim_facts = connection.execute(
        text(
            """
            select exists (
              select 1
              from messaging.ilink_object_deletion_queue
              where claim_token is not null
                or claimed_at is not null
                or lease_expires_at is not null
            )
            """
        )
    ).scalar_one()
    if has_background_lease_facts or has_ilink_claim_facts:
        raise RuntimeError(
            "JOB-LEASE@v1 claim or lease facts exist; use a forward compatibility migration"
        )

    op.execute("drop index if exists messaging.ilink_object_deletion_lease_recovery_idx")
    op.execute(
        "alter table messaging.ilink_object_deletion_queue "
        "drop constraint if exists ilink_object_deletion_lease_fencing_check"
    )
    op.execute(
        "alter table messaging.ilink_object_deletion_queue "
        "drop column lease_expires_at, "
        "drop column claimed_at, "
        "drop column claim_token"
    )
    op.execute("drop index if exists platform.platform_background_jobs_lease_recovery_idx")
    op.execute(
        "alter table platform.background_jobs "
        "drop constraint if exists background_jobs_lease_fencing_check"
    )
    op.execute("alter table platform.background_jobs drop column lease_expires_at")
