from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.task_content.schemas import MaterialPosition, SafeMaterial, SafeQuestion
from app.domains.xingce_learning.real_attempt.schemas import (
    AnnotationStroke,
    AttemptActionEvent,
)


class MockExamBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class MockExamModuleBlueprint(MockExamBaseModel):
    module_name: str = Field(alias="moduleName")
    question_count: int = Field(alias="questionCount")


class MockExamStartRequest(MockExamBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    plan_task_id: UUID | None = Field(default=None, alias="planTaskId")


class MockExamSession(MockExamBaseModel):
    session_id: str = Field(alias="sessionId")
    plan_task_id: UUID | None = Field(default=None, alias="planTaskId")
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
    paper_version_id: str = Field(alias="paperVersionId")
    scoring_policy_version_id: str = Field(alias="scoringPolicyVersionId")
    score_metric_name: str = Field(alias="scoreMetricName")
    score_metric_unit: str = Field(alias="scoreMetricUnit")
    score_metric_definition: str = Field(alias="scoreMetricDefinition")
    next_trajectory_event_seq: int = Field(alias="nextTrajectoryEventSeq", ge=1)
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
    # 契约增量 v1：模考检查点复用行测真实训练的批注/动作事实结构
    # （import 自 real_attempt.schemas，不复制定义）。模考端没有批注层入口，
    # annotations 通常为空数组；动作事件与溢出标记语义与 AnswerCheckpoint 一致。
    annotations: list[AnnotationStroke] = Field(default_factory=list, max_length=120)
    action_events: list[AttemptActionEvent] = Field(
        default_factory=list,
        alias="actionEvents",
        max_length=400,
    )
    action_events_truncated: bool | None = Field(
        default=None, alias="actionEventsTruncated"
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
        stroke_ids = [stroke.stroke_id for stroke in self.annotations]
        if len(stroke_ids) != len(set(stroke_ids)):
            raise ValueError("annotation stroke identifiers must be unique")
        # 契约增量 v1：atMs 为本题累计作答时长（非降序即可，同值合法）。
        if any(
            later.at_ms < earlier.at_ms
            for earlier, later in zip(self.action_events, self.action_events[1:])
        ):
            raise ValueError("action events must be ordered by atMs")
        session_marks = [
            event.session_at_ms
            for event in self.action_events
            if event.session_at_ms is not None
        ]
        if any(
            later < earlier for earlier, later in zip(session_marks, session_marks[1:])
        ):
            raise ValueError("action events must be ordered by sessionAtMs")
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


class MockExamPerformanceRange(MockExamBaseModel):
    lower: float = Field(ge=0, le=100)
    recommended: float = Field(ge=0, le=100)
    upper: float = Field(ge=0, le=100)


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
    performance_index: float = Field(alias="performanceIndex", ge=0, le=100)
    performance_range: MockExamPerformanceRange = Field(alias="performanceRange")
    metric_name: Literal[
        "balanced_performance_index", "question_weighted_performance_index"
    ] = Field(alias="metricName")
    metric_unit: Literal["index_points"] = Field(alias="metricUnit")
    metric_definition: str = Field(alias="metricDefinition")
    interval_definition: str = Field(alias="intervalDefinition")
    paper_version_id: str = Field(alias="paperVersionId")
    scoring_policy_version_id: str = Field(alias="scoringPolicyVersionId")
    submitted_at: str = Field(alias="submittedAt")
    viewed: bool
    modules: list[MockExamModuleReview]
    wrong_clusters: list[MockExamWrongCluster] = Field(alias="wrongClusters")
    next_week_reason: str = Field(alias="nextWeekReason")


class MockExamSubmitResponse(MockExamBaseModel):
    session_id: str = Field(alias="sessionId")
    status: Literal["submitted", "reviewed"]
    review: MockExamReview


class MockExamTrajectoryEventCommand(MockExamBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_seq: int = Field(alias="eventSeq", ge=1)
    event_type: Literal[
        "visited", "answer_changed", "mark_changed", "skipped", "left", "resumed"
    ] = Field(alias="eventType")
    question_id: str | None = Field(default=None, alias="questionId", min_length=1, max_length=240)
    client_occurred_at: datetime = Field(alias="clientOccurredAt")
    session_revision: int = Field(alias="sessionRevision", ge=1)
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_question_scope(self) -> "MockExamTrajectoryEventCommand":
        if self.event_type in {"visited", "answer_changed", "mark_changed", "skipped"}:
            if self.question_id is None:
                raise ValueError("question events require questionId")
        elif self.question_id is not None:
            raise ValueError("lifecycle events cannot bind questionId")
        if self.client_occurred_at.tzinfo is None:
            raise ValueError("clientOccurredAt must include timezone")
        return self


class MockExamTrajectoryEventReceipt(MockExamBaseModel):
    trajectory_event_id: str = Field(alias="trajectoryEventId")
    session_id: str = Field(alias="sessionId")
    event_seq: int = Field(alias="eventSeq")
    event_type: str = Field(alias="eventType")
    accepted_at: datetime = Field(alias="acceptedAt")
