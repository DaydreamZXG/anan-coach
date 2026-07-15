from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocketBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class DocketOption(DocketBaseModel):
    label: str
    text: str
    html: str | None = None

    @model_validator(mode="after")
    def normalize_display_text(self) -> DocketOption:
        self.text = _strip_option_label_prefix(self.text, self.label)
        return self


class RankSnapshot(DocketBaseModel):
    rank_code: str = Field(alias="rankCode")
    rank_level: int = Field(alias="rankLevel")
    rank_name: str = Field(alias="rankName")
    xp: int
    fame: int
    stamina: int
    consecutive_days: int = Field(alias="consecutiveDays")
    ui_theme: str = Field(alias="uiTheme")
    unlock_features: list[str] = Field(default_factory=list, alias="unlockFeatures")
    next_rank_name: str | None = Field(default=None, alias="nextRankName")
    xp_to_next: int | None = Field(default=None, alias="xpToNext")


class CourtStateSnapshot(DocketBaseModel):
    popular_sentiment: int = Field(alias="popularSentiment")
    stability: int
    demerits: int
    risk_level: Literal["stable", "watch", "danger"] = Field(alias="riskLevel")
    background_tone: Literal["paper", "muted_red", "dark_red"] = Field(alias="backgroundTone")


class DocketCard(DocketBaseModel):
    card_id: str = Field(alias="cardId")
    type: Literal["daily_affair"]
    track: Literal["xingce"]
    interaction: Literal["swipe_verdict"] = "swipe_verdict"
    question_id: str = Field(alias="questionId")
    module_name: str = Field(alias="moduleName")
    world_label: str = Field(alias="worldLabel")
    title: str
    narrative: str
    stem: str
    stem_html: str | None = Field(default=None, alias="stemHtml")
    material_text: str | None = Field(default=None, alias="materialText")
    material_html: str | None = Field(default=None, alias="materialHtml")
    options: list[DocketOption]
    source_grounding: Literal["product.questions"] = Field(
        default="product.questions",
        alias="sourceGrounding",
    )
    source_summary: str = Field(alias="sourceSummary")
    occurrence_count: int | None = Field(default=None, alias="occurrenceCount")
    latest_year: int | None = Field(default=None, alias="latestYear")


class DocketOpenCourtResponse(DocketBaseModel):
    docket_id: str = Field(alias="docketId")
    opened_at: datetime = Field(alias="openedAt")
    source_grounding: Literal["product.questions + product.knowledge_map_nodes"] = Field(
        default="product.questions + product.knowledge_map_nodes",
        alias="sourceGrounding",
    )
    companion_line: str = Field(alias="companionLine")
    rank_snapshot: RankSnapshot = Field(alias="rankSnapshot")
    court_state: CourtStateSnapshot = Field(alias="courtState")
    cards: list[DocketCard]


def _strip_option_label_prefix(text: str, label: str) -> str:
    normalized = text.strip()
    label = label.strip()
    if not label or not normalized.startswith(label):
        return normalized
    normalized = normalized[len(label):]
    separators = {"、", ".", "．", "，", ",", ":", "：", ")", "）", " "}
    while normalized and normalized[0] in separators:
        normalized = normalized[1:]
    return normalized
