"""Add the database authority for background consumer liveness.

Revision ID: 20260730_80
Revises: 20260730_79
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260730_80"
down_revision: str | Sequence[str] | None = "20260730_79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        create table if not exists platform.background_job_consumer_heartbeats (
          consumer_unit text primary key,
          consumer_entrypoint text not null,
          process_instance_id uuid not null,
          started_at timestamptz not null,
          last_heartbeat_at timestamptz not null,
          constraint background_job_consumer_heartbeats_unit_check
            check (consumer_unit ~ '^gongkao-dazi-[a-z0-9-]+$'),
          constraint background_job_consumer_heartbeats_entrypoint_check
            check (consumer_entrypoint ~ '^app[.]workers[.][a-z0-9_]+$'),
          constraint background_job_consumer_heartbeats_time_check
            check (last_heartbeat_at >= started_at)
        )
        """
    )
    op.execute(
        """
        create index if not exists background_job_consumer_heartbeats_recency_idx
          on platform.background_job_consumer_heartbeats (
            last_heartbeat_at desc,
            consumer_unit
          )
        """
    )
    op.execute("revoke all on platform.background_job_consumer_heartbeats from public")
    if _runtime_role_exists():
        op.execute(
            "grant select, insert, update "
            "on platform.background_job_consumer_heartbeats to gongkao_api"
        )


def downgrade() -> None:
    raise RuntimeError(
        "background consumer heartbeat authority is forward-only; "
        "publish a forward compatibility migration"
    )


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')"))
        .scalar_one()
    )
