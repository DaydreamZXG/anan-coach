"""Complete learner memory, recheck and grounded teach-back runtime.

Revision ID: 20260813_109
Revises: 20260813_108
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260813_109"
down_revision: str | Sequence[str] | None = "20260813_108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table learning.xingce_commands "
        "drop constraint if exists xingce_commands_wave1_shape_check"
    )
    op.execute(
        """
        alter table learning.xingce_commands
          add constraint xingce_commands_wave1_shape_check check (
            char_length(command_id) between 8 and 260
            and length(request_fingerprint) = 64
            and jsonb_typeof(response_payload) = 'object'
            and command_type in (
              'session_start', 'checkpoint_save', 'attempt_submit',
              'reflection_submit', 'cause_followup', 'cause_decision',
              'review_verdict', 'teach_back_transfer'
            )
          )
        """
    )
    op.execute(
        """
        create table learning.coach_judgment_recheck_versions (
          request_version_id uuid primary key,
          request_id uuid not null,
          command_id text not null,
          request_fingerprint char(64) not null check (
            request_fingerprint ~ '^[0-9a-f]{64}$'
          ),
          user_id uuid not null references learner.users(user_id),
          revision bigint not null check (revision >= 1),
          profile_version_id uuid not null,
          pattern_id char(64) not null check (pattern_id ~ '^[0-9a-f]{64}$'),
          action text not null check (action = 'not_accurate'),
          note text check (note is null or char_length(note) <= 500),
          state text not null check (state in ('pending_reverification', 'resolved')),
          supersedes_request_version_id uuid,
          created_at timestamptz not null,
          unique (user_id, command_id),
          unique (request_id, revision),
          foreign key (supersedes_request_version_id) references
            learning.coach_judgment_recheck_versions(request_version_id)
        )
        """
    )
    op.execute(
        """
        create unique index coach_judgment_recheck_one_successor_idx
          on learning.coach_judgment_recheck_versions(supersedes_request_version_id)
          where supersedes_request_version_id is not null
        """
    )
    op.execute(
        """
        create trigger coach_judgment_recheck_versions_append_only
          before update or delete on learning.coach_judgment_recheck_versions
          for each row execute function learning.reject_xingce_fact_mutation()
        """
    )
    op.execute(
        """
        create table learning.xingce_teach_back_session_versions (
          session_version_id uuid primary key,
          session_id uuid not null,
          command_id text not null,
          request_fingerprint char(64) not null check (
            request_fingerprint ~ '^[0-9a-f]{64}$'
          ),
          user_id uuid not null references learner.users(user_id),
          revision bigint not null check (revision >= 1),
          state text not null check (state in (
            'ready_for_transfer', 'blocked', 'provider_failed', 'completed'
          )),
          review_id uuid not null,
          source_question_id text not null,
          package_fingerprint char(64) not null check (
            package_fingerprint ~ '^[0-9a-f]{64}$'
          ),
          package_authority jsonb not null check (
            jsonb_typeof(package_authority) = 'object'
          ),
          user_explanation text not null check (
            char_length(user_explanation) between 1 and 50000
          ),
          student_output jsonb,
          grounding_output jsonb,
          call_audits jsonb not null check (jsonb_typeof(call_audits) = 'array'),
          transfer_selected_option_ids jsonb,
          transfer_confidence text check (
            transfer_confidence is null or transfer_confidence in ('low', 'medium', 'high')
          ),
          transfer_correct boolean,
          mastery_version_id uuid,
          supersedes_session_version_id uuid,
          created_at timestamptz not null,
          unique (user_id, command_id),
          unique (session_id, revision),
          foreign key (supersedes_session_version_id) references
            learning.xingce_teach_back_session_versions(session_version_id),
          check (
            (state = 'completed' and transfer_selected_option_ids is not null
              and transfer_confidence is not null and transfer_correct is not null
              and mastery_version_id is not null)
            or (state <> 'completed' and transfer_selected_option_ids is null
              and transfer_confidence is null and transfer_correct is null
              and mastery_version_id is null)
          )
        )
        """
    )
    op.execute(
        """
        create unique index xingce_teach_back_one_successor_idx
          on learning.xingce_teach_back_session_versions(supersedes_session_version_id)
          where supersedes_session_version_id is not null
        """
    )
    op.execute(
        """
        create index xingce_teach_back_owner_current_idx
          on learning.xingce_teach_back_session_versions(user_id, session_id, revision desc)
        """
    )
    op.execute(
        """
        create trigger xingce_teach_back_session_versions_append_only
          before update or delete on learning.xingce_teach_back_session_versions
          for each row execute function learning.reject_xingce_fact_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "drop trigger if exists xingce_teach_back_session_versions_append_only "
        "on learning.xingce_teach_back_session_versions"
    )
    op.execute("drop table if exists learning.xingce_teach_back_session_versions")
    op.execute(
        "drop trigger if exists coach_judgment_recheck_versions_append_only "
        "on learning.coach_judgment_recheck_versions"
    )
    op.execute("drop table if exists learning.coach_judgment_recheck_versions")
    op.execute(
        "alter table learning.xingce_commands "
        "drop constraint if exists xingce_commands_wave1_shape_check"
    )
    op.execute(
        """
        alter table learning.xingce_commands
          add constraint xingce_commands_wave1_shape_check check (
            char_length(command_id) between 8 and 260
            and length(request_fingerprint) = 64
            and jsonb_typeof(response_payload) = 'object'
            and command_type in (
              'session_start', 'checkpoint_save', 'attempt_submit',
              'reflection_submit', 'cause_followup', 'cause_decision',
              'review_verdict'
            )
          )
        """
    )
