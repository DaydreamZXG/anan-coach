"""Add idempotent, auditable feature-usage reservations.

Revision ID: 20260713_14
Revises: 20260713_13
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_14"
down_revision: str | Sequence[str] | None = "20260713_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feature_usage_reservations",
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("limit_count", sa.Integer(), nullable=False),
        sa.Column("usage_count_at_reservation", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("business_key", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "char_length(reservation_key) between 1 and 300",
            name="feature_usage_reservation_key_check",
        ),
        sa.CheckConstraint(
            "char_length(feature) between 1 and 120",
            name="feature_usage_reservation_feature_check",
        ),
        sa.CheckConstraint(
            "char_length(business_key) between 1 and 300",
            name="feature_usage_reservation_business_key_check",
        ),
        sa.CheckConstraint(
            "limit_count > 0",
            name="feature_usage_reservation_limit_check",
        ),
        sa.CheckConstraint(
            "usage_count_at_reservation between 1 and limit_count",
            name="feature_usage_reservation_count_check",
        ),
        sa.CheckConstraint(
            "status in ('reserved', 'consumed', 'refunded')",
            name="feature_usage_reservation_status_check",
        ),
        sa.CheckConstraint(
            "(status = 'reserved' and consumed_at is null and refunded_at is null) "
            "or (status = 'consumed' and consumed_at is not null and refunded_at is null) "
            "or (status = 'refunded' and consumed_at is null and refunded_at is not null)",
            name="feature_usage_reservation_transition_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(["job_id"], ["platform.background_jobs.job_id"]),
        sa.PrimaryKeyConstraint("reservation_id"),
        sa.UniqueConstraint(
            "reservation_key",
            name="feature_usage_reservations_reservation_key_key",
        ),
        sa.UniqueConstraint(
            "user_id",
            "feature",
            "business_key",
            name="feature_usage_reservations_business_key_key",
        ),
        sa.UniqueConstraint(
            "job_id",
            name="feature_usage_reservations_job_id_key",
        ),
        schema="commerce",
    )
    op.create_index(
        "commerce_feature_usage_reservations_usage_idx",
        "feature_usage_reservations",
        ["user_id", "feature", "usage_date", "status"],
        schema="commerce",
    )
    op.execute(
        """
        create function commerce.enforce_feature_usage_reservation_update()
        returns trigger language plpgsql as $$
        begin
          if tg_op = 'DELETE' then
            raise exception using
              errcode = '23514',
              message = 'feature usage reservation history cannot be deleted';
          end if;
          if old.status <> 'reserved' then
            raise exception using
              errcode = '23514',
              message = 'terminal feature usage reservation is immutable';
          end if;
          if row(
            new.reservation_id,
            new.reservation_key,
            new.user_id,
            new.feature,
            new.usage_date,
            new.limit_count,
            new.usage_count_at_reservation,
            new.business_key,
            new.reserved_at
          ) is distinct from row(
            old.reservation_id,
            old.reservation_key,
            old.user_id,
            old.feature,
            old.usage_date,
            old.limit_count,
            old.usage_count_at_reservation,
            old.business_key,
            old.reserved_at
          ) then
            raise exception using
              errcode = '23514',
              message = 'feature usage reservation identity is immutable';
          end if;
          if old.job_id is not null and new.job_id is distinct from old.job_id then
            raise exception using
              errcode = '23514',
              message = 'feature usage reservation job binding is immutable';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger feature_usage_reservation_enforce_update "
        "before update or delete on commerce.feature_usage_reservations "
        "for each row execute function "
        "commerce.enforce_feature_usage_reservation_update()"
    )


def downgrade() -> None:
    op.drop_index(
        "commerce_feature_usage_reservations_usage_idx",
        table_name="feature_usage_reservations",
        schema="commerce",
    )
    op.drop_table("feature_usage_reservations", schema="commerce")
    op.execute("drop function commerce.enforce_feature_usage_reservation_update()")
