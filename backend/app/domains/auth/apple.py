from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import jwt
from jwt import PyJWKClient

from app.core.config import Settings

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"


class AppleIdentityVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class AppleIdentityClaims:
    apple_sub: str
    email: str | None
    email_verified: bool
    is_private_email: bool


class AppleIdentityVerifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks_client = PyJWKClient(APPLE_JWKS_URL)

    def verify(self, *, identity_token: str, raw_nonce: str) -> AppleIdentityClaims:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(identity_token)
            payload = jwt.decode(
                identity_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.bundle_id,
                issuer=APPLE_ISSUER,
                options={"require": ["iss", "aud", "exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AppleIdentityVerificationError("Apple identity token is invalid") from exc

        expected_nonce = sha256(raw_nonce.encode("utf-8")).hexdigest()
        token_nonce = payload.get("nonce")
        if token_nonce != expected_nonce:
            raise AppleIdentityVerificationError("Apple identity nonce is invalid")

        apple_sub = payload.get("sub")
        if not isinstance(apple_sub, str) or not apple_sub:
            raise AppleIdentityVerificationError("Apple identity subject is missing")

        return AppleIdentityClaims(
            apple_sub=apple_sub,
            email=_optional_str(payload.get("email")),
            email_verified=_bool_claim(payload.get("email_verified")),
            is_private_email=_bool_claim(payload.get("is_private_email")),
        )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bool_claim(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return False
