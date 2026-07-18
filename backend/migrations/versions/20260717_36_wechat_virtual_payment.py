"""Move Mini Program digital membership to WeChat virtual payment.

Revision ID: 20260717_36
Revises: 20260717_35
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260717_36"
down_revision: str | Sequence[str] | None = "20260717_35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _STATEMENTS:
        bind.exec_driver_sql(statement.replace("%", "%%"))
    _grant_runtime_access()


_STATEMENTS = (
        """
        alter table commerce.product_offer_versions
          drop constraint if exists product_offer_versions_channel_check
        """,
        """
        alter table commerce.product_offer_versions
          add constraint product_offer_versions_channel_check
          check (channel in ('wechat_jsapi', 'wechat_virtual_payment'))
        """,
        """
        alter table commerce.wechat_orders
          add column payment_channel text not null default 'wechat_jsapi',
          add column client_platform text,
          add column virtual_payload_sha256 text,
          add column provider_order_type integer,
          add column provider_wx_order_id text,
          add column provider_channel_order_id text,
          add column provider_delivery_state text
        """,
        """
        alter table commerce.wechat_orders
          add constraint wechat_orders_payment_channel_check
            check (payment_channel in ('wechat_jsapi', 'wechat_virtual_payment')),
          add constraint wechat_orders_client_platform_check
            check (client_platform is null or client_platform in ('ios', 'android', 'harmony', 'windows')),
          add constraint wechat_orders_virtual_payload_hash_check
            check (virtual_payload_sha256 is null or length(virtual_payload_sha256) = 64),
          add constraint wechat_orders_virtual_shape_check
            check (
              (payment_channel = 'wechat_jsapi' and client_platform is null)
              or (payment_channel = 'wechat_virtual_payment' and client_platform is not null)
            )
        """,
        """
        create unique index commerce_wechat_orders_provider_wx_order_idx
          on commerce.wechat_orders(provider_wx_order_id)
          where provider_wx_order_id is not null
        """,
        """
        do $$
        begin
          if exists (
            select 1 from commerce.wechat_orders
            where payment_channel = 'wechat_jsapi'
              and state in ('creating', 'awaiting_payment', 'payment_uncertain', 'refund_processing')
          ) then
            raise exception using
              message = 'unsettled legacy JSAPI orders block virtual-payment cutover';
          end if;
        end
        $$
        """,
        """
        alter table commerce.founder_cohort_slots
          add column cohort_key text
        """,
        """
        update commerce.founder_cohort_slots slots
        set cohort_key = offers.offer_code
        from commerce.product_offer_versions offers
        where offers.offer_version_id = slots.offer_version_id
        """,
        """
        alter table commerce.founder_cohort_slots
          alter column cohort_key set not null,
          add constraint founder_cohort_slots_cohort_key_check
            check (char_length(cohort_key) between 3 and 120)
        """,
        "drop index if exists commerce.commerce_founder_slot_active_number_idx",
        "drop index if exists commerce.commerce_founder_slot_active_user_idx",
        """
        create unique index commerce_founder_slot_active_number_idx
          on commerce.founder_cohort_slots(cohort_key, slot_number)
          where state in ('reserved', 'consumed')
        """,
        """
        create unique index commerce_founder_slot_active_user_idx
          on commerce.founder_cohort_slots(cohort_key, user_id)
          where state in ('reserved', 'consumed')
        """,
        """
        alter table commerce.wechat_payment_notifications
          drop constraint if exists wechat_payment_notifications_event_type_check
        """,
        """
        alter table commerce.wechat_payment_notifications
          add constraint wechat_payment_notifications_event_type_check check (
            event_type in (
              'TRANSACTION.SUCCESS', 'REFUND.SUCCESS', 'REFUND.ABNORMAL', 'REFUND.CLOSED',
              'xpay_goods_deliver_notify', 'xpay_refund_notify',
              'xpay_subscribe_ios_refund_query_notify'
            )
          )
        """,
        """
        alter table commerce.wechat_payment_notifications
          add column response_snapshot jsonb,
          add constraint wechat_payment_notifications_response_snapshot_check
            check (
              response_snapshot is null
              or jsonb_typeof(response_snapshot) = 'object'
            )
        """,
        """
        update commerce.commercial_policy_versions
        set state = 'retired', retired_at = now()
        where policy_version = 'founder-refund@v1' and state = 'active'
        """,
        """
        insert into commerce.commercial_policy_versions (
          policy_version, policy_kind, title, content, content_sha256, state,
          published_at, retired_at
        ) values (
          'founder-refund@v2', 'refund', '30 天无成绩条件退款规则',
          '{"windowDays":30,"reason":"not_suitable","scoreEvidenceRequired":false,"interviewRequired":false,"completionRateRequired":false,"assessmentCountRequired":false,"androidRoute":"wechat_virtual_payment_original_route","iosRoute":"user_applies_in_app_store","iosDeveloperRecommendation":"recommend_refund_when_within_window","iosFinalDecisionOwner":"Apple","priorCashRewardsCountTowardReturnedAmount":true}'::jsonb,
          encode(digest(convert_to('{"windowDays":30,"reason":"not_suitable","scoreEvidenceRequired":false,"interviewRequired":false,"completionRateRequired":false,"assessmentCountRequired":false,"androidRoute":"wechat_virtual_payment_original_route","iosRoute":"user_applies_in_app_store","iosDeveloperRecommendation":"recommend_refund_when_within_window","iosFinalDecisionOwner":"Apple","priorCashRewardsCountTowardReturnedAmount":true}', 'UTF8'), 'sha256'), 'hex'),
          'active', now(), null
        )
        """,
        """
        update commerce.product_offer_versions
        set state = 'retired',
            effective_until = greatest(
              clock_timestamp(),
              effective_from + interval '1 microsecond'
            )
        where offer_code = 'founder-experience-30d' and state = 'active'
        """,
        """
        insert into commerce.product_offer_versions (
          offer_version_id, offer_code, version_no, product_id, channel, currency,
          amount_minor, service_days, cohort_limit, service_terms_version,
          refund_policy_version, reward_policy_version, marketing_policy_version,
          state, effective_from, effective_until
        ) values (
          '36000000-0000-0000-0000-000000000001', 'founder-experience-30d', 2,
          'founder_experience_30d', 'wechat_virtual_payment', 'CNY', 6900, 30, 30,
          'founder-service-terms@v1', 'founder-refund@v2',
          'founder-process-reward@v1', 'founder-marketing@v1',
          'active', now(), null
        )
        """,
)


def downgrade() -> None:
    bind = op.get_bind()
    has_virtual_facts = bind.execute(
        sa.text(
            "select exists (select 1 from commerce.wechat_orders "
            "where payment_channel = 'wechat_virtual_payment')"
        )
    ).scalar_one()
    if has_virtual_facts:
        raise RuntimeError(
            "Virtual-payment facts exist; destructive downgrade is forbidden. "
            "Apply a forward migration."
        )
    bind.exec_driver_sql(
        "delete from commerce.product_offer_versions where offer_version_id = "
        "'36000000-0000-0000-0000-000000000001'"
    )
    bind.exec_driver_sql(
        "update commerce.product_offer_versions set state = 'active', effective_until = null "
        "where offer_version_id = '35000000-0000-0000-0000-000000000001'"
    )
    bind.exec_driver_sql(
        "delete from commerce.commercial_policy_versions where policy_version = 'founder-refund@v2'"
    )
    bind.exec_driver_sql(
        "update commerce.commercial_policy_versions set state = 'active', retired_at = null "
        "where policy_version = 'founder-refund@v1'"
    )
    bind.exec_driver_sql(
        "alter table commerce.wechat_payment_notifications "
        "drop constraint if exists wechat_payment_notifications_event_type_check"
    )
    bind.exec_driver_sql(
        "alter table commerce.wechat_payment_notifications "
        "add constraint wechat_payment_notifications_event_type_check check ("
        "event_type in ('TRANSACTION.SUCCESS', 'REFUND.SUCCESS', "
        "'REFUND.ABNORMAL', 'REFUND.CLOSED'))"
    )
    bind.exec_driver_sql(
        "alter table commerce.wechat_payment_notifications "
        "drop column response_snapshot"
    )
    bind.exec_driver_sql(
        "drop index if exists commerce.commerce_wechat_orders_provider_wx_order_idx"
    )
    bind.exec_driver_sql(
        "alter table commerce.wechat_orders drop column provider_delivery_state, "
        "drop column provider_channel_order_id, drop column provider_wx_order_id, "
        "drop column provider_order_type, drop column virtual_payload_sha256, "
        "drop column client_platform, drop column payment_channel"
    )
    bind.exec_driver_sql(
        "drop index if exists commerce.commerce_founder_slot_active_number_idx"
    )
    bind.exec_driver_sql(
        "drop index if exists commerce.commerce_founder_slot_active_user_idx"
    )
    bind.exec_driver_sql(
        "alter table commerce.founder_cohort_slots drop column cohort_key"
    )
    bind.exec_driver_sql(
        "create unique index commerce_founder_slot_active_number_idx "
        "on commerce.founder_cohort_slots(offer_version_id, slot_number) "
        "where state in ('reserved', 'consumed')"
    )
    bind.exec_driver_sql(
        "create unique index commerce_founder_slot_active_user_idx "
        "on commerce.founder_cohort_slots(offer_version_id, user_id) "
        "where state in ('reserved', 'consumed')"
    )
    bind.exec_driver_sql(
        "alter table commerce.product_offer_versions "
        "drop constraint if exists product_offer_versions_channel_check"
    )
    bind.exec_driver_sql(
        "alter table commerce.product_offer_versions "
        "add constraint product_offer_versions_channel_check "
        "check (channel = 'wechat_jsapi')"
    )


def _grant_runtime_access() -> None:
    bind = op.get_bind()
    if not bind.execute(
        sa.text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
    ).scalar_one():
        return
    bind.exec_driver_sql(
        "grant select, insert, update on all tables in schema commerce to gongkao_api"
    )
