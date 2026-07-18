"""Add Stage 8 WeChat commerce, process rewards, retake, and data rights.

Revision ID: 20260717_35
Revises: 20260716_34
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260717_35"
down_revision: str | Sequence[str] | None = "20260716_34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _STATEMENTS:
        bind.exec_driver_sql(statement.replace("%", "%%"))
    _grant_runtime_access(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_stage8_records(bind):
        raise RuntimeError(
            "Stage 8 commercial or privacy records exist; destructive downgrade is "
            "forbidden. Repair the schema with a forward migration."
        )
    definitions = bind.execute(
        sa.text(
            "select definition from privacy.append_only_function_snapshots "
            "order by function_name"
        )
    ).scalars()
    for definition in definitions:
        bind.exec_driver_sql(str(definition))
    op.execute("drop function if exists privacy.erasure_authorized(jsonb)")
    for table in reversed(_PRIVACY_TABLES):
        op.drop_table(table, schema="privacy")
    op.execute("drop function if exists privacy.reject_support_access_mutation()")
    for table in reversed(_COMMERCE_TABLES):
        op.drop_table(table, schema="commerce")
    op.drop_table("retake_program_versions", schema="learning")
    op.drop_column("score_evidence", "erased_at", schema="learning")
    op.execute("drop function if exists commerce.enforce_stage8_state_transition()")
    op.execute("drop schema if exists privacy")
    op.execute(
        "delete from commerce.iap_products where product_id = "
        "'founder_experience_30d'"
    )


def _has_stage8_records(bind: Connection) -> bool:
    queries = [
        f"select 1 from commerce.{table}" for table in _COMMERCE_FACT_TABLES
    ] + [f"select 1 from privacy.{table}" for table in _PRIVACY_FACT_TABLES]
    queries.append("select 1 from learning.retake_program_versions")
    return bool(
        bind.execute(sa.text(f"select exists ({' union all '.join(queries)})"))
        .scalar_one()
    )


def _grant_runtime_access(bind: Connection) -> None:
    """Keep the application role's access aligned with owner-created objects.

    Production migrations run as ``gongkao_owner`` while the API and workers run
    as ``gongkao_api``.  New schemas therefore need both current-object grants and
    default privileges for future forward migrations.  Local/test databases do
    not necessarily define the production role, so the policy is conditional on
    that role existing.
    """
    runtime_role_exists = bind.execute(
        sa.text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
    ).scalar_one()
    if not runtime_role_exists:
        return

    application_schemas = (
        "learner",
        "learning",
        "platform",
        "privacy",
        "commerce",
        "product",
        "ai_audit",
    )
    preparer = bind.dialect.identifier_preparer
    for schema in application_schemas:
        bind.exec_driver_sql(f"grant usage on schema {schema} to gongkao_api")
        owned_relations = bind.execute(
            sa.text(
                """
                select c.relname, c.relkind
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = :schema
                  and c.relkind in ('r', 'p', 'S')
                  and pg_get_userbyid(c.relowner) = current_user
                order by c.relname
                """
            ),
            {"schema": schema},
        )
        for relation_name, relation_kind in owned_relations:
            qualified_name = (
                f"{preparer.quote_schema(schema)}."
                f"{preparer.quote(str(relation_name))}"
            )
            if relation_kind == "S":
                bind.exec_driver_sql(
                    "grant usage, select, update on sequence "
                    f"{qualified_name} to gongkao_api"
                )
            else:
                bind.exec_driver_sql(
                    "grant select, insert, update, delete on table "
                    f"{qualified_name} to gongkao_api"
                )
        bind.exec_driver_sql(
            "alter default privileges for role gongkao_owner in schema "
            f"{schema} grant select, insert, update, delete on tables to gongkao_api"
        )
        bind.exec_driver_sql(
            "alter default privileges for role gongkao_owner in schema "
            f"{schema} grant usage, select, update on sequences to gongkao_api"
        )

    bind.exec_driver_sql("grant select on public.alembic_version to gongkao_api")
    bind.exec_driver_sql(
        "revoke all privileges on learner.mock_exam_session_supersessions "
        "from gongkao_api"
    )
    bind.exec_driver_sql(
        "revoke all privileges on privacy.append_only_function_snapshots "
        "from gongkao_api"
    )


_COMMERCE_TABLES = (
    "commercial_policy_versions",
    "product_offer_versions",
    "wechat_orders",
    "founder_cohort_slots",
    "wechat_payment_notifications",
    "wechat_refunds",
    "entitlement_grants",
    "process_reward_awards",
    "weekly_reflection_confirmations",
)

_COMMERCE_FACT_TABLES = (
    "wechat_orders",
    "founder_cohort_slots",
    "wechat_payment_notifications",
    "wechat_refunds",
    "entitlement_grants",
    "process_reward_awards",
    "weekly_reflection_confirmations",
)

_PRIVACY_TABLES = (
    "policy_versions",
    "append_only_function_snapshots",
    "consent_events",
    "support_access_audit_events",
    "data_export_requests",
    "data_export_payloads",
    "account_deletion_requests",
    "deletion_events",
    "account_merge_requests",
)

_PRIVACY_FACT_TABLES = (
    "consent_events",
    "support_access_audit_events",
    "data_export_requests",
    "account_deletion_requests",
    "deletion_events",
    "account_merge_requests",
)


_STATEMENTS = (
    "create schema if not exists privacy",
    "create schema if not exists commerce",
    "create extension if not exists pgcrypto",
    """
    create table if not exists commerce.iap_products (
      product_row_id uuid primary key default gen_random_uuid(),
      platform text not null default 'ios',
      product_id text not null unique,
      tier text not null check (tier in ('free', 'pro')),
      billing_period text not null check (billing_period in ('monthly', 'yearly')),
      title text not null,
      subtitle text not null,
      price_band text not null,
      sort_order integer not null,
      feature_highlights jsonb not null default '[]'::jsonb,
      is_active boolean not null default true,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    )
    """,
    """
    create table if not exists commerce.user_entitlements (
      entitlement_id uuid primary key default gen_random_uuid(),
      user_id uuid not null unique references learner.users(user_id),
      tier text not null default 'free' check (tier in ('free', 'pro')),
      source text not null default 'free',
      subscription_start timestamptz,
      subscription_end timestamptz,
      product_ids jsonb not null default '[]'::jsonb,
      features jsonb not null default '{}'::jsonb,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    )
    """,
    """
    create table if not exists commerce.app_store_transactions (
      transaction_row_id uuid primary key default gen_random_uuid(),
      user_id uuid not null references learner.users(user_id),
      product_id text not null references commerce.iap_products(product_id),
      original_transaction_id text not null,
      transaction_id text not null unique,
      web_order_line_item_id text,
      environment text not null,
      purchase_date timestamptz not null,
      expires_date timestamptz,
      revocation_date timestamptz,
      signed_transaction_info text not null,
      verification_status text not null check (
        verification_status in (
          'verified_active', 'verified_expired', 'verified_revoked'
        )
      ),
      created_at timestamptz not null default now()
    )
    """,
    """
    create table if not exists commerce.app_store_notification_events (
      event_id uuid primary key default gen_random_uuid(),
      notification_uuid text not null unique,
      notification_type text not null,
      subtype text,
      signed_payload text not null,
      processed_at timestamptz,
      created_at timestamptz not null default now()
    )
    """,
    """
    create table if not exists commerce.entitlement_usage_daily (
      user_id uuid not null references learner.users(user_id),
      feature text not null,
      usage_date date not null,
      used_count integer not null default 0,
      limit_count integer not null,
      updated_at timestamptz not null default now(),
      primary key (user_id, feature, usage_date)
    )
    """,
    """
    create table if not exists commerce.entitlement_audit_logs (
      audit_log_id uuid primary key default gen_random_uuid(),
      user_id uuid not null references learner.users(user_id),
      action text not null,
      feature text,
      allowed boolean,
      metadata jsonb not null default '{}'::jsonb,
      created_at timestamptz not null default now()
    )
    """,
    """
    create index if not exists commerce_app_store_transactions_user_idx
      on commerce.app_store_transactions(user_id, created_at desc)
    """,
    """
    create index if not exists commerce_app_store_transactions_original_idx
      on commerce.app_store_transactions(original_transaction_id, created_at desc)
    """,
    """
    create index if not exists commerce_app_store_notification_events_created_idx
      on commerce.app_store_notification_events(created_at desc)
    """,
    """
    create index if not exists commerce_entitlement_audit_logs_user_idx
      on commerce.entitlement_audit_logs(user_id, created_at desc)
    """,
    "alter table learning.score_evidence add column erased_at timestamptz",
    """
    insert into commerce.iap_products (
      product_row_id, platform, product_id, tier, billing_period, title,
      subtitle, price_band, sort_order, feature_highlights, is_active,
      created_at, updated_at
    ) values (
      gen_random_uuid(), 'wechat', 'founder_experience_30d', 'pro', 'monthly',
      '创始体验官 30 天',
      '单次购买、不自动续费；退款与考试成绩完全脱钩。',
      'CNY_VERSIONED_OFFER', 5,
      '["30 个自然日完整服务", "20 个有效学习日赠 15 天陪练", "4 次周复盘返 30 元学习奖励", "完成 80% 核心任务解锁专项诊断"]'::jsonb,
      true, now(), now()
    ) on conflict (product_id) do update set
      platform = excluded.platform,
      tier = excluded.tier,
      billing_period = excluded.billing_period,
      title = excluded.title,
      subtitle = excluded.subtitle,
      price_band = excluded.price_band,
      sort_order = excluded.sort_order,
      feature_highlights = excluded.feature_highlights,
      is_active = true,
      updated_at = now()
    """,
    """
    create table commerce.commercial_policy_versions (
      policy_version text primary key,
      policy_kind text not null check (
        policy_kind in ('service_terms', 'refund', 'process_reward', 'marketing')
      ),
      title text not null check (char_length(trim(title)) > 0),
      content jsonb not null check (jsonb_typeof(content) = 'object'),
      content_sha256 text not null check (length(content_sha256) = 64),
      state text not null check (state in ('active', 'retired')),
      published_at timestamptz not null,
      retired_at timestamptz,
      check (
        (state = 'active' and retired_at is null)
        or (state = 'retired' and retired_at is not null)
      )
    )
    """,
    """
    create unique index commerce_commercial_policy_active_kind_idx
      on commerce.commercial_policy_versions(policy_kind)
      where state = 'active'
    """,
    """
    insert into commerce.commercial_policy_versions (
      policy_version, policy_kind, title, content, content_sha256, state,
      published_at, retired_at
    ) values
    (
      'founder-service-terms@v1', 'service_terms', '创始体验官服务条款',
      '{"serviceDays":30,"firstPurchaseOnly":true,"automaticRenewal":false,"cohortLimit":30,"serviceContent":["岗位与备考规划","双科训练与复盘","申论评阅","模考分析","岸岸学习辅导"]}'::jsonb,
      encode(digest(convert_to('{"serviceDays":30,"firstPurchaseOnly":true,"automaticRenewal":false,"cohortLimit":30,"serviceContent":["岗位与备考规划","双科训练与复盘","申论评阅","模考分析","岸岸学习辅导"]}', 'UTF8'), 'sha256'), 'hex'),
      'active', now(), null
    ),
    (
      'founder-refund@v1', 'refund', '30 天无成绩条件退款规则',
      '{"windowDays":30,"reason":"not_suitable","scoreEvidenceRequired":false,"interviewRequired":false,"completionRateRequired":false,"assessmentCountRequired":false,"refundRoute":"original_payment_route","arrivalNotice":"微信支付受理后按原路到账，具体时间以支付渠道为准","priorCashRewardsCountTowardReturnedAmount":true}'::jsonb,
      encode(digest(convert_to('{"windowDays":30,"reason":"not_suitable","scoreEvidenceRequired":false,"interviewRequired":false,"completionRateRequired":false,"assessmentCountRequired":false,"refundRoute":"original_payment_route","arrivalNotice":"微信支付受理后按原路到账，具体时间以支付渠道为准","priorCashRewardsCountTowardReturnedAmount":true}', 'UTF8'), 'sha256'), 'hex'),
      'active', now(), null
    ),
    (
      'founder-process-reward@v1', 'process_reward', '学习过程奖励规则',
      '{"effectiveDay":{"requiredCount":20,"definition":"当日至少一项 mandatory 核心任务以 canonical 证据完成","rewardServiceDays":15},"weeklyReflection":{"requiredCount":4,"definition":"用户确认四个不同自然周的周报告复盘","rewardMinorUnits":3000},"coreCompletion":{"requiredRatio":0.8,"definition":"服务期内 mandatory 核心任务完成数除以已到期核心任务数","reward":"special_diagnostic"},"scoreFieldsExcluded":true}'::jsonb,
      encode(digest(convert_to('{"effectiveDay":{"requiredCount":20,"definition":"当日至少一项 mandatory 核心任务以 canonical 证据完成","rewardServiceDays":15},"weeklyReflection":{"requiredCount":4,"definition":"用户确认四个不同自然周的周报告复盘","rewardMinorUnits":3000},"coreCompletion":{"requiredRatio":0.8,"definition":"服务期内 mandatory 核心任务完成数除以已到期核心任务数","reward":"special_diagnostic"},"scoreFieldsExcluded":true}', 'UTF8'), 'sha256'), 'hex'),
      'active', now(), null
    ),
    (
      'founder-marketing@v1', 'marketing', '创始体验与二战计划宣传边界',
      '{"allowed":["付费体验 30 天，不适合可退","二战失分回收计划","学习过程奖励"],"prohibited":["不提分全退","每提 1 分返现","提分失败赔付","考试通过或上岸保证","平台与考生对赌"],"previousScoresUsedOnlyFor":["loss_structure","daily_training"]}'::jsonb,
      encode(digest(convert_to('{"allowed":["付费体验 30 天，不适合可退","二战失分回收计划","学习过程奖励"],"prohibited":["不提分全退","每提 1 分返现","提分失败赔付","考试通过或上岸保证","平台与考生对赌"],"previousScoresUsedOnlyFor":["loss_structure","daily_training"]}', 'UTF8'), 'sha256'), 'hex'),
      'active', now(), null
    )
    """,
    """
    create table commerce.product_offer_versions (
      offer_version_id uuid primary key,
      offer_code text not null check (char_length(offer_code) between 3 and 120),
      version_no integer not null check (version_no >= 1),
      product_id text not null references commerce.iap_products(product_id),
      channel text not null check (channel = 'wechat_jsapi'),
      currency text not null check (currency = 'CNY'),
      amount_minor integer not null check (amount_minor > 0),
      service_days integer not null check (service_days > 0),
      cohort_limit integer check (cohort_limit is null or cohort_limit > 0),
      service_terms_version text not null references commerce.commercial_policy_versions(policy_version),
      refund_policy_version text not null references commerce.commercial_policy_versions(policy_version),
      reward_policy_version text not null references commerce.commercial_policy_versions(policy_version),
      marketing_policy_version text not null references commerce.commercial_policy_versions(policy_version),
      state text not null check (state in ('draft', 'active', 'retired')),
      effective_from timestamptz not null,
      effective_until timestamptz,
      created_at timestamptz not null default now(),
      unique (offer_code, version_no),
      check (effective_until is null or effective_until > effective_from)
    )
    """,
    """
    create unique index commerce_product_offer_one_active_idx
      on commerce.product_offer_versions(offer_code)
      where state = 'active'
    """,
    """
    insert into commerce.product_offer_versions (
      offer_version_id, offer_code, version_no, product_id, channel, currency,
      amount_minor, service_days, cohort_limit, service_terms_version,
      refund_policy_version, reward_policy_version, marketing_policy_version,
      state, effective_from, effective_until
    ) values (
      '35000000-0000-0000-0000-000000000001', 'founder-experience-30d', 1,
      'founder_experience_30d', 'wechat_jsapi', 'CNY', 6900, 30, 30,
      'founder-service-terms@v1', 'founder-refund@v1',
      'founder-process-reward@v1', 'founder-marketing@v1',
      'active', now(), null
    )
    """,
    """
    create table commerce.wechat_orders (
      order_id uuid primary key,
      command_key text not null check (char_length(command_key) between 8 and 160),
      request_fingerprint text not null check (length(request_fingerprint) = 64),
      user_id uuid not null references learner.users(user_id),
      offer_version_id uuid not null references commerce.product_offer_versions(offer_version_id),
      out_trade_no text not null unique check (char_length(out_trade_no) between 8 and 32),
      payer_open_id text not null check (char_length(payer_open_id) between 8 and 128),
      currency text not null check (currency = 'CNY'),
      amount_minor integer not null check (amount_minor > 0),
      state text not null check (state in (
        'creating', 'awaiting_payment', 'paid', 'partially_refunded',
        'refund_processing', 'refunded', 'closed', 'payment_uncertain'
      )),
      terms_snapshot jsonb not null check (jsonb_typeof(terms_snapshot) = 'object'),
      terms_snapshot_sha256 text not null check (length(terms_snapshot_sha256) = 64),
      prepay_id text,
      provider_transaction_id text unique,
      provider_trade_state text,
      refunded_minor integer not null default 0 check (
        refunded_minor >= 0 and refunded_minor <= amount_minor
      ),
      expires_at timestamptz not null,
      paid_at timestamptz,
      closed_at timestamptz,
      updated_at timestamptz not null default now(),
      created_at timestamptz not null default now(),
      unique (user_id, command_key),
      check (
        (state = 'creating' and prepay_id is null and paid_at is null)
        or (state in ('awaiting_payment', 'closed', 'payment_uncertain') and paid_at is null)
        or (state in ('paid', 'partially_refunded', 'refund_processing', 'refunded') and paid_at is not null)
      )
    )
    """,
    """
    create index commerce_wechat_orders_user_created_idx
      on commerce.wechat_orders(user_id, created_at desc)
    """,
    """
    create table commerce.founder_cohort_slots (
      slot_id uuid primary key,
      offer_version_id uuid not null references commerce.product_offer_versions(offer_version_id),
      slot_number integer not null check (slot_number >= 1),
      user_id uuid not null references learner.users(user_id),
      order_id uuid not null unique references commerce.wechat_orders(order_id),
      state text not null check (state in ('reserved', 'consumed', 'released')),
      reserved_at timestamptz not null,
      consumed_at timestamptz,
      released_at timestamptz,
      check (
        (state = 'reserved' and consumed_at is null and released_at is null)
        or (state = 'consumed' and consumed_at is not null and released_at is null)
        or (state = 'released' and consumed_at is null and released_at is not null)
      )
    )
    """,
    """
    create unique index commerce_founder_slot_active_number_idx
      on commerce.founder_cohort_slots(offer_version_id, slot_number)
      where state in ('reserved', 'consumed')
    """,
    """
    create unique index commerce_founder_slot_active_user_idx
      on commerce.founder_cohort_slots(offer_version_id, user_id)
      where state in ('reserved', 'consumed')
    """,
    """
    create table commerce.wechat_payment_notifications (
      event_id uuid primary key,
      notification_id text not null unique,
      event_type text not null check (event_type in ('TRANSACTION.SUCCESS', 'REFUND.SUCCESS', 'REFUND.ABNORMAL', 'REFUND.CLOSED')),
      platform_serial text not null,
      body_sha256 text not null check (length(body_sha256) = 64),
      resource_type text not null,
      resource_snapshot jsonb not null check (jsonb_typeof(resource_snapshot) = 'object'),
      processed boolean not null,
      processing_error_code text,
      received_at timestamptz not null,
      processed_at timestamptz
    )
    """,
    """
    create table commerce.wechat_refunds (
      refund_id uuid primary key,
      command_key text not null check (char_length(command_key) between 8 and 160),
      request_fingerprint text not null check (length(request_fingerprint) = 64),
      order_id uuid not null references commerce.wechat_orders(order_id),
      user_id uuid not null references learner.users(user_id),
      out_refund_no text not null unique check (char_length(out_refund_no) between 8 and 32),
      refund_kind text not null check (refund_kind in ('not_suitable', 'weekly_reflection_reward', 'late_payment_reversal')),
      reason text not null check (char_length(trim(reason)) > 0),
      amount_minor integer not null check (amount_minor > 0),
      currency text not null check (currency = 'CNY'),
      policy_version text not null references commerce.commercial_policy_versions(policy_version),
      state text not null check (state in ('requested', 'processing', 'succeeded', 'closed', 'abnormal', 'provider_uncertain')),
      provider_refund_id text unique,
      provider_status text,
      requested_at timestamptz not null,
      processed_at timestamptz,
      updated_at timestamptz not null,
      unique (user_id, command_key),
      unique (order_id, refund_kind)
    )
    """,
    """
    create index commerce_wechat_refunds_order_idx
      on commerce.wechat_refunds(order_id, requested_at)
    """,
    """
    create table commerce.entitlement_grants (
      grant_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      source_type text not null check (source_type in ('wechat_purchase', 'process_reward')),
      source_ref text not null check (char_length(source_ref) between 8 and 240),
      product_id text not null references commerce.iap_products(product_id),
      starts_at timestamptz not null,
      ends_at timestamptz not null check (ends_at > starts_at),
      state text not null check (state in ('active', 'revoked', 'expired')),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) > 0
      ),
      created_at timestamptz not null,
      revoked_at timestamptz,
      revocation_reason text,
      unique (source_type, source_ref),
      check (
        (state = 'active' and revoked_at is null and revocation_reason is null)
        or (state in ('revoked', 'expired') and revoked_at is not null and char_length(trim(revocation_reason)) > 0)
      )
    )
    """,
    """
    create index commerce_entitlement_grants_user_active_idx
      on commerce.entitlement_grants(user_id, ends_at desc)
      where state = 'active'
    """,
    """
    create table commerce.weekly_reflection_confirmations (
      confirmation_id uuid primary key,
      command_key text not null,
      user_id uuid not null references learner.users(user_id),
      order_id uuid not null references commerce.wechat_orders(order_id),
      report_version_id uuid not null references learning.report_projection_versions(projection_version_id),
      week_start date not null,
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) > 0
      ),
      confirmed_at timestamptz not null,
      unique (user_id, command_key),
      unique (user_id, order_id, week_start)
    )
    """,
    """
    create table commerce.process_reward_awards (
      award_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      order_id uuid not null references commerce.wechat_orders(order_id),
      reward_type text not null check (reward_type in ('membership_15d', 'weekly_reflection_cny30', 'special_diagnostic')),
      state text not null check (state in ('eligible', 'processing', 'granted', 'failed', 'revoked')),
      policy_version text not null references commerce.commercial_policy_versions(policy_version),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) > 0
      ),
      grant_id uuid references commerce.entitlement_grants(grant_id),
      refund_id uuid references commerce.wechat_refunds(refund_id),
      granted_at timestamptz,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      unique (order_id, reward_type),
      check (
        (reward_type = 'membership_15d' and refund_id is null)
        or (reward_type = 'weekly_reflection_cny30' and grant_id is null)
        or (reward_type = 'special_diagnostic' and refund_id is null)
      )
    )
    """,
    """
    create table learning.retake_program_versions (
      program_version_id uuid primary key,
      program_id uuid not null,
      command_key text not null,
      user_id uuid not null references learner.users(user_id),
      source_score_evidence_id uuid not null references learning.score_evidence(evidence_id),
      source_exam_year integer not null check (source_exam_year between 2000 and 2100),
      target_exam_year integer not null check (target_exam_year = source_exam_year + 1),
      version_no integer not null check (version_no >= 1),
      state text not null check (state in ('active', 'completed', 'withdrawn')),
      loss_structure jsonb not null check (jsonb_typeof(loss_structure) = 'object'),
      score_use_boundary jsonb not null check (
        score_use_boundary @> '{"refund":false,"cashReward":false,"training":true}'::jsonb
      ),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) > 0
      ),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      supersedes_program_version_id uuid,
      created_at timestamptz not null,
      unique (user_id, command_key),
      unique (program_id, version_no),
      foreign key (supersedes_program_version_id, program_id, user_id)
        references learning.retake_program_versions(program_version_id, program_id, user_id),
      unique (program_version_id, program_id, user_id),
      check (
        (version_no = 1 and supersedes_program_version_id is null)
        or (version_no > 1 and supersedes_program_version_id is not null)
      )
    )
    """,
    """
    create unique index learning_retake_program_one_current_idx
      on learning.retake_program_versions(user_id)
      where state = 'active'
    """,
    """
    create table privacy.policy_versions (
      policy_version text primary key,
      title text not null,
      content jsonb not null check (jsonb_typeof(content) = 'object'),
      content_sha256 text not null check (length(content_sha256) = 64),
      state text not null check (state in ('active', 'retired')),
      published_at timestamptz not null,
      retired_at timestamptz
    )
    """,
    """
    create table privacy.append_only_function_snapshots (
      function_name text primary key,
      definition text not null
    )
    """,
    """
    create unique index privacy_policy_one_active_idx
      on privacy.policy_versions((content->>'policyKind'))
      where state = 'active'
    """,
    """
    insert into privacy.policy_versions (
      policy_version, title, content, content_sha256, state, published_at, retired_at
    ) values (
      'personal-data-control@v1', '个人数据查看、导出与删除规则',
      '{"policyKind":"personal_data_control","rawMaterialRetention":"账户存续期间；完成账户删除任务时清除","exportScope":["identity_profile","qualification_position","learning_evidence","original_material_metadata","ai_candidates_and_summaries","commerce_and_entitlements","audit_events"],"deleteScope":["identity_mapping","sessions","raw_objects","raw_expression","derived_profiles","ai_summaries","vector_and_search_mappings"],"financialRecords":"仅保留履行法定义务所需且与个人学习账户解除直接映射的账务事实"}'::jsonb,
      encode(digest(convert_to('{"policyKind":"personal_data_control","rawMaterialRetention":"账户存续期间；完成账户删除任务时清除","exportScope":["identity_profile","qualification_position","learning_evidence","original_material_metadata","ai_candidates_and_summaries","commerce_and_entitlements","audit_events"],"deleteScope":["identity_mapping","sessions","raw_objects","raw_expression","derived_profiles","ai_summaries","vector_and_search_mappings"],"financialRecords":"仅保留履行法定义务所需且与个人学习账户解除直接映射的账务事实"}', 'UTF8'), 'sha256'), 'hex'),
      'active', now(), null
    )
    """,
    """
    create table privacy.consent_events (
      consent_event_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      policy_version text not null references privacy.policy_versions(policy_version),
      action text not null check (action in ('accepted', 'withdrawn')),
      surface text not null check (char_length(surface) between 3 and 120),
      request_id text not null check (char_length(request_id) between 8 and 160),
      created_at timestamptz not null,
      unique (user_id, policy_version, action, request_id)
    )
    """,
    """
    create table privacy.support_access_audit_events (
      access_event_id uuid primary key,
      actor_subject text not null check (char_length(actor_subject) between 3 and 240),
      ticket_ref text not null check (char_length(ticket_ref) between 4 and 120),
      access_reason text not null check (char_length(access_reason) between 8 and 500),
      action text not null check (action = 'commerce_order_status_read'),
      target_ref_sha256 text not null check (length(target_ref_sha256) = 64),
      response_fields jsonb not null check (
        jsonb_typeof(response_fields) = 'array'
        and jsonb_array_length(response_fields) > 0
      ),
      occurred_at timestamptz not null
    )
    """,
    """
    create function privacy.reject_support_access_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception using errcode = '23514',
        message = 'support access audit events are append-only';
    end;
    $$
    """,
    """
    create trigger support_access_audit_events_append_only
      before update or delete on privacy.support_access_audit_events
      for each row execute function privacy.reject_support_access_mutation()
    """,
    """
    create table privacy.data_export_requests (
      export_request_id uuid primary key,
      command_key text not null,
      user_id uuid not null references learner.users(user_id),
      policy_version text not null references privacy.policy_versions(policy_version),
      state text not null check (state in ('queued', 'running', 'completed', 'failed', 'expired')),
      job_id uuid not null unique references platform.background_jobs(job_id),
      payload_sha256 text check (payload_sha256 is null or length(payload_sha256) = 64),
      byte_size bigint check (byte_size is null or byte_size >= 0),
      requested_at timestamptz not null,
      completed_at timestamptz,
      expires_at timestamptz,
      failure_code text,
      unique (user_id, command_key),
      check (
        (state = 'completed' and completed_at is not null and expires_at is not null and payload_sha256 is not null and byte_size is not null)
        or state <> 'completed'
      )
    )
    """,
    """
    create table privacy.data_export_payloads (
      export_request_id uuid primary key references privacy.data_export_requests(export_request_id),
      user_id uuid not null references learner.users(user_id),
      payload jsonb not null check (jsonb_typeof(payload) = 'object'),
      created_at timestamptz not null
    )
    """,
    """
    create table privacy.account_deletion_requests (
      deletion_request_id uuid primary key,
      command_key text not null,
      user_id uuid not null references learner.users(user_id),
      policy_version text not null references privacy.policy_versions(policy_version),
      confirmation_phrase_hash text not null check (length(confirmation_phrase_hash) = 64),
      state text not null check (state in ('queued', 'running', 'completed', 'failed')),
      job_id uuid not null unique references platform.background_jobs(job_id),
      deletion_manifest jsonb check (deletion_manifest is null or jsonb_typeof(deletion_manifest) = 'object'),
      requested_at timestamptz not null,
      completed_at timestamptz,
      failure_code text,
      unique (user_id, command_key)
    )
    """,
    """
    create table privacy.deletion_events (
      event_id uuid primary key,
      deletion_request_id uuid not null references privacy.account_deletion_requests(deletion_request_id),
      user_id uuid not null references learner.users(user_id),
      phase text not null check (phase in ('requested', 'objects_deleted', 'personal_rows_erased', 'identity_mapping_deleted', 'completed', 'failed')),
      evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
      created_at timestamptz not null
    )
    """,
    """
    create table privacy.account_merge_requests (
      merge_request_id uuid primary key,
      command_key text not null,
      primary_user_id uuid not null references learner.users(user_id),
      secondary_user_id uuid not null references learner.users(user_id),
      primary_session_id uuid not null references learner.app_sessions(session_id),
      secondary_session_id uuid not null references learner.app_sessions(session_id),
      state text not null check (state in ('preview', 'confirmed', 'processing', 'completed', 'conflict', 'failed')),
      conflict_summary jsonb not null check (jsonb_typeof(conflict_summary) = 'object'),
      merge_manifest jsonb check (merge_manifest is null or jsonb_typeof(merge_manifest) = 'object'),
      requested_at timestamptz not null,
      completed_at timestamptz,
      unique (primary_user_id, command_key),
      check (primary_user_id <> secondary_user_id)
    )
    """,
    """
    insert into privacy.append_only_function_snapshots(function_name, definition)
    select function_name, pg_get_functiondef(function_name::regprocedure)
    from unnest(array[
      'learning.reject_stage4_mutation()',
      'learning.reject_xingce_fact_mutation()',
      'learning.reject_external_learning_fact_mutation()',
      'learning.reject_learning_media_fact_mutation()',
      'learning.reject_essay_intelligence_mutation()',
      'platform.enforce_companion_paid_command_transition()'
    ]::text[]) as functions(function_name)
    """,
    """
    create function privacy.erasure_authorized(row_data jsonb)
    returns boolean
    language sql
    stable
    security definer
    set search_path = pg_catalog, privacy
    as $$
      select coalesce(current_setting('app.privacy_erasure_user_id', true), '') <> ''
        and coalesce(
          row_data->>'user_id',
          row_data->>'owner_user_id',
          row_data->>'learner_user_id'
        ) = current_setting('app.privacy_erasure_user_id', true)
        and exists (
          select 1 from privacy.account_deletion_requests request
          where request.user_id::text = current_setting(
            'app.privacy_erasure_user_id', true
          )
            and request.state = 'running'
        )
    $$
    """,
    """
    create or replace function learning.reject_stage4_mutation()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      raise exception using errcode = '23514',
        message = 'stage4 learning-brain records are append-only';
    end;
    $$
    """,
    """
    create or replace function learning.reject_xingce_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      raise exception using errcode = '23514',
        message = 'xingce learning facts are append-only';
    end;
    $$
    """,
    """
    create or replace function learning.reject_external_learning_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      raise exception using errcode = '23514',
        message = 'external-learning facts are append-only';
    end;
    $$
    """,
    """
    create or replace function learning.reject_learning_media_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      raise exception 'learning media facts are append-only';
    end;
    $$
    """,
    """
    create or replace function learning.reject_essay_intelligence_mutation()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      raise exception using errcode = '23514',
        message = tg_table_schema || '.' || tg_table_name || ' is append-only';
    end;
    $$
    """,
    """
    create or replace function platform.enforce_companion_paid_command_transition()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      if tg_op = 'DELETE' then
        raise exception using errcode = '23514',
          message = 'companion paid command history cannot be deleted';
      end if;
      if old.status in ('succeeded', 'failed') then
        raise exception using errcode = '23514',
          message = 'terminal companion paid command is immutable';
      end if;
      if row(
        new.command_id, new.user_id, new.feature, new.idempotency_key,
        new.request_fingerprint, new.request_contract_version,
        new.execution_payload, new.response_contract_version,
        new.reservation_key, new.provider_idempotency_key, new.created_at
      ) is distinct from row(
        old.command_id, old.user_id, old.feature, old.idempotency_key,
        old.request_fingerprint, old.request_contract_version,
        old.execution_payload, old.response_contract_version,
        old.reservation_key, old.provider_idempotency_key, old.created_at
      ) then
        raise exception using errcode = '23514',
          message = 'companion paid command input is immutable';
      end if;
      if new.delivery_count < old.delivery_count
         or new.provider_attempt_count < old.provider_attempt_count then
        raise exception using errcode = '23514',
          message = 'companion paid command counters cannot decrease';
      end if;
      if not (
        (old.status = 'processing' and new.status in (
          'processing', 'provider_uncertain', 'succeeded', 'failed'
        )) or (old.status = 'provider_uncertain' and new.status in (
          'provider_uncertain', 'processing'
        ))
      ) then
        raise exception using errcode = '23514',
          message = 'invalid companion paid command status transition';
      end if;
      return new;
    end;
    $$
    """,
    """
    create or replace function commerce.enforce_stage8_state_transition()
    returns trigger language plpgsql as $$
    begin
      if tg_op = 'DELETE' then
        raise exception using errcode = '23514', message = 'stage8 commercial facts cannot be deleted';
      end if;
      if tg_table_name = 'wechat_orders' then
        if row(new.order_id, new.user_id, new.offer_version_id, new.out_trade_no,
               new.currency, new.amount_minor, new.terms_snapshot,
               new.terms_snapshot_sha256, new.created_at)
           is distinct from
           row(old.order_id, old.user_id, old.offer_version_id, old.out_trade_no,
               old.currency, old.amount_minor, old.terms_snapshot,
               old.terms_snapshot_sha256, old.created_at) then
          raise exception using errcode = '23514', message = 'wechat order identity is immutable';
        end if;
        if not (
          (old.state = 'creating' and new.state in ('creating', 'awaiting_payment', 'paid', 'payment_uncertain', 'closed'))
          or (old.state = 'awaiting_payment' and new.state in ('awaiting_payment', 'paid', 'closed', 'payment_uncertain'))
          or (old.state = 'payment_uncertain' and new.state in ('payment_uncertain', 'awaiting_payment', 'paid', 'closed'))
          or (old.state = 'paid' and new.state in ('paid', 'partially_refunded', 'refund_processing', 'refunded'))
          or (old.state = 'partially_refunded' and new.state in ('partially_refunded', 'refund_processing', 'refunded'))
          or (old.state = 'refund_processing' and new.state in ('refund_processing', 'partially_refunded', 'refunded', 'paid'))
          or (old.state in ('closed', 'refunded') and new.state = old.state)
        ) then
          raise exception using errcode = '23514', message = 'invalid wechat order state transition';
        end if;
      elsif tg_table_name = 'wechat_refunds' then
        if row(new.refund_id, new.order_id, new.user_id, new.out_refund_no,
               new.refund_kind, new.amount_minor, new.currency, new.policy_version,
               new.requested_at)
           is distinct from
           row(old.refund_id, old.order_id, old.user_id, old.out_refund_no,
               old.refund_kind, old.amount_minor, old.currency, old.policy_version,
               old.requested_at) then
          raise exception using errcode = '23514', message = 'wechat refund identity is immutable';
        end if;
        if not (
          (old.state = 'requested' and new.state in ('requested', 'processing', 'provider_uncertain', 'succeeded', 'closed', 'abnormal'))
          or (old.state in ('processing', 'provider_uncertain') and new.state in ('processing', 'provider_uncertain', 'succeeded', 'closed', 'abnormal'))
          or (old.state in ('succeeded', 'closed', 'abnormal') and new.state = old.state)
        ) then
          raise exception using errcode = '23514', message = 'invalid wechat refund state transition';
        end if;
      elsif tg_table_name = 'founder_cohort_slots' then
        if old.state <> 'reserved' or new.state not in ('reserved', 'consumed', 'released') then
          raise exception using errcode = '23514', message = 'founder cohort slot is terminal';
        end if;
      elsif tg_table_name = 'entitlement_grants' then
        if old.state <> 'active' or new.state not in ('active', 'revoked', 'expired') then
          raise exception using errcode = '23514', message = 'entitlement grant is terminal';
        end if;
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger wechat_orders_enforce_transition
      before update or delete on commerce.wechat_orders
      for each row execute function commerce.enforce_stage8_state_transition()
    """,
    """
    create trigger wechat_refunds_enforce_transition
      before update or delete on commerce.wechat_refunds
      for each row execute function commerce.enforce_stage8_state_transition()
    """,
    """
    create trigger founder_cohort_slots_enforce_transition
      before update or delete on commerce.founder_cohort_slots
      for each row execute function commerce.enforce_stage8_state_transition()
    """,
    """
    create trigger entitlement_grants_enforce_transition
      before update or delete on commerce.entitlement_grants
      for each row execute function commerce.enforce_stage8_state_transition()
    """,
)
