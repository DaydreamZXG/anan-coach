"""Publish immutable structured visual teaching evidence.

Revision ID: 20260813_107
Revises: 20260810_106
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260813_107"
down_revision: str | Sequence[str] | None = "20260810_106"
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
        create table if not exists product.structured_visual_evidence_versions (
          evidence_version_id char(64) primary key check (
            evidence_version_id ~ '^[0-9a-f]{64}$'
          ),
          evidence_ref text not null check (char_length(evidence_ref) between 1 and 240),
          revision integer not null check (revision >= 1),
          source_asset_sha256 char(64) not null check (
            source_asset_sha256 ~ '^[0-9a-f]{64}$'
          ),
          source_payload_sha256 char(64) not null check (
            source_payload_sha256 ~ '^[0-9a-f]{64}$'
          ),
          verification_policy_version text not null check (
            char_length(verification_policy_version) between 1 and 200
          ),
          chart_type text not null check (char_length(chart_type) between 1 and 120),
          payload jsonb not null check (
            jsonb_typeof(payload) = 'object'
            and jsonb_typeof(payload -> 'facts') = 'array'
            and jsonb_array_length(payload -> 'facts') between 1 and 5000
            and payload ? 'chartType'
          ),
          created_at timestamptz not null,
          unique (evidence_ref, revision),
          unique (
            evidence_ref, source_payload_sha256, verification_policy_version
          )
        )
        """
    )
    op.execute(
        """
        create or replace function product.reject_structured_visual_evidence_version_mutation()
        returns trigger language plpgsql as $$
        begin
          raise exception using
            errcode = '23514',
            message = 'structured visual evidence versions are append-only';
        end;
        $$
        """
    )
    op.execute(
        """
        drop trigger if exists structured_visual_evidence_versions_append_only
          on product.structured_visual_evidence_versions;
        create trigger structured_visual_evidence_versions_append_only
          before update or delete on product.structured_visual_evidence_versions
          for each row execute function
            product.reject_structured_visual_evidence_version_mutation()
        """
    )
    op.execute(
        """
        create table if not exists product.structured_visual_evidence_heads (
          evidence_ref text primary key check (char_length(evidence_ref) between 1 and 240),
          evidence_version_id char(64) not null unique references
            product.structured_visual_evidence_versions(evidence_version_id),
          activated_by_batch_id text not null check (
            char_length(activated_by_batch_id) between 1 and 240
          ),
          updated_at timestamptz not null
        )
        """
    )
    op.execute(
        """
        create table if not exists product.question_structured_visual_evidence_bindings (
          binding_id char(64) primary key check (binding_id ~ '^[0-9a-f]{64}$'),
          evidence_ref text not null,
          evidence_version_id char(64) not null references
            product.structured_visual_evidence_versions(evidence_version_id),
          question_id text not null references product.questions(question_id),
          content_version_id text not null,
          runtime_asset_id text not null references product.question_assets(asset_id),
          source_asset_sha256 char(64) not null check (
            source_asset_sha256 ~ '^[0-9a-f]{64}$'
          ),
          batch_id text not null check (char_length(batch_id) between 1 and 240),
          bound_at timestamptz not null,
          foreign key (question_id, content_version_id) references
            product.question_content_versions(question_id, content_version_id),
          unique (
            evidence_ref, evidence_version_id, question_id,
            content_version_id, source_asset_sha256
          )
        )
        """
    )
    op.execute(
        """
        create index if not exists question_structured_visual_current_lookup_idx
          on product.question_structured_visual_evidence_bindings (
            question_id, content_version_id, evidence_ref
          )
        """
    )
    op.execute(
        """
        create index if not exists question_structured_visual_batch_idx
          on product.question_structured_visual_evidence_bindings (batch_id)
        """
    )
    op.execute(
        """
        create table if not exists product.structured_visual_evidence_import_receipts (
          batch_id text primary key check (char_length(batch_id) between 1 and 240),
          plan_sha256 char(64) not null check (plan_sha256 ~ '^[0-9a-f]{64}$'),
          verification_policy_version text not null,
          state text not null check (state in ('active', 'withdrawn')),
          evidence_count integer not null check (evidence_count >= 0),
          binding_count integer not null check (binding_count >= 0),
          quarantined_count integer not null check (quarantined_count >= 0),
          previous_heads jsonb not null check (jsonb_typeof(previous_heads) = 'object'),
          report jsonb not null check (jsonb_typeof(report) = 'object'),
          created_at timestamptz not null,
          withdrawn_at timestamptz,
          check ((state = 'withdrawn') = (withdrawn_at is not null))
        )
        """
    )
    if _runtime_role_exists():
        op.execute(
            "grant select on "
            "product.structured_visual_evidence_versions, "
            "product.structured_visual_evidence_heads, "
            "product.question_structured_visual_evidence_bindings, "
            "product.structured_visual_evidence_import_receipts "
            "to gongkao_api"
        )


def downgrade() -> None:
    raise RuntimeError(
        "structured visual evidence publication is forward-only; "
        "withdraw an exact batch instead of deleting audit versions"
    )
