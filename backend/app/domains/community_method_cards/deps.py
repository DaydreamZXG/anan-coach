from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import (
    get_auth_repository,
    get_current_user,
    require_bearer_token,
)
from app.domains.auth.repository import AuthRepository, AuthenticatedUser
from app.domains.community_method_cards.composition import (
    AuthenticatedSessionContext,
    build_community_method_card_service,
)
from app.domains.community_method_cards.effects import EffectPolicyUnavailable
from app.domains.community_method_cards.moderation import ReviewPolicyUnavailable
from app.domains.community_method_cards.service import (
    CommunityMethodCardError,
    CommunityMethodCardService,
)


def get_authenticated_session_context(
    access_token: Annotated[str, Depends(require_bearer_token)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> AuthenticatedSessionContext:
    app_session = repository.get_active_session(
        access_token=access_token,
        user_id=current_user.user_id,
    )
    if app_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization bearer session is invalid",
        )
    return AuthenticatedSessionContext(
        session_id=app_session.session_id,
        user_id=current_user.user_id,
        device_id=app_session.device_id,
    )


def get_community_method_card_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authenticated_session: Annotated[
        AuthenticatedSessionContext,
        Depends(get_authenticated_session_context),
    ],
) -> CommunityMethodCardService:
    try:
        return build_community_method_card_service(
            session=session,
            settings=settings,
            authenticated_session=authenticated_session,
        )
    except (EffectPolicyUnavailable, ReviewPolicyUnavailable) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Published community policy is unavailable",
        ) from exc
    except CommunityMethodCardError as exc:
        if "policy" not in exc.code:
            raise
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Published community policy is unavailable",
        ) from exc
