from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProgressionBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CourtStateSnapshot(ProgressionBaseModel):
    popular_sentiment: int = Field(alias="popularSentiment")
    stability: int
    demerits: int
    risk_level: Literal["stable", "watch", "danger"] = Field(alias="riskLevel")
    background_tone: Literal["paper", "muted_red", "dark_red"] = Field(
        alias="backgroundTone"
    )


class ProgressionEventResult(ProgressionBaseModel):
    merit_delta: int = Field(alias="meritDelta")
    fame_delta: int = Field(alias="fameDelta")
    court_risk_delta: int = Field(alias="courtRiskDelta")
    rank_changed: bool = Field(alias="rankChanged")
    old_rank_name: str | None = Field(default=None, alias="oldRankName")
    new_rank_name: str | None = Field(default=None, alias="newRankName")
    locked_privileges: list[str] = Field(default_factory=list, alias="lockedPrivileges")
    narrative: str
