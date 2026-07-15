from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domains.knowledge.schemas import QuestionDetail


class PracticeBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class PracticeTask(PracticeBaseModel):
    task_id: str = Field(alias="taskId")
    title: str
    track: str
    question_count: int = Field(alias="questionCount")
    questions: list[QuestionDetail]


class AnswerSubmission(PracticeBaseModel):
    question_id: str = Field(alias="questionId")
    selected_answer: str = Field(alias="selectedAnswer", min_length=1, max_length=8)


class AnswerFeedback(PracticeBaseModel):
    task_id: str = Field(alias="taskId")
    question_id: str = Field(alias="questionId")
    selected_answer: str = Field(alias="selectedAnswer")
    correct_answer: str | None = Field(alias="correctAnswer")
    correct: bool
    explanation: str | None
    explanation_source: str | None = Field(alias="explanationSource")
    source_count: int = Field(alias="sourceCount")
    source_summary: str = Field(alias="sourceSummary")
    next_action: str = Field(alias="nextAction")
    xp_gained: int = Field(default=0, alias="xpGained")
