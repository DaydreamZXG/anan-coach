"""Publish free cold-start access and preserve usage-mode evidence.

Revision ID: 20260718_38
Revises: 20260718_37
"""

from typing import Sequence

from alembic import op


revision: str = "20260718_38"
down_revision: str | Sequence[str] | None = "20260718_37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UPDATE_GUARD_WITH_ACCESS_MODE = """
create or replace function commerce.enforce_feature_usage_reservation_update()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation history cannot be deleted';
  end if;
  if old.status <> 'reserved' then
    raise exception using
      errcode = '23514',
      message = 'terminal feature usage reservation is immutable';
  end if;
  if row(
    new.reservation_id, new.reservation_key, new.user_id, new.feature,
    new.usage_date, new.limit_count, new.usage_count_at_reservation,
    new.access_mode, new.business_key, new.reserved_at
  ) is distinct from row(
    old.reservation_id, old.reservation_key, old.user_id, old.feature,
    old.usage_date, old.limit_count, old.usage_count_at_reservation,
    old.access_mode, old.business_key, old.reserved_at
  ) then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation identity is immutable';
  end if;
  if old.job_id is not null and new.job_id is distinct from old.job_id then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation job binding is immutable';
  end if;
  return new;
end;
$$
"""

_UPDATE_GUARD_WITHOUT_ACCESS_MODE = """
create or replace function commerce.enforce_feature_usage_reservation_update()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation history cannot be deleted';
  end if;
  if old.status <> 'reserved' then
    raise exception using
      errcode = '23514',
      message = 'terminal feature usage reservation is immutable';
  end if;
  if row(
    new.reservation_id, new.reservation_key, new.user_id, new.feature,
    new.usage_date, new.limit_count, new.usage_count_at_reservation,
    new.business_key, new.reserved_at
  ) is distinct from row(
    old.reservation_id, old.reservation_key, old.user_id, old.feature,
    old.usage_date, old.limit_count, old.usage_count_at_reservation,
    old.business_key, old.reserved_at
  ) then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation identity is immutable';
  end if;
  if old.job_id is not null and new.job_id is distinct from old.job_id then
    raise exception using
      errcode = '23514',
      message = 'feature usage reservation job binding is immutable';
  end if;
  return new;
end;
$$
"""

_ESSAY_REVIEW_INTENT_STATE_WITH_ACCESS_MODE = """
alter table learning.essay_review_intent_versions
add constraint essay_review_intent_state_shape_check check (
  (status = 'pending' and
   num_nonnulls(
     job_id, quota_feature, quota_usage_date, quota_used,
     quota_access_mode, quota_limit, retry_after
   ) = 0)
  or
  (status = 'waiting_quota' and job_id is null and
   quota_feature = 'essay_review' and
   quota_access_mode = 'entitlement_quota' and
   num_nonnulls(
     quota_usage_date, quota_used, quota_access_mode, quota_limit, retry_after
   ) = 5 and quota_limit >= 0 and quota_used >= quota_limit)
  or
  (status = 'queued' and job_id is not null and
   quota_feature = 'essay_review' and retry_after is null and
   quota_access_mode in ('unlimited', 'entitlement_quota') and
   num_nonnulls(quota_usage_date, quota_used, quota_access_mode) = 3 and
   (
     (quota_access_mode = 'entitlement_quota' and
      quota_limit >= 1 and quota_used between 1 and quota_limit)
     or
     (quota_access_mode = 'unlimited' and
      quota_limit is null and quota_used >= 1)
   ))
)
"""

_ESSAY_REVIEW_INTENT_STATE_WITHOUT_ACCESS_MODE = """
alter table learning.essay_review_intent_versions
add constraint essay_review_intent_state_shape_check check (
  (status = 'pending' and
   num_nonnulls(
     job_id, quota_feature, quota_usage_date, quota_used, quota_limit, retry_after
   ) = 0)
  or
  (status = 'waiting_quota' and job_id is null and
   quota_feature = 'essay_review' and
   num_nonnulls(quota_usage_date, quota_used, quota_limit, retry_after) = 4 and
   quota_limit >= 0 and quota_used >= quota_limit)
  or
  (status = 'queued' and job_id is not null and
   quota_feature = 'essay_review' and retry_after is null and
   num_nonnulls(quota_usage_date, quota_used, quota_limit) = 3 and
   quota_limit >= 1 and quota_used between 1 and quota_limit)
)
"""

_TEST_STATEMENTS: tuple[str, ...] = (
    "alter table commerce.entitlement_usage_daily add column access_mode text "
    "not null default 'entitlement_quota'",
    "alter table commerce.entitlement_usage_daily add constraint "
    "entitlement_usage_daily_access_mode_check check "
    "(access_mode in ('unlimited', 'entitlement_quota'))",
    "alter table commerce.feature_usage_reservations add column access_mode text "
    "not null default 'entitlement_quota'",
    "alter table commerce.feature_usage_reservations add constraint "
    "feature_usage_reservation_access_mode_check check "
    "(access_mode in ('unlimited', 'entitlement_quota'))",
    "alter table commerce.feature_usage_reservations drop constraint "
    "feature_usage_reservation_count_check",
    "alter table commerce.feature_usage_reservations add constraint "
    "feature_usage_reservation_count_check check "
    "(usage_count_at_reservation >= 1 and "
    "(access_mode = 'unlimited' or usage_count_at_reservation <= limit_count))",
    _UPDATE_GUARD_WITH_ACCESS_MODE,
    "alter table learning.essay_review_intent_versions add column "
    "quota_access_mode text",
    "update learning.essay_review_intent_versions set "
    "quota_access_mode = 'entitlement_quota' where quota_feature is not null",
    "alter table learning.essay_review_intent_versions drop constraint "
    "essay_review_intent_state_shape_check",
    _ESSAY_REVIEW_INTENT_STATE_WITH_ACCESS_MODE,
)


def upgrade() -> None:
    for statement in _TEST_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1 from learning.essay_review_intent_versions
            where quota_access_mode = 'unlimited'
          ) then
            raise exception 'cannot remove unlimited essay review intent evidence';
          end if;
        end;
        $$
        """
    )
    op.execute(
        "alter table learning.essay_review_intent_versions drop constraint "
        "essay_review_intent_state_shape_check"
    )
    op.execute(_ESSAY_REVIEW_INTENT_STATE_WITHOUT_ACCESS_MODE)
    op.execute(
        "alter table learning.essay_review_intent_versions drop column "
        "quota_access_mode"
    )
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1 from commerce.feature_usage_reservations
            where access_mode = 'unlimited'
              and usage_count_at_reservation > limit_count
          ) then
            raise exception 'cannot remove unlimited access evidence with over-limit rows';
          end if;
        end;
        $$
        """
    )
    op.execute(_UPDATE_GUARD_WITHOUT_ACCESS_MODE)
    op.execute(
        "alter table commerce.feature_usage_reservations drop constraint "
        "feature_usage_reservation_count_check"
    )
    op.execute(
        "alter table commerce.feature_usage_reservations add constraint "
        "feature_usage_reservation_count_check check "
        "(usage_count_at_reservation between 1 and limit_count)"
    )
    op.execute(
        "alter table commerce.feature_usage_reservations drop constraint "
        "feature_usage_reservation_access_mode_check"
    )
    op.execute(
        "alter table commerce.feature_usage_reservations drop column access_mode"
    )
    op.execute(
        "alter table commerce.entitlement_usage_daily drop constraint "
        "entitlement_usage_daily_access_mode_check"
    )
    op.execute("alter table commerce.entitlement_usage_daily drop column access_mode")
