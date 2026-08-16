"""Bind verifier-only analysis media evidence without fake question assets.

Revision ID: 20260813_108
Revises: 20260813_107
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260813_108"
down_revision: str | Sequence[str] | None = "20260813_107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .exec_driver_sql(
            "select exists (select 1 from pg_roles where rolname = 'gongkao_api')"
        )
        .scalar_one()
    )


def upgrade() -> None:
    op.execute(
        """
        create table if not exists product.question_analysis_visual_evidence_bindings (
          binding_id char(64) primary key check (binding_id ~ '^[0-9a-f]{64}$'),
          evidence_ref text not null check (char_length(evidence_ref) between 1 and 240),
          evidence_version_id char(64) not null references
            product.structured_visual_evidence_versions(evidence_version_id),
          question_id text not null references product.questions(question_id),
          content_version_id text not null,
          analysis_source text not null check (char_length(analysis_source) between 1 and 120),
          source_occurrence_question_key text not null check (
            char_length(source_occurrence_question_key) between 1 and 240
          ),
          source_image_ref text not null check (
            char_length(source_image_ref) between 1 and 2000
          ),
          runtime_object_key text,
          source_asset_sha256 char(64) not null check (
            source_asset_sha256 ~ '^[0-9a-f]{64}$'
          ),
          batch_id text not null check (char_length(batch_id) between 1 and 240),
          bound_at timestamptz not null,
          foreign key (question_id, content_version_id) references
            product.question_content_versions(question_id, content_version_id),
          unique (
            evidence_ref, evidence_version_id, question_id,
            content_version_id, source_occurrence_question_key,
            source_image_ref, source_asset_sha256
          )
        )
        """
    )
    op.execute(
        """
        create index if not exists question_analysis_visual_current_lookup_idx
          on product.question_analysis_visual_evidence_bindings (
            question_id, content_version_id, evidence_ref
          )
        """
    )
    op.execute(
        """
        create index if not exists question_analysis_visual_batch_idx
          on product.question_analysis_visual_evidence_bindings (batch_id)
        """
    )
    if _runtime_role_exists():
        op.execute(
            "grant select on product.question_analysis_visual_evidence_bindings "
            "to gongkao_api"
        )


def downgrade() -> None:
    raise RuntimeError(
        "analysis media evidence publication is forward-only; "
        "withdraw exact batches instead"
    )
