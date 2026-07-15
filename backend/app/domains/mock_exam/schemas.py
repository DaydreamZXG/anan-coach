from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.task_content.schemas import MaterialPosition, SafeMaterial, SafeQuestion


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
    revision: int = Field(ge=1)
    started_at: datetime = Field(alias="startedAt")
    deadline_at: datetime = Field(alias="deadlineAt")
    checkpoint: "MockExamCheckpoint"
    bundle_version_id: str = Field(alias="bundleVersionId", min_length=64, max_length=64)
    materials: list[SafeMaterial]
    questions: list[SafeQuestion]
    source_grounding: str = Field(alias="sourceGrounding")


class MockExamAnswer(MockExamBaseModel):
    question_id: str = Field(alias="questionId")
    selected_answer: str | None = Field(
        default=None, alias="selectedAnswer", max_length=8
    )

    @model_validator(mode="after")
    def normalize_answer(self) -> "MockExamAnswer":
        self.question_id = self.question_id.strip()
        self.selected_answer = (self.selected_answer or "").strip().upper() or None
        if not self.question_id:
            raise ValueError("question identifier cannot be blank")
        return self


class MockExamSubmitRequest(MockExamBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=1)
    answers: list[MockExamAnswer] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_questions(self) -> "MockExamSubmitRequest":
        ids = [answer.question_id for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("mock exam answers must contain unique question IDs")
        return self


class MockExamAnswerCheckpoint(MockExamBaseModel):
    question_id: str = Field(alias="questionId", min_length=1, max_length=240)
    selected_answer: str | None = Field(
        default=None, alias="selectedAnswer", max_length=8
    )
    eliminated_option_ids: list[str] = Field(
        default_factory=list, alias="eliminatedOptionIds", max_length=12
    )
    marked: bool = False
    draft_text: str = Field(default="", alias="draftText", max_length=4_000)
    material_position: MaterialPosition | None = Field(
        default=None, alias="materialPosition"
    )

    @model_validator(mode="after")
    def normalize_state(self) -> "MockExamAnswerCheckpoint":
        self.question_id = self.question_id.strip()
        self.selected_answer = (self.selected_answer or "").strip().upper() or None
        self.eliminated_option_ids = [
            item.strip().upper() for item in self.eliminated_option_ids
        ]
        self.draft_text = self.draft_text.strip()
        if not self.question_id or any(not item for item in self.eliminated_option_ids):
            raise ValueError("checkpoint identifiers cannot be blank")
        if len(self.eliminated_option_ids) != len(set(self.eliminated_option_ids)):
            raise ValueError("eliminated option identifiers must be unique")
        if self.selected_answer in set(self.eliminated_option_ids):
            raise ValueError("selected and eliminated options cannot overlap")
        return self


class MockExamCheckpoint(MockExamBaseModel):
    current_question_id: str = Field(alias="currentQuestionId")
    answers: list[MockExamAnswerCheckpoint]


class MockExamCheckpointRequest(MockExamBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    current_question_id: str = Field(
        alias="currentQuestionId", min_length=1, max_length=240
    )
    answers: list[MockExamAnswerCheckpoint] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_questions(self) -> "MockExamCheckpointRequest":
        ids = [answer.question_id for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("checkpoint must contain unique question IDs")
        return self


class MockExamCheckpointReceipt(MockExamBaseModel):
    session_id: str = Field(alias="sessionId")
    status: Literal["assigned"]
    revision: int = Field(ge=2)
    checkpoint: MockExamCheckpoint
    checkpoint_updated_at: datetime = Field(alias="checkpointUpdatedAt")


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
