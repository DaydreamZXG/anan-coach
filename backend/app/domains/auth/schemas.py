from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    legal_accepted: bool = Field(default=False, alias="legalAccepted")


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
    display_name: str | None = Field(default=None, alias="displayName")
    avatar_style: Literal[
        "ocean",
        "aurora",
        "sunrise",
        "violet",
        "graphite",
        "mint",
    ] = Field(alias="avatarStyle")
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


class AccountProfileUpdate(AuthBaseModel):
    display_name: str = Field(alias="displayName", min_length=1, max_length=24)
    avatar_style: Literal[
        "ocean",
        "aurora",
        "sunrise",
        "violet",
        "graphite",
        "mint",
    ] = Field(alias="avatarStyle")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("displayName must contain visible characters")
        return normalized


class AccountProfileView(AuthBaseModel):
    display_name: str = Field(alias="displayName")
    avatar_style: Literal[
        "ocean",
        "aurora",
        "sunrise",
        "violet",
        "graphite",
        "mint",
    ] = Field(alias="avatarStyle")
    updated_at: datetime = Field(alias="updatedAt")


class SessionRevocationResult(AuthBaseModel):
    session_id: str = Field(alias="sessionId")
    revoked: bool


class LogoutAllResult(AuthBaseModel):
    revoked_session_count: int = Field(alias="revokedSessionCount", ge=0)
