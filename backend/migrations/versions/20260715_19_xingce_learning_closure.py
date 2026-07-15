"""Add the canonical Xingce attempt, reflection and planning-evidence writer.

Revision ID: 20260715_19
Revises: 20260713_18
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260715_19"
down_revision: str | Sequence[str] | None = "20260713_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_xingce_learning_records(bind):
        raise RuntimeError(
            "Xingce learning records exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    for table in _APPEND_ONLY_TABLES:
        op.execute(f"drop trigger if exists {table}_append_only on learning.{table}")
    op.execute("drop function if exists learning.reject_xingce_fact_mutation()")
    for table in (
        "xingce_attempt_review_versions",
        "plan_task_late_evidence_versions",
        "xingce_plan_evidence_versions",
        "xingce_cause_decision_versions",
        "xingce_cause_candidate_versions",
        "xingce_reflection_versions",
        "xingce_assessments",
        "xingce_attempt_records",
        "xingce_answer_versions",
        "xingce_task_sessions",
        "xingce_commands",
    ):
        op.drop_table(table, schema="learning")


def _has_xingce_learning_records(bind: Connection) -> bool:
    query = " union all ".join(
        f"select 1 from learning.{table}" for table in _XINGCE_TABLES
    )
    return bool(bind.execute(sa.text(f"select exists ({query})")).scalar_one())


_XINGCE_TABLES = (
    "xingce_commands",
    "xingce_task_sessions",
    "xingce_answer_versions",
    "xingce_attempt_records",
    "xingce_assessments",
    "xingce_attempt_review_versions",
    "xingce_reflection_versions",
    "xingce_cause_candidate_versions",
    "xingce_cause_decision_versions",
    "xingce_plan_evidence_versions",
    "plan_task_late_evidence_versions",
)

_APPEND_ONLY_TABLES = tuple(
    table for table in _XINGCE_TABLES if table != "xingce_task_sessions"
)


_STATEMENTS = (
    """
    create table learning.xingce_commands (
      user_id uuid not null references learner.users(user_id),
      command_id text not null,
      command_type text not null check (command_type in (
        'session_start', 'checkpoint_save', 'attempt_submit',
        'reflection_submit', 'cause_decision'
      )),
      target_id uuid,
      request_fingerprint text not null check (length(request_fingerprint) = 64),
      response_payload jsonb not null check (jsonb_typeof(response_payload) = 'object'),
      created_at timestamptz not null,
      primary key (user_id, command_id),
      check (char_length(command_id) between 8 and 260)
    )
    """,
    """
    create index learning_xingce_commands_user_created_idx
      on learning.xingce_commands(user_id, created_at)
    """,
    """
    create table learning.xingce_task_sessions (
      session_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      source_plan_task_id uuid not null references learning.daily_plan_tasks(task_id),
      source_plan_version_id uuid not null references learning.daily_plan_versions(plan_version_id),
      local_date date not null,
      work_item_key text not null check (length(work_item_key) = 64),
      mode text not null check (mode in ('baseline', 'targeted')),
      state text not null check (
        state in ('active', 'submitted', 'settled')
      ),
      attempt_group_id uuid unique,
      revision bigint not null check (revision >= 1),
      bundle_version_id text not null check (length(bundle_version_id) = 64),
      safe_bundle jsonb not null check (
        jsonb_typeof(safe_bundle) = 'object'
        and not (safe_bundle::text ~* 'correct(answer|option)|explanation')
      ),
      scoring_bundle jsonb not null check (jsonb_typeof(scoring_bundle) = 'object'),
      current_question_id text not null,
      checkpoint jsonb not null check (
        jsonb_typeof(checkpoint) = 'object'
        and jsonb_typeof(checkpoint->'answers') = 'array'
      ),
      total_elapsed_seconds integer not null check (total_elapsed_seconds between 0 and 86400),
      created_at timestamptz not null,
      updated_at timestamptz not null,
      submitted_at timestamptz,
      completed_at timestamptz,
      unique (session_id, user_id),
      unique (user_id, work_item_key),
      check (submitted_at is null or state <> 'active'),
      check ((state = 'active') = (attempt_group_id is null)),
      check ((state = 'settled') = (completed_at is not null))
    )
    """,
    """
    create index learning_xingce_sessions_owner_state_idx
      on learning.xingce_task_sessions(user_id, state, updated_at)
    """,
    """
    create table learning.xingce_answer_versions (
      answer_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      session_id uuid not null,
      question_id text not null references product.questions(question_id),
      version_no bigint not null check (version_no >= 1),
      payload jsonb not null check (
        jsonb_typeof(payload) = 'object'
        and payload->>'questionId' = question_id
      ),
      payload_sha256 text not null check (length(payload_sha256) = 64),
      supersedes_answer_version_id uuid,
      created_at timestamptz not null,
      unique (answer_version_id, user_id, session_id, question_id),
      unique (session_id, question_id, version_no),
      check (
        (version_no = 1 and supersedes_answer_version_id is null)
        or (version_no > 1 and supersedes_answer_version_id is not null)
      ),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_answer_version_id, user_id, session_id, question_id
      ) references learning.xingce_answer_versions(
        answer_version_id, user_id, session_id, question_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    create unique index learning_xingce_answer_one_successor_idx
      on learning.xingce_answer_versions(supersedes_answer_version_id)
      where supersedes_answer_version_id is not null
    """,
    """
    create table learning.xingce_attempt_records (
      attempt_record_id uuid primary key,
      attempt_group_id uuid not null,
      item_attempt_id text not null check (length(item_attempt_id) = 64),
      submit_command_id text not null,
      user_id uuid not null,
      session_id uuid not null,
      question_id text not null references product.questions(question_id),
      session_revision bigint not null check (session_revision >= 2),
      answer_version_id uuid not null,
      content_version_id text not null check (length(content_version_id) = 64),
      scoring_policy_version text not null,
      timing_evidence_ref text not null check (length(timing_evidence_ref) = 64),
      timing_evidence jsonb not null check (jsonb_typeof(timing_evidence) = 'object'),
      submission_hash text not null check (length(submission_hash) = 64),
      source_lineage_refs jsonb not null check (
        jsonb_typeof(source_lineage_refs) = 'array'
        and jsonb_array_length(source_lineage_refs) >= 3
      ),
      created_at timestamptz not null,
      unique (user_id, item_attempt_id),
      unique (session_id, question_id),
      unique (attempt_record_id, user_id, session_id, question_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (answer_version_id, user_id, session_id, question_id)
        references learning.xingce_answer_versions(
          answer_version_id, user_id, session_id, question_id
        ),
      foreign key (user_id, submit_command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    create index learning_xingce_attempt_records_owner_created_idx
      on learning.xingce_attempt_records(user_id, created_at)
    """,
    """
    create table learning.xingce_assessments (
      assessment_id uuid primary key,
      attempt_record_id uuid not null,
      submit_command_id text not null,
      user_id uuid not null,
      session_id uuid not null,
      question_id text not null references product.questions(question_id),
      answer_version_id uuid not null,
      content_version_id text not null check (length(content_version_id) = 64),
      scoring_policy_version text not null,
      assessment_key_hash text not null unique check (length(assessment_key_hash) = 64),
      result_code text not null check (
        result_code in ('correct', 'incorrect', 'unanswered', 'timeout', 'guessed_correct')
      ),
      correct boolean not null,
      result_snapshot jsonb not null check (jsonb_typeof(result_snapshot) = 'object'),
      created_at timestamptz not null,
      unique (session_id, question_id),
      check (correct = (result_code in ('correct', 'guessed_correct'))),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (answer_version_id, user_id, session_id, question_id)
        references learning.xingce_answer_versions(
          answer_version_id, user_id, session_id, question_id
        ),
      foreign key (attempt_record_id, user_id, session_id, question_id)
        references learning.xingce_attempt_records(
          attempt_record_id, user_id, session_id, question_id
        ),
      foreign key (user_id, submit_command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    create table learning.xingce_attempt_review_versions (
      review_version_id uuid primary key,
      command_id text not null,
      review_id uuid not null,
      user_id uuid not null,
      session_id uuid not null,
      revision bigint not null check (revision >= 1),
      state text not null check (
        state in (
          'reflection_due', 'cause_candidate_pending',
          'cause_decision_due', 'immediate_verification_due'
        )
      ),
      requires_cause_decision boolean not null,
      reflection_version_id uuid,
      cause_candidate_version_id uuid,
      cause_decision_version_id uuid,
      supersedes_review_version_id uuid,
      created_at timestamptz not null,
      unique (review_id, revision),
      unique (review_version_id, review_id, user_id, session_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_review_version_id, review_id, user_id, session_id
      ) references learning.xingce_attempt_review_versions(
        review_version_id, review_id, user_id, session_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred,
      check (
        (revision = 1 and supersedes_review_version_id is null)
        or (revision > 1 and supersedes_review_version_id is not null)
      ),
      check (
        (state = 'reflection_due'
          and reflection_version_id is null
          and cause_candidate_version_id is null
          and cause_decision_version_id is null)
        or (state = 'cause_candidate_pending'
          and reflection_version_id is not null
          and cause_candidate_version_id is null
          and cause_decision_version_id is null)
        or (state = 'cause_decision_due'
          and reflection_version_id is not null
          and cause_candidate_version_id is not null
          and cause_decision_version_id is null)
        or (state = 'immediate_verification_due'
          and reflection_version_id is not null
          and cause_candidate_version_id is not null
          and cause_decision_version_id is not null)
      )
    )
    """,
    """
    create unique index learning_xingce_review_one_successor_idx
      on learning.xingce_attempt_review_versions(supersedes_review_version_id)
      where supersedes_review_version_id is not null
    """,
    """
    create table learning.xingce_reflection_versions (
      reflection_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      payload jsonb not null check (
        jsonb_typeof(payload) = 'object'
        and length(trim(payload->>'rawText')) > 0
        and jsonb_typeof(payload->'tags') = 'array'
        and jsonb_array_length(payload->'tags') > 0
      ),
      payload_sha256 text not null check (length(payload_sha256) = 64),
      supersedes_reflection_version_id uuid,
      created_at timestamptz not null,
      unique (review_id, version_no),
      unique (user_id, command_id),
      unique (reflection_version_id, user_id, review_id, session_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_reflection_version_id, user_id, review_id, session_id
      ) references learning.xingce_reflection_versions(
        reflection_version_id, user_id, review_id, session_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    create table learning.xingce_cause_decision_versions (
      decision_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      decision text not null check (decision in ('replace', 'skip')),
      replacement_cause text,
      supersedes_decision_version_id uuid,
      created_at timestamptz not null,
      unique (review_id, version_no),
      unique (user_id, command_id),
      unique (decision_version_id, user_id, review_id, session_id),
      check (
        (decision = 'replace' and length(trim(replacement_cause)) > 0)
        or (decision = 'skip' and replacement_cause is null)
      ),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_decision_version_id, user_id, review_id, session_id
      ) references learning.xingce_cause_decision_versions(
        decision_version_id, user_id, review_id, session_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    create table learning.xingce_cause_candidate_versions (
      candidate_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      state text not null check (state = 'unavailable'),
      reason_code text not null check (reason_code = 'scenario_not_activated'),
      charged boolean not null check (charged = false),
      input_version_refs jsonb not null check (
        jsonb_typeof(input_version_refs) = 'array'
        and jsonb_array_length(input_version_refs) > 0
      ),
      created_at timestamptz not null,
      unique (review_id, version_no),
      unique (candidate_version_id, user_id, review_id, session_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    alter table learning.xingce_attempt_review_versions
      add constraint xingce_review_reflection_fk
      foreign key (reflection_version_id, user_id, review_id, session_id)
      references learning.xingce_reflection_versions(
        reflection_version_id, user_id, review_id, session_id
      )
    """,
    """
    alter table learning.xingce_attempt_review_versions
      add constraint xingce_review_decision_fk
      foreign key (cause_decision_version_id, user_id, review_id, session_id)
      references learning.xingce_cause_decision_versions(
        decision_version_id, user_id, review_id, session_id
      )
    """,
    """
    alter table learning.xingce_attempt_review_versions
      add constraint xingce_review_candidate_fk
      foreign key (cause_candidate_version_id, user_id, review_id, session_id)
      references learning.xingce_cause_candidate_versions(
        candidate_version_id, user_id, review_id, session_id
      )
    """,
    """
    create table learning.xingce_plan_evidence_versions (
      evidence_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      session_id uuid not null,
      work_item_key text not null check (length(work_item_key) = 64),
      version_no bigint not null check (version_no >= 1),
      state text not null check (state in ('pending_reflection', 'settled')),
      assessment_ids jsonb not null check (
        jsonb_typeof(assessment_ids) = 'array' and jsonb_array_length(assessment_ids) > 0
      ),
      execution_version_id uuid references learning.task_execution_versions(execution_version_id),
      supersedes_evidence_version_id uuid,
      created_at timestamptz not null,
      unique (session_id, version_no),
      unique (user_id, command_id),
      unique (evidence_version_id, user_id, session_id, work_item_key),
      check (
        (state = 'pending_reflection' and execution_version_id is null)
        or (state = 'settled' and execution_version_id is not null)
      ),
      check (
        (version_no = 1 and supersedes_evidence_version_id is null)
        or (version_no > 1 and supersedes_evidence_version_id is not null)
      ),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        supersedes_evidence_version_id, user_id, session_id, work_item_key
      ) references learning.xingce_plan_evidence_versions(
        evidence_version_id, user_id, session_id, work_item_key
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    create unique index learning_xingce_evidence_one_successor_idx
      on learning.xingce_plan_evidence_versions(supersedes_evidence_version_id)
      where supersedes_evidence_version_id is not null
    """,
    """
    create table learning.plan_task_late_evidence_versions (
      late_evidence_id uuid primary key,
      command_id text not null,
      user_id uuid not null references learner.users(user_id),
      session_id uuid not null,
      work_item_key text not null check (length(work_item_key) = 64),
      source_execution_version_id uuid not null
        references learning.task_execution_versions(execution_version_id),
      reason_code text not null,
      input_version_refs jsonb not null check (
        jsonb_typeof(input_version_refs) = 'array'
        and jsonb_array_length(input_version_refs) > 0
      ),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) > 0
      ),
      created_at timestamptz not null,
      unique (user_id, command_id),
      unique (session_id, source_execution_version_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id)
    )
    """,
    """
    create function learning.reject_xingce_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception using
        errcode = '23514',
        message = 'xingce learning facts are append-only';
    end;
    $$
    """,
    *(
        f"create trigger {table}_append_only before update or delete "
        f"on learning.{table} for each row execute function "
        "learning.reject_xingce_fact_mutation()"
        for table in _APPEND_ONLY_TABLES
    ),
)
