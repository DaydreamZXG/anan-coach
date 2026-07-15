from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_wechat_identity_exchange
from app.domains.auth.wechat import (
    CODE_TO_SESSION_PATH,
    WechatIdentityClaims,
    WechatIdentityExchange,
    WechatLoginRejectedError,
)
from app.main import create_app

APP_ID = "wx-test-app"


class FakeWechatIdentityExchange:
    def exchange(self, *, code: str) -> WechatIdentityClaims:
        if code == "invalid-code":
            raise WechatLoginRejectedError(
                error_code=40029,
                error_message="invalid code",
            )
        identity_suffix = "learner-2" if code == "another-user-code" else "learner-1"
        return WechatIdentityClaims(
            app_id=APP_ID,
            open_id=f"openid-{identity_suffix}",
            union_id=f"unionid-{identity_suffix}",
            session_key_hash=sha256(b"provider-session-key").hexdigest(),
        )


def _make_client(
    db_session_factory: sessionmaker[Session],
) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_wechat_identity_exchange] = (
        lambda: FakeWechatIdentityExchange()
    )
    return TestClient(app)


def test_wechat_login_creates_identity_profile_and_internal_session(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)

    response = client.post(
        "/api/v2/auth/wechat",
        headers={"X-Request-ID": "mp-login-contract-1"},
        json={"code": "valid-code", "deviceId": "wechat-device-1"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "mp-login-contract-1"
    body = response.json()
    assert body["requestId"] == "mp-login-contract-1"
    assert body["data"]["tokenType"] == "Bearer"
    assert body["data"]["accessToken"].startswith("gkd_")
    assert body["data"]["onboardingComplete"] is False
    assert body["data"]["onboardingProgress"]["nextStep"] == "qualification"
    assert body["data"]["onboardingProgress"]["completed"] is False
    user_id = body["data"]["user"]["userId"]

    with db_session_factory() as session:
        identity = session.execute(
            text(
                """
                select app_id, open_id, union_id, session_key_hash
                from learner.wechat_identities
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        stored_session = session.execute(
            text(
                """
                select session_token_hash, device_id
                from learner.app_sessions
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        assert identity["app_id"] == APP_ID
        assert identity["open_id"] == "openid-learner-1"
        assert identity["union_id"] == "unionid-learner-1"
        assert identity["session_key_hash"] == sha256(
            b"provider-session-key"
        ).hexdigest()
        assert stored_session["session_token_hash"] != body["data"]["accessToken"]
        assert stored_session["device_id"] == "wechat-device-1"


def test_wechat_login_is_idempotent_for_same_app_and_openid(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)

    first = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-1"},
    )
    second = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-2"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["user"]["userId"] == second.json()["data"]["user"][
        "userId"
    ]
    with db_session_factory() as session:
        assert session.execute(text("select count(*) from learner.users")).scalar_one() == 1
        assert (
            session.execute(
                text("select count(*) from learner.wechat_identities")
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(text("select count(*) from learner.app_sessions")).scalar_one()
            == 2
        )


def test_account_session_inventory_revocation_and_logout_all(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)
    first = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-first"},
    ).json()["data"]
    second = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-second"},
    ).json()["data"]

    overview = client.get(
        "/api/v2/auth/account",
        headers={"Authorization": f"Bearer {second['accessToken']}"},
    )
    assert overview.status_code == 200
    account = overview.json()["data"]
    assert account["accountStatus"] == "active"
    assert account["wechatLinked"] is True
    assert len(account["sessions"]) == 2
    current = next(item for item in account["sessions"] if item["isCurrent"])
    other = next(item for item in account["sessions"] if not item["isCurrent"])
    assert current["deviceId"] == "wechat-device-second"

    revoked = client.delete(
        f"/api/v2/auth/sessions/{other['sessionId']}",
        headers={"Authorization": f"Bearer {second['accessToken']}"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["revoked"] is True
    assert client.get(
        "/api/v2/auth/account",
        headers={"Authorization": f"Bearer {first['accessToken']}"},
    ).status_code == 401

    logged_out = client.post(
        "/api/v2/auth/logout-all",
        headers={"Authorization": f"Bearer {second['accessToken']}"},
    )
    assert logged_out.status_code == 200
    assert logged_out.json()["data"]["revokedSessionCount"] == 1
    assert client.get(
        "/api/v2/auth/account",
        headers={"Authorization": f"Bearer {second['accessToken']}"},
    ).status_code == 401


def test_account_cannot_revoke_another_users_session(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)
    owner = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-owner"},
    ).json()["data"]
    other = client.post(
        "/api/v2/auth/wechat",
        json={"code": "another-user-code", "deviceId": "wechat-device-other"},
    ).json()["data"]
    other_overview = client.get(
        "/api/v2/auth/account",
        headers={"Authorization": f"Bearer {other['accessToken']}"},
    ).json()["data"]

    response = client.delete(
        f"/api/v2/auth/sessions/{other_overview['sessions'][0]['sessionId']}",
        headers={"Authorization": f"Bearer {owner['accessToken']}"},
    )

    assert response.status_code == 404
    assert client.get(
        "/api/v2/auth/account",
        headers={"Authorization": f"Bearer {other['accessToken']}"},
    ).status_code == 200


def test_current_logout_is_audited_and_invalidates_token(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)
    login = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-logout"},
    ).json()["data"]

    response = client.post(
        "/api/v2/auth/logout",
        headers={"Authorization": f"Bearer {login['accessToken']}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["revokedSessionCount"] == 1
    assert client.get(
        "/api/v2/auth/account",
        headers={"Authorization": f"Bearer {login['accessToken']}"},
    ).status_code == 401
    with db_session_factory() as session:
        event = session.execute(
            text(
                """
                select event_type, metadata
                from learner.account_events
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": login["user"]["userId"]},
        ).mappings().one()
    assert event["event_type"] == "session_revoked"
    assert event["metadata"]["reason"] == "user_logout"


def test_verified_wechat_identity_recovers_account_and_revokes_old_sessions(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)
    first = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-before-recovery"},
    ).json()["data"]
    user_id = first["user"]["userId"]
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learner.users
                set account_status = 'recovery_required', is_active = false,
                    account_status_changed_at = now()
                where user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )

    recovered = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-after-recovery"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["data"]["user"]["userId"] == user_id
    with db_session_factory() as session:
        user = session.execute(
            text("select account_status, is_active from learner.users where user_id = :id"),
            {"id": user_id},
        ).mappings().one()
        old_session = session.execute(
            text(
                """
                select revoked_reason from learner.app_sessions
                where device_id = 'wechat-device-before-recovery'
                """
            )
        ).scalar_one()
        event = session.execute(
            text("select event_type from learner.account_events where user_id = :id"),
            {"id": user_id},
        ).scalar_one()
    assert user["account_status"] == "active"
    assert user["is_active"] is True
    assert old_session == "account_recovery"
    assert event == "account_recovered"


def test_disabled_wechat_account_returns_stable_error_without_creating_duplicate_user(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)
    first = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-disabled"},
    ).json()["data"]
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learner.users
                set account_status = 'disabled', is_active = false,
                    account_status_changed_at = now()
                where user_id = :user_id
                """
            ),
            {"user_id": first["user"]["userId"]},
        )

    response = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-disabled-retry"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "account_disabled"
    with db_session_factory() as session:
        assert session.execute(text("select count(*) from learner.users")).scalar_one() == 1


def test_wechat_login_rejection_returns_structured_public_error(
    db_session_factory: sessionmaker[Session],
) -> None:
    response = _make_client(db_session_factory).post(
        "/api/v2/auth/wechat",
        headers={"X-Request-ID": "mp-invalid-code"},
        json={"code": "invalid-code", "deviceId": "wechat-device-1"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "wechat_login_rejected",
        "message": "微信登录凭证无效或已过期，请重新登录。",
        "requestId": "mp-invalid-code",
    }
    assert "invalid code" not in response.text


def test_code_exchange_uses_official_contract_and_hashes_session_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == CODE_TO_SESSION_PATH
        assert request.url.params["appid"] == APP_ID
        assert request.url.params["secret"] == "server-only-secret"
        assert request.url.params["js_code"] == "temporary-code"
        assert request.url.params["grant_type"] == "authorization_code"
        return httpx.Response(
            200,
            json={
                "openid": "openid-provider",
                "unionid": "unionid-provider",
                "session_key": "raw-session-key",
            },
        )

    exchange = WechatIdentityExchange(
        Settings(
            wechat_mini_app_id=APP_ID,
            wechat_mini_app_secret="server-only-secret",
        ),
        transport=httpx.MockTransport(handler),
    )

    claims = exchange.exchange(code="temporary-code")

    assert claims.open_id == "openid-provider"
    assert claims.union_id == "unionid-provider"
    assert claims.session_key_hash == sha256(b"raw-session-key").hexdigest()
    assert "raw-session-key" not in repr(claims)
