from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.domains.auth.apple import (
    AppleIdentityClaims,
    AppleIdentityVerificationError,
)
from app.domains.auth.deps import get_apple_identity_verifier, get_auth_repository
from app.domains.auth.repository import AuthenticatedUser, CreatedSession
from app.main import create_app


class FakeAppleIdentityVerifier:
    def verify(self, *, identity_token: str, raw_nonce: str) -> AppleIdentityClaims:
        if identity_token == "invalid-apple-token":
            raise AppleIdentityVerificationError("invalid")
        assert raw_nonce == "0123456789abcdef"
        return AppleIdentityClaims(
            apple_sub="apple-sub-1",
            email="learner@example.com",
            email_verified=True,
            is_private_email=False,
        )


class InMemoryAuthRepository:
    def __init__(self) -> None:
        self.user_by_sub: dict[str, AuthenticatedUser] = {}
        self.user_by_token: dict[str, AuthenticatedUser] = {}

    def upsert_apple_user(
        self,
        *,
        claims: AppleIdentityClaims,
        display_name: str | None,
        identity_token_hash: str,
    ) -> AuthenticatedUser:
        user = self.user_by_sub.get(claims.apple_sub)
        if user is None:
            user = AuthenticatedUser(
                user_id="user-auth-1",
                learner_key="user-auth-1",
                display_name=display_name,
                email=claims.email,
            )
            self.user_by_sub[claims.apple_sub] = user
        return user

    def create_session(
        self,
        *,
        user: AuthenticatedUser,
        device_id: str,
    ) -> CreatedSession:
        assert device_id == "ios-device-1"
        access_token = "session-token-1"
        self.user_by_token[access_token] = user
        return CreatedSession(
            access_token=access_token,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            user=user,
        )

    def get_user_by_access_token(self, access_token: str) -> AuthenticatedUser | None:
        return self.user_by_token.get(access_token)

    def revoke_session(self, access_token: str) -> None:
        self.user_by_token.pop(access_token, None)


def make_client() -> TestClient:
    app = create_app()
    repository = InMemoryAuthRepository()
    app.dependency_overrides[get_auth_repository] = lambda: repository
    app.dependency_overrides[get_apple_identity_verifier] = (
        lambda: FakeAppleIdentityVerifier()
    )
    return TestClient(app)


def test_apple_login_creates_backend_session_and_me_contract() -> None:
    client = make_client()

    response = client.post(
        "/api/v1/auth/apple",
        json={
            "identityToken": "valid-apple-token",
            "authorizationCode": "apple-code",
            "nonce": "0123456789abcdef",
            "deviceId": "ios-device-1",
            "displayName": "小张",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tokenType"] == "Bearer"
    assert body["accessToken"] == "session-token-1"
    assert body["user"]["learnerKey"] == "user-auth-1"

    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer session-token-1"},
    )

    assert me.status_code == 200
    assert me.json()["email"] == "learner@example.com"


def test_apple_login_rejects_invalid_identity_token() -> None:
    response = make_client().post(
        "/api/v1/auth/apple",
        json={
            "identityToken": "invalid-apple-token",
            "authorizationCode": "apple-code",
            "nonce": "0123456789abcdef",
            "deviceId": "ios-device-1",
        },
    )

    assert response.status_code == 401


def test_me_requires_bearer_session_not_legacy_header() -> None:
    client = make_client()

    missing = client.get("/api/v1/auth/me")
    legacy = client.get("/api/v1/auth/me", headers={"X-Learner-Id": "any"})

    assert missing.status_code == 401
    assert legacy.status_code == 401
