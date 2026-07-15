"""Add the Stage 1.5 external-learning and content-license owners.

Revision ID: 20260715_20
Revises: 20260715_19
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260715_20"
down_revision: str | Sequence[str] | None = "20260715_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_stage15_records(bind):
        raise RuntimeError(
            "Stage 1.5 external-learning records exist; destructive downgrade is "
            "forbidden. Repair the schema with a forward migration."
        )
    for schema, table in _APPEND_ONLY_TABLES:
        op.execute(f"drop trigger if exists {table}_append_only on {schema}.{table}")
    for table in (
        "content_license_authorization_snapshots",
        "content_license_heads",
        "content_license_record_versions",
        "content_license_commands",
    ):
        op.drop_table(table, schema="product")
    for table in (
        "answer_ingestion_versions",
        "offline_packet_versions",
        "content_match_versions",
        "recognition_revisions",
        "source_artifact_versions",
    ):
        op.drop_table(table, schema="learning")
    op.execute("drop function if exists product.reject_content_license_history_mutation()")
    op.execute("drop function if exists learning.reject_external_learning_fact_mutation()")


def _has_stage15_records(bind: Connection) -> bool:
    selects = [
        f"select 1 from {schema}.{table}"
        for schema, table in _STAGE15_TABLES
        if table not in {"content_license_heads"}
    ]
    query = " union all ".join(selects)
    return bool(bind.execute(sa.text(f"select exists ({query})")).scalar_one())


_STAGE15_TABLES = (
    ("learning", "source_artifact_versions"),
    ("learning", "recognition_revisions"),
    ("learning", "content_match_versions"),
    ("learning", "offline_packet_versions"),
    ("learning", "answer_ingestion_versions"),
    ("product", "content_license_commands"),
    ("product", "content_license_record_versions"),
    ("product", "content_license_heads"),
    ("product", "content_license_authorization_snapshots"),
)

_APPEND_ONLY_TABLES = tuple(
    item for item in _STAGE15_TABLES if item[1] != "content_license_heads"
)


_STATEMENTS = (
    """
    create table learning.source_artifact_versions (
      artifact_version_id uuid primary key,
      artifact_id uuid not null,
      owner_user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      source_kind text not null check (source_kind in (
        'camera_image', 'image_import', 'pdf_upload', 'answer_sheet',
        'notes_app_export'
      )),
      state text not null check (state in (
        'uploaded', 'available', 'quarantined', 'expired', 'deleted'
      )),
      object_ref text not null check (char_length(object_ref) between 1 and 500),
      mime_type text not null check (char_length(mime_type) between 1 and 200),
      content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
      byte_size bigint not null check (byte_size > 0),
      page_count integer check (page_count >= 1),
      supersedes_artifact_version_id uuid,
      contract_payload jsonb not null check (jsonb_typeof(contract_payload) = 'object'),
      created_at timestamptz not null,
      unique (artifact_id, version_no),
      unique (artifact_version_id, owner_user_id),
      check ((version_no = 1) = (supersedes_artifact_version_id is null)),
      check (source_kind <> 'pdf_upload' or page_count is not null),
      foreign key (supersedes_artifact_version_id)
        references learning.source_artifact_versions(artifact_version_id)
    )
    """,
    """
    create unique index learning_source_artifact_one_successor_idx
      on learning.source_artifact_versions(supersedes_artifact_version_id)
      where supersedes_artifact_version_id is not null
    """,
    """
    create index learning_source_artifact_owner_created_idx
      on learning.source_artifact_versions(owner_user_id, created_at desc)
    """,
    """
    create table learning.recognition_revisions (
      recognition_revision_id uuid primary key,
      artifact_version_id uuid not null,
      owner_user_id uuid not null,
      revision_no bigint not null check (revision_no >= 1),
      recognition_kind text not null check (recognition_kind in (
        'printed_ocr', 'handwriting_ocr', 'exam_segmentation',
        'answer_sheet_omr'
      )),
      state text not null check (state in (
        'candidate', 'confirmed', 'rejected', 'expired'
      )),
      processor_policy_version text not null,
      processor_route text not null,
      input_sha256 text not null check (input_sha256 ~ '^[0-9a-f]{64}$'),
      output_sha256 text not null check (output_sha256 ~ '^[0-9a-f]{64}$'),
      candidate_payload jsonb not null check (jsonb_typeof(candidate_payload) = 'object'),
      confidence numeric(7,6) check (confidence between 0 and 1),
      expires_at timestamptz not null,
      confirmed_at timestamptz,
      created_at timestamptz not null,
      unique (artifact_version_id, recognition_kind, revision_no),
      unique (recognition_revision_id, owner_user_id, artifact_version_id),
      check ((state = 'confirmed') = (confirmed_at is not null)),
      foreign key (artifact_version_id, owner_user_id)
        references learning.source_artifact_versions(
          artifact_version_id, owner_user_id
        )
    )
    """,
    """
    create index learning_recognition_owner_state_idx
      on learning.recognition_revisions(owner_user_id, state, expires_at)
    """,
    """
    create table learning.content_match_versions (
      content_match_version_id uuid primary key,
      content_match_id uuid not null,
      artifact_version_id uuid not null,
      recognition_revision_id uuid not null,
      owner_user_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      state text not null check (state in (
        'candidate', 'confirmed', 'rejected', 'expired'
      )),
      candidates jsonb not null check (jsonb_typeof(candidates) = 'array'),
      confirmed_content_version_id text,
      matching_policy_version text not null,
      input_fingerprint text not null check (input_fingerprint ~ '^[0-9a-f]{64}$'),
      supersedes_content_match_version_id uuid,
      contract_payload jsonb not null check (jsonb_typeof(contract_payload) = 'object'),
      created_at timestamptz not null,
      unique (content_match_id, version_no),
      unique (content_match_version_id, owner_user_id),
      check ((version_no = 1) = (supersedes_content_match_version_id is null)),
      check ((state = 'confirmed') = (confirmed_content_version_id is not null)),
      foreign key (recognition_revision_id, owner_user_id, artifact_version_id)
        references learning.recognition_revisions(
          recognition_revision_id, owner_user_id, artifact_version_id
        ),
      foreign key (supersedes_content_match_version_id)
        references learning.content_match_versions(content_match_version_id)
    )
    """,
    """
    create unique index learning_content_match_one_successor_idx
      on learning.content_match_versions(supersedes_content_match_version_id)
      where supersedes_content_match_version_id is not null
    """,
    """
    create table learning.offline_packet_versions (
      packet_version_id uuid primary key,
      packet_id uuid not null,
      owner_user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      state text not null check (state in (
        'requested', 'generating', 'ready', 'failed', 'revoked', 'expired'
      )),
      render_format text not null check (render_format in ('pdf', 'note_app_file')),
      manifest_sha256 text not null check (manifest_sha256 ~ '^[0-9a-f]{64}$'),
      items jsonb not null check (
        jsonb_typeof(items) = 'array' and jsonb_array_length(items) > 0
      ),
      license_snapshot_refs jsonb not null check (
        jsonb_typeof(license_snapshot_refs) = 'array'
        and jsonb_array_length(license_snapshot_refs) > 0
      ),
      artifact_ref text,
      expires_at timestamptz,
      contract_payload jsonb not null check (jsonb_typeof(contract_payload) = 'object'),
      created_at timestamptz not null,
      unique (packet_id, version_no),
      unique (packet_version_id, owner_user_id),
      check (state <> 'ready' or artifact_ref is not null)
    )
    """,
    """
    create index learning_offline_packet_owner_state_idx
      on learning.offline_packet_versions(owner_user_id, state, created_at desc)
    """,
    """
    create table learning.answer_ingestion_versions (
      ingestion_version_id uuid primary key,
      ingestion_id uuid not null,
      owner_user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      packet_version_id uuid,
      source_artifact_version_ids jsonb not null check (
        jsonb_typeof(source_artifact_version_ids) = 'array'
        and jsonb_array_length(source_artifact_version_ids) > 0
      ),
      track text not null check (track in ('xingce', 'essay')),
      input_mode text not null check (input_mode in (
        'answer_string', 'answer_sheet_omr', 'handwriting_ocr', 'notes_app_export'
      )),
      state text not null check (state in (
        'candidate', 'confirmed', 'rejected', 'committed', 'conflict'
      )),
      candidate_payload jsonb not null check (jsonb_typeof(candidate_payload) = 'object'),
      target_writer text not null check (
        (track = 'xingce' and target_writer = 'xingce_learning')
        or (track = 'essay' and target_writer = 'essay_learning')
      ),
      writer_contract_version text not null,
      source_lineage_refs jsonb not null check (
        jsonb_typeof(source_lineage_refs) = 'array'
        and jsonb_array_length(source_lineage_refs) > 0
      ),
      confirmed_at timestamptz,
      committed_fact_ref text,
      contract_payload jsonb not null check (jsonb_typeof(contract_payload) = 'object'),
      created_at timestamptz not null,
      unique (ingestion_id, version_no),
      unique (ingestion_version_id, owner_user_id),
      check (state not in ('confirmed', 'committed') or confirmed_at is not null),
      check (state in ('confirmed', 'committed') or confirmed_at is null),
      check (state <> 'committed' or committed_fact_ref is not null),
      foreign key (packet_version_id, owner_user_id)
        references learning.offline_packet_versions(packet_version_id, owner_user_id)
    )
    """,
    """
    create index learning_answer_ingestion_owner_state_idx
      on learning.answer_ingestion_versions(owner_user_id, state, created_at desc)
    """,
    """
    create function learning.reject_external_learning_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception using
        errcode = '23514',
        message = 'external-learning facts are append-only';
    end;
    $$
    """,
    *tuple(
        f"create trigger {table}_append_only before update or delete on learning.{table} "
        "for each row execute function learning.reject_external_learning_fact_mutation()"
        for table in (
            "source_artifact_versions",
            "recognition_revisions",
            "content_match_versions",
            "offline_packet_versions",
            "answer_ingestion_versions",
        )
    ),
    """
    create table product.content_license_commands (
      command_id uuid primary key,
      actor_id text not null,
      command_type text not null check (command_type in (
        'draft_save', 'review_submit', 'activate', 'reject', 'suspend',
        'reinstate', 'expire', 'withdraw', 'supersede', 'abandon'
      )),
      idempotency_key text not null check (char_length(idempotency_key) between 8 and 260),
      content_license_id uuid not null,
      request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
      response_payload jsonb not null check (jsonb_typeof(response_payload) = 'object'),
      created_at timestamptz not null,
      unique (actor_id, command_type, idempotency_key)
    )
    """,
    """
    create index product_content_license_commands_license_created_idx
      on product.content_license_commands(content_license_id, created_at)
    """,
    """
    create table product.content_license_record_versions (
      record_version_id uuid primary key,
      content_license_id uuid not null,
      content_version_id text not null,
      revision bigint not null check (revision >= 1),
      authorization_epoch bigint not null check (authorization_epoch >= 0),
      state text not null check (state in (
        'draft', 'in_review', 'active', 'suspended', 'rejected', 'expired',
        'withdrawn', 'superseded', 'abandoned'
      )),
      created_by_actor_id text not null,
      closed_coverage_hash text not null check (closed_coverage_hash ~ '^[0-9a-f]{64}$'),
      evidence_manifest_hash text not null check (evidence_manifest_hash ~ '^[0-9a-f]{64}$'),
      grant_matrix_hash text not null check (grant_matrix_hash ~ '^[0-9a-f]{64}$'),
      record_canonical_hash text not null check (record_canonical_hash ~ '^[0-9a-f]{64}$'),
      record_payload jsonb not null check (jsonb_typeof(record_payload) = 'object'),
      reviewer_receipt jsonb,
      supersedes_content_license_id uuid,
      supersedes_record_version_id uuid,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      unique (content_license_id, revision),
      unique (record_canonical_hash),
      unique (record_version_id, content_license_id, content_version_id, revision),
      check ((revision = 1) = (supersedes_record_version_id is null)),
      check (
        (state in ('draft', 'in_review', 'rejected', 'abandoned') and authorization_epoch = 0)
        or (state in ('active', 'suspended', 'expired', 'withdrawn', 'superseded')
            and authorization_epoch >= 1)
      ),
      foreign key (supersedes_record_version_id)
        references product.content_license_record_versions(record_version_id)
    )
    """,
    """
    create unique index product_content_license_one_successor_idx
      on product.content_license_record_versions(supersedes_record_version_id)
      where supersedes_record_version_id is not null
    """,
    """
    create unique index product_content_license_one_replacement_idx
      on product.content_license_record_versions(supersedes_content_license_id)
      where revision = 1 and supersedes_content_license_id is not null
    """,
    """
    create table product.content_license_heads (
      content_license_id uuid primary key,
      content_version_id text not null,
      record_version_id uuid not null unique,
      revision bigint not null check (revision >= 1),
      authorization_epoch bigint not null check (authorization_epoch >= 0),
      state text not null,
      updated_at timestamptz not null,
      foreign key (record_version_id, content_license_id, content_version_id, revision)
        references product.content_license_record_versions(
          record_version_id, content_license_id, content_version_id, revision
        )
    )
    """,
    """
    create index product_content_license_heads_content_idx
      on product.content_license_heads(content_version_id, state)
    """,
    """
    create table product.content_license_authorization_snapshots (
      snapshot_id uuid primary key,
      snapshot_key text not null unique check (snapshot_key ~ '^[0-9a-f]{64}$'),
      content_license_id uuid not null,
      content_version_id text not null,
      license_revision bigint,
      authorization_epoch bigint,
      request_payload jsonb not null check (jsonb_typeof(request_payload) = 'object'),
      decision_payload jsonb not null check (jsonb_typeof(decision_payload) = 'object'),
      allowed boolean not null,
      reason text not null check (reason in (
        'explicit_grant', 'license_unknown', 'content_version_mismatch',
        'license_not_active', 'authorization_epoch_stale', 'term_not_started',
        'term_expired', 'default_deny'
      )),
      evaluated_at timestamptz not null
    )
    """,
    """
    create index product_content_license_snapshots_license_created_idx
      on product.content_license_authorization_snapshots(
        content_license_id, evaluated_at desc
      )
    """,
    """
    create function product.reject_content_license_history_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception using
        errcode = '23514',
        message = 'content-license history is append-only';
    end;
    $$
    """,
    *tuple(
        f"create trigger {table}_append_only before update or delete on product.{table} "
        "for each row execute function product.reject_content_license_history_mutation()"
        for table in (
            "content_license_commands",
            "content_license_record_versions",
            "content_license_authorization_snapshots",
        )
    ),
)
