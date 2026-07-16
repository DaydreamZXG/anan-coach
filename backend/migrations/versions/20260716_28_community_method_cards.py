"""Add owner-separated community method cards, trials, and contribution rewards.

Revision ID: 20260716_28
Revises: 20260716_27
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_28"
down_revision: str | Sequence[str] | None = "20260716_27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_records(bind):
        raise RuntimeError(
            "Community method-card or reward facts exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    # Cards point at their immutable head version while versions point back to
    # their owning card. Break that intentional cycle before dropping either
    # side of the aggregate.
    op.drop_constraint(
        "community_method_card_head_fk",
        "community_method_cards",
        schema="product",
        type_="foreignkey",
    )
    for table, schema in _DROP_ORDER:
        op.drop_table(table, schema=schema)
    op.execute("drop function if exists commerce.reject_community_reward_history_mutation()")
    op.execute("drop function if exists product.reject_community_method_history_mutation()")


def _has_records(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "select "
                "exists (select 1 from product.community_method_cards) "
                "or exists (select 1 from product.community_method_card_versions) "
                "or exists (select 1 from product.community_method_card_trials) "
                "or exists (select 1 from commerce.community_reward_policies) "
                "or exists (select 1 from commerce.community_reward_awards) "
                "or exists (select 1 from commerce.community_reward_ledger_entries)"
            )
        ).scalar_one()
    )


_DROP_ORDER = (
    ("community_reward_ledger_entries", "commerce"),
    ("community_reward_cap_buckets", "commerce"),
    ("community_reward_awards", "commerce"),
    ("community_reward_policies", "commerce"),
    ("community_method_card_milestone_events", "product"),
    ("community_method_card_effect_projections", "product"),
    ("community_method_card_effect_policies", "product"),
    ("community_method_card_reports", "product"),
    ("community_method_card_votes", "product"),
    ("community_method_card_trials", "product"),
    ("community_method_card_exposures", "product"),
    ("community_method_card_review_checks", "product"),
    ("community_method_card_reviews", "product"),
    ("community_method_card_candidates", "product"),
    ("community_method_card_versions", "product"),
    ("community_method_cards", "product"),
)


_STATEMENTS = (
    """
    create or replace function product.reject_community_method_history_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception 'community method-card history is append-only';
    end;
    $$
    """,
    """
    create or replace function commerce.reject_community_reward_history_mutation()
    returns trigger language plpgsql as $$
    begin
      raise exception 'community reward history is append-only';
    end;
    $$
    """,
    """
    create table product.community_method_cards (
      card_id uuid primary key,
      author_user_id uuid not null references learner.users(user_id),
      track text not null check (track in ('xingce', 'essay')),
      state text not null check (state in (
        'private_draft', 'automated_review', 'trial', 'community_validated',
        'revision_required', 'rejected', 'restricted', 'retired'
      )),
      head_version_id uuid,
      state_version bigint not null default 1 check (state_version >= 1),
      created_at timestamptz not null,
      updated_at timestamptz not null
    )
    """,
    """
    create table product.community_method_card_versions (
      version_id uuid primary key,
      card_id uuid not null references product.community_method_cards(card_id),
      version_no bigint not null check (version_no >= 1),
      author_user_id uuid not null references learner.users(user_id),
      base_version_id uuid,
      source_type text not null check (source_type in (
        'text', 'voice_transcript', 'handwriting_extraction', 'reflection'
      )),
      source_artifact_id uuid,
      source_text_sha256 text not null check (length(source_text_sha256) = 64),
      private_source_text text not null check (char_length(private_source_text) >= 1),
      title text,
      recognition text,
      method_steps jsonb not null default '[]'::jsonb check (jsonb_typeof(method_steps) = 'array'),
      applicability jsonb not null default '[]'::jsonb check (jsonb_typeof(applicability) = 'array'),
      exclusions jsonb not null default '[]'::jsonb check (jsonb_typeof(exclusions) = 'array'),
      pitfalls jsonb not null default '[]'::jsonb check (jsonb_typeof(pitfalls) = 'array'),
      rights_declaration text check (rights_declaration in (
        'original', 'authorized_derivative', 'private_third_party'
      )),
      author_confirmed_at timestamptz,
      created_at timestamptz not null,
      unique (card_id, version_no),
      unique (version_id, card_id),
      foreign key (base_version_id, card_id)
        references product.community_method_card_versions(version_id, card_id),
      check (
        (version_no = 1 and base_version_id is null)
        or (version_no > 1 and base_version_id is not null)
      ),
      check (
        author_confirmed_at is null
        or (title is not null and recognition is not null and rights_declaration is not null
            and jsonb_array_length(method_steps) >= 1)
      )
    )
    """,
    """
    alter table product.community_method_cards
      add constraint community_method_card_head_fk
      foreign key (head_version_id, card_id)
      references product.community_method_card_versions(version_id, card_id)
      deferrable initially deferred
    """,
    """
    create unique index community_method_card_one_successor_idx
      on product.community_method_card_versions(base_version_id)
      where base_version_id is not null
    """,
    """
    create table product.community_method_card_candidates (
      candidate_id uuid primary key,
      card_id uuid not null,
      source_version_id uuid not null,
      author_user_id uuid not null references learner.users(user_id),
      prompt_version text not null,
      model_policy_version text not null,
      status text not null check (status in ('processing', 'ready', 'failed', 'expired')),
      candidate_payload jsonb,
      source_span_refs jsonb not null default '[]'::jsonb check (jsonb_typeof(source_span_refs) = 'array'),
      input_fingerprint text not null check (length(input_fingerprint) = 64),
      failure_code text,
      created_at timestamptz not null,
      completed_at timestamptz,
      unique (author_user_id, input_fingerprint),
      foreign key (source_version_id, card_id)
        references product.community_method_card_versions(version_id, card_id)
    )
    """,
    """
    create table product.community_method_card_reviews (
      review_id uuid primary key,
      card_id uuid not null,
      version_id uuid not null,
      policy_version text not null,
      status text not null check (status in ('trial', 'revision_required', 'rejected')),
      request_key text not null,
      request_fingerprint text not null check (length(request_fingerprint) = 64),
      created_at timestamptz not null,
      unique (version_id),
      unique (request_key),
      foreign key (version_id, card_id)
        references product.community_method_card_versions(version_id, card_id)
    )
    """,
    """
    create table product.community_method_card_review_checks (
      review_id uuid not null references product.community_method_card_reviews(review_id),
      dimension text not null check (dimension in (
        'duplicate', 'copyright', 'safety', 'knowledge', 'answer_consistency'
      )),
      outcome text not null check (outcome in ('passed', 'needs_revision', 'failed')),
      reason_code text not null,
      evidence_refs jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence_refs) = 'array'),
      created_at timestamptz not null,
      primary key (review_id, dimension)
    )
    """,
    """
    create table product.community_method_card_exposures (
      exposure_id uuid primary key,
      card_id uuid not null,
      version_id uuid not null,
      learner_user_id uuid not null references learner.users(user_id),
      applicability_snapshot jsonb not null check (jsonb_typeof(applicability_snapshot) = 'array'),
      ranking_policy_version text not null,
      opted_in_at timestamptz not null,
      unique (version_id, learner_user_id),
      foreign key (version_id, card_id)
        references product.community_method_card_versions(version_id, card_id)
    )
    """,
    """
    create table product.community_method_card_trials (
      trial_id uuid primary key,
      exposure_id uuid not null references product.community_method_card_exposures(exposure_id),
      card_id uuid not null,
      version_id uuid not null,
      learner_user_id uuid not null references learner.users(user_id),
      question_attempt_id text not null,
      question_version_id text not null,
      correct_before boolean,
      correct_after boolean not null,
      duration_before_ms integer check (duration_before_ms >= 0),
      duration_after_ms integer not null check (duration_after_ms >= 0),
      eligibility_status text not null check (eligibility_status in ('eligible', 'excluded')),
      exclusion_reason text,
      device_cluster_hash text not null check (length(device_cluster_hash) = 64),
      delayed_verification_due_at timestamptz,
      delayed_verification_outcome text check (delayed_verification_outcome in ('passed', 'failed', 'missed')),
      completed_at timestamptz not null,
      unique (version_id, learner_user_id, question_attempt_id),
      foreign key (version_id, card_id)
        references product.community_method_card_versions(version_id, card_id),
      check (
        (eligibility_status = 'eligible' and exclusion_reason is null)
        or (eligibility_status = 'excluded' and exclusion_reason is not null)
      )
    )
    """,
    """
    create table product.community_method_card_votes (
      vote_id uuid primary key,
      trial_id uuid not null unique references product.community_method_card_trials(trial_id),
      card_id uuid not null,
      version_id uuid not null,
      voter_user_id uuid not null references learner.users(user_id),
      vote text not null check (vote in ('helpful', 'not_helpful')),
      reason_code text not null check (reason_code in (
        'clear', 'transferable', 'too_specific', 'unclear', 'incorrect', 'unsafe'
      )),
      created_at timestamptz not null,
      unique (version_id, voter_user_id),
      foreign key (version_id, card_id)
        references product.community_method_card_versions(version_id, card_id)
    )
    """,
    """
    create table product.community_method_card_reports (
      report_id uuid primary key,
      card_id uuid not null,
      version_id uuid not null,
      reporter_user_id uuid not null references learner.users(user_id),
      category text not null check (category in (
        'copyright', 'incorrect', 'unsafe', 'advertising', 'contact_information', 'other_fixed'
      )),
      evidence_ref text not null,
      evidence_sha256 text not null check (length(evidence_sha256) = 64),
      decision text not null default 'pending' check (decision in ('pending', 'confirmed', 'rejected')),
      created_at timestamptz not null,
      unique (version_id, reporter_user_id, evidence_sha256),
      foreign key (version_id, card_id)
        references product.community_method_card_versions(version_id, card_id)
    )
    """,
    """
    create table product.community_method_card_effect_policies (
      policy_id uuid primary key,
      policy_version text not null unique,
      state text not null check (state in ('draft', 'published', 'retired')),
      rules jsonb not null check (jsonb_typeof(rules) = 'object'),
      rules_sha256 text not null check (length(rules_sha256) = 64),
      published_at timestamptz,
      created_at timestamptz not null,
      check ((state = 'published' and published_at is not null) or state <> 'published')
    )
    """,
    """
    create unique index community_method_card_one_effect_policy_idx
      on product.community_method_card_effect_policies ((state)) where state = 'published'
    """,
    """
    create table product.community_method_card_effect_projections (
      card_id uuid not null,
      version_id uuid not null,
      policy_id uuid not null references product.community_method_card_effect_policies(policy_id),
      eligible_trial_count bigint not null default 0 check (eligible_trial_count >= 0),
      delayed_verified_count bigint not null default 0 check (delayed_verified_count >= 0),
      helpful_count bigint not null default 0 check (helpful_count >= 0),
      not_helpful_count bigint not null default 0 check (not_helpful_count >= 0),
      conservative_score numeric(10, 6),
      risk_flags jsonb not null default '[]'::jsonb check (jsonb_typeof(risk_flags) = 'array'),
      evidence_cursor text not null,
      updated_at timestamptz not null,
      primary key (version_id, policy_id),
      foreign key (version_id, card_id)
        references product.community_method_card_versions(version_id, card_id)
    )
    """,
    """
    create table product.community_method_card_milestone_events (
      event_id uuid primary key,
      author_user_id uuid not null references learner.users(user_id),
      card_id uuid not null,
      version_id uuid not null,
      milestone text not null check (milestone in (
        'automated_review_passed', 'independent_evidence_sufficient',
        'conservative_effectiveness_reached', 'report_confirmed', 'official_derivation'
      )),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1
      ),
      occurred_at timestamptz not null,
      unique (version_id, milestone),
      foreign key (version_id, card_id)
        references product.community_method_card_versions(version_id, card_id)
    )
    """,
    """
    create table commerce.community_reward_policies (
      policy_id uuid primary key,
      policy_version text not null unique,
      milestone text not null,
      reward_unit text not null check (reward_unit in ('ai_quota', 'membership_seconds')),
      reward_value bigint not null check (reward_value > 0),
      author_period_cap bigint not null check (author_period_cap > 0),
      card_period_cap bigint not null check (card_period_cap > 0),
      milestone_period_cap bigint not null check (milestone_period_cap > 0),
      global_period_cap bigint not null check (global_period_cap > 0),
      period_kind text not null check (period_kind in ('day', 'month', 'campaign')),
      state text not null check (state in ('draft', 'published', 'retired')),
      rules_sha256 text not null check (length(rules_sha256) = 64),
      published_at timestamptz,
      created_at timestamptz not null,
      check ((state = 'published' and published_at is not null) or state <> 'published')
    )
    """,
    """
    create unique index community_reward_one_published_milestone_idx
      on commerce.community_reward_policies(milestone) where state = 'published'
    """,
    """
    create table commerce.community_reward_awards (
      award_id uuid primary key,
      event_id uuid not null unique references product.community_method_card_milestone_events(event_id),
      policy_id uuid not null references commerce.community_reward_policies(policy_id),
      beneficiary_user_id uuid not null references learner.users(user_id),
      card_id uuid not null,
      version_id uuid not null,
      milestone text not null,
      period_start date not null,
      reward_unit text not null check (reward_unit in ('ai_quota', 'membership_seconds')),
      granted_value bigint not null check (granted_value > 0),
      consumed_value bigint not null default 0 check (consumed_value >= 0),
      reversed_value bigint not null default 0 check (reversed_value >= 0),
      status text not null check (status in ('granted', 'partially_consumed', 'consumed', 'reversed')),
      evidence_refs jsonb not null check (jsonb_typeof(evidence_refs) = 'array'),
      entitlement_before jsonb not null check (jsonb_typeof(entitlement_before) = 'object'),
      entitlement_after jsonb not null check (jsonb_typeof(entitlement_after) = 'object'),
      cost_minor_units bigint not null default 0 check (cost_minor_units >= 0),
      created_at timestamptz not null,
      unique (policy_id, beneficiary_user_id, card_id, version_id, milestone, period_start),
      check (consumed_value + reversed_value <= granted_value)
    )
    """,
    """
    create table commerce.community_reward_cap_buckets (
      policy_id uuid not null references commerce.community_reward_policies(policy_id),
      dimension text not null check (dimension in ('author', 'card', 'milestone', 'global')),
      scope_key text not null,
      period_start date not null,
      granted_value bigint not null default 0 check (granted_value >= 0),
      limit_value bigint not null check (limit_value > 0),
      updated_at timestamptz not null,
      primary key (policy_id, dimension, scope_key, period_start),
      check (granted_value <= limit_value)
    )
    """,
    """
    create table commerce.community_reward_ledger_entries (
      ledger_entry_id uuid primary key,
      award_id uuid not null references commerce.community_reward_awards(award_id),
      beneficiary_user_id uuid not null references learner.users(user_id),
      entry_type text not null check (entry_type in ('grant', 'consume', 'reversal', 'expire')),
      reward_unit text not null check (reward_unit in ('ai_quota', 'membership_seconds')),
      value_delta bigint not null check (value_delta <> 0),
      idempotency_key text not null unique,
      reason_code text not null,
      balance_before bigint not null check (balance_before >= 0),
      balance_after bigint not null check (balance_after >= 0),
      evidence_refs jsonb not null check (jsonb_typeof(evidence_refs) = 'array'),
      created_at timestamptz not null,
      check (balance_after = balance_before + value_delta)
    )
    """,
    *(
        f"create trigger {table}_append_only before update or delete on {schema}.{table} "
        f"for each row execute function {function}()"
        for schema, table, function in (
            ("product", "community_method_card_versions", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_candidates", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_reviews", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_review_checks", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_exposures", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_trials", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_votes", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_reports", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_effect_policies", "product.reject_community_method_history_mutation"),
            ("product", "community_method_card_milestone_events", "product.reject_community_method_history_mutation"),
            ("commerce", "community_reward_policies", "commerce.reject_community_reward_history_mutation"),
            ("commerce", "community_reward_ledger_entries", "commerce.reject_community_reward_history_mutation"),
        )
    ),
)
