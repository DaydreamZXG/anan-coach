from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MethodCardBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class MethodCardApplicability(MethodCardBaseModel):
    include: list[str]
    exclude: list[str]


class MethodCardErrorSignature(MethodCardBaseModel):
    code: str
    description: str


class MethodCard(MethodCardBaseModel):
    id: str
    exam_scope: str = Field(alias="examScope")
    track: Literal["xingce", "shenlun"]
    module_name: str = Field(alias="moduleName")
    tag_path: str = Field(alias="tagPath")
    title: str
    recognition: str
    applicability: MethodCardApplicability
    method_steps: list[str] = Field(alias="methodSteps")
    error_signatures: list[MethodCardErrorSignature] = Field(alias="errorSignatures")
    fix_action: str = Field(alias="fixAction")
    time_budget_sec: int = Field(alias="timeBudgetSec")
    accuracy_rule: str = Field(alias="accuracyRule")
    pitfall: str | None = None
    priority: int
    status: Literal["published"]
