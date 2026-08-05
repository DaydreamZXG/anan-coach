from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.deps import get_wechat_identity_exchange
from app.domains.auth.repository import AuthRepository
from app.domains.auth.wechat import WechatIdentityClaims
from app.main import create_app

APP_ID = "wx-consent-test-app"


class FakeWechatIdentityExchange:
    def exchange(self, *, code: str) -> WechatIdentityClaims:
        return WechatIdentityClaims(
            app_id=APP_ID,
            open_id="openid-consent-learner",
            union_id="unionid-consent-learner",
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


def _consent_rows(
    db_session_factory: sessionmaker[Session],
    user_id: str,
) -> list[dict[str, object]]:
    with db_session_factory() as session:
        rows = session.execute(
            text(
                """
                select
                  user_id::text as user_id,
                  policy_version,
                  action,
                  surface,
                  request_id
                from privacy.consent_events
                where user_id = cast(:user_id as uuid)
                order by created_at, consent_event_id
                """
            ),
            {"user_id": user_id},
        ).mappings().all()
        return [dict(row) for row in rows]


def test_wechat_login_with_consent_records_one_accepted_event(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)

    response = client.post(
        "/api/v2/auth/wechat",
        json={
            "code": "valid-code",
            "deviceId": "wechat-device-consent",
            "legalAccepted": True,
        },
    )

    assert response.status_code == 200
    user_id = response.json()["data"]["user"]["userId"]
    rows = _consent_rows(db_session_factory, user_id)
    assert len(rows) == 1
    assert rows[0]["user_id"] == user_id
    assert rows[0]["policy_version"] == "login-terms@v1"
    assert rows[0]["action"] == "accepted"
    assert rows[0]["surface"] == "wechat_miniprogram_login"
    assert len(str(rows[0]["request_id"])) == 64


def test_wechat_login_without_consent_records_no_event(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)

    response = client.post(
        "/api/v2/auth/wechat",
        json={"code": "valid-code", "deviceId": "wechat-device-no-consent"},
    )

    assert response.status_code == 200
    user_id = response.json()["data"]["user"]["userId"]
    assert _consent_rows(db_session_factory, user_id) == []


def test_wechat_login_with_consent_is_idempotent_across_devices(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)

    first = client.post(
        "/api/v2/auth/wechat",
        json={
            "code": "valid-code",
            "deviceId": "wechat-device-consent-a",
            "legalAccepted": True,
        },
    )
    second = client.post(
        "/api/v2/auth/wechat",
        json={
            "code": "valid-code",
            "deviceId": "wechat-device-consent-b",
            "legalAccepted": True,
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    user_id = first.json()["data"]["user"]["userId"]
    assert second.json()["data"]["user"]["userId"] == user_id
    rows = _consent_rows(db_session_factory, user_id)
    assert len(rows) == 1


def test_terms_update_publishes_a_fresh_acceptance_for_the_new_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _make_client(db_session_factory)
    login = client.post(
        "/api/v2/auth/wechat",
        json={
            "code": "valid-code",
            "deviceId": "wechat-device-consent-v1",
            "legalAccepted": True,
        },
    ).json()["data"]
    user_id = login["user"]["userId"]

    # Publish a newer terms version, as a future migration would: the partial
    # unique index permits one active policy per policyKind, so the previous
    # version is retired before the new one goes live.
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update privacy.policy_versions
                set state = 'retired', retired_at = now()
                where policy_version = 'login-terms@v1'
                """
            )
        )
        content = (
            '{"policyKind":"login_terms","title":"用户协议与隐私保护指引",'
            '"surfaces":["wechat_miniprogram_login"],"effectiveFrom":"2026-09-01"}'
        )
        session.execute(
            text(
                """
                insert into privacy.policy_versions (
                  policy_version, title, content, content_sha256,
                  state, published_at, retired_at
                ) values (
                  'login-terms@v2', '用户协议与隐私保护指引',
                  cast(:content_json as jsonb),
                  encode(digest(convert_to(:content_sha, 'UTF8'), 'sha256'), 'hex'),
                  'active', now(), null
                )
                """
            ),
            {"content_json": content, "content_sha": content},
        )

    with db_session_factory() as session:
        AuthRepository(session).record_terms_acceptance(
            user_id=user_id,
            policy_version="login-terms@v2",
        )

    rows = _consent_rows(db_session_factory, user_id)
    assert {str(row["policy_version"]) for row in rows} == {
        "login-terms@v1",
        "login-terms@v2",
    }
    assert len(rows) == 2
