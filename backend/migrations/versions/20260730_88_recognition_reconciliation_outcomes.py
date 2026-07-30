"""Close stale OCR dispatches with explicit terminal and usage outcomes.

Revision ID: 20260730_88
Revises: 20260730_87
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "20260730_88"
down_revision: str | Sequence[str] | None = "20260730_87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    alter table ai_audit.external_processing_receipts
      add column receipt_phase text not null
        default 'authorized_dispatch_intent',
      add constraint external_processing_receipt_phase_check
        check (receipt_phase = 'authorized_dispatch_intent')
    """,
    """
    alter table ai_audit.external_ai_operation_ledger
      add column provider_dispatch_claimed_at timestamptz,
      add constraint external_ai_operation_dispatch_claim_time_check
        check (
          provider_dispatch_claimed_at is null
          or provider_dispatch_claimed_at >= started_at
        )
    """,
    """
    comment on column ai_audit.external_processing_receipts.receipt_phase is
      'append-only authorization intent recorded before provider I/O; '
      'join the owning ledger usage_state to determine observed dispatch outcome'
    """,
    """
    comment on column ai_audit.external_processing_receipts.dispatched_at is
      'time the authorized dispatch intent became durable; not standalone proof '
      'that request bytes reached or were accepted by the provider'
    """,
    """
    drop trigger if exists external_ai_operation_ledger_append_only
      on ai_audit.external_ai_operation_ledger
    """,
    """
    alter table ai_audit.external_ai_operation_ledger
      drop constraint if exists external_ai_operation_state_shape_v2_check,
      drop constraint if exists external_ai_operation_status_v2_check,
      drop constraint if exists external_ai_operation_usage_state_check,
      drop constraint if exists external_ai_operation_entitlement_state_check
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
           and pg_get_constraintdef(oid) ilike '%total_cost_cny%'
           and pg_get_constraintdef(oid) ilike '%unit_price_cny%'
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
    update ai_audit.external_ai_operation_ledger
       set status = 'outcome_unknown'
     where status = 'failed'
       and usage_state = 'unknown'
    """,
    """
    update ai_audit.external_ai_operation_ledger
       set entitlement_state = case
         when reservation_id is null then 'not_applicable'
         when status = 'running' then 'reserved'
         when status = 'succeeded' then 'consumed'
         else 'released'
       end
    """,
    """
    alter table ai_audit.external_ai_operation_ledger
      add constraint external_ai_operation_status_v3_check
        check (
          status in ('running', 'succeeded', 'failed', 'outcome_unknown')
        ),
      add constraint external_ai_operation_usage_state_v2_check
        check (
          usage_state in ('planned', 'actual', 'unknown', 'not_dispatched')
        ),
      add constraint external_ai_operation_entitlement_state_v2_check
        check (
          (
            reservation_id is null
            and reservation_source is null
            and entitlement_state = 'not_applicable'
          )
          or
          (
            reservation_id is not null
            and reservation_source in ('catalog', 'community_reward')
            and entitlement_state in ('reserved', 'consumed', 'released')
          )
        ),
      add constraint external_ai_operation_state_shape_v3_check
        check (
          (
            status = 'running'
            and usage_state = 'planned'
            and entitlement_state = case
              when reservation_id is null then 'not_applicable'
              else 'reserved'
            end
            and num_nonnulls(
              total_cost_cny, output_fingerprint, latency_ms,
              error_code, completed_at
            ) = 0
          )
          or
          (
            status = 'succeeded'
            and usage_state = 'actual'
            and entitlement_state = case
              when reservation_id is null then 'not_applicable'
              else 'consumed'
            end
            and total_cost_cny = round(unit_count * unit_price_cny, 8)
            and output_fingerprint is not null
            and latency_ms is not null
            and error_code is null
            and completed_at is not null
          )
          or
          (
            status = 'failed'
            and usage_state in ('actual', 'not_dispatched')
            and entitlement_state = case
              when reservation_id is null then 'not_applicable'
              else 'released'
            end
            and output_fingerprint is null
            and latency_ms is not null
            and coalesce(char_length(error_code), 0) > 0
            and completed_at is not null
            and (
              (
                usage_state = 'actual'
                and total_cost_cny = round(unit_count * unit_price_cny, 8)
              )
              or
              (usage_state = 'not_dispatched' and total_cost_cny = 0)
            )
          )
          or
          (
            status = 'outcome_unknown'
            and usage_state = 'unknown'
            and entitlement_state = case
              when reservation_id is null then 'not_applicable'
              else 'released'
            end
            and total_cost_cny is null
            and output_fingerprint is null
            and latency_ms is not null
            and coalesce(char_length(error_code), 0) > 0
            and completed_at is not null
          )
        )
    """,
    """
    comment on column ai_audit.external_ai_operation_ledger.usage_state is
      'planned before dispatch, not_dispatched for proven local rejection, '
      'actual after a provider response is observed and cost is conservatively '
      'attributed, unknown when provider outcome is unknowable'
    """,
    """
    comment on column
      ai_audit.external_ai_operation_ledger.provider_dispatch_claimed_at is
      'durable server-side fence claimed immediately before provider client '
      'construction; resets reconciliation age but is not proof of network egress'
    """,
    """
    comment on column ai_audit.external_ai_operation_ledger.entitlement_state is
      'quota lifecycle projection: reserved while running, consumed on success, '
      'released on failed or outcome_unknown, or not_applicable without reservation'
    """,
    """
    create or replace function
      ai_audit.guard_external_ai_operation_terminal_transition()
    returns trigger
    language plpgsql
    as $$
    begin
      if tg_op = 'DELETE' then
        raise exception using
          errcode = '55000',
          message = 'external AI operation ledger rows cannot be deleted';
      end if;
      if old.status = 'running' and new.status = 'running' then
        if old.provider_dispatch_claimed_at is not null
           or new.provider_dispatch_claimed_at is null
           or (
             to_jsonb(old) - 'provider_dispatch_claimed_at'
           ) is distinct from (
             to_jsonb(new) - 'provider_dispatch_claimed_at'
           ) then
          raise exception using
            errcode = '55000',
            message = 'external AI operation permits one provider dispatch claim';
        end if;
        return new;
      end if;
      if old.status <> 'running'
         or new.status not in ('succeeded', 'failed', 'outcome_unknown') then
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
        old.started_at, old.provider_dispatch_claimed_at
      ) is distinct from row(
        new.operation_id, new.operation_key, new.user_id, new.scenario_key,
        new.operation, new.responding_agent, new.impact_level, new.model_tier,
        new.deployment_id, new.evaluation_state, new.provider,
        new.provider_release, new.model, new.prompt_version,
        new.schema_version, new.price_version, new.billing_unit,
        new.unit_count, new.unit_price_cny, new.input_fingerprint,
        new.evidence_refs, new.reservation_id, new.reservation_source,
        new.started_at, new.provider_dispatch_claimed_at
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
    create trigger external_ai_operation_ledger_append_only
      before update or delete on ai_audit.external_ai_operation_ledger
      for each row execute function
        ai_audit.guard_external_ai_operation_terminal_transition()
    """,
    """
    create index external_ai_operation_running_reconciliation_idx
      on ai_audit.external_ai_operation_ledger (
        (coalesce(provider_dispatch_claimed_at, started_at)),
        operation_id
      )
      where status = 'running'
    """,
)


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _STATEMENTS:
        connection.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260730_88 is forward-only: OCR outcome reconciliation, "
        "zero-cost non-dispatch evidence and quota lifecycle cannot be weakened"
    )
