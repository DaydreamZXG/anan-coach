from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.commerce.app_store import (
    StaticAppStoreTransactionVerifier,
    VerifiedAppStoreNotification,
    VerifiedAppStoreTransaction,
)
from app.domains.commerce.deps import get_app_store_transaction_verifier
from app.domains.commerce.repository import CommerceQuotaExceededError, CommerceRepository
from app.domains.commerce.schemas import AppStoreTransactionRequest
from app.domains.commerce.service import CommerceService
from app.main import create_app


def test_commerce_catalog_contract(db_session_factory: sessionmaker[Session]) -> None:
    client = _make_client(db_session_factory)

    response = client.get("/api/v1/commerce/catalog")

    assert response.status_code == 200
    body = response.json()
    assert [item["productId"] for item in body["products"]] == [
        "gongkao_ai_pro_monthly",
        "gongkao_ai_pro_yearly",
    ]
    assert body["freeQuotas"][0] == {
        "feature": "companion_ask",
        "title": "岸岸 AI 问答",
        "limit": 3,
        "used": 0,
        "remaining": 3,
        "period": "day",
    }
    assert body["proQuotas"][0]["limit"] == 60


def test_entitlement_defaults_to_free(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="free-token")
    client = _make_client(db_session_factory)

    response = client.get(
        "/api/v1/commerce/entitlement",
        headers={"Authorization": "Bearer free-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "free"
    assert body["isActive"] is True
    assert body["featureQuotas"][0]["limit"] == 3
    assert body["productIds"] == []


def test_verified_active_app_store_transaction_grants_pro(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="pro-token")
    signed_transaction = "signed.transaction.info.from.storekit." + ("x" * 40)
    transaction = _verified_transaction(
        product_id="gongkao_ai_pro_monthly",
        transaction_id="tx_active_1",
        original_transaction_id="otx_active_1",
        expires_date=datetime.now(UTC) + timedelta(days=30),
        app_account_token=user_id,
    )
    client = _make_client(
        db_session_factory,
        verifier=StaticAppStoreTransactionVerifier(transaction),
    )

    response = client.post(
        "/api/v1/commerce/app-store/transactions",
        headers={"Authorization": "Bearer pro-token"},
        json={
            "productId": "gongkao_ai_pro_monthly",
            "transactionId": "tx_active_1",
            "originalTransactionId": "otx_active_1",
            "signedTransactionInfo": signed_transaction,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["verificationStatus"] == "verified_active"
    assert body["entitlement"]["tier"] == "pro"
    assert body["entitlement"]["productIds"] == ["gongkao_ai_pro_monthly"]

    with db_session_factory() as session:
        row = session.execute(
            text(
                """
                select verification_status, user_id::text
                from commerce.app_store_transactions
                where transaction_id = 'tx_active_1'
                """
            )
        ).mappings().one()
        assert row["verification_status"] == "verified_active"
        assert row["user_id"] == user_id


def test_verified_expired_transaction_does_not_grant_pro(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="expired-token")
    transaction = _verified_transaction(
        product_id="gongkao_ai_pro_monthly",
        transaction_id="tx_expired_1",
        original_transaction_id="otx_expired_1",
        expires_date=datetime.now(UTC) - timedelta(days=1),
        app_account_token=user_id,
    )
    client = _make_client(
        db_session_factory,
        verifier=StaticAppStoreTransactionVerifier(transaction),
    )

    response = client.post(
        "/api/v1/commerce/app-store/transactions",
        headers={"Authorization": "Bearer expired-token"},
        json={
            "productId": "gongkao_ai_pro_monthly",
            "transactionId": "tx_expired_1",
            "originalTransactionId": "otx_expired_1",
            "signedTransactionInfo": "signed.transaction.info.from.storekit." + ("y" * 40),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["verificationStatus"] == "verified_expired"
    assert body["entitlement"]["tier"] == "free"


def test_feature_usage_switches_to_pro_limit_after_purchase(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="upgrade-token")

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_monthly",
                    transaction_id="tx_unused",
                    original_transaction_id="otx_unused",
                    expires_date=datetime.now(UTC) + timedelta(days=30),
                    app_account_token=user_id,
                )
            ),
        )
        first = _reserve(
            service, user_id, "external_wrong_intake", "free-intake-1"
        )
        assert first.limit == 1
        with pytest.raises(CommerceQuotaExceededError):
            _reserve(
                service, user_id, "external_wrong_intake", "free-intake-2"
            )

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_monthly",
                    transaction_id="tx_upgrade_1",
                    original_transaction_id="otx_upgrade_1",
                    expires_date=datetime.now(UTC) + timedelta(days=30),
                    app_account_token=user_id,
                )
            ),
        )
        service.register_app_store_transaction(
            user_id=user_id,
            request=AppStoreTransactionRequest(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_upgrade_1",
                original_transaction_id="otx_upgrade_1",
                signed_transaction_info="signed.transaction.info." + ("z" * 40),
            ),
        )
        upgraded = _reserve(
            service, user_id, "external_wrong_intake", "pro-intake-1"
        )

    assert upgraded.used == 2
    assert upgraded.limit == 30


def test_monthly_feature_usage_does_not_reset_next_day(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="monthly-quota-token")
    current_now = datetime(2026, 7, 9, 10, 0, tzinfo=UTC)

    def now() -> datetime:
        return current_now

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_monthly",
                    transaction_id="tx_monthly_quota_1",
                    original_transaction_id="otx_monthly_quota_1",
                    expires_date=datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
                    app_account_token=user_id,
                )
            ),
            now=now,
        )
        service.register_app_store_transaction(
            user_id=user_id,
            request=AppStoreTransactionRequest(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_monthly_quota_1",
                original_transaction_id="otx_monthly_quota_1",
                signed_transaction_info="signed.transaction.info.monthly." + ("m" * 40),
            ),
        )
        for index in range(10):
            _reserve(
                service, user_id, "study_plan_regeneration", f"monthly-{index}"
            )
        with pytest.raises(CommerceQuotaExceededError):
            _reserve(
                service, user_id, "study_plan_regeneration", "monthly-over-limit"
            )

        current_now = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
        with pytest.raises(CommerceQuotaExceededError):
            _reserve(
                service, user_id, "study_plan_regeneration", "monthly-next-day"
            )

        entitlement = service.get_entitlement(user_id=user_id)
        monthly_quota = next(
            quota
            for quota in entitlement.feature_quotas
            if quota.feature == "study_plan_regeneration"
        )

    assert monthly_quota.period == "month"
    assert monthly_quota.used == 10
    assert monthly_quota.remaining == 0


def test_daily_feature_usage_resets_next_day(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="daily-quota-token")
    current_now = datetime(2026, 7, 9, 10, 0, tzinfo=UTC)

    def now() -> datetime:
        return current_now

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_monthly",
                    transaction_id="tx_daily_unused",
                    original_transaction_id="otx_daily_unused",
                    expires_date=datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
                    app_account_token=user_id,
                )
            ),
            now=now,
        )
        for index in range(3):
            _reserve(
                service, user_id, "companion_ask", f"daily-{index}"
            )
        with pytest.raises(CommerceQuotaExceededError):
            _reserve(
                service, user_id, "companion_ask", "daily-over-limit"
            )

        current_now = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
        next_day = _reserve(
            service, user_id, "companion_ask", "daily-next-day"
        )
        entitlement = service.get_entitlement(user_id=user_id)
        daily_quota = next(
            quota for quota in entitlement.feature_quotas if quota.feature == "companion_ask"
        )

    assert next_day.used == 1
    assert daily_quota.period == "day"
    assert daily_quota.used == 1
    assert daily_quota.remaining == 2


def test_transaction_app_account_token_must_match_authenticated_user(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="owner-token")
    attacker_id = _seed_user(db_session_factory, token="attacker-token")
    transaction = _verified_transaction(
        product_id="gongkao_ai_pro_monthly",
        transaction_id="tx_owner_1",
        original_transaction_id="otx_owner_1",
        expires_date=datetime.now(UTC) + timedelta(days=30),
        app_account_token=str(uuid4()),
    )
    client = _make_client(
        db_session_factory,
        verifier=StaticAppStoreTransactionVerifier(transaction),
    )

    response = client.post(
        "/api/v1/commerce/app-store/transactions",
        headers={"Authorization": "Bearer attacker-token"},
        json={
            "productId": "gongkao_ai_pro_monthly",
            "transactionId": "tx_owner_1",
            "originalTransactionId": "otx_owner_1",
            "signedTransactionInfo": "signed.transaction.info.bound." + ("b" * 40),
        },
    )

    assert response.status_code == 400
    with db_session_factory() as session:
        count = session.execute(
            text(
                """
                select count(*)
                from commerce.user_entitlements
                where user_id = cast(:user_id as uuid)
                  and tier = 'pro'
                """
            ),
            {"user_id": attacker_id},
        ).scalar_one()
        assert count == 0


def test_app_store_notification_revocation_expires_pro_entitlement(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="notify-token")
    revocation_date = datetime.now(UTC)
    active_expires = revocation_date + timedelta(days=30)
    transaction = _verified_transaction(
        product_id="gongkao_ai_pro_monthly",
        transaction_id="tx_notify_1",
        original_transaction_id="otx_notify_1",
        expires_date=active_expires,
        app_account_token=user_id,
        revocation_date=revocation_date,
    )
    notification = VerifiedAppStoreNotification(
        notification_uuid="notification-refund-1",
        notification_type="REFUND",
        subtype=None,
        environment="Sandbox",
        signed_transaction_info="signed.transaction.info.notification." + ("n" * 40),
        raw_payload={},
    )

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_monthly",
                    transaction_id="tx_original_active",
                    original_transaction_id="otx_notify_1",
                    expires_date=active_expires,
                    app_account_token=user_id,
                )
            ),
        )
        service.register_app_store_transaction(
            user_id=user_id,
            request=AppStoreTransactionRequest(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_original_active",
                original_transaction_id="otx_notify_1",
                signed_transaction_info="signed.transaction.info.original." + ("o" * 40),
            ),
        )

    client = _make_client(
        db_session_factory,
        verifier=StaticAppStoreTransactionVerifier(
            transaction=transaction,
            notification=notification,
        ),
    )
    response = client.post(
        "/api/v1/commerce/app-store/notifications",
        json={"signedPayload": "signed.notification.payload." + ("p" * 40)},
    )

    assert response.status_code == 200
    assert response.json()["processed"] is True
    with db_session_factory() as session:
        entitlement_end = session.execute(
            text(
                """
                select subscription_end
                from commerce.user_entitlements
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        event_count = session.execute(
            text(
                """
                select count(*)
                from commerce.app_store_notification_events
                where notification_uuid = 'notification-refund-1'
                """
            )
        ).scalar_one()
    assert entitlement_end <= revocation_date
    assert event_count == 1


def test_revoked_monthly_transaction_does_not_expire_active_yearly_entitlement(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="switch-token")
    now = datetime.now(UTC)
    monthly_expires = now + timedelta(days=30)
    yearly_expires = now + timedelta(days=365)

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_monthly",
                    transaction_id="tx_switch_monthly",
                    original_transaction_id="otx_switch_monthly",
                    expires_date=monthly_expires,
                    app_account_token=user_id,
                )
            ),
        )
        service.register_app_store_transaction(
            user_id=user_id,
            request=AppStoreTransactionRequest(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_switch_monthly",
                original_transaction_id="otx_switch_monthly",
                signed_transaction_info="signed.transaction.info.switch.monthly." + ("m" * 40),
            ),
        )

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_yearly",
                    transaction_id="tx_switch_yearly",
                    original_transaction_id="otx_switch_yearly",
                    expires_date=yearly_expires,
                    app_account_token=user_id,
                )
            ),
        )
        service.register_app_store_transaction(
            user_id=user_id,
            request=AppStoreTransactionRequest(
                product_id="gongkao_ai_pro_yearly",
                transaction_id="tx_switch_yearly",
                original_transaction_id="otx_switch_yearly",
                signed_transaction_info="signed.transaction.info.switch.yearly." + ("y" * 40),
            ),
        )

    client = _make_client(
        db_session_factory,
        verifier=StaticAppStoreTransactionVerifier(
            transaction=_verified_transaction(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_switch_monthly",
                original_transaction_id="otx_switch_monthly",
                expires_date=monthly_expires,
                app_account_token=user_id,
                revocation_date=now,
            ),
            notification=VerifiedAppStoreNotification(
                notification_uuid="notification-switch-monthly-refund",
                notification_type="REFUND",
                subtype=None,
                environment="Sandbox",
                signed_transaction_info="signed.transaction.info.switch.refund." + ("r" * 40),
                raw_payload={},
            ),
        ),
    )
    response = client.post(
        "/api/v1/commerce/app-store/notifications",
        json={"signedPayload": "signed.notification.payload.switch.refund." + ("s" * 40)},
    )

    assert response.status_code == 200
    assert response.json()["processed"] is True
    with db_session_factory() as session:
        row = session.execute(
            text(
                """
                select tier, subscription_end, product_ids
                from commerce.user_entitlements
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        monthly_status = session.execute(
            text(
                """
                select verification_status
                from commerce.app_store_transactions
                where transaction_id = 'tx_switch_monthly'
                """
            )
        ).scalar_one()

    assert row["tier"] == "pro"
    assert row["subscription_end"] >= yearly_expires
    assert row["product_ids"] == ["gongkao_ai_pro_yearly"]
    assert monthly_status == "verified_revoked"


def test_revoked_original_transaction_does_not_expire_later_renewal_in_same_chain(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="renewal-chain-token")
    now = datetime.now(UTC)
    original_expires = now + timedelta(days=30)
    renewal_expires = now + timedelta(days=365)
    original_transaction_id = "otx_same_chain"

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_monthly",
                    transaction_id="tx_chain_original",
                    original_transaction_id=original_transaction_id,
                    expires_date=original_expires,
                    app_account_token=user_id,
                )
            ),
        )
        service.register_app_store_transaction(
            user_id=user_id,
            request=AppStoreTransactionRequest(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_chain_original",
                original_transaction_id=original_transaction_id,
                signed_transaction_info="signed.transaction.info.chain.original." + ("c" * 40),
            ),
        )

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_yearly",
                    transaction_id="tx_chain_renewal",
                    original_transaction_id=original_transaction_id,
                    expires_date=renewal_expires,
                    app_account_token=user_id,
                )
            ),
        )
        service.register_app_store_transaction(
            user_id=user_id,
            request=AppStoreTransactionRequest(
                product_id="gongkao_ai_pro_yearly",
                transaction_id="tx_chain_renewal",
                original_transaction_id=original_transaction_id,
                signed_transaction_info="signed.transaction.info.chain.renewal." + ("d" * 40),
            ),
        )

    client = _make_client(
        db_session_factory,
        verifier=StaticAppStoreTransactionVerifier(
            transaction=_verified_transaction(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_chain_original_refund",
                original_transaction_id=original_transaction_id,
                expires_date=original_expires,
                app_account_token=user_id,
                revocation_date=now,
            ),
            notification=VerifiedAppStoreNotification(
                notification_uuid="notification-chain-original-refund",
                notification_type="REFUND",
                subtype=None,
                environment="Sandbox",
                signed_transaction_info="signed.transaction.info.chain.refund." + ("e" * 40),
                raw_payload={},
            ),
        ),
    )
    response = client.post(
        "/api/v1/commerce/app-store/notifications",
        json={"signedPayload": "signed.notification.payload.chain.refund." + ("f" * 40)},
    )

    assert response.status_code == 200
    with db_session_factory() as session:
        row = session.execute(
            text(
                """
                select tier, subscription_end, product_ids
                from commerce.user_entitlements
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().one()

    assert row["tier"] == "pro"
    assert row["subscription_end"] >= renewal_expires
    assert row["product_ids"] == ["gongkao_ai_pro_yearly"]


def test_duplicate_app_store_notification_is_not_reprocessed(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="notify-replay-token")
    revocation_date = datetime.now(UTC)
    notification_uuid = "notification-replay-1"
    active_expires = revocation_date + timedelta(days=30)

    with db_session_factory() as session:
        service = CommerceService(
            repository=CommerceRepository(session),
            app_store_verifier=StaticAppStoreTransactionVerifier(
                _verified_transaction(
                    product_id="gongkao_ai_pro_monthly",
                    transaction_id="tx_replay_original",
                    original_transaction_id="otx_replay_1",
                    expires_date=active_expires,
                    app_account_token=user_id,
                )
            ),
        )
        service.register_app_store_transaction(
            user_id=user_id,
            request=AppStoreTransactionRequest(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_replay_original",
                original_transaction_id="otx_replay_1",
                signed_transaction_info="signed.transaction.info.replay.original." + ("r" * 40),
            ),
        )

    refund_client = _make_client(
        db_session_factory,
        verifier=StaticAppStoreTransactionVerifier(
            transaction=_verified_transaction(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_replay_refund",
                original_transaction_id="otx_replay_1",
                expires_date=active_expires,
                app_account_token=user_id,
                revocation_date=revocation_date,
            ),
            notification=VerifiedAppStoreNotification(
                notification_uuid=notification_uuid,
                notification_type="REFUND",
                subtype=None,
                environment="Sandbox",
                signed_transaction_info="signed.transaction.info.replay.refund." + ("f" * 40),
                raw_payload={},
            ),
        ),
    )
    refund_response = refund_client.post(
        "/api/v1/commerce/app-store/notifications",
        json={"signedPayload": "signed.notification.payload.replay.refund." + ("p" * 40)},
    )

    duplicate_client = _make_client(
        db_session_factory,
        verifier=StaticAppStoreTransactionVerifier(
            transaction=_verified_transaction(
                product_id="gongkao_ai_pro_monthly",
                transaction_id="tx_replay_duplicate_active",
                original_transaction_id="otx_replay_1",
                expires_date=datetime.now(UTC) + timedelta(days=30),
                app_account_token=user_id,
            ),
            notification=VerifiedAppStoreNotification(
                notification_uuid=notification_uuid,
                notification_type="DID_RENEW",
                subtype=None,
                environment="Sandbox",
                signed_transaction_info="signed.transaction.info.replay.duplicate." + ("d" * 40),
                raw_payload={},
            ),
        ),
    )
    duplicate_response = duplicate_client.post(
        "/api/v1/commerce/app-store/notifications",
        json={"signedPayload": "signed.notification.payload.replay.duplicate." + ("q" * 40)},
    )

    assert refund_response.status_code == 200
    assert refund_response.json()["processed"] is True
    assert duplicate_response.status_code == 200
    assert duplicate_response.json()["processed"] is False
    with db_session_factory() as session:
        row = session.execute(
            text(
                """
                select subscription_end
                from commerce.user_entitlements
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        duplicate_transaction_count = session.execute(
            text(
                """
                select count(*)
                from commerce.app_store_transactions
                where transaction_id = 'tx_replay_duplicate_active'
                """
            )
        ).scalar_one()
        event_count = session.execute(
            text(
                """
                select count(*)
                from commerce.app_store_notification_events
                where notification_uuid = :notification_uuid
                  and processed_at is not null
                """
            ),
            {"notification_uuid": notification_uuid},
        ).scalar_one()

    assert row["subscription_end"] <= revocation_date
    assert duplicate_transaction_count == 0
    assert event_count == 1


def _make_client(
    db_session_factory: sessionmaker[Session],
    *,
    verifier: StaticAppStoreTransactionVerifier | None = None,
) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    if verifier is not None:
        app.dependency_overrides[get_app_store_transaction_verifier] = lambda: verifier
    return TestClient(app)


def _reserve(
    service: CommerceService,
    user_id: str,
    feature: str,
    business_key: str,
):
    return service.reserve_feature_usage(
        user_id=user_id,
        feature=feature,
        reservation_key=f"test:{user_id}:{feature}:{business_key}",
        business_key=business_key,
    )


def _seed_user(db_session_factory: sessionmaker[Session], *, token: str) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '学习者')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id,
                  user_id,
                  session_token_hash,
                  device_id,
                  expires_at
                ) values (
                  cast(:session_id as uuid),
                  cast(:user_id as uuid),
                  :session_token_hash,
                  'ios-test',
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "session_token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.commit()
    return user_id


def _verified_transaction(
    *,
    product_id: str,
    transaction_id: str,
    original_transaction_id: str,
    expires_date: datetime,
    app_account_token: str | None,
    revocation_date: datetime | None = None,
) -> VerifiedAppStoreTransaction:
    purchase_date = datetime.now(UTC) - timedelta(minutes=1)
    return VerifiedAppStoreTransaction(
        product_id=product_id,
        transaction_id=transaction_id,
        original_transaction_id=original_transaction_id,
        web_order_line_item_id="web_order_1",
        environment="Sandbox",
        purchase_date=purchase_date,
        expires_date=expires_date,
        revocation_date=revocation_date,
        app_account_token=app_account_token,
        raw_payload={},
    )
