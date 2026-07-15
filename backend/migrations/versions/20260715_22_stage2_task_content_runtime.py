"""Add the Stage 2 task-content dispute ledger.

Revision ID: 20260715_22
Revises: 20260715_21
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260715_22"
down_revision: str | Sequence[str] | None = "20260715_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_stage2_runtime_records(bind):
        raise RuntimeError(
            "Stage 2 task runtime records exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    op.execute(
        "drop trigger if exists xingce_question_disputes_append_only "
        "on learning.xingce_question_disputes"
    )
    op.execute(
        "drop trigger if exists xingce_question_dispute_session_guard "
        "on learning.xingce_question_disputes"
    )
    op.drop_table("xingce_question_disputes", schema="learning")
    op.execute("drop function if exists learning.validate_xingce_dispute_session()")
    op.execute(
        "drop trigger if exists mock_exam_commands_append_only "
        "on learner.mock_exam_commands"
    )
    op.drop_table("mock_exam_commands", schema="learner")
    op.execute("drop index if exists learner.mock_exam_one_assigned_per_owner_idx")
    for column in (
        "scoring_snapshot",
        "learner_bundle",
        "checkpoint_updated_at",
        "checkpoint",
        "deadline_at",
        "started_at",
        "revision",
    ):
        op.drop_column("mock_exam_sessions", column, schema="learner")


def _has_stage2_runtime_records(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "select exists (select 1 from learning.xingce_question_disputes) "
                "or exists ("
                "select 1 from learner.mock_exam_sessions "
                "where revision > 1 "
                "or jsonb_array_length(checkpoint->'answers') > 0 "
                "or checkpoint ? 'bundle' "
                "or learner_bundle <> '{}'::jsonb "
                "or scoring_snapshot <> '{}'::jsonb "
                ") or exists (select 1 from learner.mock_exam_commands)"
            )
        ).scalar_one()
    )


_STATEMENTS = (
    """
    alter table learner.mock_exam_sessions
      add column revision bigint not null default 1 check (revision >= 1),
      add column started_at timestamptz default now(),
      add column deadline_at timestamptz default (now() + interval '120 minutes'),
      add column checkpoint jsonb not null default
        '{"currentQuestionId": null, "answers": []}'::jsonb,
      add column learner_bundle jsonb not null default '{}'::jsonb,
      add column scoring_snapshot jsonb not null default '{}'::jsonb,
      add column checkpoint_updated_at timestamptz default now()
    """,
    """
    update learner.mock_exam_sessions
    set
      started_at = created_at,
      deadline_at = created_at + interval '120 minutes',
      checkpoint_updated_at = updated_at
    """,
    """
    alter table learner.mock_exam_sessions
      alter column started_at set not null,
      alter column deadline_at set not null,
      alter column checkpoint_updated_at set not null,
      add constraint mock_exam_deadline_after_start_check
        check (deadline_at > started_at),
      add constraint mock_exam_checkpoint_shape_check
        check (
          jsonb_typeof(checkpoint) = 'object'
          and checkpoint ? 'currentQuestionId'
          and jsonb_typeof(checkpoint->'answers') = 'array'
        ),
      add constraint mock_exam_scoring_snapshot_shape_check
        check (
          jsonb_typeof(scoring_snapshot) = 'object'
          and (
            scoring_snapshot = '{}'::jsonb
            or (
              scoring_snapshot ? 'bundleVersionId'
              and scoring_snapshot ? 'scoringPolicyVersion'
              and jsonb_typeof(scoring_snapshot->'questions') = 'array'
            )
          )
        ),
      add constraint mock_exam_learner_bundle_shape_check
        check (
          jsonb_typeof(learner_bundle) = 'object'
          and (
            learner_bundle = '{}'::jsonb
            or (
              learner_bundle ? 'bundleVersionId'
              and jsonb_typeof(learner_bundle->'materials') = 'array'
              and jsonb_typeof(learner_bundle->'questions') = 'array'
            )
          )
        )
    """,
    """
    create unique index mock_exam_one_assigned_per_owner_idx
      on learner.mock_exam_sessions(user_id, exam_scope, track)
      where status = 'assigned'
    """,
    """
    create table learner.mock_exam_commands (
      user_id uuid not null references learner.users(user_id),
      idempotency_key text not null check (
        char_length(idempotency_key) between 8 and 160
      ),
      command_type text not null check (command_type in ('checkpoint', 'submit')),
      session_id uuid not null references learner.mock_exam_sessions(session_id),
      request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
      response_payload jsonb not null check (jsonb_typeof(response_payload) = 'object'),
      created_at timestamptz not null,
      primary key (user_id, idempotency_key)
    )
    """,
    """
    create index learner_mock_exam_commands_session_created_idx
      on learner.mock_exam_commands(session_id, created_at)
    """,
    """
    create trigger mock_exam_commands_append_only
      before update or delete on learner.mock_exam_commands
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    create table learning.xingce_question_disputes (
      dispute_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      session_kind text not null check (session_kind in ('xingce_task', 'mock_exam')),
      session_id uuid not null,
      question_id text not null references product.questions(question_id),
      content_version_id text not null check (
        content_version_id ~ '^[0-9a-f]{64}$'
      ),
      category text not null check (category in (
        'stem_error', 'option_error', 'answer_error',
        'material_mismatch', 'image_unreadable', 'other'
      )),
      description text not null check (char_length(description) between 1 and 2000),
      evidence_context jsonb not null check (
        jsonb_typeof(evidence_context) = 'object'
        and evidence_context ? 'sessionRevision'
        and evidence_context ? 'bundleVersionId'
        and evidence_context ? 'currentQuestionId'
      ),
      idempotency_key text not null check (
        char_length(idempotency_key) between 8 and 160
      ),
      created_at timestamptz not null,
      unique (user_id, idempotency_key)
    )
    """,
    """
    create function learning.validate_xingce_dispute_session()
    returns trigger language plpgsql as $$
    begin
      if new.session_kind = 'xingce_task' then
        if not exists (
          select 1 from learning.xingce_task_sessions
          where session_id = new.session_id and user_id = new.user_id
        ) then
          raise exception using
            errcode = '23503',
            message = 'xingce dispute session owner does not exist';
        end if;
      elsif new.session_kind = 'mock_exam' then
        if not exists (
          select 1 from learner.mock_exam_sessions
          where session_id = new.session_id and user_id = new.user_id
        ) then
          raise exception using
            errcode = '23503',
            message = 'mock dispute session owner does not exist';
        end if;
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger xingce_question_dispute_session_guard
      before insert on learning.xingce_question_disputes
      for each row execute function learning.validate_xingce_dispute_session()
    """,
    """
    create index learning_xingce_question_disputes_owner_created_idx
      on learning.xingce_question_disputes(user_id, created_at desc)
    """,
    """
    create index learning_xingce_question_disputes_question_version_idx
      on learning.xingce_question_disputes(question_id, content_version_id, created_at desc)
    """,
    """
    create index learning_xingce_question_disputes_session_idx
      on learning.xingce_question_disputes(session_kind, session_id, created_at desc)
    """,
    """
    create trigger xingce_question_disputes_append_only
      before update or delete on learning.xingce_question_disputes
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
)
