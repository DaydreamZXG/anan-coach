"""Add AI supervision preferences and WeChat subscription message facts.

Revision ID: 20260719_45
Revises: 20260719_44
"""

from alembic import op


revision = "20260719_45"
down_revision = "20260719_44"
branch_labels = None
depends_on = None


_TEST_STATEMENTS = (
    "create schema if not exists messaging",
    """
    create function messaging.reject_fact_mutation()
    returns trigger language plpgsql as $$
    begin
      if privacy.erasure_authorized(to_jsonb(old)) then
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end if;
      raise exception using errcode = '23514', message = 'messaging facts are append-only';
    end
    $$
    """,
    """
    create function messaging.enforce_grant_transition()
    returns trigger language plpgsql as $$
    begin
      if tg_op = 'DELETE' then
        if privacy.erasure_authorized(to_jsonb(old)) then return old; end if;
        raise exception using errcode = '23514',
          message = 'wechat subscription grants cannot be deleted';
      end if;
      if row(
        new.grant_id, new.user_id, new.authorization_event_id,
        new.template_code, new.template_id, new.accepted_at
      ) is distinct from row(
        old.grant_id, old.user_id, old.authorization_event_id,
        old.template_code, old.template_id, old.accepted_at
      ) then
        raise exception using errcode = '23514',
          message = 'wechat subscription grant identity is immutable';
      end if;
      if not (
        (old.state = 'available' and new.state in ('claimed', 'invalidated'))
        or (old.state = 'claimed' and new.state in ('consumed', 'invalidated'))
      ) then
        raise exception using errcode = '23514',
          message = 'invalid wechat subscription grant transition';
      end if;
      return new;
    end
    $$
    """,
    """
    create function messaging.enforce_delivery_transition()
    returns trigger language plpgsql as $$
    begin
      if tg_op = 'DELETE' then
        if privacy.erasure_authorized(to_jsonb(old)) then return old; end if;
        raise exception using errcode = '23514',
          message = 'coach notification deliveries cannot be deleted';
      end if;
      if row(
        new.delivery_id, new.decision_id, new.user_id, new.grant_id,
        new.template_release_id, new.template_id, new.category,
        new.page_path, new.payload, new.payload_fingerprint, new.created_at
      ) is distinct from row(
        old.delivery_id, old.decision_id, old.user_id, old.grant_id,
        old.template_release_id, old.template_id, old.category,
        old.page_path, old.payload, old.payload_fingerprint, old.created_at
      ) then
        raise exception using errcode = '23514',
          message = 'coach notification delivery identity is immutable';
      end if;
      if not (
        (old.state = 'queued' and new.state in ('sending', 'canceled'))
        or (old.state = 'sending' and new.state in (
          'queued', 'platform_accepted', 'platform_rejected',
          'failed', 'outcome_unknown', 'canceled'
        ))
      ) then
        raise exception using errcode = '23514',
          message = 'invalid coach notification delivery transition';
      end if;
      return new;
    end
    $$
    """,
    """
    create table messaging.coach_preference_versions (
      preference_version_id uuid primary key,
      command_id text not null check (char_length(command_id) between 8 and 240),
      request_fingerprint text not null check (request_fingerprint ~ '^[0-9a-f]{64}$'),
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      enabled boolean not null,
      task_reminders_enabled boolean not null,
      reflection_reminders_enabled boolean not null,
      retest_reminders_enabled boolean not null,
      mock_reminders_enabled boolean not null,
      weekly_summary_enabled boolean not null,
      receive_start time,
      receive_end time,
      quiet_enabled boolean not null,
      quiet_start time,
      quiet_end time,
      daily_limit integer not null check (daily_limit between 1 and 24),
      timezone text not null check (timezone = 'Asia/Shanghai'),
      source_type text not null check (source_type in ('system_default', 'user_confirmed', 'user_disabled')),
      supersedes_preference_version_id uuid,
      created_at timestamptz not null,
      unique (user_id, version_no),
      unique (user_id, command_id),
      unique (preference_version_id, user_id),
      foreign key (supersedes_preference_version_id, user_id)
        references messaging.coach_preference_versions(preference_version_id, user_id),
      check ((version_no = 1) = (supersedes_preference_version_id is null)),
      check (
        not enabled or (
          receive_start is not null and receive_end is not null
          and receive_start <> receive_end
          and (
            task_reminders_enabled or reflection_reminders_enabled
            or retest_reminders_enabled or mock_reminders_enabled
            or weekly_summary_enabled
          )
        )
      ),
      check (
        (quiet_enabled and quiet_start is not null and quiet_end is not null
          and quiet_start <> quiet_end)
        or (not quiet_enabled and quiet_start is null and quiet_end is null)
      ),
      check ((source_type = 'system_default') = (version_no = 1))
    )
    """,
    """
    create unique index coach_preference_one_successor_idx
      on messaging.coach_preference_versions(supersedes_preference_version_id)
      where supersedes_preference_version_id is not null
    """,
    """
    create index coach_preference_current_idx
      on messaging.coach_preference_versions(user_id, version_no desc)
    """,
    """
    create trigger coach_preference_versions_append_only
      before update or delete on messaging.coach_preference_versions
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create table messaging.coach_evaluation_cursors (
      user_id uuid primary key references learner.users(user_id),
      next_evaluation_at timestamptz not null,
      last_evaluated_at timestamptz,
      updated_at timestamptz not null
    )
    """,
    """
    create index coach_evaluation_due_idx
      on messaging.coach_evaluation_cursors(next_evaluation_at)
    """,
    """
    create table messaging.wechat_subscription_events (
      event_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      command_id text not null check (char_length(command_id) between 8 and 240),
      source text not null check (source in ('request_prompt', 'settings_snapshot')),
      template_code text not null check (template_code = 'coach_reminder'),
      template_id text not null check (char_length(template_id) between 1 and 200),
      result text not null check (result in ('accept', 'reject', 'ban', 'filter')),
      occurred_at timestamptz not null,
      unique (user_id, command_id, template_code),
      unique (event_id, user_id)
    )
    """,
    """
    create trigger wechat_subscription_events_append_only
      before update or delete on messaging.wechat_subscription_events
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create table messaging.wechat_subscription_grants (
      grant_id uuid primary key,
      user_id uuid not null,
      authorization_event_id uuid not null,
      template_code text not null check (template_code = 'coach_reminder'),
      template_id text not null,
      state text not null check (state in ('available', 'claimed', 'consumed', 'invalidated')),
      claimed_delivery_id uuid,
      accepted_at timestamptz not null,
      consumed_at timestamptz,
      invalidated_at timestamptz,
      unique (authorization_event_id),
      unique (grant_id, user_id),
      foreign key (authorization_event_id, user_id)
        references messaging.wechat_subscription_events(event_id, user_id),
      check (
        (state = 'available' and claimed_delivery_id is null
          and consumed_at is null and invalidated_at is null)
        or (state = 'claimed' and claimed_delivery_id is not null
          and consumed_at is null and invalidated_at is null)
        or (state = 'consumed' and claimed_delivery_id is not null
          and consumed_at is not null and invalidated_at is null)
        or (state = 'invalidated' and consumed_at is null and invalidated_at is not null)
      )
    )
    """,
    """
    create index wechat_subscription_grant_available_idx
      on messaging.wechat_subscription_grants(user_id, accepted_at)
      where state = 'available'
    """,
    """
    create table messaging.coach_notification_decisions (
      decision_id uuid primary key,
      decision_key text not null unique check (decision_key ~ '^[0-9a-f]{64}$'),
      user_id uuid not null references learner.users(user_id),
      preference_version_id uuid not null,
      local_date date not null,
      category text not null check (category in ('task', 'reflection', 'retest', 'mock', 'weekly_summary')),
      outcome text not null check (outcome in (
        'queued', 'suppressed_outside_window', 'suppressed_quiet_hours',
        'suppressed_daily_limit', 'suppressed_no_grant', 'suppressed_template_unavailable'
      )),
      policy_version text not null check (policy_version = 'coach-supervision-policy@1'),
      fact_fingerprint text not null check (fact_fingerprint ~ '^[0-9a-f]{64}$'),
      fact_refs jsonb not null check (
        jsonb_typeof(fact_refs) = 'array' and jsonb_array_length(fact_refs) > 0
      ),
      task_count integer not null check (task_count >= 0),
      grant_id uuid,
      decided_at timestamptz not null,
      unique (decision_id, user_id),
      foreign key (preference_version_id, user_id)
        references messaging.coach_preference_versions(preference_version_id, user_id),
      foreign key (grant_id, user_id)
        references messaging.wechat_subscription_grants(grant_id, user_id),
      check ((outcome = 'queued') = (grant_id is not null))
    )
    """,
    """
    create index coach_notification_decision_fact_idx
      on messaging.coach_notification_decisions(
        user_id, local_date, category, fact_fingerprint, decided_at desc
      )
    """,
    """
    create trigger coach_notification_decisions_append_only
      before update or delete on messaging.coach_notification_decisions
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create table messaging.coach_notification_deliveries (
      delivery_id uuid primary key,
      decision_id uuid not null,
      user_id uuid not null,
      grant_id uuid not null,
      template_release_id text not null,
      template_id text not null,
      category text not null check (category in ('task', 'reflection', 'retest', 'mock', 'weekly_summary')),
      state text not null check (state in (
        'queued', 'sending', 'platform_accepted', 'platform_rejected',
        'failed', 'outcome_unknown', 'canceled'
      )),
      page_path text not null check (
        page_path ~ '^pages/[a-z0-9-]+/index\\?notificationDeliveryId=[0-9a-f-]{36}$'
      ),
      payload jsonb not null check (jsonb_typeof(payload) = 'object'),
      payload_fingerprint text not null check (payload_fingerprint ~ '^[0-9a-f]{64}$'),
      platform_error_code integer,
      platform_error_message text,
      created_at timestamptz not null,
      accepted_at timestamptz,
      terminal_at timestamptz,
      unique (decision_id),
      unique (grant_id),
      unique (delivery_id, user_id),
      foreign key (decision_id, user_id)
        references messaging.coach_notification_decisions(decision_id, user_id),
      foreign key (grant_id, user_id)
        references messaging.wechat_subscription_grants(grant_id, user_id),
      check (
        (state in ('queued', 'sending') and accepted_at is null and terminal_at is null)
        or (state = 'platform_accepted' and accepted_at is not null and terminal_at is not null
          and platform_error_code is null)
        or (state in ('platform_rejected', 'failed', 'outcome_unknown', 'canceled')
          and accepted_at is null
          and terminal_at is not null)
      )
    )
    """,
    """
    alter table messaging.wechat_subscription_grants
      add constraint wechat_subscription_grant_delivery_fk
      foreign key (claimed_delivery_id, user_id)
      references messaging.coach_notification_deliveries(delivery_id, user_id)
      deferrable initially deferred
    """,
    """
    create trigger wechat_subscription_grants_transition
      before update or delete on messaging.wechat_subscription_grants
      for each row execute function messaging.enforce_grant_transition()
    """,
    """
    create trigger coach_notification_deliveries_transition
      before update or delete on messaging.coach_notification_deliveries
      for each row execute function messaging.enforce_delivery_transition()
    """,
    """
    create index coach_notification_delivery_user_idx
      on messaging.coach_notification_deliveries(user_id, created_at desc)
    """,
    """
    create table messaging.coach_notification_delivery_events (
      delivery_event_id uuid primary key,
      delivery_id uuid not null,
      user_id uuid not null,
      event_type text not null check (event_type in (
        'queued', 'sending', 'retry_scheduled', 'platform_accepted',
        'platform_rejected', 'failed', 'outcome_unknown', 'canceled'
      )),
      attempt_no integer not null check (attempt_no >= 0),
      evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
      occurred_at timestamptz not null,
      foreign key (delivery_id, user_id)
        references messaging.coach_notification_deliveries(delivery_id, user_id)
    )
    """,
    """
    create index coach_notification_delivery_event_idx
      on messaging.coach_notification_delivery_events(delivery_id, occurred_at)
    """,
    """
    create trigger coach_notification_delivery_events_append_only
      before update or delete on messaging.coach_notification_delivery_events
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create table messaging.coach_notification_open_events (
      open_event_id uuid primary key,
      delivery_id uuid not null,
      user_id uuid not null,
      command_id text not null check (char_length(command_id) between 8 and 240),
      opened_at timestamptz not null,
      unique (delivery_id),
      unique (user_id, command_id),
      foreign key (delivery_id, user_id)
        references messaging.coach_notification_deliveries(delivery_id, user_id)
    )
    """,
    """
    create trigger coach_notification_open_events_append_only
      before update or delete on messaging.coach_notification_open_events
      for each row execute function messaging.reject_fact_mutation()
    """,
)


def upgrade() -> None:
    for statement in _TEST_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("drop schema if exists messaging cascade")
