from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


PlanBasis = Literal["rules_v1"]

FeedCardType = Literal[
    "goal_gap",
    "coach_brief",
    "task_practice",
    "task_essay",
    "task_method_card",
    "mock_exam_due",
    "mock_review_pending",
    "wrong_cluster",
    "streak_restart",
    "calibration_note",
    "quota_notice",
    "companion_prompt",
    "external_capture_nudge",
    "fallback",
]


class GoalGapPayload(FeedBaseModel):
    target_score: float | None = Field(default=None, alias="targetScore")
    estimated_score: float | None = Field(default=None, alias="estimatedScore")
    days_left: int | None = Field(default=None, alias="daysLeft")
    position_name: str | None = Field(default=None, alias="positionName")


class CoachBriefPayload(FeedBaseModel):
    text: str
    plan_basis: PlanBasis = Field(alias="planBasis")


class TaskPracticePayload(FeedBaseModel):
    task_id: str = Field(alias="taskId")
    title: str
    subtitle: str
    module_name: str = Field(alias="moduleName")
    question_count: int = Field(alias="questionCount", ge=0)
    est_minutes: int = Field(alias="estMinutes", ge=0)
    start_url: str = Field(alias="startUrl")
    completed: bool


class TaskEssayPayload(FeedBaseModel):
    task_id: str = Field(alias="taskId")
    title: str
    subtitle: str
    question_type: str = Field(alias="questionType")
    est_minutes: int = Field(alias="estMinutes", ge=0)
    start_url: str = Field(alias="startUrl")
    completed: bool


class TaskMethodCardPayload(FeedBaseModel):
    task_id: str = Field(alias="taskId")
    card_id: str = Field(alias="cardId")
    card_title: str = Field(alias="cardTitle")
    est_minutes: int = Field(alias="estMinutes", ge=0)
    start_url: str = Field(alias="startUrl")
    completed: bool


class MockExamSessionSpec(FeedBaseModel):
    task_id: str = Field(alias="taskId")
    title: str
    start_url: str = Field(alias="startUrl")
    question_count: int | None = Field(default=None, alias="questionCount", ge=0)


class MockExamDuePayload(FeedBaseModel):
    session_spec: MockExamSessionSpec = Field(alias="sessionSpec")
    est_minutes: int = Field(alias="estMinutes", ge=0)
    completed: bool


class MockReviewPendingPayload(FeedBaseModel):
    task_id: str = Field(alias="taskId")
    session_id: str = Field(alias="sessionId")
    estimated_score: float | None = Field(default=None, alias="estimatedScore")
    review_url: str = Field(alias="reviewUrl")
    completed: bool


class WrongClusterPayload(FeedBaseModel):
    node_id: str = Field(alias="nodeId")
    display_path: str = Field(alias="displayPath")
    wrong_count: int = Field(alias="wrongCount", ge=0)


class StreakRestartPayload(FeedBaseModel):
    days_idle: int = Field(alias="daysIdle", ge=0)
    mini_task_id: str = Field(alias="miniTaskId")


class CalibrationNotePayload(FeedBaseModel):
    status_label: str = Field(alias="statusLabel")
    data_line: str = Field(alias="dataLine")
    delta: float | None = None


class QuotaNoticePayload(FeedBaseModel):
    remaining: int = Field(ge=0)
    reset_at: str | None = Field(default=None, alias="resetAt")


class CompanionPromptPayload(FeedBaseModel):
    suggested_question: str = Field(alias="suggestedQuestion")
    context_object_id: str | None = Field(default=None, alias="contextObjectId")


class ExternalCaptureNudgePayload(FeedBaseModel):
    text: str


class FallbackPayload(FeedBaseModel):
    title: str
    body: str
    deeplink: str | None = None


class FeedCardBase(FeedBaseModel):
    card_id: str = Field(alias="cardId")
    type: FeedCardType
    priority: int
    reason: str
    data_line: str | None = Field(default=None, alias="dataLine")


class GoalGapCard(FeedCardBase):
    type: Literal["goal_gap"] = "goal_gap"
    payload: GoalGapPayload


class CoachBriefCard(FeedCardBase):
    type: Literal["coach_brief"] = "coach_brief"
    payload: CoachBriefPayload


class TaskPracticeCard(FeedCardBase):
    type: Literal["task_practice"] = "task_practice"
    payload: TaskPracticePayload


class TaskEssayCard(FeedCardBase):
    type: Literal["task_essay"] = "task_essay"
    payload: TaskEssayPayload


class TaskMethodCard(FeedCardBase):
    type: Literal["task_method_card"] = "task_method_card"
    payload: TaskMethodCardPayload


class MockExamDueCard(FeedCardBase):
    type: Literal["mock_exam_due"] = "mock_exam_due"
    payload: MockExamDuePayload


class MockReviewPendingCard(FeedCardBase):
    type: Literal["mock_review_pending"] = "mock_review_pending"
    payload: MockReviewPendingPayload


class WrongClusterCard(FeedCardBase):
    type: Literal["wrong_cluster"] = "wrong_cluster"
    payload: WrongClusterPayload


class StreakRestartCard(FeedCardBase):
    type: Literal["streak_restart"] = "streak_restart"
    payload: StreakRestartPayload


class CalibrationNoteCard(FeedCardBase):
    type: Literal["calibration_note"] = "calibration_note"
    payload: CalibrationNotePayload


class QuotaNoticeCard(FeedCardBase):
    type: Literal["quota_notice"] = "quota_notice"
    payload: QuotaNoticePayload


class CompanionPromptCard(FeedCardBase):
    type: Literal["companion_prompt"] = "companion_prompt"
    payload: CompanionPromptPayload


class ExternalCaptureNudgeCard(FeedCardBase):
    type: Literal["external_capture_nudge"] = "external_capture_nudge"
    payload: ExternalCaptureNudgePayload


class FallbackCard(FeedCardBase):
    type: Literal["fallback"] = "fallback"
    payload: FallbackPayload


FeedCard = Annotated[
    GoalGapCard
    | CoachBriefCard
    | TaskPracticeCard
    | TaskEssayCard
    | TaskMethodCard
    | MockExamDueCard
    | MockReviewPendingCard
    | WrongClusterCard
    | StreakRestartCard
    | CalibrationNoteCard
    | QuotaNoticeCard
    | CompanionPromptCard
    | ExternalCaptureNudgeCard
    | FallbackCard,
    Field(discriminator="type"),
]


class TodayFeed(FeedBaseModel):
    generated_at: str = Field(alias="generatedAt")
    plan_basis: PlanBasis = Field(alias="planBasis")
    cards: list[FeedCard]
