from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domains.knowledge.schemas import QuestionDetail


class PracticeBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class PracticeQuestionVersion(PracticeBaseModel):
    question_id: str = Field(alias="questionId")
    content_version_id: str = Field(
        alias="contentVersionId",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class PracticeTaskVersion(PracticeBaseModel):
    release_id: str = Field(alias="releaseId")
    node_id: str = Field(alias="nodeId")
    bundle_version_id: str = Field(
        alias="bundleVersionId",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    questions: list[PracticeQuestionVersion] = Field(min_length=3, max_length=3)


class PracticeQuestion(QuestionDetail):
    content_version_id: str = Field(alias="contentVersionId")


class PracticeTask(PracticeBaseModel):
    task_id: str = Field(alias="taskId")
    title: str
    track: str
    question_count: int = Field(alias="questionCount")
    task_version: PracticeTaskVersion = Field(alias="taskVersion")
    questions: list[PracticeQuestion]


class AnswerSubmission(PracticeBaseModel):
    question_id: str = Field(alias="questionId")
    selected_answer: str = Field(alias="selectedAnswer", min_length=1, max_length=8)
    task_version: PracticeTaskVersion = Field(alias="taskVersion")


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
