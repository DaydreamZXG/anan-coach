"""Add the commercial Xingce cause-agent lifecycle.

Revision ID: 20260716_24
Revises: 20260716_23
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_24"
down_revision: str | Sequence[str] | None = "20260716_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_cause_agent_records(bind):
        raise RuntimeError(
            "Xingce cause-agent records exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    op.execute(
        "alter table learning.xingce_cause_decision_versions "
        "drop constraint if exists xingce_cause_decision_confirmed_fk"
    )
    for table in ("xingce_confirmed_cause_versions", "xingce_cause_followup_versions"):
        op.execute(f"drop trigger if exists {table}_append_only on learning.{table}")
        op.drop_table(table, schema="learning")
    for table in (
        "agent_tool_call_events",
        "agent_run_versions",
        "agent_definition_releases",
    ):
        op.execute(f"drop trigger if exists {table}_append_only on ai_audit.{table}")
        op.drop_table(table, schema="ai_audit")

    op.execute("drop index if exists learning.learning_xingce_decision_one_successor_idx")
    op.execute(
        "alter table learning.xingce_cause_decision_versions "
        "drop constraint if exists xingce_cause_decision_candidate_fk"
    )
    op.execute(
        "alter table learning.xingce_cause_decision_versions "
        "drop constraint if exists xingce_cause_decision_versions_v2_shape_check"
    )
    op.drop_column(
        "xingce_cause_decision_versions",
        "confirmed_cause_version_id",
        schema="learning",
    )
    op.drop_column(
        "xingce_cause_decision_versions",
        "candidate_version_id",
        schema="learning",
    )
    op.execute(
        """
        alter table learning.xingce_cause_decision_versions
          add constraint xingce_cause_decision_versions_legacy_shape_check check (
            version_no >= 1
            and decision in ('replace', 'skip')
            and (
              (decision = 'replace' and length(trim(replacement_cause)) > 0)
              or (decision = 'skip' and replacement_cause is null)
            )
          )
        """
    )

    op.execute("drop index if exists learning.learning_xingce_candidate_one_successor_idx")
    op.execute(
        "alter table learning.xingce_cause_candidate_versions "
        "drop constraint if exists xingce_cause_candidate_supersedes_fk"
    )
    op.execute(
        "alter table learning.xingce_cause_candidate_versions "
        "drop constraint if exists xingce_cause_candidate_versions_v2_shape_check"
    )
    for column in (
        "agent_run_id",
        "payload",
        "output_fingerprint",
        "entitlement_policy_version",
        "supersedes_candidate_version_id",
    ):
        op.drop_column("xingce_cause_candidate_versions", column, schema="learning")
    op.execute(
        "alter table learning.xingce_cause_candidate_versions "
        "alter column reason_code set not null"
    )
    op.execute(
        "alter table learning.xingce_cause_candidate_versions "
        "add constraint xingce_cause_candidate_versions_state_check "
        "check (state = 'unavailable')"
    )
    op.execute(
        "alter table learning.xingce_cause_candidate_versions "
        "add constraint xingce_cause_candidate_versions_reason_check "
        "check (reason_code = 'scenario_not_activated')"
    )
    op.execute(
        "alter table learning.xingce_cause_candidate_versions "
        "add constraint xingce_cause_candidate_versions_charged_check "
        "check (charged = false)"
    )

    op.execute(
        "alter table learning.xingce_attempt_review_versions "
        "drop constraint if exists xingce_attempt_review_versions_stage3_ai_state_check"
    )
    op.execute(
        "alter table learning.xingce_attempt_review_versions "
        "drop constraint if exists xingce_attempt_review_versions_stage3_ai_payload_check"
    )
    op.execute(
        """
        alter table learning.xingce_attempt_review_versions
          add constraint xingce_attempt_review_versions_stage3_state_check check (
            revision >= 1 and state in (
              'reflection_due', 'cause_candidate_pending', 'cause_decision_due',
              'immediate_verification_due', 'mastery_recorded'
            )
          )
        """
    )
    op.execute(
        """
        alter table learning.xingce_attempt_review_versions
          add constraint xingce_attempt_review_versions_stage3_payload_check check (
            ((revision = 1 and supersedes_review_version_id is null)
              or (revision > 1 and supersedes_review_version_id is not null))
            and (
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
          )
        """
    )

    op.execute(
        "alter table learning.xingce_commands "
        "drop constraint if exists xingce_commands_stage3_ai_shape_check"
    )
    op.execute(
        """
        alter table learning.xingce_commands
          add constraint xingce_commands_stage2_shape_check check (
            char_length(command_id) between 8 and 260
            and length(request_fingerprint) = 64
            and jsonb_typeof(response_payload) = 'object'
            and command_type in (
              'session_start', 'checkpoint_save', 'attempt_submit',
              'reflection_submit', 'cause_decision'
            )
          )
        """
    )


def _has_cause_agent_records(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "select exists ("
                "select 1 from ai_audit.agent_run_versions union all "
                "select 1 from ai_audit.agent_tool_call_events union all "
                "select 1 from learning.xingce_cause_followup_versions union all "
                "select 1 from learning.xingce_confirmed_cause_versions union all "
                "select 1 from learning.xingce_cause_candidate_versions "
                "where state <> 'unavailable' union all "
                "select 1 from learning.xingce_cause_decision_versions "
                "where decision not in ('replace', 'skip') "
                "or candidate_version_id is not null"
                ")"
            )
        ).scalar_one()
    )


_STATEMENTS = (
    """
    create table ai_audit.agent_definition_releases (
      definition_release_id text primary key,
      agent_key text not null,
      contract_version text not null,
      prompt_version text not null,
      schema_version text not null,
      behavior_version text not null,
      completion_condition_version text not null,
      tool_policy_version text not null,
      toolset_snapshot jsonb not null check (
        jsonb_typeof(toolset_snapshot) = 'array'
        and jsonb_array_length(toolset_snapshot) between 1 and 6
      ),
      budget_snapshot jsonb not null check (
        jsonb_typeof(budget_snapshot) = 'object'
        and (budget_snapshot->>'maxModelCalls')::integer = 2
        and (budget_snapshot->>'maxToolCalls')::integer = 6
        and (budget_snapshot->>'maxFollowupRounds')::integer = 1
        and (budget_snapshot->>'maxConsecutiveNoProgress')::integer = 2
      ),
      definition_sha256 text not null unique check (length(definition_sha256) = 64),
      created_at timestamptz not null
    )
    """,
    """
    create trigger agent_definition_releases_append_only
      before update or delete on ai_audit.agent_definition_releases
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table ai_audit.agent_run_versions (
      run_version_id uuid primary key,
      run_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      user_id uuid not null references learner.users(user_id),
      review_id uuid not null,
      session_id uuid not null,
      reflection_version_id uuid not null,
      definition_release_id text not null references ai_audit.agent_definition_releases(
        definition_release_id
      ),
      state text not null check (state in (
        'queued', 'running', 'followup_required', 'candidate_ready',
        'evidence_insufficient', 'terminated', 'failed'
      )),
      job_id uuid references platform.background_jobs(job_id),
      input_version_refs jsonb not null check (
        jsonb_typeof(input_version_refs) = 'array'
        and jsonb_array_length(input_version_refs) >= 2
      ),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      provider text not null,
      model text not null,
      provider_release_id text not null,
      prompt_version text not null,
      schema_version text not null,
      toolset_snapshot jsonb not null check (jsonb_typeof(toolset_snapshot) = 'array'),
      budget_snapshot jsonb not null check (jsonb_typeof(budget_snapshot) = 'object'),
      progress_fingerprint text check (
        progress_fingerprint is null or length(progress_fingerprint) = 64
      ),
      model_call_count integer not null check (model_call_count between 0 and 2),
      tool_call_count integer not null check (tool_call_count between 0 and 6),
      consecutive_no_progress integer not null check (
        consecutive_no_progress between 0 and 2
      ),
      followup_round integer not null check (followup_round between 0 and 1),
      termination_reason text,
      candidate_version_id uuid,
      supersedes_run_version_id uuid,
      created_at timestamptz not null,
      unique (run_id, version_no),
      unique (run_version_id, run_id, user_id, review_id, session_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (reflection_version_id, user_id, review_id, session_id)
        references learning.xingce_reflection_versions(
          reflection_version_id, user_id, review_id, session_id
        ),
      foreign key (supersedes_run_version_id, run_id, user_id, review_id, session_id)
        references ai_audit.agent_run_versions(
          run_version_id, run_id, user_id, review_id, session_id
        ),
      check (
        (version_no = 1 and supersedes_run_version_id is null)
        or (version_no > 1 and supersedes_run_version_id is not null)
      ),
      check (
        (state in ('queued', 'running') and termination_reason is null)
        or (state in ('followup_required', 'candidate_ready', 'evidence_insufficient')
          and candidate_version_id is not null and termination_reason is null)
        or (state in ('terminated', 'failed') and termination_reason is not null)
      )
    )
    """,
    """
    create unique index ai_agent_run_one_successor_idx
      on ai_audit.agent_run_versions(supersedes_run_version_id)
      where supersedes_run_version_id is not null
    """,
    """
    create index ai_agent_run_owner_review_idx
      on ai_audit.agent_run_versions(user_id, review_id, created_at)
    """,
    """
    create trigger agent_run_versions_append_only
      before update or delete on ai_audit.agent_run_versions
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table ai_audit.agent_tool_call_events (
      event_id uuid primary key,
      call_id uuid not null,
      run_id uuid not null,
      run_version_id uuid not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      sequence_no integer not null check (sequence_no between 1 and 6),
      event_type text not null check (event_type in (
        'requested', 'allowed', 'denied', 'succeeded', 'failed'
      )),
      tool_name text not null,
      tool_schema_version text not null,
      tool_behavior_version text not null,
      permission_policy_version text not null,
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      output_fingerprint text check (
        output_fingerprint is null or length(output_fingerprint) = 64
      ),
      progress_fingerprint text check (
        progress_fingerprint is null or length(progress_fingerprint) = 64
      ),
      error_code text,
      occurred_at timestamptz not null,
      unique (call_id, event_type),
      foreign key (run_version_id, run_id, user_id, review_id, session_id)
        references ai_audit.agent_run_versions(
          run_version_id, run_id, user_id, review_id, session_id
        )
    )
    """,
    """
    create index ai_agent_tool_events_run_idx
      on ai_audit.agent_tool_call_events(run_id, sequence_no, occurred_at)
    """,
    """
    create trigger agent_tool_call_events_append_only
      before update or delete on ai_audit.agent_tool_call_events
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    do $$
    declare constraint_row record;
    begin
      for constraint_row in
        select conname
        from pg_constraint
        where conrelid = 'learning.xingce_cause_candidate_versions'::regclass
          and contype = 'c'
      loop
        execute format(
          'alter table learning.xingce_cause_candidate_versions drop constraint %I',
          constraint_row.conname
        );
      end loop;
    end;
    $$
    """,
    """
    alter table learning.xingce_cause_candidate_versions
      add column agent_run_id uuid,
      add column payload jsonb,
      add column output_fingerprint text,
      add column entitlement_policy_version text,
      add column supersedes_candidate_version_id uuid
    """,
    """
    update learning.xingce_cause_candidate_versions
       set payload = '{}'::jsonb,
           entitlement_policy_version = 'legacy-unavailable@v1'
    """,
    """
    alter table learning.xingce_cause_candidate_versions
      alter column payload set not null,
      alter column entitlement_policy_version set not null,
      alter column reason_code drop not null,
      add constraint xingce_cause_candidate_versions_v2_shape_check check (
        version_no >= 1
        and jsonb_typeof(input_version_refs) = 'array'
        and jsonb_array_length(input_version_refs) > 0
        and jsonb_typeof(payload) = 'object'
        and (output_fingerprint is null or length(output_fingerprint) = 64)
        and (
          (state = 'unavailable' and reason_code = 'scenario_not_activated'
            and charged = false and agent_run_id is null)
          or (state in (
              'processing', 'candidate_ready', 'followup_required',
              'evidence_insufficient', 'terminated'
            ) and charged = false and agent_run_id is not null)
        )
        and (
          (version_no = 1 and supersedes_candidate_version_id is null)
          or (version_no > 1 and supersedes_candidate_version_id is not null)
        )
      )
    """,
    """
    alter table learning.xingce_cause_candidate_versions
      add constraint xingce_cause_candidate_supersedes_fk
      foreign key (
        supersedes_candidate_version_id, user_id, review_id, session_id
      ) references learning.xingce_cause_candidate_versions(
        candidate_version_id, user_id, review_id, session_id
      )
    """,
    """
    create unique index learning_xingce_candidate_one_successor_idx
      on learning.xingce_cause_candidate_versions(supersedes_candidate_version_id)
      where supersedes_candidate_version_id is not null
    """,
    """
    alter table ai_audit.agent_run_versions
      add constraint ai_agent_run_candidate_fk
      foreign key (candidate_version_id, user_id, review_id, session_id)
      references learning.xingce_cause_candidate_versions(
        candidate_version_id, user_id, review_id, session_id
      ) deferrable initially deferred
    """,
    """
    create table learning.xingce_cause_followup_versions (
      followup_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      run_id uuid not null,
      candidate_version_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      question text not null check (length(trim(question)) > 0),
      answer text not null check (length(trim(answer)) > 0),
      payload_sha256 text not null check (length(payload_sha256) = 64),
      created_at timestamptz not null,
      unique (review_id, version_no),
      unique (user_id, command_id),
      foreign key (session_id, user_id)
        references learning.xingce_task_sessions(session_id, user_id),
      foreign key (candidate_version_id, user_id, review_id, session_id)
        references learning.xingce_cause_candidate_versions(
          candidate_version_id, user_id, review_id, session_id
        ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    create trigger xingce_cause_followup_versions_append_only
      before update or delete on learning.xingce_cause_followup_versions
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    alter table learning.xingce_cause_decision_versions
      add column candidate_version_id uuid,
      add column confirmed_cause_version_id uuid
    """,
    """
    do $$
    declare constraint_row record;
    begin
      for constraint_row in
        select conname
        from pg_constraint
        where conrelid = 'learning.xingce_cause_decision_versions'::regclass
          and contype = 'c'
      loop
        execute format(
          'alter table learning.xingce_cause_decision_versions drop constraint %I',
          constraint_row.conname
        );
      end loop;
    end;
    $$
    """,
    """
    alter table learning.xingce_cause_decision_versions
      add constraint xingce_cause_decision_versions_v2_shape_check check (
        version_no >= 1
        and decision in ('replace', 'skip', 'confirm', 'reject', 'defer')
        and (
          (decision = 'replace' and length(trim(replacement_cause)) > 0)
          or (decision <> 'replace' and replacement_cause is null)
        )
        and (
          (decision = 'skip' and candidate_version_id is null)
          or (decision <> 'skip' and candidate_version_id is not null)
        )
        and (
          (version_no = 1 and supersedes_decision_version_id is null)
          or (version_no > 1 and supersedes_decision_version_id is not null)
        )
      )
    """,
    """
    alter table learning.xingce_cause_decision_versions
      add constraint xingce_cause_decision_candidate_fk
      foreign key (candidate_version_id, user_id, review_id, session_id)
      references learning.xingce_cause_candidate_versions(
        candidate_version_id, user_id, review_id, session_id
      )
    """,
    """
    create unique index learning_xingce_decision_one_successor_idx
      on learning.xingce_cause_decision_versions(supersedes_decision_version_id)
      where supersedes_decision_version_id is not null
    """,
    """
    create table learning.xingce_confirmed_cause_versions (
      confirmed_cause_version_id uuid primary key,
      confirmed_cause_id uuid not null,
      command_id text not null,
      user_id uuid not null,
      review_id uuid not null,
      session_id uuid not null,
      decision_version_id uuid not null,
      candidate_version_id uuid not null,
      version_no bigint not null check (version_no >= 1),
      source_kind text not null check (source_kind in ('ai_confirmed', 'user_replacement')),
      cause_code text not null,
      cause_text text not null check (length(trim(cause_text)) > 0),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) > 0
      ),
      supersedes_confirmed_cause_version_id uuid,
      created_at timestamptz not null,
      unique (confirmed_cause_id, version_no),
      unique (decision_version_id),
      unique (confirmed_cause_version_id, user_id, review_id, session_id),
      foreign key (decision_version_id, user_id, review_id, session_id)
        references learning.xingce_cause_decision_versions(
          decision_version_id, user_id, review_id, session_id
        ) deferrable initially deferred,
      foreign key (candidate_version_id, user_id, review_id, session_id)
        references learning.xingce_cause_candidate_versions(
          candidate_version_id, user_id, review_id, session_id
        ),
      foreign key (
        supersedes_confirmed_cause_version_id, user_id, review_id, session_id
      ) references learning.xingce_confirmed_cause_versions(
        confirmed_cause_version_id, user_id, review_id, session_id
      ),
      foreign key (user_id, command_id)
        references learning.xingce_commands(user_id, command_id)
        deferrable initially deferred
    )
    """,
    """
    create trigger xingce_confirmed_cause_versions_append_only
      before update or delete on learning.xingce_confirmed_cause_versions
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    alter table learning.xingce_cause_decision_versions
      add constraint xingce_cause_decision_confirmed_fk
      foreign key (
        confirmed_cause_version_id, user_id, review_id, session_id
      ) references learning.xingce_confirmed_cause_versions(
        confirmed_cause_version_id, user_id, review_id, session_id
      ) deferrable initially deferred
    """,
    """
    do $$
    declare constraint_row record;
    begin
      for constraint_row in
        select conname
        from pg_constraint
        where conrelid = 'learning.xingce_attempt_review_versions'::regclass
          and contype = 'c'
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
      add constraint xingce_attempt_review_versions_stage3_ai_state_check check (
        revision >= 1 and state in (
          'reflection_due', 'cause_candidate_pending', 'cause_followup_due',
          'cause_decision_due', 'cause_evidence_insufficient',
          'cause_agent_terminated', 'immediate_verification_due', 'mastery_recorded'
        )
      )
    """,
    """
    alter table learning.xingce_attempt_review_versions
      add constraint xingce_attempt_review_versions_stage3_ai_payload_check check (
        ((revision = 1 and supersedes_review_version_id is null)
          or (revision > 1 and supersedes_review_version_id is not null))
        and ((state = 'reflection_due'
          and reflection_version_id is null
          and cause_candidate_version_id is null
          and cause_decision_version_id is null)
        or (state = 'cause_candidate_pending'
          and reflection_version_id is not null
          and cause_decision_version_id is null)
        or (state in (
              'cause_followup_due', 'cause_decision_due',
              'cause_evidence_insufficient', 'cause_agent_terminated'
            )
          and reflection_version_id is not null
          and cause_candidate_version_id is not null
          and cause_decision_version_id is null)
        or (state in ('immediate_verification_due', 'mastery_recorded')
          and reflection_version_id is not null
          and cause_candidate_version_id is not null
          and cause_decision_version_id is not null))
      )
    """,
    """
    do $$
    declare constraint_row record;
    begin
      for constraint_row in
        select conname
        from pg_constraint
        where conrelid = 'learning.xingce_commands'::regclass
          and contype = 'c'
      loop
        execute format(
          'alter table learning.xingce_commands drop constraint %I',
          constraint_row.conname
        );
      end loop;
    end;
    $$
    """,
    """
    alter table learning.xingce_commands
      add constraint xingce_commands_stage3_ai_shape_check check (
        char_length(command_id) between 8 and 260
        and length(request_fingerprint) = 64
        and jsonb_typeof(response_payload) = 'object'
        and command_type in (
          'session_start', 'checkpoint_save', 'attempt_submit',
          'reflection_submit', 'cause_followup', 'cause_decision'
        )
      )
    """,
)
