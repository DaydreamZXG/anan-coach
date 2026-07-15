"""Add durable, replay-safe Companion paid-command ledger.

Revision ID: 20260713_17
Revises: 20260713_16
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260713_17"
down_revision: str | Sequence[str] | None = "20260713_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companion_paid_commands",
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("request_contract_version", sa.Text(), nullable=False),
        sa.Column(
            "execution_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column(
            "response_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("response_fingerprint", sa.Text(), nullable=True),
        sa.Column("response_contract_version", sa.Text(), nullable=False),
        sa.Column("reservation_key", sa.Text(), nullable=False),
        sa.Column("provider_idempotency_key", sa.Text(), nullable=True),
        sa.Column("delivery_count", sa.Integer(), nullable=False),
        sa.Column("provider_attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider_request_id_header", sa.Text(), nullable=True),
        sa.Column("provider_request_id_fingerprint", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "feature in ('companion_ask', 'external_wrong_intake')",
            name="companion_paid_commands_feature_check",
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) between 8 and 160",
            name="companion_paid_commands_idempotency_key_check",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="companion_paid_commands_request_fingerprint_check",
        ),
        sa.CheckConstraint(
            "char_length(request_contract_version) between 1 and 120 "
            "and char_length(response_contract_version) between 1 and 120",
            name="companion_paid_commands_contract_version_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(execution_payload) = 'object'",
            name="companion_paid_commands_execution_payload_check",
        ),
        sa.CheckConstraint(
            "status in ('processing', 'provider_uncertain', 'succeeded', 'failed')",
            name="companion_paid_commands_status_check",
        ),
        sa.CheckConstraint(
            "char_length(reservation_key) between 1 and 300",
            name="companion_paid_commands_reservation_key_check",
        ),
        sa.CheckConstraint(
            "(feature = 'companion_ask' and provider_idempotency_key is not null and "
            "char_length(provider_idempotency_key) between 8 and 300) "
            "or (feature = 'external_wrong_intake' and "
            "provider_idempotency_key is null)",
            name="companion_paid_commands_provider_key_check",
        ),
        sa.CheckConstraint(
            "delivery_count >= 1 and provider_attempt_count >= 0",
            name="companion_paid_commands_attempt_count_check",
        ),
        sa.CheckConstraint(
            "feature <> 'external_wrong_intake' or provider_attempt_count = 0",
            name="companion_paid_commands_external_attempt_check",
        ),
        sa.CheckConstraint(
            "status <> 'provider_uncertain' or (feature = 'companion_ask' and "
            "last_error_code is not null and "
            "char_length(last_error_code) between 1 and 160)",
            name="companion_paid_commands_uncertain_feature_check",
        ),
        sa.CheckConstraint(
            "(provider_request_id_header is null and "
            "provider_request_id_fingerprint is null) or "
            "(provider_request_id_header is not null and "
            "provider_request_id_fingerprint is not null and "
            "char_length(provider_request_id_header) between 1 and 120 and "
            "provider_request_id_fingerprint ~ '^[0-9a-f]{64}$')",
            name="companion_paid_commands_provider_request_id_check",
        ),
        sa.CheckConstraint(
            "(status in ('processing', 'provider_uncertain') and "
            "response_status_code is null and response_payload is null and "
            "response_fingerprint is null and completed_at is null) or "
            "(status = 'succeeded' and response_status_code is not null and "
            "response_status_code = 200 and response_payload is not null and "
            "jsonb_typeof(response_payload) = 'object' and "
            "response_fingerprint is not null and "
            "response_fingerprint ~ '^[0-9a-f]{64}$' and "
            "last_error_code is null and completed_at is not null) or "
            "(status = 'failed' and response_status_code is not null and "
            "response_status_code between 400 and 599 and "
            "response_payload is not null and "
            "jsonb_typeof(response_payload) = 'object' and "
            "response_fingerprint is not null and "
            "response_fingerprint ~ '^[0-9a-f]{64}$' and "
            "last_error_code is not null and "
            "char_length(last_error_code) between 1 and 160 and "
            "completed_at is not null)",
            name="companion_paid_commands_response_checkpoint_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.PrimaryKeyConstraint("command_id"),
        sa.UniqueConstraint(
            "user_id",
            "feature",
            "idempotency_key",
            name="companion_paid_commands_identity_key",
        ),
        sa.UniqueConstraint(
            "reservation_key",
            name="companion_paid_commands_reservation_key_key",
        ),
        schema="platform",
    )
    op.create_index(
        "platform_companion_paid_commands_user_created_idx",
        "companion_paid_commands",
        ["user_id", "feature", "created_at"],
        schema="platform",
    )
    op.create_index(
        "platform_companion_paid_commands_open_idx",
        "companion_paid_commands",
        ["status", "updated_at"],
        schema="platform",
        postgresql_where=sa.text("status in ('processing', 'provider_uncertain')"),
    )
    op.create_index(
        "platform_companion_paid_commands_provider_key_key",
        "companion_paid_commands",
        ["provider_idempotency_key"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("provider_idempotency_key is not null"),
    )
    op.execute(
        """
        create function platform.enforce_companion_paid_command_transition()
        returns trigger language plpgsql as $$
        begin
          if tg_op = 'DELETE' then
            raise exception using
              errcode = '23514',
              message = 'companion paid command history cannot be deleted';
          end if;
          if old.status in ('succeeded', 'failed') then
            raise exception using
              errcode = '23514',
              message = 'terminal companion paid command is immutable';
          end if;
          if row(
            new.command_id,
            new.user_id,
            new.feature,
            new.idempotency_key,
            new.request_fingerprint,
            new.request_contract_version,
            new.execution_payload,
            new.response_contract_version,
            new.reservation_key,
            new.provider_idempotency_key,
            new.created_at
          ) is distinct from row(
            old.command_id,
            old.user_id,
            old.feature,
            old.idempotency_key,
            old.request_fingerprint,
            old.request_contract_version,
            old.execution_payload,
            old.response_contract_version,
            old.reservation_key,
            old.provider_idempotency_key,
            old.created_at
          ) then
            raise exception using
              errcode = '23514',
              message = 'companion paid command input is immutable';
          end if;
          if new.delivery_count < old.delivery_count
             or new.provider_attempt_count < old.provider_attempt_count then
            raise exception using
              errcode = '23514',
              message = 'companion paid command counters cannot decrease';
          end if;
          if not (
            (old.status = 'processing' and
             new.status in ('processing', 'provider_uncertain', 'succeeded', 'failed'))
            or
            (old.status = 'provider_uncertain' and
             new.status in ('provider_uncertain', 'processing'))
          ) then
            raise exception using
              errcode = '23514',
              message = 'invalid companion paid command state transition';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger companion_paid_command_enforce_transition "
        "before update or delete on platform.companion_paid_commands "
        "for each row execute function "
        "platform.enforce_companion_paid_command_transition()"
    )


def downgrade() -> None:
    op.drop_index(
        "platform_companion_paid_commands_provider_key_key",
        table_name="companion_paid_commands",
        schema="platform",
    )
    op.drop_index(
        "platform_companion_paid_commands_open_idx",
        table_name="companion_paid_commands",
        schema="platform",
    )
    op.drop_index(
        "platform_companion_paid_commands_user_created_idx",
        table_name="companion_paid_commands",
        schema="platform",
    )
    op.drop_table("companion_paid_commands", schema="platform")
    op.execute("drop function platform.enforce_companion_paid_command_transition()")
