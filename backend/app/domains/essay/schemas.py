from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EssayBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class EssayMaterial(EssayBaseModel):
    index: int
    title: str
    text: str
    source: str | None = None


class EssayTask(EssayBaseModel):
    task_id: str = Field(alias="taskId")
    question_id: str | None = Field(default=None, alias="questionId")
    title: str
    material: str
    materials: list[EssayMaterial]
    material_incomplete: bool = Field(alias="materialIncomplete")
    prompt: str
    word_limit: int = Field(alias="wordLimit")
    dimensions: list[str]


class AIQuotaState(EssayBaseModel):
    feature: str
    used: int
    access_mode: Literal["unlimited", "entitlement_quota"] = Field(alias="accessMode")
    limit: int | None
    date: str
