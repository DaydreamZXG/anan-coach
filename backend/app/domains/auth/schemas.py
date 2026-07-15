from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.onboarding.v2_schemas import OnboardingProgress


class AuthBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class AppleLoginRequest(AuthBaseModel):
    identity_token: str = Field(alias="identityToken", min_length=10)
    authorization_code: str = Field(alias="authorizationCode", min_length=1)
    nonce: str = Field(min_length=16, max_length=128)
    device_id: str = Field(alias="deviceId", min_length=3, max_length=160)
    display_name: str | None = Field(default=None, alias="displayName", max_length=120)


class AuthenticatedUserResponse(AuthBaseModel):
    user_id: str = Field(alias="userId")
    learner_key: str = Field(alias="learnerKey")
    display_name: str | None = Field(default=None, alias="displayName")
    email: str | None = None


class AuthSessionResponse(AuthBaseModel):
    access_token: str = Field(alias="accessToken")
    token_type: str = Field(default="Bearer", alias="tokenType")
    expires_at: str = Field(alias="expiresAt")
    user: AuthenticatedUserResponse


class WechatLoginRequest(AuthBaseModel):
    code: str = Field(min_length=1, max_length=256)
    device_id: str = Field(alias="deviceId", min_length=8, max_length=160)


class WechatAuthSessionData(AuthSessionResponse):
    onboarding_complete: bool = Field(alias="onboardingComplete")
    onboarding_progress: OnboardingProgress = Field(alias="onboardingProgress")


class WechatAuthSessionEnvelope(AuthBaseModel):
    data: WechatAuthSessionData
    request_id: str = Field(alias="requestId")
    generated_at: str = Field(alias="generatedAt")


class AccountSessionView(AuthBaseModel):
    session_id: str = Field(alias="sessionId")
    device_id: str = Field(alias="deviceId")
    device_label: str = Field(alias="deviceLabel")
    created_at: datetime = Field(alias="createdAt")
    last_seen_at: datetime = Field(alias="lastSeenAt")
    expires_at: datetime = Field(alias="expiresAt")
    is_current: bool = Field(alias="isCurrent")


class AccountOverview(AuthBaseModel):
    account_id: str = Field(alias="accountId")
    account_status: Literal[
        "active",
        "recovery_required",
        "deletion_pending",
        "disabled",
    ] = Field(alias="accountStatus")
    wechat_linked: bool = Field(alias="wechatLinked")
    profile_version: int = Field(alias="profileVersion", ge=1)
    established_at: datetime = Field(alias="establishedAt")
    sessions: list[AccountSessionView]


class SessionRevocationResult(AuthBaseModel):
    session_id: str = Field(alias="sessionId")
    revoked: bool


class LogoutAllResult(AuthBaseModel):
    revoked_session_count: int = Field(alias="revokedSessionCount", ge=0)
