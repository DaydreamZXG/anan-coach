"""Add math ink recognition and owner-private knowledge version chains.

Revision ID: 20260719_44
Revises: 20260719_43
"""

from alembic import op


revision = "20260719_44"
down_revision = "20260719_43"
branch_labels = None
depends_on = None


_TEST_STATEMENTS = (
    """
    create table learning.math_ink_commands (
      request_id uuid primary key,
      owner_user_id uuid not null references learner.users(user_id),
      session_id uuid not null,
      question_id text not null check (char_length(question_id) between 1 and 240),
      idempotency_key text not null check (char_length(idempotency_key) between 8 and 160),
      input_fingerprint text not null check (input_fingerprint ~ '^[0-9a-f]{64}$'),
      ink_payload jsonb not null check (
        jsonb_typeof(ink_payload) = 'object'
        and jsonb_typeof(ink_payload->'strokes') = 'array'
      ),
      provider_release text not null,
      provider_endpoint_fingerprint text not null check (
        provider_endpoint_fingerprint ~ '^[0-9a-f]{64}$'
      ),
      state text not null check (state in ('candidate', 'failed')),
      output_fingerprint text check (
        output_fingerprint is null or output_fingerprint ~ '^[0-9a-f]{64}$'
      ),
      failure_code text,
      latency_ms integer not null check (latency_ms >= 0),
      created_at timestamptz not null,
      completed_at timestamptz not null,
      unique (owner_user_id, idempotency_key),
      unique (request_id, owner_user_id),
      foreign key (session_id, owner_user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      check (
        (state = 'candidate' and output_fingerprint is not null and failure_code is null)
        or (state = 'failed' and output_fingerprint is null
          and char_length(failure_code) > 0)
      )
    )
    """,
    """
    create index math_ink_commands_owner_created_idx
      on learning.math_ink_commands(owner_user_id, created_at desc)
    """,
    """
    create table learning.math_formula_versions (
      formula_version_id uuid primary key,
      formula_id uuid not null,
      request_id uuid not null,
      owner_user_id uuid not null,
      session_id uuid not null,
      question_id text not null,
      version_no bigint not null check (version_no >= 1),
      state text not null check (state in ('candidate', 'confirmed', 'rejected')),
      latex text not null check (char_length(latex) between 1 and 4000),
      source text not null check (source in ('myscript_candidate', 'user_confirmed')),
      supersedes_formula_version_id uuid,
      created_at timestamptz not null,
      confirmed_at timestamptz,
      unique (formula_id, version_no),
      unique (formula_version_id, owner_user_id),
      foreign key (request_id, owner_user_id)
        references learning.math_ink_commands(request_id, owner_user_id),
      foreign key (session_id, owner_user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (supersedes_formula_version_id)
        references learning.math_formula_versions(formula_version_id),
      check ((version_no = 1) = (supersedes_formula_version_id is null)),
      check ((state = 'confirmed') = (confirmed_at is not null)),
      check ((version_no = 1 and state = 'candidate' and source = 'myscript_candidate')
        or (version_no > 1 and source = 'user_confirmed'))
    )
    """,
    """
    create unique index math_formula_one_successor_idx
      on learning.math_formula_versions(supersedes_formula_version_id)
      where supersedes_formula_version_id is not null
    """,
    """
    create trigger math_formula_versions_append_only
      before update or delete on learning.math_formula_versions
      for each row execute function learning.reject_external_learning_fact_mutation()
    """,
    """
    create table learning.private_knowledge_commands (
      command_id uuid primary key,
      owner_user_id uuid not null references learner.users(user_id),
      command_type text not null check (command_type in ('source_create', 'source_import')),
      idempotency_key text not null check (char_length(idempotency_key) between 8 and 160),
      request_fingerprint text not null check (request_fingerprint ~ '^[0-9a-f]{64}$'),
      source_version_id uuid not null,
      created_at timestamptz not null,
      unique (owner_user_id, command_type, idempotency_key)
    )
    """,
    """
    create table learning.private_knowledge_source_versions (
      source_version_id uuid primary key,
      source_id uuid not null,
      owner_user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      title text not null check (char_length(title) between 1 and 200),
      document_kind text not null check (document_kind in ('lecture', 'note', 'summary')),
      artifact_version_id uuid not null,
      recognition_revision_id uuid,
      canonical_markdown text not null check (char_length(canonical_markdown) between 1 and 2000000),
      content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
      supersedes_source_version_id uuid,
      created_at timestamptz not null,
      unique (source_id, version_no),
      unique (source_version_id, owner_user_id),
      unique (artifact_version_id),
      foreign key (artifact_version_id, owner_user_id)
        references learning.source_artifact_versions(artifact_version_id, owner_user_id),
      foreign key (recognition_revision_id, owner_user_id, artifact_version_id)
        references learning.recognition_revisions(
          recognition_revision_id, owner_user_id, artifact_version_id
        ),
      foreign key (supersedes_source_version_id)
        references learning.private_knowledge_source_versions(source_version_id),
      check ((version_no = 1) = (supersedes_source_version_id is null))
    )
    """,
    """
    alter table learning.private_knowledge_commands
      add constraint private_knowledge_command_source_fk
      foreign key (source_version_id, owner_user_id)
      references learning.private_knowledge_source_versions(source_version_id, owner_user_id)
      on delete cascade
    """,
    """
    create unique index private_knowledge_one_successor_idx
      on learning.private_knowledge_source_versions(supersedes_source_version_id)
      where supersedes_source_version_id is not null
    """,
    """
    create index private_knowledge_owner_created_idx
      on learning.private_knowledge_source_versions(owner_user_id, created_at desc)
    """,
    """
    create table learning.private_knowledge_page_versions (
      page_version_id uuid primary key,
      source_version_id uuid not null,
      owner_user_id uuid not null,
      page_no integer not null check (page_no >= 1),
      heading_path jsonb not null check (jsonb_typeof(heading_path) = 'array'),
      content text not null check (char_length(content) between 1 and 500000),
      content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
      created_at timestamptz not null,
      unique (source_version_id, page_no),
      unique (page_version_id, owner_user_id),
      foreign key (source_version_id, owner_user_id)
        references learning.private_knowledge_source_versions(source_version_id, owner_user_id)
        on delete cascade
    )
    """,
    """
    create table learning.private_knowledge_chunks (
      chunk_id uuid primary key,
      page_version_id uuid not null,
      source_version_id uuid not null,
      owner_user_id uuid not null,
      ordinal integer not null check (ordinal >= 1),
      anchor text not null check (char_length(anchor) between 1 and 300),
      content text not null check (char_length(content) between 1 and 8000),
      content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
      created_at timestamptz not null,
      unique (page_version_id, ordinal),
      unique (chunk_id, owner_user_id),
      foreign key (page_version_id, owner_user_id)
        references learning.private_knowledge_page_versions(page_version_id, owner_user_id)
        on delete cascade,
      foreign key (source_version_id, owner_user_id)
        references learning.private_knowledge_source_versions(source_version_id, owner_user_id)
        on delete cascade
    )
    """,
    """
    create table learning.private_knowledge_search_index (
      index_entry_id uuid primary key,
      chunk_id uuid not null,
      source_version_id uuid not null,
      owner_user_id uuid not null,
      policy_version text not null,
      lexical_terms text[] not null check (cardinality(lexical_terms) > 0),
      feature_vector real[] not null check (cardinality(feature_vector) = 64),
      created_at timestamptz not null,
      unique (chunk_id, policy_version),
      foreign key (chunk_id, owner_user_id)
        references learning.private_knowledge_chunks(chunk_id, owner_user_id)
        on delete cascade,
      foreign key (source_version_id, owner_user_id)
        references learning.private_knowledge_source_versions(source_version_id, owner_user_id)
        on delete cascade
    )
    """,
    """
    create index private_knowledge_search_terms_idx
      on learning.private_knowledge_search_index using gin(lexical_terms)
    """,
    """
    create index private_knowledge_search_owner_idx
      on learning.private_knowledge_search_index(owner_user_id, source_version_id)
    """,
    """
    create trigger private_knowledge_source_versions_append_only
      before update or delete on learning.private_knowledge_source_versions
      for each row execute function learning.reject_external_learning_fact_mutation()
    """,
    """
    create table privacy.private_material_deletion_requests (
      deletion_request_id uuid primary key,
      owner_user_id uuid not null references learner.users(user_id),
      source_id uuid not null,
      idempotency_key text not null check (char_length(idempotency_key) between 8 and 160),
      state text not null check (state in ('queued', 'running', 'completed', 'failed')),
      source_version_count integer not null check (source_version_count > 0),
      object_refs jsonb not null check (
        jsonb_typeof(object_refs) = 'array' and jsonb_array_length(object_refs) > 0
      ),
      evidence jsonb,
      failure_code text,
      requested_at timestamptz not null,
      completed_at timestamptz,
      unique (owner_user_id, idempotency_key),
      unique (deletion_request_id, owner_user_id),
      check ((state = 'completed') = (completed_at is not null)),
      check ((state = 'failed') = (failure_code is not null))
    )
    """,
    """
    create table privacy.private_material_deletion_events (
      event_id uuid primary key,
      deletion_request_id uuid not null,
      owner_user_id uuid not null,
      phase text not null check (phase in ('requested', 'objects_deleted', 'derived_deleted', 'completed', 'failed')),
      evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
      created_at timestamptz not null,
      foreign key (deletion_request_id, owner_user_id)
        references privacy.private_material_deletion_requests(deletion_request_id, owner_user_id)
    )
    """,
    """
    create trigger private_material_deletion_events_append_only
      before update or delete on privacy.private_material_deletion_events
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create or replace function privacy.private_material_erasure_authorized(row_data jsonb)
    returns boolean
    language sql
    stable
    security definer
    set search_path = pg_catalog, privacy
    as $$
      select coalesce(current_setting('app.private_material_deletion_id', true), '') <> ''
        and coalesce(row_data->>'owner_user_id', row_data->>'user_id') =
          current_setting('app.private_material_owner_user_id', true)
        and exists (
          select 1 from privacy.private_material_deletion_requests request
          where request.deletion_request_id::text =
                current_setting('app.private_material_deletion_id', true)
            and request.owner_user_id::text =
                current_setting('app.private_material_owner_user_id', true)
            and request.state = 'running'
        )
    $$
    """,
    """
    create or replace function learning.reject_external_learning_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old))
        or privacy.private_material_erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      raise exception using errcode = '23514',
        message = 'external-learning facts are append-only';
    end;
    $$
    """,
)


def upgrade() -> None:
    for statement in _TEST_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        """
        do $$ begin
          if exists (select 1 from learning.math_ink_commands)
            or exists (select 1 from learning.private_knowledge_source_versions)
            or exists (select 1 from privacy.private_material_deletion_requests) then
            raise exception 'step 6 records exist; use a forward repair migration';
          end if;
        end $$
        """
    )
    op.execute(
        """
        create or replace function learning.reject_external_learning_fact_mutation()
        returns trigger language plpgsql as $$
        begin
          if privacy.erasure_authorized(to_jsonb(old)) then
            if tg_op = 'DELETE' then return old; end if;
            return new;
          end if;
          raise exception using errcode = '23514',
            message = 'external-learning facts are append-only';
        end;
        $$
        """
    )
    op.execute("drop function privacy.private_material_erasure_authorized(jsonb)")
    for table in (
        "private_material_deletion_events",
        "private_material_deletion_requests",
    ):
        op.execute(f"drop table privacy.{table}")
    for table in (
        "private_knowledge_search_index",
        "private_knowledge_chunks",
        "private_knowledge_page_versions",
        "private_knowledge_commands",
        "private_knowledge_source_versions",
        "math_formula_versions",
        "math_ink_commands",
    ):
        op.execute(f"drop table learning.{table} cascade")
