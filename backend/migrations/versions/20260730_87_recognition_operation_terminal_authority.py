"""Make OCR dispatch intent durable before external provider I/O.

Revision ID: 20260730_87
Revises: 20260730_86
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260730_87"
down_revision: str | Sequence[str] | None = "20260730_86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    alter table ai_audit.external_ai_operation_ledger
      add column started_at timestamptz,
      add column usage_state text not null default 'actual',
      add column entitlement_state text not null default 'not_applicable'
    """,
    """
    update ai_audit.external_ai_operation_ledger
       set started_at = completed_at,
           entitlement_state = case
             when reservation_id is null then 'not_applicable'
             else 'reserved'
           end
    """,
    """
    alter table ai_audit.external_ai_operation_ledger
      alter column started_at set default now(),
      alter column started_at set not null,
      alter column total_cost_cny drop not null,
      alter column latency_ms drop not null,
      alter column completed_at drop not null
    """,
    """
    do $$
    declare constraint_row record;
    begin
      for constraint_row in
        select conname
          from pg_constraint
         where conrelid =
               'ai_audit.external_ai_operation_ledger'::regclass
           and contype = 'c'
           and pg_get_constraintdef(oid) like '%status%'
           and pg_get_constraintdef(oid) like '%succeeded%'
      loop
        execute format(
          'alter table ai_audit.external_ai_operation_ledger '
          'drop constraint %I',
          constraint_row.conname
        );
      end loop;
    end
    $$
    """,
    """
    alter table ai_audit.external_ai_operation_ledger
      add constraint external_ai_operation_status_v2_check
        check (status in ('running', 'succeeded', 'failed')),
      add constraint external_ai_operation_usage_state_check
        check (usage_state in ('planned', 'actual', 'unknown')),
      add constraint external_ai_operation_entitlement_state_check
        check (
          (entitlement_state = 'reserved'
            and reservation_id is not null
            and reservation_source in ('catalog', 'community_reward'))
          or
          (entitlement_state = 'not_applicable'
            and reservation_id is null
            and reservation_source is null)
        ),
      add constraint external_ai_operation_state_shape_v2_check
        check (
          (
            status = 'running'
            and usage_state = 'planned'
            and num_nonnulls(
              total_cost_cny, output_fingerprint, latency_ms,
              error_code, completed_at
            ) = 0
          )
          or
          (
            status = 'succeeded'
            and usage_state = 'actual'
            and total_cost_cny = round(unit_count * unit_price_cny, 8)
            and output_fingerprint is not null
            and latency_ms is not null
            and error_code is null
            and completed_at is not null
          )
          or
          (
            status = 'failed'
            and usage_state in ('actual', 'unknown')
            and output_fingerprint is null
            and latency_ms is not null
            and coalesce(char_length(error_code), 0) > 0
            and completed_at is not null
            and (
              (usage_state = 'actual'
                and total_cost_cny =
                    round(unit_count * unit_price_cny, 8))
              or
              (usage_state = 'unknown' and total_cost_cny is null)
            )
          )
        )
    """,
    """
    comment on column ai_audit.external_ai_operation_ledger.usage_state is
      'planned before dispatch, actual when provider usage is known, '
      'unknown when dispatch outcome cannot establish billable usage'
    """,
    """
    comment on column ai_audit.external_ai_operation_ledger.entitlement_state is
      'immutable attribution to a quota reservation, or explicit not_applicable'
    """,
    """
    create function ai_audit.guard_external_ai_operation_terminal_transition()
    returns trigger
    language plpgsql
    as $$
    begin
      if tg_op = 'DELETE' then
        raise exception using
          errcode = '55000',
          message = 'external AI operation ledger rows cannot be deleted';
      end if;
      if old.status <> 'running'
         or new.status not in ('succeeded', 'failed') then
        raise exception using
          errcode = '55000',
          message = 'external AI operation permits one running-to-terminal transition';
      end if;
      if row(
        old.operation_id, old.operation_key, old.user_id, old.scenario_key,
        old.operation, old.responding_agent, old.impact_level, old.model_tier,
        old.deployment_id, old.evaluation_state, old.provider,
        old.provider_release, old.model, old.prompt_version,
        old.schema_version, old.price_version, old.billing_unit,
        old.unit_count, old.unit_price_cny, old.input_fingerprint,
        old.evidence_refs, old.reservation_id, old.reservation_source,
        old.entitlement_state, old.started_at
      ) is distinct from row(
        new.operation_id, new.operation_key, new.user_id, new.scenario_key,
        new.operation, new.responding_agent, new.impact_level, new.model_tier,
        new.deployment_id, new.evaluation_state, new.provider,
        new.provider_release, new.model, new.prompt_version,
        new.schema_version, new.price_version, new.billing_unit,
        new.unit_count, new.unit_price_cny, new.input_fingerprint,
        new.evidence_refs, new.reservation_id, new.reservation_source,
        new.entitlement_state, new.started_at
      ) then
        raise exception using
          errcode = '55000',
          message = 'external AI operation immutable attribution changed';
      end if;
      return new;
    end
    $$
    """,
    """
    revoke all on function
      ai_audit.guard_external_ai_operation_terminal_transition()
      from public
    """,
    """
    drop trigger external_ai_operation_ledger_append_only
      on ai_audit.external_ai_operation_ledger
    """,
    """
    create trigger external_ai_operation_ledger_append_only
      before update or delete on ai_audit.external_ai_operation_ledger
      for each row execute function
        ai_audit.guard_external_ai_operation_terminal_transition()
    """,
)


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _STATEMENTS:
        connection.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260730_87 is forward-only: durable OCR dispatch and "
        "terminal audit authority cannot be weakened"
    )
