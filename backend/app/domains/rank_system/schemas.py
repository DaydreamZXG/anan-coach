from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RankConfigItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rank_code: str
    rank_level: int
    rank_name: str
    xp_threshold: int
    ui_theme: str
    ai_privilege: str | None = None
    unlock_features: list[str] = []


class OfficialRankProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str
    rank_code: str
    rank_level: int
    rank_name: str
    xp: int
    fame: int
    stamina: int
    consecutive_days: int
    demerits: int
    ui_theme: str
    next_rank_name: str | None = None
    next_rank_xp_threshold: int | None = None
    xp_to_next: int | None = None
    unlock_features: list[str] = []


class XpAwardResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    xp_gained: int
    fame_delta: int
    new_xp: int
    new_fame: int
    promoted: bool = False
    old_rank_name: str | None = None
    new_rank_name: str | None = None
    combo_bonus: int = 0
