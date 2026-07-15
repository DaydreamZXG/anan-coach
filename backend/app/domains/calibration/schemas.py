from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CalibrationBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CalibrationSources(CalibrationBaseModel):
    baseline: int
    practice_answers: int = Field(alias="practiceAnswers")
    mock_exams: int = Field(alias="mockExams")


class ModuleAbilityEstimate(CalibrationBaseModel):
    module_name: str = Field(alias="moduleName")
    accuracy: float
    sample_size: int = Field(alias="sampleSize")
    source: Literal["practice", "mock_exam"]


class AbilityEstimate(CalibrationBaseModel):
    xingce_score: float | None = Field(alias="xingceScore")
    confidence: Literal["unavailable", "low", "medium", "high"]
    status_label: str = Field(alias="statusLabel")
    data_line: str = Field(alias="dataLine")
    sources: CalibrationSources
    module_estimates: list[ModuleAbilityEstimate] = Field(alias="moduleEstimates")
    latest_mock_reason: str | None = Field(default=None, alias="latestMockReason")
