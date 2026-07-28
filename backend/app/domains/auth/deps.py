from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.apple import AppleIdentityVerifier
from app.domains.auth.repository import AuthRepository, AuthenticatedUser
from app.domains.auth.service import AuthService
from app.domains.auth.wechat import WechatIdentityExchange


bearer_security = HTTPBearer(
    scheme_name="BearerAuth",
    description="Opaque access token issued by the authentication endpoints.",
    auto_error=False,
)


def get_auth_repository(
    session: Annotated[Session, Depends(get_db_session)],
) -> AuthRepository:
    return AuthRepository(session)


def get_apple_identity_verifier() -> AppleIdentityVerifier:
    return AppleIdentityVerifier(get_settings())


def get_wechat_identity_exchange() -> WechatIdentityExchange:
    return WechatIdentityExchange(get_settings())


def get_auth_service(
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    apple_verifier: Annotated[AppleIdentityVerifier, Depends(get_apple_identity_verifier)],
    wechat_exchange: Annotated[
        WechatIdentityExchange,
        Depends(get_wechat_identity_exchange),
    ],
) -> AuthService:
    return AuthService(
        repository=repository,
        apple_verifier=apple_verifier,
        wechat_exchange=wechat_exchange,
    )


def require_bearer_token(
    request: Request,
    _security_credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_security),
    ],
) -> str:
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization bearer token is required",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization bearer token is invalid",
        )
    return token


def get_current_user(
    access_token: Annotated[str, Depends(require_bearer_token)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> AuthenticatedUser:
    user = repository.get_user_by_access_token(access_token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization bearer token is invalid",
        )
    return user
