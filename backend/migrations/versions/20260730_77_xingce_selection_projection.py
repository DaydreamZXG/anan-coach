"""Add indexed distribution and exposure projections for Xingce selection.

Revision ID: 20260730_77
Revises: 20260729_76
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260730_77"
down_revision: str | Sequence[str] | None = "20260729_76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PREPARE_STATEMENTS = (
    """
    create table if not exists learning.xingce_question_exposures (
      user_id uuid not null,
      session_id uuid not null,
      question_id text not null check (char_length(btrim(question_id)) > 0),
      exposed_at timestamptz not null,
      created_at timestamptz not null default transaction_timestamp(),
      primary key (user_id, session_id, question_id),
      constraint xingce_question_exposures_session_fk
        foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id)
        on delete cascade
    )
    """,
    """
    create or replace function learning.sync_xingce_question_exposures(
      p_user_id uuid,
      p_session_id uuid,
      p_safe_bundle jsonb,
      p_exposed_at timestamptz
    )
    returns integer
    language plpgsql
    security definer
    set search_path = pg_catalog, learning
    as $$
    declare
      inserted_count integer;
    begin
      delete from learning.xingce_question_exposures exposure
      where exposure.user_id = p_user_id
        and exposure.session_id = p_session_id;

      insert into learning.xingce_question_exposures (
        user_id,
        session_id,
        question_id,
        exposed_at
      )
      select
        p_user_id,
        p_session_id,
        btrim(question ->> 'questionId'),
        p_exposed_at
      from jsonb_array_elements(
        case
          when jsonb_typeof(p_safe_bundle -> 'questions') = 'array'
            then p_safe_bundle -> 'questions'
          else '[]'::jsonb
        end
      ) question
      where jsonb_typeof(question) = 'object'
        and jsonb_typeof(question -> 'questionId') = 'string'
        and char_length(btrim(question ->> 'questionId')) > 0
      on conflict (user_id, session_id, question_id) do nothing;

      get diagnostics inserted_count = row_count;
      return inserted_count;
    end
    $$
    """,
    """
    revoke all on function learning.sync_xingce_question_exposures(
      uuid, uuid, jsonb, timestamptz
    ) from public
    """,
    """
    create or replace function learning.capture_xingce_question_exposures()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, learning
    as $$
    begin
      perform learning.sync_xingce_question_exposures(
        new.user_id,
        new.session_id,
        new.safe_bundle,
        new.created_at
      );
      return new;
    end
    $$
    """,
    """
    revoke all on function learning.capture_xingce_question_exposures()
      from public
    """,
)


_CONCURRENT_INDEX_STATEMENTS = (
    """
    create index concurrently
      product_knowledge_map_binding_release_selection_idx
      on product.knowledge_map_question_bindings (
        release_id, (digest(question_id, 'sha256')), question_id
      )
      include (
        content_version_id,
        canonical_module,
        visual_required,
        shared_context_required,
        latest_year,
        occurrence_count
      )
      where delivery_eligible
    """,
    """
    create index concurrently
      product_knowledge_map_binding_node_selection_idx
      on product.knowledge_map_question_bindings (
        release_id, node_id, (digest(question_id, 'sha256')), question_id
      )
      include (
        content_version_id,
        canonical_module,
        visual_required,
        shared_context_required,
        latest_year,
        occurrence_count
      )
      where delivery_eligible
    """,
    """
    create index concurrently
      product_knowledge_map_binding_module_selection_idx_v2
      on product.knowledge_map_question_bindings (
        release_id, canonical_module,
        (digest(question_id, 'sha256')), question_id
      )
      include (
        content_version_id,
        visual_required,
        shared_context_required,
        latest_year,
        occurrence_count
      )
      where delivery_eligible
    """,
    """
    create index concurrently
      learning_xingce_assessments_user_question_idx
      on learning.xingce_assessments (user_id, question_id)
    """,
    """
    create index concurrently
      learning_xingce_exposures_user_question_idx
      on learning.xingce_question_exposures (user_id, question_id)
      include (session_id, exposed_at)
    """,
)

_CONCURRENT_INDEX_NAMES = (
    ("product", "product_knowledge_map_binding_release_selection_idx"),
    ("product", "product_knowledge_map_binding_node_selection_idx"),
    ("product", "product_knowledge_map_binding_module_selection_idx_v2"),
    ("learning", "learning_xingce_assessments_user_question_idx"),
    ("learning", "learning_xingce_exposures_user_question_idx"),
)

_INDEX_STATE_SQL = text(
    """
    select index_metadata.indisvalid, index_metadata.indisready
    from pg_catalog.pg_class index_relation
    join pg_catalog.pg_namespace namespace
      on namespace.oid = index_relation.relnamespace
    join pg_catalog.pg_index index_metadata
      on index_metadata.indexrelid = index_relation.oid
    where namespace.nspname = :schema_name
      and index_relation.relname = :index_name
    """
)


_TRIGGER_STATEMENTS = (
    """
    drop trigger if exists xingce_task_session_capture_question_exposures
      on learning.xingce_task_sessions
    """,
    """
    create trigger xingce_task_session_capture_question_exposures
      after insert or update of safe_bundle
      on learning.xingce_task_sessions
      for each row execute function learning.capture_xingce_question_exposures()
    """,
)


_BACKFILL_BATCH_SIZE = 500
_BACKFILL_BATCH_SQL = text(
    """
    with locked_sessions as materialized (
      select
        task_session.user_id,
        task_session.session_id,
        task_session.safe_bundle,
        task_session.created_at
      from learning.xingce_task_sessions task_session
      where (
        cast(:cursor as uuid) is null
        or task_session.session_id > cast(:cursor as uuid)
      )
      order by task_session.session_id
      limit :batch_size
      for update
    ),
    synced_sessions as materialized (
      select
        session_id,
        learning.sync_xingce_question_exposures(
          user_id,
          session_id,
          safe_bundle,
          created_at
        ) as exposure_count
      from locked_sessions
    )
    select
      (
        select session_id::text
        from locked_sessions
        order by session_id desc
        limit 1
      ) as next_cursor,
      (select count(*)::integer from locked_sessions) as session_count,
      coalesce(
        (select sum(exposure_count) from synced_sessions),
        0
      )::integer as exposure_count
    """
)


_CONCURRENT_DOWN_STATEMENTS = (
    """
    drop index concurrently if exists
      product.product_knowledge_map_binding_release_selection_idx
    """,
    """
    drop index concurrently if exists
      product.product_knowledge_map_binding_node_selection_idx
    """,
    """
    drop index concurrently if exists
      product.product_knowledge_map_binding_module_selection_idx_v2
    """,
    """
    drop index concurrently if exists
      learning.learning_xingce_assessments_user_question_idx
    """,
    """
    drop index concurrently if exists
      learning.learning_xingce_exposures_user_question_idx
    """,
)


_DOWN_STATEMENTS = (
    """
    drop trigger if exists xingce_task_session_capture_question_exposures
      on learning.xingce_task_sessions
    """,
    """
    drop function if exists learning.capture_xingce_question_exposures()
    """,
    """
    drop function if exists learning.sync_xingce_question_exposures(
      uuid, uuid, jsonb, timestamptz
    )
    """,
    """
    drop table if exists learning.xingce_question_exposures
    """,
)


def _grant_runtime_access() -> None:
    bind = op.get_bind()
    role_exists = bind.execute(
        text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
    ).scalar_one()
    if not role_exists:
        return
    bind.exec_driver_sql(
        "revoke insert, update, delete, truncate, references, trigger "
        "on learning.xingce_question_exposures from gongkao_api"
    )
    bind.exec_driver_sql("grant select on learning.xingce_question_exposures to gongkao_api")


def _ensure_concurrent_indexes() -> None:
    bind = op.get_bind()
    for (schema_name, index_name), statement in zip(
        _CONCURRENT_INDEX_NAMES,
        _CONCURRENT_INDEX_STATEMENTS,
        strict=True,
    ):
        index_state = (
            bind.execute(
                _INDEX_STATE_SQL,
                {"schema_name": schema_name, "index_name": index_name},
            )
            .mappings()
            .one_or_none()
        )
        if index_state is not None:
            if bool(index_state["indisvalid"]) and bool(index_state["indisready"]):
                continue
            op.execute(f'drop index concurrently if exists "{schema_name}"."{index_name}"')
        op.execute(statement)


def upgrade() -> None:
    for statement in _PREPARE_STATEMENTS:
        op.execute(statement)

    context = op.get_context()
    with context.autocommit_block():
        _ensure_concurrent_indexes()

    for statement in _TRIGGER_STATEMENTS:
        op.execute(statement)

    # Entering the autocommit block commits the short trigger DDL transaction.
    # Every following statement locks and reconciles at most one bounded batch,
    # so concurrent task-session updates either precede the snapshot or wait
    # briefly and then fire the installed trigger with the newer bundle.
    cursor: str | None = None
    with context.autocommit_block():
        while True:
            row = (
                op.get_bind()
                .execute(
                    _BACKFILL_BATCH_SQL,
                    {"cursor": cursor, "batch_size": _BACKFILL_BATCH_SIZE},
                )
                .mappings()
                .one()
            )
            if int(row["session_count"]) == 0:
                break
            cursor = str(row["next_cursor"])

    _grant_runtime_access()


def downgrade() -> None:
    context = op.get_context()
    with context.autocommit_block():
        for statement in _CONCURRENT_DOWN_STATEMENTS:
            op.execute(statement)
    for statement in _DOWN_STATEMENTS:
        op.execute(statement)
