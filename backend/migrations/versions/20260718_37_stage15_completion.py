"""Complete Stage 1.5 multi-question and AI-answer persistence.

Revision ID: 20260718_37
Revises: 20260717_36
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260718_37"
down_revision: str | Sequence[str] | None = "20260717_36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(_EXPAND_AI_EVALUATION_FAMILY)
    op.execute(
        "alter table learning.content_match_versions "
        "add column question_unit_id text"
    )
    op.execute(
        "update learning.content_match_versions set question_unit_id = "
        "md5(recognition_revision_id::text) || "
        "md5('stage15:' || recognition_revision_id::text) "
        "where question_unit_id is null"
    )
    op.execute(
        "update learning.content_match_versions set contract_payload = "
        "jsonb_set(contract_payload, '{questionUnitId}', to_jsonb(question_unit_id), true) "
        "where not contract_payload ? 'questionUnitId'"
    )
    op.execute(
        "alter table learning.content_match_versions "
        "alter column question_unit_id set not null, "
        "add constraint content_match_question_unit_id_check "
        "check (question_unit_id ~ '^[0-9a-f]{64}$')"
    )
    op.execute(
        "create index learning_content_match_recognition_unit_idx "
        "on learning.content_match_versions(recognition_revision_id, question_unit_id, version_no desc)"
    )
    op.execute(_AI_ANSWER_TABLE)
    op.execute(
        "create unique index learning_external_ai_answer_one_successor_idx "
        "on learning.external_ai_answer_versions(supersedes_answer_version_id) "
        "where supersedes_answer_version_id is not null"
    )
    op.execute(
        "create index learning_external_ai_answer_owner_import_idx "
        "on learning.external_ai_answer_versions(owner_user_id, import_id, created_at desc)"
    )
    op.execute(
        "create function learning.reject_external_ai_answer_mutation() returns trigger "
        "language plpgsql as $$ begin raise exception "
        "'external AI-answer history is append-only'; end $$"
    )
    op.execute(
        "create trigger external_ai_answer_versions_append_only before update or delete "
        "on learning.external_ai_answer_versions for each row execute function "
        "learning.reject_external_ai_answer_mutation()"
    )
    _grant_runtime_access()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("select exists (select 1 from learning.external_ai_answer_versions)")
    ).scalar_one():
        raise RuntimeError(
            "External AI-answer facts exist; destructive downgrade is forbidden."
        )
    if bind.execute(
        sa.text(
            "select exists (select 1 from ai_audit.evaluation_suite_releases "
            "where family = 'external_question_answer')"
        )
    ).scalar_one():
        raise RuntimeError(
            "External question-answer evaluation facts exist; downgrade is forbidden."
        )
    op.execute(
        "drop trigger if exists external_ai_answer_versions_append_only "
        "on learning.external_ai_answer_versions"
    )
    op.execute("drop function learning.reject_external_ai_answer_mutation()")
    op.drop_table("external_ai_answer_versions", schema="learning")
    op.execute("drop index if exists learning.learning_content_match_recognition_unit_idx")
    op.execute(
        "alter table learning.content_match_versions "
        "drop constraint content_match_question_unit_id_check, "
        "drop column question_unit_id"
    )
    op.execute(_RESTORE_AI_EVALUATION_FAMILY)


_EXPAND_AI_EVALUATION_FAMILY = """
alter table ai_audit.evaluation_suite_releases
  drop constraint evaluation_suite_releases_family_check,
  add constraint evaluation_suite_releases_family_check check (family in (
    'xingce_cause', 'essay', 'companion_tone',
    'strategic_deprioritization', 'ocr', 'combined',
    'external_question_answer'
  ))
"""


_RESTORE_AI_EVALUATION_FAMILY = """
alter table ai_audit.evaluation_suite_releases
  drop constraint evaluation_suite_releases_family_check,
  add constraint evaluation_suite_releases_family_check check (family in (
    'xingce_cause', 'essay', 'companion_tone',
    'strategic_deprioritization', 'ocr', 'combined'
  ))
"""


_AI_ANSWER_TABLE = """
create table learning.external_ai_answer_versions (
  answer_version_id uuid primary key,
  answer_id uuid not null,
  import_id uuid not null references learning.upload_sessions(session_id),
  artifact_version_id uuid not null,
  recognition_revision_id uuid not null,
  question_unit_id text not null check (question_unit_id ~ '^[0-9a-f]{64}$'),
  owner_user_id uuid not null references learner.users(user_id),
  version_no bigint not null check (version_no >= 1),
  state text not null check (state in (
    'requested', 'processing', 'candidate', 'confirmed', 'failed'
  )),
  learning_track text not null check (learning_track in ('xingce', 'essay')),
  input_fingerprint text not null check (input_fingerprint ~ '^[0-9a-f]{64}$'),
  output_fingerprint text check (
    output_fingerprint is null or output_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  answer_payload jsonb,
  provider text,
  model text,
  prompt_version text,
  schema_version text,
  reservation_id uuid,
  reservation_source text,
  job_id uuid references platform.background_jobs(job_id),
  model_call_id uuid references ai_audit.model_call_ledger(call_id),
  provider_request_id_fingerprint text check (
    provider_request_id_fingerprint is null
    or provider_request_id_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  failure_code text,
  supersedes_answer_version_id uuid references learning.external_ai_answer_versions(answer_version_id),
  contract_payload jsonb not null check (jsonb_typeof(contract_payload) = 'object'),
  created_at timestamptz not null,
  unique (answer_id, version_no),
  unique (answer_id, owner_user_id, import_id, question_unit_id, version_no),
  check ((version_no = 1) = (supersedes_answer_version_id is null)),
  check ((state in ('candidate', 'confirmed')) = (answer_payload is not null)),
  check ((state in ('candidate', 'confirmed')) = (output_fingerprint is not null)),
  check ((state = 'failed') = (failure_code is not null)),
  foreign key (recognition_revision_id, owner_user_id, artifact_version_id)
    references learning.recognition_revisions(
      recognition_revision_id, owner_user_id, artifact_version_id
    )
)
"""


_TEST_STATEMENTS = (
    _EXPAND_AI_EVALUATION_FAMILY,
    "alter table learning.content_match_versions add column question_unit_id text not null",
    "alter table learning.content_match_versions add constraint content_match_question_unit_id_check check (question_unit_id ~ '^[0-9a-f]{64}$')",
    "create index learning_content_match_recognition_unit_idx on learning.content_match_versions(recognition_revision_id, question_unit_id, version_no desc)",
    _AI_ANSWER_TABLE,
    "create unique index learning_external_ai_answer_one_successor_idx on learning.external_ai_answer_versions(supersedes_answer_version_id) where supersedes_answer_version_id is not null",
    "create index learning_external_ai_answer_owner_import_idx on learning.external_ai_answer_versions(owner_user_id, import_id, created_at desc)",
    "create function learning.reject_external_ai_answer_mutation() returns trigger language plpgsql as $$ begin raise exception 'external AI-answer history is append-only'; end $$",
    "create trigger external_ai_answer_versions_append_only before update or delete on learning.external_ai_answer_versions for each row execute function learning.reject_external_ai_answer_mutation()",
)


def _grant_runtime_access() -> None:
    bind = op.get_bind()
    if not bind.execute(
        sa.text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
    ).scalar_one():
        return
    bind.exec_driver_sql(
        "grant select, insert on learning.external_ai_answer_versions to gongkao_api"
    )
