"""Close production evidence, policy, and reward-lot gaps for community cards.

Revision ID: 20260716_30
Revises: 20260716_29
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_30"
down_revision: str | Sequence[str] | None = "20260716_29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_records(bind):
        raise RuntimeError(
            "Community production-closure facts or policy resources exist; destructive "
            "downgrade is forbidden. Repair the schema with a forward migration."
        )
    for table, schema in _DROP_ORDER:
        op.drop_table(table, schema=schema)
    for constraint, table, schema in _ADDED_CONSTRAINTS:
        op.drop_constraint(constraint, table, schema=schema, type_="unique")
    op.execute("drop function if exists commerce.validate_community_membership_activation()")
    op.execute("drop function if exists commerce.validate_community_ai_reservation_event()")
    op.execute("drop function if exists commerce.validate_community_ai_reservation()")
    op.execute("drop function if exists product.enforce_community_policy_resource_update()")


def _has_records(bind: Connection) -> bool:
    selects = " union all ".join(
        f"select 1 from {schema}.{table}" for table, schema in _DROP_ORDER
    )
    return bool(bind.execute(sa.text(f"select exists ({selects})")).scalar_one())


_DROP_ORDER = (
    ("community_reward_ai_usage_reservation_events", "commerce"),
    ("community_reward_ai_usage_reservations", "commerce"),
    ("community_reward_membership_activations", "commerce"),
    ("community_reward_policy_scopes", "commerce"),
    ("community_method_card_report_decisions", "product"),
    ("community_method_card_eligibility_decisions", "product"),
    ("community_method_card_trial_session_evidence", "product"),
    ("community_method_card_state_events", "product"),
    ("community_method_card_validation_bindings", "product"),
    ("community_method_card_source_bindings", "product"),
    ("community_method_card_policy_resources", "product"),
)


_ADDED_CONSTRAINTS = (
    (
        "community_method_card_versions_owner_key",
        "community_method_card_versions",
        "product",
    ),
    (
        "community_method_cards_owner_track_key",
        "community_method_cards",
        "product",
    ),
    (
        "community_method_card_trials_owner_key",
        "community_method_card_trials",
        "product",
    ),
    (
        "community_method_card_reports_owner_key",
        "community_method_card_reports",
        "product",
    ),
    (
        "xingce_reflection_versions_owner_key",
        "xingce_reflection_versions",
        "learning",
    ),
    (
        "learning_media_artifact_versions_owner_key",
        "learning_media_artifact_versions",
        "learning",
    ),
    (
        "community_reward_policies_unit_key",
        "community_reward_policies",
        "commerce",
    ),
    (
        "community_reward_awards_owner_unit_key",
        "community_reward_awards",
        "commerce",
    ),
    (
        "community_reward_ledger_owner_key",
        "community_reward_ledger_entries",
        "commerce",
    ),
)


_STATEMENTS = (
    """
    alter table product.community_method_card_versions
      add constraint community_method_card_versions_owner_key
      unique (version_id, card_id, author_user_id)
    """,
    """
    alter table product.community_method_cards
      add constraint community_method_cards_owner_track_key
      unique (card_id, author_user_id, track)
    """,
    """
    alter table product.community_method_card_trials
      add constraint community_method_card_trials_owner_key
      unique (trial_id, card_id, version_id, learner_user_id)
    """,
    """
    alter table product.community_method_card_reports
      add constraint community_method_card_reports_owner_key
      unique (report_id, card_id, version_id, reporter_user_id)
    """,
    """
    alter table learning.xingce_reflection_versions
      add constraint xingce_reflection_versions_owner_key
      unique (reflection_version_id, user_id)
    """,
    """
    alter table learning.learning_media_artifact_versions
      add constraint learning_media_artifact_versions_owner_key
      unique (artifact_version_id, user_id)
    """,
    """
    alter table commerce.community_reward_policies
      add constraint community_reward_policies_unit_key
      unique (policy_id, reward_unit)
    """,
    """
    alter table commerce.community_reward_awards
      add constraint community_reward_awards_owner_unit_key
      unique (award_id, beneficiary_user_id, reward_unit)
    """,
    """
    alter table commerce.community_reward_ledger_entries
      add constraint community_reward_ledger_owner_key
      unique (ledger_entry_id, award_id, beneficiary_user_id)
    """,
    """
    create table product.community_method_card_source_bindings (
      source_binding_id uuid primary key,
      card_id uuid not null,
      version_id uuid not null,
      owner_user_id uuid not null references learner.users(user_id),
      binding_kind text not null check (binding_kind in (
        'direct_text', 'source_artifact_version',
        'learning_media_reflection_version', 'xingce_reflection_version'
      )),
      source_role text not null check (source_role in ('primary', 'supporting')),
      artifact_version_id uuid,
      learning_media_artifact_version_id uuid,
      reflection_version_id uuid,
      direct_text_sha256 text,
      extraction_policy_version text,
      source_span_refs jsonb not null check (
        jsonb_typeof(source_span_refs) = 'array'
        and jsonb_array_length(source_span_refs) >= 1
      ),
      bound_at timestamptz not null,
      foreign key (version_id, card_id, owner_user_id)
        references product.community_method_card_versions(
          version_id, card_id, author_user_id
        ),
      foreign key (artifact_version_id, owner_user_id)
        references learning.source_artifact_versions(
          artifact_version_id, owner_user_id
        ),
      foreign key (learning_media_artifact_version_id, owner_user_id)
        references learning.learning_media_artifact_versions(
          artifact_version_id, user_id
        ),
      foreign key (reflection_version_id, owner_user_id)
        references learning.xingce_reflection_versions(
          reflection_version_id, user_id
        ),
      check (
        (binding_kind = 'direct_text' and artifact_version_id is null
          and learning_media_artifact_version_id is null
          and reflection_version_id is null
          and direct_text_sha256 ~ '^[0-9a-f]{64}$'
          and extraction_policy_version is null)
        or (binding_kind = 'source_artifact_version' and artifact_version_id is not null
          and learning_media_artifact_version_id is null
          and reflection_version_id is null and direct_text_sha256 is null
          and char_length(extraction_policy_version) between 3 and 160)
        or (binding_kind = 'learning_media_reflection_version'
          and artifact_version_id is null
          and learning_media_artifact_version_id is not null
          and reflection_version_id is not null and direct_text_sha256 is null
          and extraction_policy_version is null)
        or (binding_kind = 'xingce_reflection_version' and artifact_version_id is null
          and learning_media_artifact_version_id is null
          and reflection_version_id is not null and direct_text_sha256 is null
          and extraction_policy_version is null)
      )
    )
    """,
    """
    create unique index community_method_card_one_primary_source_idx
      on product.community_method_card_source_bindings(version_id)
      where source_role = 'primary'
    """,
    """
    create table product.community_method_card_validation_bindings (
      validation_binding_id uuid primary key,
      card_id uuid not null,
      version_id uuid not null unique,
      owner_user_id uuid not null references learner.users(user_id),
      track text not null check (track in ('xingce', 'essay')),
      validation_scope text not null check (validation_scope in (
        'canonical_question', 'canonical_task', 'general_method'
      )),
      knowledge_node_version_ref text not null
        check (char_length(knowledge_node_version_ref) between 3 and 240),
      canonical_question_id text,
      canonical_content_version_id text,
      canonical_task_version_ref text,
      answer_evidence_refs jsonb not null check (
        jsonb_typeof(answer_evidence_refs) = 'array'
      ),
      validation_policy_version text not null
        check (char_length(validation_policy_version) between 3 and 160),
      general_method_outcome text,
      bound_at timestamptz not null,
      foreign key (version_id, card_id, owner_user_id)
        references product.community_method_card_versions(
          version_id, card_id, author_user_id
        ),
      foreign key (card_id, owner_user_id, track)
        references product.community_method_cards(card_id, author_user_id, track),
      foreign key (canonical_question_id, canonical_content_version_id)
        references product.question_content_versions(question_id, content_version_id),
      check (
        (validation_scope = 'canonical_question'
          and canonical_question_id is not null
          and canonical_content_version_id is not null
          and canonical_task_version_ref is null
          and jsonb_array_length(answer_evidence_refs) >= 1
          and general_method_outcome is null)
        or (validation_scope = 'canonical_task'
          and canonical_question_id is null
          and canonical_content_version_id is null
          and char_length(canonical_task_version_ref) between 3 and 240
          and jsonb_array_length(answer_evidence_refs) >= 1
          and general_method_outcome is null)
        or (validation_scope = 'general_method'
          and canonical_question_id is null
          and canonical_content_version_id is null
          and canonical_task_version_ref is null
          and jsonb_array_length(answer_evidence_refs) = 0
          and general_method_outcome = 'needs_revision')
      )
    )
    """,
    """
    create table product.community_method_card_policy_resources (
      policy_resource_id uuid primary key,
      operation text not null check (operation in (
        'candidate_generation', 'automated_review', 'report_decision'
      )),
      policy_version text not null,
      state text not null check (state in ('draft', 'published', 'retired')),
      rules jsonb not null check (
        jsonb_typeof(rules) = 'object' and rules <> '{}'::jsonb
      ),
      rules_sha256 text not null check (rules_sha256 ~ '^[0-9a-f]{64}$'),
      prompt_version text not null check (char_length(prompt_version) between 3 and 160),
      provider text not null check (char_length(provider) between 2 and 120),
      model text not null check (char_length(model) between 2 and 160),
      provider_release_id text not null
        check (char_length(provider_release_id) between 3 and 200),
      output_schema_version text not null
        check (char_length(output_schema_version) between 3 and 160),
      published_at timestamptz,
      retired_at timestamptz,
      created_at timestamptz not null,
      unique (operation, policy_version),
      unique (policy_resource_id, operation),
      check (
        (state = 'draft' and published_at is null and retired_at is null)
        or (state = 'published' and published_at is not null and retired_at is null)
        or (state = 'retired' and published_at is not null and retired_at is not null
          and retired_at >= published_at)
      )
    )
    """,
    """
    create unique index community_method_card_one_published_operation_idx
      on product.community_method_card_policy_resources(operation)
      where state = 'published'
    """,
    """
    create function product.enforce_community_policy_resource_update()
    returns trigger language plpgsql as $$
    begin
      if tg_op = 'DELETE' then
        raise exception using errcode = '23514',
          message = 'community policy resources cannot be deleted';
      end if;
      if row(new.policy_resource_id, new.operation, new.policy_version, new.rules,
          new.rules_sha256, new.prompt_version, new.provider, new.model,
          new.provider_release_id, new.output_schema_version, new.created_at)
        is distinct from
        row(old.policy_resource_id, old.operation, old.policy_version, old.rules,
          old.rules_sha256, old.prompt_version, old.provider, old.model,
          old.provider_release_id, old.output_schema_version, old.created_at) then
        raise exception using errcode = '23514',
          message = 'community policy resource contents are immutable';
      end if;
      if not ((old.state = 'draft' and new.state = 'published'
          and old.published_at is null and new.published_at is not null
          and new.retired_at is null)
        or (old.state = 'published' and new.state = 'retired'
          and new.published_at = old.published_at and new.retired_at is not null)) then
        raise exception using errcode = '23514',
          message = 'invalid community policy lifecycle transition';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger community_method_policy_resource_lifecycle
      before update or delete on product.community_method_card_policy_resources
      for each row execute function product.enforce_community_policy_resource_update()
    """,
    """
    create table product.community_method_card_state_events (
      state_event_id uuid primary key,
      card_id uuid not null references product.community_method_cards(card_id),
      version_id uuid,
      state_version bigint not null check (state_version >= 1),
      from_state text check (from_state in (
        'private_draft', 'automated_review', 'trial', 'community_validated',
        'revision_required', 'rejected', 'restricted', 'retired'
      )),
      to_state text not null check (to_state in (
        'private_draft', 'automated_review', 'trial', 'community_validated',
        'revision_required', 'rejected', 'restricted', 'retired'
      )),
      actor_kind text not null check (actor_kind in ('author', 'moderator', 'system')),
      actor_user_id uuid references learner.users(user_id),
      reason_code text not null check (char_length(reason_code) between 2 and 120),
      authority_version text not null check (char_length(authority_version) between 3 and 160),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1
      ),
      occurred_at timestamptz not null,
      unique (card_id, state_version),
      foreign key (version_id, card_id)
        references product.community_method_card_versions(version_id, card_id),
      check ((state_version = 1) = (from_state is null)),
      check (from_state is null or from_state <> to_state),
      check ((actor_kind = 'system') = (actor_user_id is null))
    )
    """,
    """
    create table product.community_method_card_trial_session_evidence (
      session_evidence_id uuid primary key,
      trial_id uuid not null,
      card_id uuid not null,
      version_id uuid not null,
      learner_user_id uuid not null references learner.users(user_id),
      server_session_kind text not null check (server_session_kind in (
        'xingce_task_session', 'essay_attempt_session', 'community_trial_session'
      )),
      server_session_id text not null check (char_length(server_session_id) between 8 and 240),
      session_evidence_sha256 text not null check (session_evidence_sha256 ~ '^[0-9a-f]{64}$'),
      attestation_policy_version text not null
        check (char_length(attestation_policy_version) between 3 and 160),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1
      ),
      recorded_at timestamptz not null,
      unique (trial_id),
      unique (session_evidence_id, trial_id, card_id, version_id, learner_user_id),
      foreign key (trial_id, card_id, version_id, learner_user_id)
        references product.community_method_card_trials(
          trial_id, card_id, version_id, learner_user_id
        )
    )
    """,
    """
    create unique index community_method_card_trial_server_session_idx
      on product.community_method_card_trial_session_evidence(
        server_session_kind, server_session_id, learner_user_id
      )
    """,
    """
    create table product.community_method_card_eligibility_decisions (
      eligibility_decision_id uuid primary key,
      session_evidence_id uuid not null,
      trial_id uuid not null unique,
      card_id uuid not null,
      version_id uuid not null,
      learner_user_id uuid not null references learner.users(user_id),
      actor_cluster_hash text not null check (actor_cluster_hash ~ '^[0-9a-f]{64}$'),
      cluster_policy_version text not null
        check (char_length(cluster_policy_version) between 3 and 160),
      decision text not null check (decision in ('eligible', 'excluded')),
      reason_code text,
      decision_snapshot jsonb not null check (jsonb_typeof(decision_snapshot) = 'object'),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1
      ),
      decided_at timestamptz not null,
      foreign key (
        session_evidence_id, trial_id, card_id, version_id, learner_user_id
      ) references product.community_method_card_trial_session_evidence(
        session_evidence_id, trial_id, card_id, version_id, learner_user_id
      ),
      check (
        (decision = 'eligible' and reason_code is null)
        or (decision = 'excluded' and char_length(reason_code) between 2 and 120)
      )
    )
    """,
    """
    create table product.community_method_card_report_decisions (
      report_decision_id uuid primary key,
      report_id uuid not null,
      card_id uuid not null,
      version_id uuid not null,
      reporter_user_id uuid not null references learner.users(user_id),
      decision_version bigint not null check (decision_version >= 1),
      supersedes_report_decision_id uuid,
      decision text not null check (decision in ('confirmed', 'rejected')),
      reason_code text not null check (char_length(reason_code) between 2 and 120),
      policy_resource_id uuid not null,
      policy_operation text not null default 'report_decision'
        check (policy_operation = 'report_decision'),
      reporter_is_beneficiary boolean not null,
      beneficiary_user_id uuid references learner.users(user_id),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1
      ),
      actor_ref text not null check (char_length(actor_ref) between 3 and 200),
      decided_at timestamptz not null,
      unique (report_id, decision_version),
      unique (report_decision_id, report_id),
      foreign key (report_id, card_id, version_id, reporter_user_id)
        references product.community_method_card_reports(
          report_id, card_id, version_id, reporter_user_id
        ),
      foreign key (supersedes_report_decision_id, report_id)
        references product.community_method_card_report_decisions(
          report_decision_id, report_id
        ),
      foreign key (policy_resource_id, policy_operation)
        references product.community_method_card_policy_resources(
          policy_resource_id, operation
        ),
      check (
        (decision_version = 1 and supersedes_report_decision_id is null)
        or (decision_version > 1 and supersedes_report_decision_id is not null)
      ),
      check (
        (reporter_is_beneficiary and beneficiary_user_id = reporter_user_id)
        or (not reporter_is_beneficiary and beneficiary_user_id is null)
      )
    )
    """,
    """
    create unique index community_method_report_decision_one_successor_idx
      on product.community_method_card_report_decisions(supersedes_report_decision_id)
      where supersedes_report_decision_id is not null
    """,
    """
    create table commerce.community_reward_policy_scopes (
      scope_resource_id uuid primary key,
      policy_id uuid not null,
      reward_unit text not null check (reward_unit in ('ai_quota', 'membership_seconds')),
      scope_version text not null check (char_length(scope_version) between 3 and 160),
      scope_kind text not null check (scope_kind in ('ai_features', 'membership_activation')),
      eligible_ai_features jsonb,
      membership_product_ref text,
      membership_activation_contract_version text,
      membership_cost_policy_version text,
      cost_metadata jsonb,
      rules_sha256 text not null check (rules_sha256 ~ '^[0-9a-f]{64}$'),
      created_at timestamptz not null,
      unique (policy_id),
      unique (scope_resource_id, policy_id, reward_unit, scope_kind),
      foreign key (policy_id, reward_unit)
        references commerce.community_reward_policies(policy_id, reward_unit),
      check (
        (scope_kind = 'ai_features' and reward_unit = 'ai_quota'
          and jsonb_typeof(eligible_ai_features) = 'array'
          and jsonb_array_length(eligible_ai_features) >= 1
          and membership_product_ref is null
          and membership_activation_contract_version is null
          and membership_cost_policy_version is null and cost_metadata is null)
        or (scope_kind = 'membership_activation' and reward_unit = 'membership_seconds'
          and eligible_ai_features is null
          and char_length(membership_product_ref) between 3 and 200
          and char_length(membership_activation_contract_version) between 3 and 160
          and char_length(membership_cost_policy_version) between 3 and 160
          and jsonb_typeof(cost_metadata) = 'object' and cost_metadata <> '{}'::jsonb)
      )
    )
    """,
    """
    create table commerce.community_reward_ai_usage_reservations (
      reservation_id uuid primary key,
      award_id uuid not null,
      beneficiary_user_id uuid not null references learner.users(user_id),
      reward_unit text not null default 'ai_quota' check (reward_unit = 'ai_quota'),
      scope_resource_id uuid not null,
      policy_id uuid not null,
      scope_kind text not null default 'ai_features' check (scope_kind = 'ai_features'),
      eligible_feature text not null check (char_length(eligible_feature) between 2 and 120),
      reserved_value bigint not null check (reserved_value > 0),
      reservation_key text not null unique check (char_length(reservation_key) between 8 and 300),
      request_fingerprint text not null check (request_fingerprint ~ '^[0-9a-f]{64}$'),
      server_session_ref text not null check (char_length(server_session_ref) between 8 and 240),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1
      ),
      reserved_at timestamptz not null,
      unique (reservation_id, award_id, beneficiary_user_id, reserved_value),
      foreign key (award_id, beneficiary_user_id, reward_unit)
        references commerce.community_reward_awards(
          award_id, beneficiary_user_id, reward_unit
        ),
      foreign key (scope_resource_id, policy_id, reward_unit, scope_kind)
        references commerce.community_reward_policy_scopes(
          scope_resource_id, policy_id, reward_unit, scope_kind
        )
    )
    """,
    """
    create function commerce.validate_community_ai_reservation()
    returns trigger language plpgsql as $$
    declare
      award_policy_id uuid;
      award_available bigint;
      outstanding bigint;
      features jsonb;
    begin
      select a.policy_id, a.granted_value - a.consumed_value - a.reversed_value
        into award_policy_id, award_available
        from commerce.community_reward_awards a
        where a.award_id = new.award_id for update;
      if award_policy_id is distinct from new.policy_id then
        raise exception using errcode = '23514',
          message = 'AI reservation scope policy does not own the award lot';
      end if;
      select s.eligible_ai_features into features
        from commerce.community_reward_policy_scopes s
        where s.scope_resource_id = new.scope_resource_id;
      if features is null or not (features ? new.eligible_feature) then
        raise exception using errcode = '23514',
          message = 'AI feature is not eligible under the award policy scope';
      end if;
      select coalesce(sum(r.reserved_value), 0) into outstanding
        from commerce.community_reward_ai_usage_reservations r
        where r.award_id = new.award_id
          and not exists (
            select 1 from commerce.community_reward_ai_usage_reservation_events e
            where e.reservation_id = r.reservation_id
          );
      if outstanding + new.reserved_value > award_available then
        raise exception using errcode = '23514',
          message = 'AI reservation exceeds the unused originating award lot';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger community_reward_ai_reservation_validate
      before insert on commerce.community_reward_ai_usage_reservations
      for each row execute function commerce.validate_community_ai_reservation()
    """,
    """
    create table commerce.community_reward_ai_usage_reservation_events (
      reservation_event_id uuid primary key,
      reservation_id uuid not null unique,
      award_id uuid not null,
      beneficiary_user_id uuid not null references learner.users(user_id),
      reserved_value bigint not null check (reserved_value > 0),
      event_type text not null check (event_type in ('consumed', 'released')),
      ledger_entry_id uuid,
      reason_code text not null check (char_length(reason_code) between 2 and 120),
      provider_usage_ref text,
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1
      ),
      occurred_at timestamptz not null,
      foreign key (reservation_id, award_id, beneficiary_user_id, reserved_value)
        references commerce.community_reward_ai_usage_reservations(
          reservation_id, award_id, beneficiary_user_id, reserved_value
        ),
      foreign key (ledger_entry_id, award_id, beneficiary_user_id)
        references commerce.community_reward_ledger_entries(
          ledger_entry_id, award_id, beneficiary_user_id
        ),
      check (
        (event_type = 'consumed' and ledger_entry_id is not null
          and char_length(provider_usage_ref) between 3 and 240)
        or (event_type = 'released' and ledger_entry_id is null
          and provider_usage_ref is null)
      )
    )
    """,
    """
    create function commerce.validate_community_ai_reservation_event()
    returns trigger language plpgsql as $$
    declare
      entry_type_value text;
      entry_unit text;
      entry_delta bigint;
    begin
      if new.event_type = 'consumed' then
        select l.entry_type, l.reward_unit, l.value_delta
          into entry_type_value, entry_unit, entry_delta
          from commerce.community_reward_ledger_entries l
          where l.ledger_entry_id = new.ledger_entry_id;
        if entry_type_value is distinct from 'consume'
          or entry_unit is distinct from 'ai_quota'
          or entry_delta is distinct from -new.reserved_value then
          raise exception using errcode = '23514',
            message = 'AI reservation consumption must bind a matching award-lot ledger entry';
        end if;
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger community_reward_ai_event_validate
      before insert on commerce.community_reward_ai_usage_reservation_events
      for each row execute function commerce.validate_community_ai_reservation_event()
    """,
    """
    create table commerce.community_reward_membership_activations (
      membership_activation_id uuid primary key,
      award_id uuid not null unique,
      beneficiary_user_id uuid not null references learner.users(user_id),
      reward_unit text not null default 'membership_seconds'
        check (reward_unit = 'membership_seconds'),
      scope_resource_id uuid not null,
      policy_id uuid not null,
      scope_kind text not null default 'membership_activation'
        check (scope_kind = 'membership_activation'),
      ledger_entry_id uuid not null,
      activation_seconds bigint not null check (activation_seconds > 0),
      activation_key text not null unique check (char_length(activation_key) between 8 and 300),
      membership_entitlement_ref text not null
        check (char_length(membership_entitlement_ref) between 3 and 240),
      purchase_history_snapshot_ref text not null
        check (char_length(purchase_history_snapshot_ref) between 3 and 240),
      prior_expires_at timestamptz not null,
      activated_from timestamptz not null,
      activated_until timestamptz not null,
      cost_metadata_snapshot jsonb not null check (
        jsonb_typeof(cost_metadata_snapshot) = 'object'
        and cost_metadata_snapshot <> '{}'::jsonb
      ),
      evidence_refs jsonb not null check (
        jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) >= 1
      ),
      activated_at timestamptz not null,
      foreign key (award_id, beneficiary_user_id, reward_unit)
        references commerce.community_reward_awards(
          award_id, beneficiary_user_id, reward_unit
        ),
      foreign key (scope_resource_id, policy_id, reward_unit, scope_kind)
        references commerce.community_reward_policy_scopes(
          scope_resource_id, policy_id, reward_unit, scope_kind
        ),
      foreign key (ledger_entry_id, award_id, beneficiary_user_id)
        references commerce.community_reward_ledger_entries(
          ledger_entry_id, award_id, beneficiary_user_id
        ),
      check (activated_from >= prior_expires_at),
      check (activated_until > activated_from),
      check (extract(epoch from (activated_until - activated_from)) = activation_seconds)
    )
    """,
    """
    create function commerce.validate_community_membership_activation()
    returns trigger language plpgsql as $$
    declare
      granted bigint;
      entry_type_value text;
      entry_unit text;
      entry_delta bigint;
      scope_cost_metadata jsonb;
    begin
      select a.granted_value into granted
        from commerce.community_reward_awards a where a.award_id = new.award_id;
      if granted is distinct from new.activation_seconds then
        raise exception using errcode = '23514',
          message = 'membership activation must consume the complete originating award lot';
      end if;
      select l.entry_type, l.reward_unit, l.value_delta
        into entry_type_value, entry_unit, entry_delta
        from commerce.community_reward_ledger_entries l
        where l.ledger_entry_id = new.ledger_entry_id;
      if entry_type_value is distinct from 'consume'
        or entry_unit is distinct from 'membership_seconds'
        or entry_delta is distinct from -new.activation_seconds then
        raise exception using errcode = '23514',
          message = 'membership activation must bind a matching award-lot ledger entry';
      end if;
      select s.cost_metadata into scope_cost_metadata
        from commerce.community_reward_policy_scopes s
        where s.scope_resource_id = new.scope_resource_id;
      if scope_cost_metadata is null then
        raise exception using errcode = '23514',
          message = 'membership activation requires published cost metadata scope';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger community_reward_membership_activation_validate
      before insert on commerce.community_reward_membership_activations
      for each row execute function commerce.validate_community_membership_activation()
    """,
    *(
        f"create trigger {trigger} before update or delete on {schema}.{table} "
        f"for each row execute function {function}()"
        for schema, table, trigger, function in (
            (
                "product",
                "community_method_card_source_bindings",
                "community_method_source_binding_append_only",
                "product.reject_community_method_history_mutation",
            ),
            (
                "product",
                "community_method_card_validation_bindings",
                "community_method_validation_binding_append_only",
                "product.reject_community_method_history_mutation",
            ),
            (
                "product",
                "community_method_card_state_events",
                "community_method_state_event_append_only",
                "product.reject_community_method_history_mutation",
            ),
            (
                "product",
                "community_method_card_trial_session_evidence",
                "community_method_trial_session_append_only",
                "product.reject_community_method_history_mutation",
            ),
            (
                "product",
                "community_method_card_eligibility_decisions",
                "community_method_eligibility_append_only",
                "product.reject_community_method_history_mutation",
            ),
            (
                "product",
                "community_method_card_report_decisions",
                "community_method_report_decision_append_only",
                "product.reject_community_method_history_mutation",
            ),
            (
                "commerce",
                "community_reward_policy_scopes",
                "community_reward_scope_append_only",
                "commerce.reject_community_reward_history_mutation",
            ),
            (
                "commerce",
                "community_reward_ai_usage_reservations",
                "community_reward_ai_reservation_append_only",
                "commerce.reject_community_reward_history_mutation",
            ),
            (
                "commerce",
                "community_reward_ai_usage_reservation_events",
                "community_reward_ai_event_append_only",
                "commerce.reject_community_reward_history_mutation",
            ),
            (
                "commerce",
                "community_reward_membership_activations",
                "community_reward_membership_activation_append_only",
                "commerce.reject_community_reward_history_mutation",
            ),
        )
    ),
)
