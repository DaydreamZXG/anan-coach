from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from math import isfinite
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MathInkModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class InkStroke(MathInkModel):
    x: tuple[float, ...] = Field(min_length=2, max_length=2000)
    y: tuple[float, ...] = Field(min_length=2, max_length=2000)
    t: tuple[int, ...] = Field(min_length=2, max_length=2000)

    @model_validator(mode="after")
    def aligned_points(self) -> "InkStroke":
        if not (len(self.x) == len(self.y) == len(self.t)):
            raise ValueError("stroke coordinate and time arrays must align")
        if any(not isfinite(value) or value < 0 or value > 10000 for value in (*self.x, *self.y)):
            raise ValueError("stroke coordinates are outside the supported surface")
        if any(value < 0 for value in self.t) or any(
            later < earlier for earlier, later in pairwise(self.t)
        ):
            raise ValueError("stroke timestamps must be non-negative and monotonic")
        return self


class RecognizeMathInkCommand(MathInkModel):
    session_id: UUID = Field(alias="sessionId")
    question_id: str = Field(alias="questionId", min_length=1, max_length=240)
    session_at_ms: int | None = Field(
        default=None,
        alias="sessionAtMs",
        ge=0,
        le=86_400_000,
    )
    question_elapsed_ms: int | None = Field(
        default=None,
        alias="questionElapsedMs",
        ge=0,
        le=86_400_000,
    )
    scale_x: float = Field(alias="scaleX", gt=0.01, le=5)
    scale_y: float = Field(alias="scaleY", gt=0.01, le=5)
    strokes: tuple[InkStroke, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounded_request(self) -> "RecognizeMathInkCommand":
        if sum(len(stroke.x) for stroke in self.strokes) > 20_000:
            raise ValueError("math ink request exceeds 20000 points")
        if (
            self.session_at_ms is not None
            and self.question_elapsed_ms is not None
            and self.question_elapsed_ms > self.session_at_ms
        ):
            raise ValueError("question elapsed time cannot exceed the session active time")
        return self


class ConfirmFormulaCommand(MathInkModel):
    expected_version_no: int = Field(alias="expectedVersionNo", ge=1)
    latex: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def normalize_latex(self) -> "ConfirmFormulaCommand":
        self.latex = self.latex.strip()
        if not self.latex:
            raise ValueError("confirmed formula cannot be blank")
        return self


class FormulaVersion(MathInkModel):
    formula_version_id: UUID = Field(alias="formulaVersionId")
    formula_id: UUID = Field(alias="formulaId")
    request_id: UUID = Field(alias="requestId")
    session_id: UUID = Field(alias="sessionId")
    question_id: str = Field(alias="questionId")
    version_no: int = Field(alias="versionNo", ge=1)
    state: Literal["candidate", "confirmed", "rejected"]
    latex: str
    source: Literal["myscript_candidate", "xfyun_candidate", "user_confirmed"]
    supersedes_formula_version_id: UUID | None = Field(alias="supersedesFormulaVersionId")
    created_at: datetime = Field(alias="createdAt")
    confirmed_at: datetime | None = Field(alias="confirmedAt")


class MathInkRecognitionResult(MathInkModel):
    state: Literal["candidate", "failed"]
    request_id: UUID = Field(alias="requestId")
    formula: FormulaVersion | None
    failure_code: str | None = Field(alias="failureCode")
