from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.community_rewards.repository import SqlCommunityRewardRepository
from app.domains.community_rewards.schemas import CommunityRewardLedgerResponse
from app.domains.community_rewards.service import CommunityRewardService


NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


@pytest.fixture()
def stage35_factory(
    db_session_factory: sessionmaker[Session],
) -> sessionmaker[Session]:
    return db_session_factory


def test_catalog_quota_precedes_fifo_reward_reservation_and_direct_worker_consumes(
    stage35_factory: sessionmaker[Session],
) -> None:
    user_id = uuid4()
    with stage35_factory() as session:
        _seed_user(session, user_id)
        first_award = _seed_reward_lot(
            session,
            user_id=user_id,
            reward_unit="ai_quota",
            reward_value=2,
            created_at=NOW - timedelta(minutes=2),
            eligible_feature="essay_review",
        )
        _seed_reward_lot(
            session,
            user_id=user_id,
            reward_unit="ai_quota",
            reward_value=2,
            created_at=NOW - timedelta(minutes=1),
            eligible_feature="essay_review",
            milestone="independent_evidence_sufficient",
        )
        session.commit()

        service = _commerce_service(session)
        base = service.reserve_feature_usage(
            user_id=str(user_id),
            feature="essay_review",
            reservation_key=f"essay-review:{user_id}:base",
            business_key="base-execution",
        )
        server_session_ref = f"app-session:{uuid4()}"
        reward = service.reserve_feature_usage(
            user_id=str(user_id),
            feature="essay_review",
            reservation_key=f"essay-review:{user_id}:reward",
            business_key="reward-execution",
            server_session_ref=server_session_ref,
        )

        assert base.source == "catalog"
        assert reward.source == "community_reward"
        assert reward.award_id == first_award
        assert session.execute(
            text("select count(*) from commerce.feature_usage_reservations")
        ).scalar_one() == 1

        replay = service.reserve_feature_usage(
            user_id=str(user_id),
            feature="essay_review",
            reservation_key=reward.reservation_key,
            business_key="reward-execution",
            server_session_ref=server_session_ref,
        )
        assert replay.reservation_id == reward.reservation_id
        assert replay.source == "community_reward"

        summary_before = CommunityRewardService(
            SqlCommunityRewardRepository(session),
            now=lambda: NOW,
        ).get_ledger_summary(beneficiary_user_id=user_id)
        assert summary_before.ai_quota_balance == 3

        consumed = CommerceRepository(session).consume_feature_usage(
            reservation_key=reward.reservation_key,
        )
        assert consumed.status == "consumed"
        assert consumed.source == "community_reward"
        assert session.execute(
            text(
                """
                select count(*)
                from commerce.community_reward_ledger_entries
                where award_id = cast(:award_id as uuid) and entry_type = 'consume'
                """
            ),
            {"award_id": str(first_award)},
        ).scalar_one() == 1


def test_membership_reward_activation_is_a_fact_and_merges_purchase_entitlement(
    stage35_factory: sessionmaker[Session],
) -> None:
    user_id = uuid4()
    purchase_end = NOW + timedelta(hours=2)
    with stage35_factory() as session:
        _seed_user(session, user_id)
        session.execute(
            text(
                """
                insert into commerce.user_entitlements (
                  entitlement_id, user_id, tier, source, subscription_start,
                  subscription_end, product_ids, features, created_at, updated_at
                ) values (
                  cast(:entitlement_id as uuid), cast(:user_id as uuid), 'pro',
                  'app_store', :starts_at, :ends_at, '["pro.monthly"]'::jsonb,
                  '{"tier":"pro"}'::jsonb, :now, :now
                )
                """
            ),
            {
                "entitlement_id": str(uuid4()),
                "user_id": str(user_id),
                "starts_at": NOW - timedelta(days=2),
                "ends_at": purchase_end,
                "now": NOW,
            },
        )
        award_id = _seed_reward_lot(
            session,
            user_id=user_id,
            reward_unit="membership_seconds",
            reward_value=3600,
            created_at=NOW,
        )
        session.commit()

        service = _commerce_service(session)
        activation = service.activate_reward_membership(
            award_id=award_id,
            beneficiary_user_id=user_id,
            activation_key=f"membership:{award_id}",
            membership_entitlement_ref=f"user:{user_id}:pro",
            purchase_history_snapshot_ref=f"purchase-snapshot:{user_id}:{NOW.isoformat()}",
            evidence_refs=(f"award:{award_id}",),
        )
        assert activation.activated_from == purchase_end
        assert activation.activated_until == purchase_end + timedelta(hours=1)

        purchase_row = session.execute(
            text(
                """
                select source, subscription_end, product_ids
                from commerce.user_entitlements
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": str(user_id)},
        ).mappings().one()
        assert purchase_row["source"] == "app_store"
        assert purchase_row["subscription_end"] == purchase_end
        assert purchase_row["product_ids"] == ["pro.monthly"]

        entitlement = service.get_entitlement(user_id=str(user_id))
        assert entitlement.source == "merged"
        assert entitlement.subscription_end == purchase_end + timedelta(hours=1)
        assert entitlement.product_ids == ["pro.monthly"]

        summary = CommunityRewardService(
            SqlCommunityRewardRepository(session),
            now=lambda: NOW,
        ).get_ledger_summary(beneficiary_user_id=user_id)
        assert summary.membership_seconds_balance == 0
        response = CommunityRewardLedgerResponse.from_record(summary)
        assert set(response.model_dump(by_alias=True)) == {
            "activePolicyVersions",
            "awardIds",
            "entryIds",
            "aiQuotaBalance",
            "membershipSecondsBalance",
            "entitlementVersion",
        }
        assert response.entitlement_version == CommunityRewardLedgerResponse.from_record(
            summary
        ).entitlement_version


def _commerce_service(session: Session) -> CommerceService:
    return CommerceService(
        repository=CommerceRepository(session),
        app_store_verifier=None,  # type: ignore[arg-type]
        commercial_mode="paid",
        usage_access_mode="entitlement_quota",
        now=lambda: NOW,
    )


def _seed_user(session: Session, user_id: UUID) -> None:
    _execute_statements(
        session,
        """
            insert into learner.users (user_id, display_name)
            values (cast(:user_id as uuid), '奖励测试用户')
            """,
        {"user_id": str(user_id)},
    )


def _seed_reward_lot(
    session: Session,
    *,
    user_id: UUID,
    reward_unit: str,
    reward_value: int,
    created_at: datetime,
    eligible_feature: str | None = None,
    milestone: str = "automated_review_passed",
) -> UUID:
    card_id = uuid4()
    version_id = uuid4()
    event_id = uuid4()
    policy_id = uuid4()
    award_id = uuid4()
    policy_version = f"policy:{policy_id}"
    _execute_statements(
        session,
        """
            insert into product.community_method_cards (
              card_id, author_user_id, track, state, head_version_id,
              state_version, created_at, updated_at
            ) values (
              cast(:card_id as uuid), cast(:user_id as uuid), 'xingce',
              'community_validated', null, 1, :created_at, :created_at
            );
            insert into product.community_method_card_versions (
              version_id, card_id, version_no, author_user_id, base_version_id,
              source_type, source_artifact_id, source_text_sha256,
              private_source_text, method_steps, applicability, exclusions,
              pitfalls, created_at
            ) values (
              cast(:version_id as uuid), cast(:card_id as uuid), 1,
              cast(:user_id as uuid), null, 'text', null, repeat('a', 64),
              '测试方法', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
              '[]'::jsonb, :created_at
            );
            update product.community_method_cards
            set head_version_id = cast(:version_id as uuid)
            where card_id = cast(:card_id as uuid);
            insert into product.community_method_card_milestone_events (
              event_id, author_user_id, card_id, version_id, milestone,
              evidence_refs, occurred_at
            ) values (
              cast(:event_id as uuid), cast(:user_id as uuid),
              cast(:card_id as uuid), cast(:version_id as uuid), :milestone,
              '["review:test"]'::jsonb, :created_at
            )
            """,
        {
            "card_id": str(card_id),
            "version_id": str(version_id),
            "event_id": str(event_id),
            "user_id": str(user_id),
            "milestone": milestone,
            "created_at": created_at,
        },
    )
    _execute_statements(
        session,
        """
            insert into commerce.community_reward_policies (
              policy_id, policy_version, milestone, reward_unit, reward_value,
              author_period_cap, card_period_cap, milestone_period_cap,
              global_period_cap, period_kind, state, rules_sha256,
              published_at, created_at
            ) values (
              cast(:policy_id as uuid), :policy_version, :milestone, :reward_unit,
              :reward_value, :reward_value, :reward_value, :reward_value,
              :reward_value, 'month', 'published', repeat('b', 64),
              :created_at, :created_at
            );
            insert into commerce.community_reward_awards (
              award_id, event_id, policy_id, beneficiary_user_id, card_id,
              version_id, milestone, period_start, reward_unit, granted_value,
              consumed_value, reversed_value, status, evidence_refs,
              entitlement_before, entitlement_after, cost_minor_units, created_at
            ) values (
              cast(:award_id as uuid), cast(:event_id as uuid),
              cast(:policy_id as uuid), cast(:user_id as uuid),
              cast(:card_id as uuid), cast(:version_id as uuid), :milestone,
              :period_start, :reward_unit, :reward_value, 0, 0, 'granted',
              '["review:test"]'::jsonb, '{}'::jsonb, '{}'::jsonb, 0, :created_at
            );
            insert into commerce.community_reward_ledger_entries (
              ledger_entry_id, award_id, beneficiary_user_id, entry_type,
              reward_unit, value_delta, idempotency_key, reason_code,
              balance_before, balance_after, evidence_refs, created_at
            ) values (
              cast(:ledger_entry_id as uuid), cast(:award_id as uuid),
              cast(:user_id as uuid), 'grant', :reward_unit, :reward_value,
              :grant_key, 'community_milestone_policy_grant', 0, :reward_value,
              '["review:test"]'::jsonb, :created_at
            )
            """,
        {
            "policy_id": str(policy_id),
            "policy_version": policy_version,
            "milestone": milestone,
            "reward_unit": reward_unit,
            "reward_value": reward_value,
            "created_at": created_at,
            "award_id": str(award_id),
            "event_id": str(event_id),
            "user_id": str(user_id),
            "card_id": str(card_id),
            "version_id": str(version_id),
            "period_start": created_at.date().replace(day=1),
            "ledger_entry_id": str(uuid4()),
            "grant_key": f"grant:{award_id}",
        },
    )
    scope_id = uuid4()
    if reward_unit == "ai_quota":
        session.execute(
            text(
                """
                insert into commerce.community_reward_policy_scopes (
                  scope_resource_id, policy_id, reward_unit, scope_version,
                  scope_kind, eligible_ai_features, rules_sha256, created_at
                ) values (
                  cast(:scope_id as uuid), cast(:policy_id as uuid), 'ai_quota',
                  :scope_version, 'ai_features', cast(:features as jsonb),
                  repeat('c', 64), :created_at
                )
                """
            ),
            {
                "scope_id": str(scope_id),
                "policy_id": str(policy_id),
                "scope_version": f"scope:{scope_id}",
                "features": f'["{eligible_feature}"]',
                "created_at": created_at,
            },
        )
    else:
        session.execute(
            text(
                """
                insert into commerce.community_reward_policy_scopes (
                  scope_resource_id, policy_id, reward_unit, scope_version,
                  scope_kind, membership_product_ref,
                  membership_activation_contract_version,
                  membership_cost_policy_version, cost_metadata,
                  rules_sha256, created_at
                ) values (
                  cast(:scope_id as uuid), cast(:policy_id as uuid),
                  'membership_seconds', :scope_version, 'membership_activation',
                  'community-pro', 'activation-contract-v1', 'cost-policy-v1',
                  '{"currency":"CNY","source":"approved-policy"}'::jsonb,
                  repeat('c', 64), :created_at
                )
                """
            ),
            {
                "scope_id": str(scope_id),
                "policy_id": str(policy_id),
                "scope_version": f"scope:{scope_id}",
                "created_at": created_at,
            },
        )
    return award_id


def _execute_statements(
    session: Session,
    sql: str,
    params: dict[str, object],
) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            session.execute(text(statement), params)
