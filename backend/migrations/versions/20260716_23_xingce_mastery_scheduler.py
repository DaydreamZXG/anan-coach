"""Add the canonical Xingce mastery and verification scheduler.

Revision ID: 20260716_23
Revises: 20260715_22
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_23"
down_revision: str | Sequence[str] | None = "20260715_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_mastery_records(bind):
        raise RuntimeError(
            "Xingce mastery records exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    op.execute(
        "alter table learning.xingce_attempt_review_versions "
        "drop constraint xingce_attempt_review_versions_stage3_state_check"
    )
    op.execute(
        "alter table learning.xingce_attempt_review_versions "
        "drop constraint xingce_attempt_review_versions_stage3_payload_check"
    )
    op.execute(
        """
        alter table learning.xingce_attempt_review_versions
          add constraint xingce_attempt_review_versions_state_check check (
            state in (
              'reflection_due', 'cause_candidate_pending',
              'cause_decision_due', 'immediate_verification_due'
            )
          )
        """
    )
    op.execute(
        """
        alter table learning.xingce_attempt_review_versions
          add constraint xingce_attempt_review_versions_payload_check check (
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
        """
    )
    op.execute(
        "alter table learning.xingce_task_sessions "
        "drop constraint xingce_task_sessions_mastery_source_check"
    )
    op.execute(
        "alter table learning.xingce_task_sessions "
        "drop constraint xingce_task_sessions_source_mastery_fk"
    )
    op.drop_column(
        "xingce_task_sessions", "source_mastery_version_id", schema="learning"
    )
    op.drop_column("xingce_task_sessions", "source_mastery_id", schema="learning")
    op.execute(
        "alter table learning.xingce_task_sessions "
        "drop constraint xingce_task_sessions_mode_check"
    )
    op.execute(
        "alter table learning.xingce_task_sessions add constraint "
        "xingce_task_sessions_mode_check check (mode in ('baseline', 'targeted'))"
    )
    op.execute(
        "drop trigger if exists xingce_mastery_state_versions_append_only "
        "on learning.xingce_mastery_state_versions"
    )
    op.drop_table("xingce_mastery_state_versions", schema="learning")


def _has_mastery_records(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "select exists (select 1 from learning.xingce_mastery_state_versions)"
            )
        ).scalar_one()
    )


_STATEMENTS = (
    """
    do $$
    declare constraint_row record;
    begin
      for constraint_row in
        select conname
        from pg_constraint
        where conrelid = 'learning.xingce_attempt_review_versions'::regclass
          and contype = 'c'
          and pg_get_constraintdef(oid) like '%immediate_verification_due%'
      loop
        execute format(
          'alter table learning.xingce_attempt_review_versions drop constraint %I',
          constraint_row.conname
        );
      end loop;
    end;
    $$
    """,
    """
    alter table learning.xingce_attempt_review_versions
      add constraint xingce_attempt_review_versions_stage3_state_check check (
        state in (
          'reflection_due', 'cause_candidate_pending',
          'cause_decision_due', 'immediate_verification_due', 'mastery_recorded'
        )
      )
    """,
    """
    alter table learning.xingce_attempt_review_versions
      add constraint xingce_attempt_review_versions_stage3_payload_check check (
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
        or (state in ('immediate_verification_due', 'mastery_recorded')
          and reflection_version_id is not null
          and cause_candidate_version_id is not null
          and cause_decision_version_id is not null)
      )
    """,
    """
    create table learning.xingce_mastery_state_versions (
      mastery_version_id uuid primary key,
      mastery_id uuid not null,
      command_id text not null,
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      state text not null check (state in (
        'reflected', 'provisionally_mastered', 'stable_mastered', 'decayed'
      )),
      verification_stage text not null check (verification_stage in (
        'immediate', 'first_review', 'delayed_review'
      )),
      due_at timestamptz,
      rating smallint check (rating between 1 and 4),
      module_name text,
      scheduler_card jsonb not null check (jsonb_typeof(scheduler_card) = 'object'),
      scheduler_review_log jsonb check (
        scheduler_review_log is null
        or jsonb_typeof(scheduler_review_log) = 'object'
      ),
      scheduler_parameters jsonb not null check (
        jsonb_typeof(scheduler_parameters) = 'array'
        and jsonb_array_length(scheduler_parameters) = 21
      ),
      scheduler_algorithm_version text not null,
      scheduler_policy_version text not null,
      used_question_ids jsonb not null check (
        jsonb_typeof(used_question_ids) = 'array'
        and jsonb_array_length(used_question_ids) > 0
      ),
      source_review_id uuid not null,
      source_review_version_id uuid not null,
      source_session_id uuid not null,
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) >= 4
      ),
      supersedes_mastery_version_id uuid,
      created_at timestamptz not null,
      unique (mastery_id, version_no),
      unique (mastery_version_id, mastery_id, user_id),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred,
      foreign key (source_session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (
        source_review_version_id, source_review_id, user_id, source_session_id
      ) references learning.xingce_attempt_review_versions(
        review_version_id, review_id, user_id, session_id
      ),
      foreign key (supersedes_mastery_version_id, mastery_id, user_id)
        references learning.xingce_mastery_state_versions(
          mastery_version_id, mastery_id, user_id
        ),
      check (
        (version_no = 1 and supersedes_mastery_version_id is null and rating is null)
        or (version_no > 1 and supersedes_mastery_version_id is not null and rating is not null)
      ),
      check (
        (state = 'stable_mastered' and due_at is null
          and verification_stage = 'delayed_review')
        or (state <> 'stable_mastered' and due_at is not null)
      )
    )
    """,
    """
    create unique index learning_xingce_mastery_one_successor_idx
      on learning.xingce_mastery_state_versions(supersedes_mastery_version_id)
      where supersedes_mastery_version_id is not null
    """,
    """
    create index learning_xingce_mastery_owner_due_idx
      on learning.xingce_mastery_state_versions(user_id, due_at)
      where due_at is not null
    """,
    """
    create trigger xingce_mastery_state_versions_append_only
      before update or delete on learning.xingce_mastery_state_versions
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    alter table learning.xingce_task_sessions
      drop constraint xingce_task_sessions_mode_check
    """,
    """
    alter table learning.xingce_task_sessions
      add constraint xingce_task_sessions_mode_check
      check (mode in ('baseline', 'targeted', 'verification'))
    """,
    """
    alter table learning.xingce_task_sessions
      add column source_mastery_id uuid,
      add column source_mastery_version_id uuid
    """,
    """
    alter table learning.xingce_task_sessions
      add constraint xingce_task_sessions_source_mastery_fk
      foreign key (source_mastery_version_id, source_mastery_id, user_id)
      references learning.xingce_mastery_state_versions(
        mastery_version_id, mastery_id, user_id
      )
    """,
    """
    alter table learning.xingce_task_sessions
      add constraint xingce_task_sessions_mastery_source_check check (
        (mode = 'verification'
          and source_mastery_id is not null
          and source_mastery_version_id is not null)
        or (mode <> 'verification'
          and source_mastery_id is null
          and source_mastery_version_id is null)
      )
    """,
)
