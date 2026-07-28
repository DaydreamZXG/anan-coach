from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


FavoriteStateName = Literal["favorited", "not_favorited"]
FavoriteAvailability = Literal["available", "unavailable"]
FAVORITE_PREVIEW_MODULE_MAX_LENGTH = 80
FAVORITE_PREVIEW_STEM_MAX_LENGTH = 240


class QuestionFavoriteModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SetQuestionFavoriteCommand(QuestionFavoriteModel):
    desired_state: FavoriteStateName = Field(alias="desiredState")
    content_version_id: str | None = Field(
        default=None,
        alias="contentVersionId",
        pattern=r"^[0-9a-f]{64}$",
    )
    source_session_id: UUID | None = Field(default=None, alias="sourceSessionId")

    @model_validator(mode="after")
    def validate_source(self) -> "SetQuestionFavoriteCommand":
        if self.desired_state == "favorited":
            if self.content_version_id is None or self.source_session_id is None:
                raise ValueError(
                    "favorited state requires contentVersionId and sourceSessionId"
                )
        elif self.content_version_id is not None or self.source_session_id is not None:
            raise ValueError(
                "not_favorited state cannot carry content or session provenance"
            )
        return self


class QuestionFavoriteReceipt(QuestionFavoriteModel):
    contract_version: Literal["xingce-question-favorite@v1"] = Field(
        default="xingce-question-favorite@v1",
        alias="contractVersion",
    )
    question_id: str = Field(alias="questionId")
    favorite_state: FavoriteStateName = Field(alias="favoriteState")
    revision: int = Field(ge=0)
    saved_content_version_id: str | None = Field(
        default=None,
        alias="savedContentVersionId",
    )
    favorited_at: datetime | None = Field(default=None, alias="favoritedAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    @model_validator(mode="after")
    def validate_receipt(self) -> "QuestionFavoriteReceipt":
        if self.favorite_state == "favorited":
            if (
                self.revision < 1
                or self.saved_content_version_id is None
                or self.favorited_at is None
                or self.updated_at is None
            ):
                raise ValueError("favorited receipt requires persisted provenance")
        else:
            if (
                self.saved_content_version_id is not None
                or self.favorited_at is not None
            ):
                raise ValueError("not_favorited receipt cannot carry provenance")
            if (self.revision == 0) != (self.updated_at is None):
                raise ValueError(
                    "revision-zero receipt must represent an absent state row"
                )
        return self


class QuestionFavoriteState(QuestionFavoriteReceipt):
    current_content_version_id: str | None = Field(
        default=None,
        alias="currentContentVersionId",
    )
    availability: FavoriteAvailability
    content_changed: bool = Field(alias="contentChanged")

    @model_validator(mode="after")
    def validate_dynamic_state(self) -> "QuestionFavoriteState":
        if self.availability == "available" and self.current_content_version_id is None:
            raise ValueError("available question requires a current content version")
        if self.content_changed and (
            self.favorite_state != "favorited"
            or self.saved_content_version_id is None
            or self.current_content_version_id is None
            or self.saved_content_version_id == self.current_content_version_id
        ):
            raise ValueError("contentChanged requires two different content versions")
        return self


class QuestionFavoritePreview(QuestionFavoriteModel):
    module_name: str | None = Field(
        default=None,
        alias="moduleName",
        max_length=FAVORITE_PREVIEW_MODULE_MAX_LENGTH,
    )
    stem: str = Field(
        min_length=1,
        max_length=FAVORITE_PREVIEW_STEM_MAX_LENGTH,
    )


class QuestionFavoriteListItem(QuestionFavoriteState):
    preview: QuestionFavoritePreview | None = None

    @model_validator(mode="after")
    def validate_preview(self) -> "QuestionFavoriteListItem":
        if self.availability == "unavailable" and self.preview is not None:
            raise ValueError("unavailable question cannot expose a preview")
        return self


class QuestionFavoritePage(QuestionFavoriteModel):
    contract_version: Literal["xingce-question-favorites-page@v1"] = Field(
        default="xingce-question-favorites-page@v1",
        alias="contractVersion",
    )
    items: list[QuestionFavoriteListItem]
    next_cursor: str | None = Field(
        default=None,
        alias="nextCursor",
        description=(
            "账户内实时 keyset 游标，不承诺跨请求的强快照；"
            "并发变更后可刷新首屏读取当前状态。"
        ),
    )
    generated_at: datetime = Field(alias="generatedAt")
