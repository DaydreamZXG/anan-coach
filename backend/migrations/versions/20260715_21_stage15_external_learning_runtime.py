"""Add the Stage 1.5 external-learning runtime ledger.

Revision ID: 20260715_21
Revises: 20260715_20
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260715_21"
down_revision: str | Sequence[str] | None = "20260715_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)
    _publish_existing_question_versions(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    if _has_runtime_records(bind):
        raise RuntimeError(
            "Stage 1.5 runtime records exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    for table in (
        "external_answer_facts",
        "packet_answer_bindings",
        "external_learning_commands",
        "upload_audit_events",
        "upload_commands",
        "upload_reservation_events",
    ):
        op.execute(f"drop trigger if exists {table}_append_only on learning.{table}")
        op.drop_table(table, schema="learning")
    op.drop_table("upload_sessions", schema="learning")
    op.drop_table("upload_reservations", schema="learning")
    op.drop_table("question_visual_fingerprints", schema="product")
    op.drop_table("question_content_heads", schema="product")
    op.execute(
        "drop trigger if exists question_content_versions_append_only "
        "on product.question_content_versions"
    )
    op.drop_table("question_content_versions", schema="product")
    op.execute("drop function product.reject_question_content_version_mutation()")
    op.execute("drop index if exists product.product_question_stem_trgm_idx")
    op.execute("drop index if exists learning.learning_source_artifact_owner_safe_dedup_idx")
    op.execute(
        "alter table learning.answer_ingestion_versions "
        "drop constraint answer_ingestion_source_lineage_check"
    )
    op.execute(
        "alter table learning.answer_ingestion_versions add constraint "
        "answer_ingestion_source_lineage_check check ("
        "jsonb_typeof(source_artifact_version_ids) = 'array' "
        "and jsonb_array_length(source_artifact_version_ids) > 0)"
    )
    op.execute(
        "alter table learning.content_match_versions drop column evaluation_reason"
    )
    op.execute("drop index if exists learning.learning_recognition_one_successor_idx")
    op.execute(
        "alter table learning.recognition_revisions "
        "drop constraint recognition_revision_lineage_check"
    )
    op.execute(
        "alter table learning.recognition_revisions "
        "drop column supersedes_recognition_revision_id"
    )


def _has_runtime_records(bind: Connection) -> bool:
    learning_query = " union all ".join(
        f"select 1 from learning.{table}"
        for table in (
            "external_answer_facts",
            "packet_answer_bindings",
            "external_learning_commands",
            "upload_audit_events",
            "upload_commands",
            "upload_sessions",
            "upload_reservations",
            "upload_reservation_events",
        )
    )
    query = (
        learning_query
        + " union all select 1 from product.question_visual_fingerprints"
        + " union all select 1 from product.question_content_versions"
    )
    return bool(bind.execute(sa.text(f"select exists ({query})")).scalar_one())


def _publish_existing_question_versions(bind: Connection) -> None:
    from sqlalchemy.orm import Session

    from app.domains.external_ingestion.matching.catalog import (
        SQLQuestionContentCatalog,
    )

    question_ids = tuple(
        str(value)
        for value in bind.execute(
            sa.text(
                "select question_id from product.questions "
                "where status in ('active', 'canonical') order by question_id"
            )
        ).scalars()
    )
    session = Session(bind=bind)
    catalog = SQLQuestionContentCatalog(session)
    for question_id in question_ids:
        catalog.publish_current(question_id=question_id)
    session.flush()


_STATEMENTS = (
    "create extension if not exists pg_trgm",
    """
    create index product_question_stem_trgm_idx
      on product.questions using gin (stem gin_trgm_ops)
    """,
    """
    create function product.reject_question_content_version_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception using
        errcode = '23514',
        message = 'question content versions are append-only';
    end;
    $$
    """,
    """
    create table product.question_content_versions (
      content_version_id text primary key
        check (content_version_id ~ '^[0-9a-f]{64}$'),
      question_id text not null references product.questions(question_id),
      track text not null check (track in ('xingce', 'essay')),
      snapshot_payload jsonb not null
        check (jsonb_typeof(snapshot_payload) = 'object'),
      normalized_search_text text not null
        check (char_length(normalized_search_text) > 0),
      created_at timestamptz not null,
      unique (question_id, content_version_id)
    )
    """,
    """
    create index product_question_content_search_trgm_idx
      on product.question_content_versions
      using gin (normalized_search_text gin_trgm_ops)
    """,
    """
    create table product.question_content_heads (
      question_id text primary key references product.questions(question_id),
      content_version_id text not null unique,
      updated_at timestamptz not null,
      foreign key (question_id, content_version_id)
        references product.question_content_versions(question_id, content_version_id)
    )
    """,
    """
    create table product.question_visual_fingerprints (
      visual_fingerprint_id uuid primary key,
      question_id text not null references product.questions(question_id),
      content_version_id text not null,
      fingerprint_sha256 text not null check (fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
      fingerprint_policy_version text not null,
      source_asset_ref text not null,
      created_at timestamptz not null,
      unique (content_version_id, fingerprint_sha256),
      foreign key (question_id, content_version_id)
        references product.question_content_versions(question_id, content_version_id)
    )
    """,
    """
    create trigger question_content_versions_append_only
      before update or delete on product.question_content_versions
      for each row execute function product.reject_question_content_version_mutation()
    """,
    """
    alter table learning.recognition_revisions
      add column supersedes_recognition_revision_id uuid
        references learning.recognition_revisions(recognition_revision_id)
    """,
    """
    alter table learning.recognition_revisions
      add constraint recognition_revision_lineage_check check (
        (revision_no = 1) = (supersedes_recognition_revision_id is null)
      )
    """,
    """
    create unique index learning_recognition_one_successor_idx
      on learning.recognition_revisions(supersedes_recognition_revision_id)
      where supersedes_recognition_revision_id is not null
    """,
    """
    alter table learning.content_match_versions
      add column evaluation_reason text not null check (evaluation_reason in (
        'essay_incomplete', 'no_eligible_candidate', 'single_exact',
        'single_similar', 'score_tie'
      ))
    """,
    """
    create index learning_source_artifact_owner_safe_dedup_idx
      on learning.source_artifact_versions(
        owner_user_id, source_kind, content_sha256, byte_size, mime_type
      ) where state = 'available'
    """,
    """
    create table learning.upload_sessions (
      session_id uuid primary key,
      artifact_id uuid not null,
      artifact_version_id uuid not null,
      owner_user_id uuid not null references learner.users(user_id),
      revision bigint not null check (revision >= 1),
      state text not null check (state in (
        'awaiting_upload', 'assembling', 'uploaded', 'scanning', 'scan_failed', 'rejecting',
        'quarantining', 'finalizing', 'available', 'deduplicated',
        'quarantined', 'expired', 'failed'
      )),
      source_kind text not null check (source_kind in (
        'camera_image', 'image_import', 'pdf_upload', 'answer_sheet',
        'notes_app_export'
      )),
      original_filename text not null check (char_length(original_filename) between 1 and 255),
      extension text not null check (extension ~ '^\\.[a-z0-9]{1,11}$'),
      mime_type text not null check (char_length(mime_type) between 1 and 200),
      byte_size bigint not null check (byte_size > 0),
      content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
      page_count integer check (page_count >= 1),
      object_ref text not null unique check (char_length(object_ref) between 1 and 500),
      reservation_id uuid not null,
      upload_policy_version text not null check (char_length(upload_policy_version) between 1 and 160),
      upload_mode text not null check (upload_mode in ('single_put', 'multipart')),
      provider_upload_id text,
      part_size bigint,
      part_count integer not null default 0 check (part_count >= 0),
      expected_parts jsonb not null default '[]'::jsonb,
      uploaded_parts jsonb not null default '[]'::jsonb,
      created_at timestamptz not null,
      expires_at timestamptz not null check (expires_at > created_at),
      updated_at timestamptz not null check (updated_at >= created_at),
      resolved_artifact_version_id uuid,
      scan_receipt jsonb check (scan_receipt is null or jsonb_typeof(scan_receipt) = 'object'),
      failure_code text,
      unique (session_id, owner_user_id),
      check (
        jsonb_typeof(expected_parts) = 'array'
        and jsonb_typeof(uploaded_parts) = 'array'
      ),
      check (
        (upload_mode = 'single_put'
          and provider_upload_id is null
          and part_size is null
          and part_count = 0
          and jsonb_array_length(expected_parts) = 0)
        or
        (upload_mode = 'multipart'
          and provider_upload_id is not null
          and part_size >= 5242880
          and part_count >= 1
          and jsonb_array_length(expected_parts) = part_count)
      ),
      check (
        state not in ('assembling', 'uploaded')
        or jsonb_array_length(uploaded_parts) = part_count
      ),
      check (
        (state in ('available', 'deduplicated', 'quarantined'))
        = (resolved_artifact_version_id is not null)
      ),
      check (
        state not in ('finalizing', 'quarantining', 'available', 'deduplicated', 'quarantined')
        or scan_receipt is not null
      ),
      foreign key (resolved_artifact_version_id, owner_user_id)
        references learning.source_artifact_versions(
          artifact_version_id, owner_user_id
        )
    )
    """,
    """
    create index learning_upload_sessions_recovery_idx
      on learning.upload_sessions(state, updated_at, session_id)
      where state in (
        'awaiting_upload', 'assembling', 'uploaded', 'scan_failed', 'scanning', 'rejecting',
        'quarantining', 'finalizing'
      )
    """,
    """
    create unique index learning_upload_sessions_provider_upload_idx
      on learning.upload_sessions(provider_upload_id)
      where provider_upload_id is not null
    """,
    """
    create table learning.upload_reservations (
      reservation_id uuid primary key,
      reservation_key text not null unique
        check (reservation_key ~ '^[0-9a-f]{64}$'),
      owner_user_id uuid not null references learner.users(user_id),
      byte_size bigint not null check (byte_size > 0),
      policy_version text not null check (char_length(policy_version) between 1 and 160),
      state text not null check (state in ('reserved', 'committed', 'released')),
      attempt_no integer not null check (attempt_no >= 1),
      artifact_version_id uuid,
      release_reason text,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      check (
        (state = 'reserved' and artifact_version_id is null and release_reason is null)
        or (state = 'committed' and artifact_version_id is not null and release_reason is null)
        or (state = 'released' and artifact_version_id is null and release_reason is not null)
      ),
      foreign key (artifact_version_id, owner_user_id)
        references learning.source_artifact_versions(
          artifact_version_id, owner_user_id
        ) deferrable initially deferred
    )
    """,
    """
    create index learning_upload_reservations_owner_state_idx
      on learning.upload_reservations(owner_user_id, state)
    """,
    """
    alter table learning.upload_sessions
      add constraint upload_session_reservation_fk foreign key (reservation_id)
        references learning.upload_reservations(reservation_id)
        deferrable initially deferred
    """,
    """
    create table learning.upload_reservation_events (
      event_id uuid primary key,
      deduplication_key text not null unique
        check (deduplication_key ~ '^[0-9a-f]{64}$'),
      reservation_id uuid not null references learning.upload_reservations(reservation_id),
      owner_user_id uuid not null references learner.users(user_id),
      attempt_no integer not null check (attempt_no >= 1),
      event_type text not null check (event_type in ('reserved', 'committed', 'released')),
      byte_size bigint not null check (byte_size > 0),
      reference text not null check (char_length(reference) between 1 and 260),
      created_at timestamptz not null,
      unique (reservation_id, attempt_no, event_type)
    )
    """,
    """
    create table learning.upload_commands (
      owner_user_id uuid not null references learner.users(user_id),
      command_type text not null check (char_length(command_type) between 1 and 160),
      idempotency_key text not null check (char_length(idempotency_key) between 8 and 260),
      request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
      session_id uuid not null,
      created_at timestamptz not null,
      primary key (owner_user_id, command_type, idempotency_key),
      foreign key (session_id, owner_user_id)
        references learning.upload_sessions(session_id, owner_user_id)
    )
    """,
    """
    create table learning.upload_audit_events (
      event_id uuid primary key,
      deduplication_key text not null unique
        check (char_length(deduplication_key) between 1 and 260),
      session_id uuid not null,
      owner_user_id uuid not null,
      event_type text not null check (char_length(event_type) between 1 and 160),
      from_state text check (from_state is null or from_state in (
        'awaiting_upload', 'assembling', 'uploaded', 'scanning', 'scan_failed', 'rejecting',
        'quarantining', 'finalizing', 'available', 'deduplicated',
        'quarantined', 'expired', 'failed'
      )),
      to_state text not null check (to_state in (
        'awaiting_upload', 'assembling', 'uploaded', 'scanning', 'scan_failed', 'rejecting',
        'quarantining', 'finalizing', 'available', 'deduplicated',
        'quarantined', 'expired', 'failed'
      )),
      details jsonb not null check (jsonb_typeof(details) = 'object'),
      created_at timestamptz not null,
      foreign key (session_id, owner_user_id)
        references learning.upload_sessions(session_id, owner_user_id)
    )
    """,
    """
    do $$
    declare constraint_name text;
    begin
      select con.conname into constraint_name
      from pg_constraint con
      join pg_class rel on rel.oid = con.conrelid
      join pg_namespace ns on ns.oid = rel.relnamespace
      where ns.nspname = 'learning'
        and rel.relname = 'answer_ingestion_versions'
        and con.contype = 'c'
        and pg_get_constraintdef(con.oid) like
            '%jsonb_array_length(source_artifact_version_ids) > 0%';
      if constraint_name is not null then
        execute format(
          'alter table learning.answer_ingestion_versions drop constraint %I',
          constraint_name
        );
      end if;
    end $$
    """,
    """
    alter table learning.answer_ingestion_versions
      add constraint answer_ingestion_source_lineage_check check (
        jsonb_typeof(source_artifact_version_ids) = 'array'
        and (
          (input_mode = 'answer_string'
           and packet_version_id is not null
           and jsonb_array_length(source_artifact_version_ids) = 0)
          or
          (input_mode <> 'answer_string'
           and jsonb_array_length(source_artifact_version_ids) > 0)
        )
      )
    """,
    """
    create table learning.external_learning_commands (
      command_id uuid primary key,
      owner_user_id uuid not null references learner.users(user_id),
      command_type text not null check (command_type in (
        'packet_request', 'packet_retry', 'packet_renew',
        'answer_candidate', 'answer_correction', 'answer_confirm'
      )),
      idempotency_key text not null check (char_length(idempotency_key) between 8 and 160),
      request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
      response_payload jsonb not null check (jsonb_typeof(response_payload) = 'object'),
      created_at timestamptz not null,
      unique (owner_user_id, command_type, idempotency_key)
    )
    """,
    """
    create table learning.packet_answer_bindings (
      binding_digest text primary key check (binding_digest ~ '^[0-9a-f]{64}$'),
      packet_id uuid not null,
      packet_version_id uuid not null,
      owner_user_id uuid not null,
      packet_item_id uuid not null unique,
      content_version_id text not null check (content_version_id ~ '^[0-9a-f]{64}$'),
      track text not null check (track in ('xingce', 'essay')),
      order_no integer not null check (order_no >= 1),
      created_at timestamptz not null,
      unique (packet_id, order_no),
      foreign key (packet_version_id, owner_user_id)
        references learning.offline_packet_versions(packet_version_id, owner_user_id)
    )
    """,
    """
    create index learning_packet_answer_binding_owner_packet_idx
      on learning.packet_answer_bindings(owner_user_id, packet_id, track, order_no)
    """,
    """
    create table learning.external_answer_facts (
      fact_id uuid primary key,
      fact_batch_id uuid not null,
      ingestion_version_id uuid not null,
      owner_user_id uuid not null,
      packet_version_id uuid not null,
      packet_item_id uuid not null,
      content_version_id text not null check (content_version_id ~ '^[0-9a-f]{64}$'),
      track text not null check (track in ('xingce', 'essay')),
      answer_payload jsonb not null check (jsonb_typeof(answer_payload) = 'object'),
      source_lineage_refs jsonb not null check (
        jsonb_typeof(source_lineage_refs) = 'array'
        and jsonb_array_length(source_lineage_refs) >= 2
      ),
      target_writer text not null check (
        (track = 'xingce' and target_writer = 'xingce_learning')
        or (track = 'essay' and target_writer = 'essay_learning')
      ),
      evidence_policy_version text not null
        check (evidence_policy_version = 'external-confirmed-lower-weight-v1'),
      review_state text not null check (review_state = 'user_confirmed'),
      created_at timestamptz not null,
      unique (ingestion_version_id, packet_item_id),
      foreign key (ingestion_version_id, owner_user_id)
        references learning.answer_ingestion_versions(
          ingestion_version_id, owner_user_id
        ) deferrable initially deferred,
      foreign key (packet_version_id, owner_user_id)
        references learning.offline_packet_versions(packet_version_id, owner_user_id)
    )
    """,
    """
    create index learning_external_answer_fact_owner_created_idx
      on learning.external_answer_facts(owner_user_id, track, created_at desc)
    """,
    *tuple(
        f"create trigger {table}_append_only before update or delete on learning.{table} "
        "for each row execute function learning.reject_external_learning_fact_mutation()"
        for table in (
            "external_learning_commands",
            "packet_answer_bindings",
            "external_answer_facts",
            "upload_commands",
            "upload_audit_events",
            "upload_reservation_events",
        )
    ),
)
