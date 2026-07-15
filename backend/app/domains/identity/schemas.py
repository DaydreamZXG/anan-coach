from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IdentityBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class LearnerIdentity(IdentityBaseModel):
    """Server-owned identity; clients never submit these identifiers as authority."""

    account_id: str = Field(alias="accountId")
    account_status: Literal[
        "active",
        "recovery_required",
        "deletion_pending",
        "disabled",
    ] = Field(alias="accountStatus")
    wechat_linked: bool = Field(alias="wechatLinked")
    profile_version: int = Field(alias="profileVersion", ge=0)
    established_at: datetime = Field(alias="establishedAt")
