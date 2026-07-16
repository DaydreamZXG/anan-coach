"""Add append-only question quality adjudication and current projection.

Revision ID: 20260716_27
Revises: 20260716_26
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_27"
down_revision: str | Sequence[str] | None = "20260716_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_records(bind):
        raise RuntimeError(
            "Question-quality decisions exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    for table in (
        "question_quality_heads",
        "question_quality_decision_disputes",
        "question_quality_commands",
        "question_quality_decision_versions",
    ):
        op.drop_table(table, schema="product")


def _has_records(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "select exists (select 1 from product.question_quality_commands) "
                "or exists (select 1 from product.question_quality_decision_versions)"
            )
        ).scalar_one()
    )


_STATEMENTS = (
    """
    create table product.question_quality_commands (
      command_id text primary key check (char_length(command_id) between 8 and 160),
      actor_ref text not null check (char_length(actor_ref) between 8 and 200),
      request_fingerprint text not null check (length(request_fingerprint) = 64),
      decision_version_id uuid not null unique,
      created_at timestamptz not null
    )
    """,
    """
    create table product.question_quality_decision_versions (
      decision_version_id uuid primary key,
      decision_id uuid not null,
      question_id text not null references product.questions(question_id),
      content_version_id text not null,
      version_no bigint not null check (version_no >= 1),
      decision text not null check (decision in (
        'needs_more_evidence', 'confirmed_valid', 'confirmed_invalid'
      )),
      reason_code text not null check (char_length(reason_code) between 2 and 120),
      rationale text not null check (char_length(rationale) between 2 and 4000),
      authority_version text not null check (char_length(authority_version) between 3 and 160),
      actor_ref text not null check (char_length(actor_ref) between 8 and 200),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) >= 2
      ),
      supersedes_decision_version_id uuid,
      created_at timestamptz not null,
      unique (decision_id, version_no),
      unique (decision_version_id, decision_id, question_id, content_version_id),
      foreign key (question_id, content_version_id)
        references product.question_content_versions(question_id, content_version_id),
      foreign key (supersedes_decision_version_id, decision_id, question_id, content_version_id)
        references product.question_quality_decision_versions(
          decision_version_id, decision_id, question_id, content_version_id
        ),
      check (
        (version_no = 1 and supersedes_decision_version_id is null)
        or (version_no > 1 and supersedes_decision_version_id is not null)
      )
    )
    """,
    """
    alter table product.question_quality_commands
      add constraint question_quality_command_decision_fk
      foreign key (decision_version_id)
      references product.question_quality_decision_versions(decision_version_id)
      deferrable initially deferred
    """,
    """
    create unique index product_question_quality_one_successor_idx
      on product.question_quality_decision_versions(supersedes_decision_version_id)
      where supersedes_decision_version_id is not null
    """,
    """
    create table product.question_quality_decision_disputes (
      decision_version_id uuid not null
        references product.question_quality_decision_versions(decision_version_id),
      dispute_id uuid not null references learning.xingce_question_disputes(dispute_id),
      created_at timestamptz not null,
      primary key (decision_version_id, dispute_id)
    )
    """,
    """
    create table product.question_quality_heads (
      question_id text not null,
      content_version_id text not null,
      decision_id uuid not null,
      decision_version_id uuid not null unique,
      decision text not null check (decision in (
        'needs_more_evidence', 'confirmed_valid', 'confirmed_invalid'
      )),
      updated_at timestamptz not null,
      primary key (question_id, content_version_id),
      foreign key (decision_version_id, decision_id, question_id, content_version_id)
        references product.question_quality_decision_versions(
          decision_version_id, decision_id, question_id, content_version_id
        )
    )
    """,
    """
    create trigger question_quality_commands_append_only
      before update or delete on product.question_quality_commands
      for each row execute function product.reject_question_content_version_mutation()
    """,
    """
    create trigger question_quality_decision_versions_append_only
      before update or delete on product.question_quality_decision_versions
      for each row execute function product.reject_question_content_version_mutation()
    """,
    """
    create trigger question_quality_decision_disputes_append_only
      before update or delete on product.question_quality_decision_disputes
      for each row execute function product.reject_question_content_version_mutation()
    """,
)
