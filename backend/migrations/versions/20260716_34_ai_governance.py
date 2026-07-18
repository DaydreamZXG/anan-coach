"""Add AI release gates, call attribution, retrieval audits, and external cost ledger.

Revision ID: 20260716_34
Revises: 20260716_33
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_34"
down_revision: str | Sequence[str] | None = "20260716_33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_stage7_records(bind):
        raise RuntimeError(
            "AI governance records exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    for table in reversed(_TABLES):
        op.execute(f"drop trigger if exists {table}_append_only on ai_audit.{table}")
    op.execute(
        "drop trigger if exists external_ai_operation_validate "
        "on ai_audit.external_ai_operation_ledger"
    )
    op.execute(
        "drop trigger if exists model_call_attribution_validate "
        "on ai_audit.model_call_attributions"
    )
    op.execute(
        "drop trigger if exists scenario_deployment_validate "
        "on ai_audit.scenario_deployments"
    )
    for function_name in (
        "ai_audit.validate_external_ai_operation",
        "ai_audit.validate_model_call_attribution",
        "ai_audit.validate_ai_scenario_deployment",
    ):
        op.execute(f"drop function if exists {function_name}()")
    for table in reversed(_TABLES):
        op.drop_table(table, schema="ai_audit")


def _has_stage7_records(bind: Connection) -> bool:
    query = " union all ".join(
        f"select 1 from ai_audit.{table}" for table in _TABLES
    )
    return bool(bind.execute(sa.text(f"select exists ({query})")).scalar_one())


_TABLES = (
    "evaluation_suite_releases",
    "release_evaluation_runs",
    "scenario_deployments",
    "retrieval_bundle_events",
    "model_call_attributions",
    "external_ai_operation_ledger",
)


_STATEMENTS = (
    """
    create table ai_audit.evaluation_suite_releases (
      suite_version text primary key,
      family text not null check (family in (
        'xingce_cause', 'essay', 'companion_tone',
        'strategic_deprioritization', 'ocr', 'combined'
      )),
      case_manifest_sha256 text not null check (length(case_manifest_sha256) = 64),
      case_count integer not null check (case_count > 0),
      required_dimensions jsonb not null check (
        jsonb_typeof(required_dimensions) = 'array'
        and jsonb_array_length(required_dimensions) > 0
      ),
      created_at timestamptz not null,
      unique (family, case_manifest_sha256)
    )
    """,
    """
    create trigger evaluation_suite_releases_append_only
      before update or delete on ai_audit.evaluation_suite_releases
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table ai_audit.release_evaluation_runs (
      evaluation_run_id uuid primary key,
      suite_version text not null references ai_audit.evaluation_suite_releases(
        suite_version
      ),
      scenario_key text not null,
      operation text not null,
      provider text not null,
      model text not null,
      model_tier text not null check (model_tier in ('small', 'high')),
      prompt_version text not null,
      schema_version text not null,
      price_version text not null,
      case_count integer not null check (case_count > 0),
      passed_count integer not null check (passed_count between 0 and case_count),
      dimension_results jsonb not null check (
        jsonb_typeof(dimension_results) = 'object'
      ),
      observed_total_cost_cny numeric(14, 8) not null check (
        observed_total_cost_cny >= 0
      ),
      observed_mean_cost_cny numeric(14, 8) not null check (
        observed_mean_cost_cny >= 0
      ),
      observed_p95_cost_cny numeric(14, 8) not null check (
        observed_p95_cost_cny >= 0
      ),
      maximum_mean_cost_cny numeric(14, 8) not null check (
        maximum_mean_cost_cny >= 0
      ),
      maximum_p95_cost_cny numeric(14, 8) not null check (
        maximum_p95_cost_cny >= 0
      ),
      report_sha256 text not null check (length(report_sha256) = 64),
      status text not null check (status in ('passed', 'failed')),
      evaluated_at timestamptz not null,
      unique (
        suite_version, scenario_key, operation, provider, model,
        prompt_version, schema_version, report_sha256
      ),
      check (
        (status = 'passed'
          and passed_count = case_count
          and observed_mean_cost_cny <= maximum_mean_cost_cny
          and observed_p95_cost_cny <= maximum_p95_cost_cny
          and not jsonb_path_exists(
            dimension_results,
            '$.* ? (@ != true)'
          ))
        or (status = 'failed' and (
          passed_count < case_count
          or observed_mean_cost_cny > maximum_mean_cost_cny
          or observed_p95_cost_cny > maximum_p95_cost_cny
          or jsonb_path_exists(dimension_results, '$.* ? (@ != true)')
        ))
      )
    )
    """,
    """
    create trigger release_evaluation_runs_append_only
      before update or delete on ai_audit.release_evaluation_runs
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table ai_audit.scenario_deployments (
      deployment_id uuid primary key,
      release_id text not null,
      scenario_key text not null,
      operation text not null,
      responding_agent text not null check (
        responding_agent in ('position', 'tutor', 'planner', 'recognition')
      ),
      impact_level text not null check (impact_level in ('low', 'medium', 'high')),
      model_tier text not null check (model_tier in ('small', 'high')),
      provider text not null,
      model text not null,
      prompt_version text not null,
      schema_version text not null,
      route_policy_version text not null,
      permission_policy_version text not null,
      injection_policy_version text not null,
      cost_policy_version text not null,
      evaluation_suite_version text not null references
        ai_audit.evaluation_suite_releases(suite_version),
      evaluation_run_id uuid not null references ai_audit.release_evaluation_runs(
        evaluation_run_id
      ),
      deployment_fingerprint text not null unique check (
        length(deployment_fingerprint) = 64
      ),
      supersedes_deployment_id uuid,
      published_at timestamptz not null,
      unique (release_id, scenario_key, operation),
      unique (deployment_id, scenario_key, operation),
      foreign key (supersedes_deployment_id)
        references ai_audit.scenario_deployments(deployment_id),
      check (supersedes_deployment_id is null or supersedes_deployment_id <> deployment_id)
    )
    """,
    """
    create unique index ai_scenario_deployment_one_successor_idx
      on ai_audit.scenario_deployments(supersedes_deployment_id)
      where supersedes_deployment_id is not null
    """,
    """
    create index ai_scenario_deployment_current_idx
      on ai_audit.scenario_deployments(scenario_key, operation, published_at desc)
    """,
    """
    create function ai_audit.validate_ai_scenario_deployment()
    returns trigger language plpgsql as $$
    declare evaluation ai_audit.release_evaluation_runs%rowtype;
    begin
      select * into evaluation
        from ai_audit.release_evaluation_runs
        where evaluation_run_id = new.evaluation_run_id;
      if evaluation.status is distinct from 'passed' then
        raise exception using errcode = '23514',
          message = 'AI deployment requires a passed evaluation and cost gate';
      end if;
      if evaluation.suite_version is distinct from new.evaluation_suite_version then
        raise exception using errcode = '23514',
          message = 'AI deployment suite does not match its evaluation run';
      end if;
      if row(
        evaluation.scenario_key, evaluation.operation, evaluation.provider,
        evaluation.model, evaluation.model_tier, evaluation.prompt_version,
        evaluation.schema_version
      ) is distinct from row(
        new.scenario_key, new.operation, new.provider, new.model,
        new.model_tier, new.prompt_version, new.schema_version
      ) then
        raise exception using errcode = '23514',
          message = 'AI deployment identity does not match its evaluation';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger scenario_deployment_validate
      before insert on ai_audit.scenario_deployments
      for each row execute function ai_audit.validate_ai_scenario_deployment()
    """,
    """
    create trigger scenario_deployments_append_only
      before update or delete on ai_audit.scenario_deployments
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table ai_audit.retrieval_bundle_events (
      retrieval_bundle_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      scenario_key text not null,
      route_id text not null,
      workspace text not null check (workspace in (
        'today', 'training', 'position', 'profile'
      )),
      policy_version text not null,
      query_fingerprint text not null check (length(query_fingerprint) = 64),
      source_refs jsonb not null check (jsonb_typeof(source_refs) = 'array'),
      blocked_source_refs jsonb not null check (
        jsonb_typeof(blocked_source_refs) = 'array'
      ),
      security_decision text not null check (security_decision in (
        'clear', 'sources_blocked', 'all_sources_blocked'
      )),
      bundle_fingerprint text not null unique check (length(bundle_fingerprint) = 64),
      created_at timestamptz not null
    )
    """,
    """
    create index ai_retrieval_bundle_owner_idx
      on ai_audit.retrieval_bundle_events(user_id, created_at desc)
    """,
    """
    create trigger retrieval_bundle_events_append_only
      before update or delete on ai_audit.retrieval_bundle_events
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table ai_audit.model_call_attributions (
      call_id uuid primary key references ai_audit.model_call_ledger(call_id),
      scenario_key text not null,
      operation text not null,
      responding_agent text not null check (
        responding_agent in ('position', 'tutor', 'planner', 'recognition')
      ),
      impact_level text not null check (impact_level in ('low', 'medium', 'high')),
      model_tier text not null check (model_tier in ('small', 'high')),
      deployment_id uuid,
      evaluation_state text not null check (
        evaluation_state in ('passed', 'local_unreleased')
      ),
      route_policy_version text not null,
      permission_policy_version text not null,
      injection_policy_version text not null,
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) > 0
      ),
      evidence_fingerprint text not null check (length(evidence_fingerprint) = 64),
      retrieval_bundle_id uuid references ai_audit.retrieval_bundle_events(
        retrieval_bundle_id
      ),
      reservation_id uuid,
      reservation_source text,
      entitlement_state text not null check (
        entitlement_state in ('reserved', 'not_applicable')
      ),
      created_at timestamptz not null,
      foreign key (deployment_id, scenario_key, operation)
        references ai_audit.scenario_deployments(
          deployment_id, scenario_key, operation
        ),
      check (
        (evaluation_state = 'passed' and deployment_id is not null)
        or (evaluation_state = 'local_unreleased' and deployment_id is null)
      ),
      check (
        (entitlement_state = 'reserved'
          and reservation_id is not null and reservation_source is not null)
        or (entitlement_state = 'not_applicable'
          and reservation_id is null and reservation_source is null)
      )
    )
    """,
    """
    create function ai_audit.validate_model_call_attribution()
    returns trigger language plpgsql as $$
    declare call_row ai_audit.model_call_ledger%rowtype;
    declare deployment ai_audit.scenario_deployments%rowtype;
    declare reservation_matches boolean;
    begin
      select * into call_row from ai_audit.model_call_ledger
        where call_id = new.call_id;
      if call_row.operation is distinct from new.operation then
        raise exception using errcode = '23514',
          message = 'AI attribution operation does not match model call';
      end if;
      if new.evaluation_state = 'passed' then
        select * into deployment from ai_audit.scenario_deployments
          where deployment_id = new.deployment_id;
        if row(
          call_row.provider, call_row.model, call_row.prompt_version,
          call_row.schema_version
        ) is distinct from row(
          deployment.provider, deployment.model, deployment.prompt_version,
          deployment.schema_version
        ) then
          raise exception using errcode = '23514',
            message = 'Model call identity does not match evaluated deployment';
        end if;
      end if;
      if new.entitlement_state = 'reserved' then
        select exists (
          select 1 from commerce.feature_usage_reservations reservation
          where reservation.reservation_id = new.reservation_id
            and new.reservation_source = 'catalog'
          union all
          select 1 from commerce.community_reward_ai_usage_reservations reservation
          where reservation.reservation_id = new.reservation_id
            and new.reservation_source = 'community_reward'
        ) into reservation_matches;
        if not reservation_matches then
          raise exception using errcode = '23514',
            message = 'AI attribution reservation does not exist in its source ledger';
        end if;
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger model_call_attribution_validate
      before insert on ai_audit.model_call_attributions
      for each row execute function ai_audit.validate_model_call_attribution()
    """,
    """
    create trigger model_call_attributions_append_only
      before update or delete on ai_audit.model_call_attributions
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
    """
    create table ai_audit.external_ai_operation_ledger (
      operation_id uuid primary key,
      operation_key text not null unique,
      user_id uuid not null references learner.users(user_id),
      scenario_key text not null,
      operation text not null,
      responding_agent text not null check (responding_agent = 'recognition'),
      impact_level text not null check (impact_level in ('medium', 'high')),
      model_tier text not null check (model_tier in ('small', 'high')),
      deployment_id uuid,
      evaluation_state text not null check (
        evaluation_state in ('passed', 'local_unreleased')
      ),
      provider text not null,
      provider_release text not null,
      model text not null,
      prompt_version text not null,
      schema_version text not null,
      price_version text not null,
      billing_unit text not null check (billing_unit in ('page', 'image', 'call')),
      unit_count integer not null check (unit_count > 0),
      unit_price_cny numeric(14, 8) not null check (unit_price_cny >= 0),
      total_cost_cny numeric(14, 8) not null check (
        total_cost_cny = round(unit_count * unit_price_cny, 8)
      ),
      status text not null check (status in ('succeeded', 'failed')),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      output_fingerprint text check (
        output_fingerprint is null or length(output_fingerprint) = 64
      ),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array'
        and jsonb_array_length(evidence_refs) > 0
      ),
      reservation_id uuid,
      reservation_source text,
      latency_ms integer not null check (latency_ms >= 0),
      error_code text,
      completed_at timestamptz not null,
      foreign key (deployment_id, scenario_key, operation)
        references ai_audit.scenario_deployments(
          deployment_id, scenario_key, operation
        ),
      check (
        (evaluation_state = 'passed' and deployment_id is not null)
        or (evaluation_state = 'local_unreleased' and deployment_id is null)
      ),
      check (
        (status = 'succeeded' and output_fingerprint is not null and error_code is null)
        or (status = 'failed' and output_fingerprint is null
          and char_length(error_code) > 0)
      )
    )
    """,
    """
    create index ai_external_operation_owner_idx
      on ai_audit.external_ai_operation_ledger(user_id, scenario_key, completed_at desc)
    """,
    """
    create function ai_audit.validate_external_ai_operation()
    returns trigger language plpgsql as $$
    declare deployment ai_audit.scenario_deployments%rowtype;
    declare reservation_matches boolean;
    begin
      if new.evaluation_state = 'passed' then
        select * into deployment from ai_audit.scenario_deployments
          where deployment_id = new.deployment_id;
        if row(
          new.scenario_key, new.operation, new.provider, new.model,
          new.prompt_version, new.schema_version
        ) is distinct from row(
          deployment.scenario_key, deployment.operation, deployment.provider,
          deployment.model, deployment.prompt_version, deployment.schema_version
        ) then
          raise exception using errcode = '23514',
            message = 'External AI operation does not match evaluated deployment';
        end if;
      end if;
      if new.reservation_id is not null then
        select exists (
          select 1 from commerce.feature_usage_reservations reservation
          where reservation.reservation_id = new.reservation_id
            and new.reservation_source = 'catalog'
          union all
          select 1 from commerce.community_reward_ai_usage_reservations reservation
          where reservation.reservation_id = new.reservation_id
            and new.reservation_source = 'community_reward'
        ) into reservation_matches;
        if not reservation_matches then
          raise exception using errcode = '23514',
            message = 'External AI operation reservation does not exist';
        end if;
      elsif new.reservation_source is not null then
        raise exception using errcode = '23514',
          message = 'External AI operation reservation source lacks reservation';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger external_ai_operation_validate
      before insert on ai_audit.external_ai_operation_ledger
      for each row execute function ai_audit.validate_external_ai_operation()
    """,
    """
    create trigger external_ai_operation_ledger_append_only
      before update or delete on ai_audit.external_ai_operation_ledger
      for each row execute function ai_audit.reject_append_only_mutation()
    """,
)
