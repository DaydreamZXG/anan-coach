from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.knowledge.schemas import QuestionDetail


class MockExamBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class MockExamModuleBlueprint(MockExamBaseModel):
    module_name: str = Field(alias="moduleName")
    question_count: int = Field(alias="questionCount")


class MockExamSession(MockExamBaseModel):
    session_id: str = Field(alias="sessionId")
    status: Literal["assigned", "submitted", "reviewed"]
    title: str
    duration_minutes: int = Field(alias="durationMinutes")
    question_count: int = Field(alias="questionCount")
    blueprint: list[MockExamModuleBlueprint]
    questions: list[QuestionDetail]
    source_grounding: str = Field(alias="sourceGrounding")


class MockExamAnswer(MockExamBaseModel):
    question_id: str = Field(alias="questionId")
    selected_answer: str = Field(alias="selectedAnswer", min_length=1, max_length=8)


class MockExamSubmitRequest(MockExamBaseModel):
    answers: list[MockExamAnswer]


class MockExamModuleReview(MockExamBaseModel):
    module_name: str = Field(alias="moduleName")
    correct: int
    total: int
    accuracy: float
    change_from_previous: float | None = Field(default=None, alias="changeFromPrevious")


class MockExamWrongCluster(MockExamBaseModel):
    node_id: str = Field(alias="nodeId")
    display_path: str = Field(alias="displayPath")
    wrong_count: int = Field(alias="wrongCount")
    method_card_ids: list[str] = Field(alias="methodCardIds")
    fix_action: str = Field(alias="fixAction")


class MockExamReview(MockExamBaseModel):
    session_id: str = Field(alias="sessionId")
    estimated_score: float = Field(alias="estimatedScore")
    estimate_source: str = Field(alias="estimateSource")
    submitted_at: str = Field(alias="submittedAt")
    viewed: bool
    modules: list[MockExamModuleReview]
    wrong_clusters: list[MockExamWrongCluster] = Field(alias="wrongClusters")
    next_week_reason: str = Field(alias="nextWeekReason")


class MockExamSubmitResponse(MockExamBaseModel):
    session_id: str = Field(alias="sessionId")
    status: Literal["submitted", "reviewed"]
    review: MockExamReview
