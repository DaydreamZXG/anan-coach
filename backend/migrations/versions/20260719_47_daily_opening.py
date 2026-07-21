"""Add daily opening, weather cache, and confirmation-gated coffee facts.

Revision ID: 20260719_47
Revises: 20260719_46
"""

from alembic import op


revision = "20260719_47"
down_revision = "20260719_46"
branch_labels = None
depends_on = None


_STATEMENTS = (
    """
    create table messaging.daily_opening_preference_versions (
      preference_version_id uuid primary key,
      command_id text not null check (char_length(command_id) between 8 and 240),
      request_fingerprint text not null check (request_fingerprint ~ '^[0-9a-f]{64}$'),
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      enabled boolean not null,
      weather_enabled boolean not null,
      coffee_prompt_enabled boolean not null,
      coffee_start time not null,
      coffee_end time not null,
      timezone text not null check (timezone = 'Asia/Shanghai'),
      source_type text not null check (
        source_type in ('system_default', 'user_confirmed', 'user_disabled')
      ),
      supersedes_preference_version_id uuid,
      created_at timestamptz not null,
      unique (user_id, version_no),
      unique (user_id, command_id),
      unique (preference_version_id, user_id),
      foreign key (supersedes_preference_version_id, user_id)
        references messaging.daily_opening_preference_versions(
          preference_version_id, user_id
        ),
      check (coffee_start < coffee_end),
      check ((version_no = 1) = (supersedes_preference_version_id is null)),
      check ((source_type = 'system_default') = (version_no = 1))
    )
    """,
    """
    create unique index daily_opening_preference_one_successor_idx
      on messaging.daily_opening_preference_versions(
        supersedes_preference_version_id
      ) where supersedes_preference_version_id is not null
    """,
    """
    create index daily_opening_preference_current_idx
      on messaging.daily_opening_preference_versions(user_id, version_no desc)
    """,
    """
    create trigger daily_opening_preference_append_only
      before update or delete on messaging.daily_opening_preference_versions
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create table messaging.daily_opening_location_versions (
      location_version_id uuid primary key,
      command_id text not null check (char_length(command_id) between 8 and 240),
      request_fingerprint text not null check (request_fingerprint ~ '^[0-9a-f]{64}$'),
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      label text not null check (char_length(label) between 1 and 120),
      latitude numeric(7, 3) not null check (latitude between -90 and 90),
      longitude numeric(8, 3) not null check (longitude between -180 and 180),
      source_type text not null check (source_type = 'wechat_choose_location'),
      supersedes_location_version_id uuid,
      created_at timestamptz not null,
      unique (user_id, version_no),
      unique (user_id, command_id),
      unique (location_version_id, user_id),
      foreign key (supersedes_location_version_id, user_id)
        references messaging.daily_opening_location_versions(
          location_version_id, user_id
        ),
      check ((version_no = 1) = (supersedes_location_version_id is null))
    )
    """,
    """
    create unique index daily_opening_location_one_successor_idx
      on messaging.daily_opening_location_versions(supersedes_location_version_id)
      where supersedes_location_version_id is not null
    """,
    """
    create trigger daily_opening_location_append_only
      before update or delete on messaging.daily_opening_location_versions
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create table messaging.weather_cache_entries (
      provider text not null check (provider = 'apple-weatherkit'),
      location_key text not null check (location_key ~ '^-?[0-9]+\\.[0-9]{3},-?[0-9]+\\.[0-9]{3}$'),
      language text not null,
      timezone text not null check (timezone = 'Asia/Shanghai'),
      summary jsonb not null check (jsonb_typeof(summary) = 'object'),
      summary_fingerprint text not null check (summary_fingerprint ~ '^[0-9a-f]{64}$'),
      read_at timestamptz not null,
      expires_at timestamptz not null,
      fetched_at timestamptz not null,
      primary key (provider, location_key, language, timezone),
      check (expires_at > fetched_at)
    )
    """,
    """
    create index weather_cache_expiry_idx
      on messaging.weather_cache_entries(expires_at)
    """,
    """
    create table messaging.luckin_connection_versions (
      connection_version_id uuid primary key,
      connection_id uuid not null,
      user_id uuid not null references learner.users(user_id),
      version_no bigint not null check (version_no >= 1),
      state text not null check (state in ('active', 'revoked', 'expired')),
      encrypted_bearer_token text,
      encryption_key_id text,
      token_fingerprint text check (token_fingerprint ~ '^[0-9a-f]{64}$'),
      expires_at timestamptz,
      supersedes_connection_version_id uuid,
      created_at timestamptz not null,
      unique (connection_id, version_no),
      unique (connection_version_id, user_id),
      foreign key (supersedes_connection_version_id, user_id)
        references messaging.luckin_connection_versions(
          connection_version_id, user_id
        ),
      check ((version_no = 1) = (supersedes_connection_version_id is null)),
      check (
        (state = 'active' and encrypted_bearer_token is not null
          and encryption_key_id is not null and token_fingerprint is not null
          and expires_at is not null)
        or (state <> 'active' and encrypted_bearer_token is null)
      )
    )
    """,
    """
    create unique index luckin_connection_one_successor_idx
      on messaging.luckin_connection_versions(supersedes_connection_version_id)
      where supersedes_connection_version_id is not null
    """,
    """
    create trigger luckin_connection_append_only
      before update or delete on messaging.luckin_connection_versions
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create table messaging.daily_opening_coffee_decisions (
      decision_id uuid primary key,
      command_id text not null check (char_length(command_id) between 8 and 240),
      user_id uuid not null references learner.users(user_id),
      local_date date not null,
      decision text not null check (decision = 'declined_today'),
      decided_at timestamptz not null,
      unique (user_id, command_id),
      unique (user_id, local_date, decision)
    )
    """,
    """
    create trigger daily_opening_coffee_decision_append_only
      before update or delete on messaging.daily_opening_coffee_decisions
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create table messaging.daily_opening_impressions (
      impression_id uuid primary key,
      command_id text not null check (char_length(command_id) between 8 and 240),
      user_id uuid not null references learner.users(user_id),
      local_date date not null,
      preference_version_id uuid not null,
      location_version_id uuid,
      plan_version_id uuid not null references learning.daily_plan_versions(plan_version_id),
      weather_fingerprint text check (weather_fingerprint ~ '^[0-9a-f]{64}$'),
      coffee_state text not null check (
        coffee_state in (
          'available', 'today_declined', 'disabled', 'outside_window',
          'not_connected', 'provider_unavailable'
        )
      ),
      displayed_at timestamptz not null,
      unique (user_id, local_date),
      unique (user_id, command_id),
      foreign key (preference_version_id, user_id)
        references messaging.daily_opening_preference_versions(
          preference_version_id, user_id
        ),
      foreign key (location_version_id, user_id)
        references messaging.daily_opening_location_versions(
          location_version_id, user_id
        )
    )
    """,
    """
    create trigger daily_opening_impression_append_only
      before update or delete on messaging.daily_opening_impressions
      for each row execute function messaging.reject_fact_mutation()
    """,
    """
    create function messaging.enforce_luckin_confirmation_transition()
    returns trigger language plpgsql as $$
    begin
      if tg_op = 'DELETE' then
        if privacy.erasure_authorized(to_jsonb(old)) then return old; end if;
        raise exception using errcode = '23514',
          message = 'luckin confirmations cannot be deleted';
      end if;
      if row(
        new.confirmation_id, new.user_id, new.preview_fingerprint,
        new.confirmed_at, new.expires_at
      ) is distinct from row(
        old.confirmation_id, old.user_id, old.preview_fingerprint,
        old.confirmed_at, old.expires_at
      ) then
        raise exception using errcode = '23514',
          message = 'luckin confirmation identity is immutable';
      end if;
      if old.used_at is not null or new.used_at is null then
        raise exception using errcode = '23514',
          message = 'luckin confirmation can be consumed exactly once';
      end if;
      return new;
    end
    $$
    """,
    """
    create table messaging.luckin_order_confirmations (
      confirmation_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      preview_fingerprint text not null check (preview_fingerprint ~ '^[0-9a-f]{64}$'),
      confirmed_at timestamptz not null,
      expires_at timestamptz not null,
      used_at timestamptz,
      check (expires_at > confirmed_at)
    )
    """,
    """
    create trigger luckin_confirmation_transition
      before update or delete on messaging.luckin_order_confirmations
      for each row execute function messaging.enforce_luckin_confirmation_transition()
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "drop trigger if exists luckin_confirmation_transition "
        "on messaging.luckin_order_confirmations"
    )
    op.execute("drop table if exists messaging.luckin_order_confirmations")
    op.execute("drop function if exists messaging.enforce_luckin_confirmation_transition()")
    op.execute(
        "drop trigger if exists daily_opening_impression_append_only "
        "on messaging.daily_opening_impressions"
    )
    op.execute("drop table if exists messaging.daily_opening_impressions")
    op.execute(
        "drop trigger if exists daily_opening_coffee_decision_append_only "
        "on messaging.daily_opening_coffee_decisions"
    )
    op.execute("drop table if exists messaging.daily_opening_coffee_decisions")
    op.execute(
        "drop trigger if exists luckin_connection_append_only "
        "on messaging.luckin_connection_versions"
    )
    op.execute("drop table if exists messaging.luckin_connection_versions")
    op.execute("drop table if exists messaging.weather_cache_entries")
    op.execute(
        "drop trigger if exists daily_opening_location_append_only "
        "on messaging.daily_opening_location_versions"
    )
    op.execute("drop table if exists messaging.daily_opening_location_versions")
    op.execute(
        "drop trigger if exists daily_opening_preference_append_only "
        "on messaging.daily_opening_preference_versions"
    )
    op.execute("drop table if exists messaging.daily_opening_preference_versions")
