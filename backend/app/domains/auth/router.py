from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.domains.auth.apple import AppleIdentityVerificationError
from app.domains.auth.deps import (
    get_auth_repository,
    get_auth_service,
    get_current_user,
    require_bearer_token,
)
from app.domains.auth.repository import (
    AuthAccountUnavailableError,
    AuthRepository,
    AuthenticatedUser,
)
from app.domains.auth.schemas import (
    AppleLoginRequest,
    AccountOverview,
    AccountProfileUpdate,
    AccountProfileView,
    AccountSessionView,
    AuthenticatedUserResponse,
    AuthSessionResponse,
    WechatAuthSessionData,
    WechatAuthSessionEnvelope,
    WechatLoginRequest,
    LogoutAllResult,
    SessionRevocationResult,
)
from app.domains.auth.service import AuthService
from app.domains.auth.wechat import (
    WechatLoginConfigurationError,
    WechatLoginRejectedError,
    WechatLoginTransportError,
)
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_error_response,
    public_success_envelope,
    request_id_from,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
wechat_router = APIRouter(
    prefix="/api/v2/auth",
    tags=["auth-v2"],
    responses=COMMON_API_ERROR_RESPONSES,
)


@router.post("/apple", response_model=AuthSessionResponse)
def login_with_apple(
    request: AppleLoginRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthSessionResponse:
    try:
        session = service.login_with_apple(request)
    except AppleIdentityVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Apple identity token is invalid",
        ) from exc
    except AuthAccountUnavailableError as exc:
        detail = (
            "Account deletion is pending"
            if exc.account_status == "deletion_pending"
            else "Account is disabled"
        )
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
                if exc.account_status == "deletion_pending"
                else status.HTTP_403_FORBIDDEN
            ),
            detail=detail,
        ) from exc
    return AuthSessionResponse(
        access_token=session.access_token,
        expires_at=session.expires_at.isoformat(),
        user=_user_response(session.user),
    )


@router.get("/me", response_model=AuthenticatedUserResponse)
def get_me(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUserResponse:
    return _user_response(current_user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    access_token: Annotated[str, Depends(require_bearer_token)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> None:
    repository.revoke_session(access_token)


@wechat_router.post("/wechat", response_model=WechatAuthSessionEnvelope)
def login_with_wechat(
    login_request: WechatLoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> WechatAuthSessionEnvelope | JSONResponse:
    request_id = request_id_from(request)
    response.headers["X-Request-ID"] = request_id
    try:
        session = service.login_with_wechat(login_request)
    except WechatLoginConfigurationError:
        return public_error_response(
            request=request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="wechat_login_not_configured",
            message="微信登录尚未完成服务端配置。",
        )
    except WechatLoginRejectedError as exc:
        if exc.error_code == 45011:
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
            error_code = "wechat_login_rate_limited"
            error_message = "微信登录请求过于频繁，请稍后重试。"
        elif exc.error_code == -1:
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            error_code = "wechat_login_upstream_unavailable"
            error_message = "微信登录服务暂时不可用。"
        else:
            status_code = status.HTTP_401_UNAUTHORIZED
            error_code = "wechat_login_rejected"
            error_message = "微信登录凭证无效或已过期，请重新登录。"
        return public_error_response(
            request=request,
            status_code=status_code,
            code=error_code,
            message=error_message,
        )
    except WechatLoginTransportError:
        return public_error_response(
            request=request,
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="wechat_login_upstream_unavailable",
            message="微信登录服务暂时不可用。",
        )
    except AuthAccountUnavailableError as exc:
        if exc.account_status == "deletion_pending":
            return public_error_response(
                request=request,
                status_code=status.HTTP_409_CONFLICT,
                code="account_deletion_pending",
                message="账户正在执行注销流程，暂时不能登录。",
            )
        return public_error_response(
            request=request,
            status_code=status.HTTP_403_FORBIDDEN,
            code="account_disabled",
            message="账户当前不可用，请联系支持人员。",
        )

    onboarding_progress = repository.get_onboarding_progress(
        user_id=session.user.user_id
    )
    return WechatAuthSessionEnvelope(
        data=WechatAuthSessionData(
            access_token=session.access_token,
            expires_at=session.expires_at.isoformat(),
            user=_user_response(session.user),
            onboarding_complete=onboarding_progress.completed,
            onboarding_progress=onboarding_progress,
        ),
        request_id=request_id,
        generated_at=datetime.now(UTC).isoformat(),
    )


def _user_response(user: AuthenticatedUser) -> AuthenticatedUserResponse:
    return AuthenticatedUserResponse(
        user_id=user.user_id,
        learner_key=user.learner_key,
        display_name=user.display_name,
        email=user.email,
    )


@wechat_router.get(
    "/account",
    response_model=PublicApiEnvelope[AccountOverview],
)
def get_account_overview(
    request: Request,
    response: Response,
    access_token: Annotated[str, Depends(require_bearer_token)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> PublicApiEnvelope[AccountOverview]:
    overview = repository.get_account_overview(user_id=current_user.user_id)
    sessions = repository.list_active_sessions(
        user_id=current_user.user_id,
        current_access_token=access_token,
    )
    return public_success_envelope(
        request=request,
        response=response,
        data=AccountOverview(
            account_id=overview.user_id,
            display_name=overview.display_name,
            avatar_style=overview.avatar_style,
            account_status=overview.account_status,
            wechat_linked=overview.wechat_linked,
            profile_version=overview.profile_version,
            established_at=overview.created_at,
            sessions=[
                AccountSessionView(
                    session_id=item.session_id,
                    device_id=item.device_id,
                    device_label="当前微信设备" if item.is_current else "另一台微信设备",
                    created_at=item.created_at,
                    last_seen_at=item.last_seen_at,
                    expires_at=item.expires_at,
                    is_current=item.is_current,
                )
                for item in sessions
            ],
        ),
    )


@wechat_router.put(
    "/account/profile",
    response_model=PublicApiEnvelope[AccountProfileView],
)
def update_account_profile(
    command: AccountProfileUpdate,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> PublicApiEnvelope[AccountProfileView]:
    display_name, avatar_style, updated_at = repository.update_account_profile(
        user_id=current_user.user_id,
        display_name=command.display_name,
        avatar_style=command.avatar_style,
    )
    return public_success_envelope(
        request=request,
        response=response,
        data=AccountProfileView(
            display_name=display_name,
            avatar_style=avatar_style,
            updated_at=updated_at,
        ),
    )


@wechat_router.delete(
    "/sessions/{session_id}",
    response_model=PublicApiEnvelope[SessionRevocationResult],
)
def revoke_account_session(
    request: Request,
    response: Response,
    session_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> PublicApiEnvelope[SessionRevocationResult]:
    parsed_session_id = str(session_id)
    revoked = repository.revoke_owned_session(
        user_id=current_user.user_id,
        session_id=parsed_session_id,
    )
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return public_success_envelope(
        request=request,
        response=response,
        data=SessionRevocationResult(session_id=parsed_session_id, revoked=True),
    )


@wechat_router.post(
    "/logout",
    response_model=PublicApiEnvelope[LogoutAllResult],
)
def logout_current_wechat_session(
    request: Request,
    response: Response,
    access_token: Annotated[str, Depends(require_bearer_token)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> PublicApiEnvelope[LogoutAllResult]:
    del current_user
    repository.revoke_session(access_token, reason="user_logout")
    return public_success_envelope(
        request=request,
        response=response,
        data=LogoutAllResult(revoked_session_count=1),
    )


@wechat_router.post(
    "/logout-all",
    response_model=PublicApiEnvelope[LogoutAllResult],
)
def logout_all_wechat_sessions(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> PublicApiEnvelope[LogoutAllResult]:
    count = repository.revoke_all_sessions(user_id=current_user.user_id)
    return public_success_envelope(
        request=request,
        response=response,
        data=LogoutAllResult(revoked_session_count=count),
    )
