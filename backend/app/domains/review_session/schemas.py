from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.progression.schemas import ProgressionEventResult


class ReviewSessionBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ReviewVerdictRequest(ReviewSessionBaseModel):
    selected_answer: str = Field(alias="selectedAnswer", min_length=1, max_length=8)
    elapsed_seconds: int | None = Field(default=None, alias="elapsedSeconds", ge=0)


class ReviewVerdictResponse(ReviewSessionBaseModel):
    verdict_id: str = Field(alias="verdictId")
    docket_id: str = Field(alias="docketId")
    card_id: str = Field(alias="cardId")
    question_id: str = Field(alias="questionId")
    selected_answer: str = Field(alias="selectedAnswer")
    correct: bool
    stamp_text: Literal["准", "驳"] = Field(alias="stampText")
    companion_line: str = Field(alias="companionLine")
    progression: ProgressionEventResult
    source_grounding: Literal["learner.verdicts + product.questions"] = Field(
        default="learner.verdicts + product.questions",
        alias="sourceGrounding",
    )
    created_at: datetime = Field(alias="createdAt")
