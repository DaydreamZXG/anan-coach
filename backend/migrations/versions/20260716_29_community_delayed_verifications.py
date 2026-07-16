"""Move delayed community-card verification into append-only history.

Revision ID: 20260716_29
Revises: 20260716_28
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_29"
down_revision: str | Sequence[str] | None = "20260716_28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _assert_no_mutable_outcomes(op.get_bind())
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_records(bind):
        raise RuntimeError(
            "Delayed community-card verification facts exist; destructive downgrade "
            "is forbidden. Repair the schema with a forward migration."
        )
    op.add_column(
        "community_method_card_trials",
        sa.Column("delayed_verification_outcome", sa.Text(), nullable=True),
        schema="product",
    )
    op.create_check_constraint(
        "community_method_card_trial_delayed_outcome_check",
        "community_method_card_trials",
        "delayed_verification_outcome in ('passed', 'failed', 'missed')",
        schema="product",
    )
    op.drop_table("community_method_card_delayed_verifications", schema="product")


def _assert_no_mutable_outcomes(bind: Connection) -> None:
    has_outcomes = bool(
        bind.execute(
            sa.text(
                "select exists ("
                "select 1 from product.community_method_card_trials "
                "where delayed_verification_outcome is not null"
                ")"
            )
        ).scalar_one()
    )
    if has_outcomes:
        raise RuntimeError(
            "Mutable delayed verification outcomes already exist; migration 29 refuses "
            "to invent append-only history. Resolve them with an explicit forward repair."
        )


def _has_records(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "select exists ("
                "select 1 from product.community_method_card_delayed_verifications"
                ")"
            )
        ).scalar_one()
    )


_STATEMENTS = (
    """
    create table product.community_method_card_delayed_verifications (
      verification_id uuid primary key,
      trial_id uuid not null unique
        references product.community_method_card_trials(trial_id),
      question_attempt_id text,
      question_version_id text,
      correct_after boolean,
      duration_after_ms integer check (duration_after_ms >= 0),
      outcome text not null check (outcome in ('passed', 'failed', 'missed')),
      evidence_refs jsonb not null default '[]'::jsonb
        check (jsonb_typeof(evidence_refs) = 'array'),
      completed_at timestamptz not null,
      check (
        (outcome = 'missed' and question_attempt_id is null
          and question_version_id is null and correct_after is null
          and duration_after_ms is null)
        or (outcome in ('passed', 'failed') and question_attempt_id is not null
          and question_version_id is not null and correct_after is not null
          and duration_after_ms is not null)
      )
    )
    """,
    """
    create trigger community_method_card_delayed_verifications_append_only
      before update or delete
      on product.community_method_card_delayed_verifications
      for each row execute function product.reject_community_method_history_mutation()
    """,
    """
    alter table product.community_method_card_trials
      drop column delayed_verification_outcome
    """,
)
