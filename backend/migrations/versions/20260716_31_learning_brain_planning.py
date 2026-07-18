"""Add the shared learning-brain and weekly planning authorities.

Revision ID: 20260716_31
Revises: 20260716_30
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_31"
down_revision: str | Sequence[str] | None = "20260716_30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_stage4_records(bind):
        raise RuntimeError(
            "Learning-brain or weekly-plan records exist; destructive downgrade is "
            "forbidden. Repair the schema with a forward migration."
        )
    for table in reversed(_TABLES):
        op.execute(
            f"drop trigger if exists {table}_append_only on learning.{table}"
        )
        op.drop_table(table, schema="learning")
    op.execute("drop function if exists learning.reject_stage4_mutation()")


def _has_stage4_records(bind: Connection) -> bool:
    query = " union all ".join(
        f"select 1 from learning.{table}" for table in _TABLES
    )
    return bool(bind.execute(sa.text(f"select exists ({query})")).scalar_one())


_TABLES = (
    "inference_candidate_versions",
    "learner_brain_profile_versions",
    "strategy_decision_versions",
    "weekly_plan_versions",
)


_STATEMENTS = (
    """
    create table learning.inference_candidate_versions (
      candidate_version_id uuid primary key,
      candidate_id uuid not null,
      command_id text not null,
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      inference_type text not null check (inference_type in (
        'error_pattern', 'module_interpretation', 'strategy_recommendation',
        'planning_explanation'
      )),
      status text not null check (status in (
        'pending_validation', 'awaiting_confirmation', 'accepted',
        'rejected', 'expired'
      )),
      producer_type text not null check (producer_type in ('rule', 'agent')),
      producer_id text not null check (char_length(trim(producer_id)) > 0),
      value jsonb not null check (jsonb_typeof(value) = 'object'),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) > 0
      ),
      confidence numeric(5, 4) not null check (confidence between 0 and 1),
      requires_user_confirmation boolean not null,
      confirmation_status text not null check (confirmation_status in (
        'not_required', 'pending', 'confirmed', 'rejected'
      )),
      validation_status text not null check (validation_status in (
        'pending', 'valid', 'invalid'
      )),
      model_id text,
      prompt_version text,
      policy_version text not null check (char_length(trim(policy_version)) > 0),
      valid_from timestamptz not null,
      expires_at timestamptz not null,
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      supersedes_candidate_version_id uuid,
      created_at timestamptz not null,
      unique (candidate_id, version_no),
      unique (user_id, command_id),
      unique (candidate_version_id, candidate_id, user_id),
      foreign key (supersedes_candidate_version_id, candidate_id, user_id)
        references learning.inference_candidate_versions(
          candidate_version_id, candidate_id, user_id
        ),
      check (expires_at > valid_from),
      check (
        (producer_type = 'rule' and model_id is null and prompt_version is null)
        or (producer_type = 'agent' and model_id is not null
          and prompt_version is not null)
      ),
      check (
        (requires_user_confirmation and confirmation_status in (
          'pending', 'confirmed', 'rejected'
        )) or (not requires_user_confirmation and confirmation_status = 'not_required')
      ),
      check (
        (version_no = 1 and supersedes_candidate_version_id is null)
        or (version_no > 1 and supersedes_candidate_version_id is not null)
      )
    )
    """,
    """
    create unique index learning_inference_candidate_one_successor_idx
      on learning.inference_candidate_versions(supersedes_candidate_version_id)
      where supersedes_candidate_version_id is not null
    """,
    """
    create index learning_inference_candidate_current_idx
      on learning.inference_candidate_versions(user_id, inference_type, created_at)
    """,
    """
    create table learning.learner_brain_profile_versions (
      profile_version_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      readiness text not null check (readiness in (
        'unavailable', 'partial', 'ready', 'stale', 'conflicted'
      )),
      ability_estimate_version_id uuid,
      ability_snapshot jsonb not null check (jsonb_typeof(ability_snapshot) = 'object'),
      evidence_summary jsonb not null check (jsonb_typeof(evidence_summary) = 'object'),
      candidate_summary jsonb not null check (jsonb_typeof(candidate_summary) = 'object'),
      module_abilities jsonb not null check (jsonb_typeof(module_abilities) = 'array'),
      error_patterns jsonb not null check (jsonb_typeof(error_patterns) = 'array'),
      decision_summary jsonb not null check (jsonb_typeof(decision_summary) = 'object'),
      conflicts jsonb not null check (jsonb_typeof(conflicts) = 'array'),
      source_refs jsonb not null check (jsonb_typeof(source_refs) = 'array'),
      input_versions jsonb not null check (jsonb_typeof(input_versions) = 'object'),
      algorithm_version text not null check (char_length(trim(algorithm_version)) > 0),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      supersedes_profile_version_id uuid,
      generated_at timestamptz not null,
      valid_until timestamptz not null,
      unique (user_id, version_no),
      unique (profile_version_id, user_id),
      foreign key (ability_estimate_version_id)
        references learning.ability_estimate_versions(estimate_version_id),
      foreign key (supersedes_profile_version_id, user_id)
        references learning.learner_brain_profile_versions(profile_version_id, user_id),
      check (valid_until > generated_at),
      check (
        (version_no = 1 and supersedes_profile_version_id is null)
        or (version_no > 1 and supersedes_profile_version_id is not null)
      )
    )
    """,
    """
    create unique index learning_brain_profile_one_successor_idx
      on learning.learner_brain_profile_versions(supersedes_profile_version_id)
      where supersedes_profile_version_id is not null
    """,
    """
    create index learning_brain_profile_current_idx
      on learning.learner_brain_profile_versions(user_id, version_no desc)
    """,
    """
    create table learning.strategy_decision_versions (
      decision_version_id uuid primary key,
      decision_id uuid not null,
      command_id text not null,
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      candidate_version_id uuid not null,
      decision_type text not null check (decision_type in (
        'exam_skip', 'phase_deprioritize', 'strategic_abandon'
      )),
      status text not null check (status in (
        'active', 'rejected', 'expired', 'review_due', 'retired'
      )),
      scope_type text not null check (scope_type in ('track', 'module', 'question_type')),
      scope_key text not null check (char_length(trim(scope_key)) > 0),
      rationale text not null check (char_length(trim(rationale)) > 0),
      benefit_evidence jsonb not null check (jsonb_typeof(benefit_evidence) = 'object'),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) > 0
      ),
      effective_from timestamptz not null,
      effective_until timestamptz not null,
      review_at timestamptz not null,
      policy_version text not null check (policy_version = 'strategy-decision@v1'),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      supersedes_decision_version_id uuid,
      created_at timestamptz not null,
      unique (decision_id, version_no),
      unique (user_id, command_id),
      unique (decision_version_id, decision_id, user_id),
      foreign key (candidate_version_id)
        references learning.inference_candidate_versions(candidate_version_id),
      foreign key (supersedes_decision_version_id, decision_id, user_id)
        references learning.strategy_decision_versions(
          decision_version_id, decision_id, user_id
        ),
      check (effective_until > effective_from),
      check (review_at > effective_from and review_at <= effective_until),
      check (
        (version_no = 1 and supersedes_decision_version_id is null)
        or (version_no > 1 and supersedes_decision_version_id is not null)
      )
    )
    """,
    """
    create unique index learning_strategy_decision_one_successor_idx
      on learning.strategy_decision_versions(supersedes_decision_version_id)
      where supersedes_decision_version_id is not null
    """,
    """
    create index learning_strategy_decision_current_idx
      on learning.strategy_decision_versions(user_id, status, review_at)
    """,
    """
    create table learning.weekly_plan_versions (
      weekly_plan_version_id uuid primary key,
      command_id text not null,
      user_id uuid not null references learner.users(user_id),
      week_start date not null,
      version_no bigint not null check (version_no >= 1),
      weekly_preference_version_id uuid not null,
      rhythm text not null check (rhythm = '5+1+1'),
      days jsonb not null check (
        jsonb_typeof(days) = 'array' and jsonb_array_length(days) = 7
      ),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) > 0
      ),
      input_versions jsonb not null check (jsonb_typeof(input_versions) = 'object'),
      replan_reason_codes jsonb not null check (jsonb_typeof(replan_reason_codes) = 'array'),
      algorithm_version text not null check (algorithm_version = 'weekly-rhythm@v1'),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      supersedes_weekly_plan_version_id uuid,
      generated_at timestamptz not null,
      valid_until timestamptz not null,
      unique (user_id, command_id),
      unique (user_id, week_start, version_no),
      unique (weekly_plan_version_id, user_id, week_start),
      foreign key (weekly_preference_version_id, user_id)
        references learning.weekly_study_preference_versions(
          preference_version_id, user_id
        ),
      foreign key (supersedes_weekly_plan_version_id, user_id, week_start)
        references learning.weekly_plan_versions(
          weekly_plan_version_id, user_id, week_start
        ),
      check (valid_until > generated_at),
      check (
        (version_no = 1 and supersedes_weekly_plan_version_id is null)
        or (version_no > 1 and supersedes_weekly_plan_version_id is not null)
      )
    )
    """,
    """
    create unique index learning_weekly_plan_one_successor_idx
      on learning.weekly_plan_versions(supersedes_weekly_plan_version_id)
      where supersedes_weekly_plan_version_id is not null
    """,
    """
    create index learning_weekly_plan_current_idx
      on learning.weekly_plan_versions(user_id, week_start, version_no desc)
    """,
    """
    create function learning.reject_stage4_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception using
        errcode = '23514',
        message = 'stage4 learning-brain records are append-only';
    end;
    $$
    """,
    *(
        f"create trigger {table}_append_only before update or delete "
        f"on learning.{table} for each row execute function "
        "learning.reject_stage4_mutation()"
        for table in _TABLES
    ),
)
