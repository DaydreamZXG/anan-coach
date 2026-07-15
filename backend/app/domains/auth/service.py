from __future__ import annotations

from hashlib import sha256

from app.domains.auth.apple import AppleIdentityVerifier
from app.domains.auth.repository import AuthRepository, CreatedSession
from app.domains.auth.schemas import AppleLoginRequest, WechatLoginRequest
from app.domains.auth.wechat import WechatIdentityExchange


class AuthService:
    def __init__(
        self,
        *,
        repository: AuthRepository,
        apple_verifier: AppleIdentityVerifier,
        wechat_exchange: WechatIdentityExchange,
    ) -> None:
        self._repository = repository
        self._apple_verifier = apple_verifier
        self._wechat_exchange = wechat_exchange

    def login_with_apple(self, request: AppleLoginRequest) -> CreatedSession:
        claims = self._apple_verifier.verify(
            identity_token=request.identity_token,
            raw_nonce=request.nonce,
        )
        user = self._repository.upsert_apple_user(
            claims=claims,
            display_name=request.display_name,
            identity_token_hash=sha256(request.identity_token.encode("utf-8")).hexdigest(),
        )
        return self._repository.create_session(
            user=user,
            device_id=request.device_id,
        )

    def login_with_wechat(self, request: WechatLoginRequest) -> CreatedSession:
        claims = self._wechat_exchange.exchange(code=request.code)
        user = self._repository.upsert_wechat_user(claims=claims)
        return self._repository.create_session(
            user=user,
            device_id=request.device_id,
        )
