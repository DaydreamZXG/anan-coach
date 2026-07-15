from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.planning.schemas import TodayDashboard


class XingceModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class StartTaskSessionCommand(XingceModel):
    plan_task_id: UUID = Field(alias="planTaskId")


class MaterialPosition(XingceModel):
    anchor_id: str = Field(alias="anchorId", min_length=1, max_length=240)
    scroll_top: int = Field(alias="scrollTop", ge=0)
    zoom: float = Field(ge=0.5, le=5)


class AnswerCheckpoint(XingceModel):
    question_id: str = Field(alias="questionId", min_length=1, max_length=240)
    selected_option_ids: list[str] = Field(
        default_factory=list,
        alias="selectedOptionIds",
        max_length=12,
    )
    eliminated_option_ids: list[str] = Field(
        default_factory=list,
        alias="eliminatedOptionIds",
        max_length=12,
    )
    confidence: Literal["low", "medium", "high"] | None = None
    marked: bool = False
    skipped: bool = False
    elapsed_seconds: int = Field(alias="elapsedSeconds", ge=0, le=86_400)
    draft_text: str = Field(default="", alias="draftText", max_length=4_000)
    material_position: MaterialPosition | None = Field(
        default=None, alias="materialPosition"
    )

    @model_validator(mode="after")
    def validate_answer_shape(self) -> "AnswerCheckpoint":
        selected = [item.strip().upper() for item in self.selected_option_ids]
        eliminated = [item.strip().upper() for item in self.eliminated_option_ids]
        if any(not item for item in selected + eliminated):
            raise ValueError("option identifiers cannot be blank")
        if len(set(selected)) != len(selected) or len(set(eliminated)) != len(
            eliminated
        ):
            raise ValueError("option identifiers must be unique")
        if set(selected) & set(eliminated):
            raise ValueError("selected and eliminated options cannot overlap")
        if self.skipped and selected:
            raise ValueError("a skipped answer cannot select an option")
        if not self.skipped and not selected and not (self.draft_text or "").strip():
            raise ValueError(
                "an answer must select an option, contain draft text, or be skipped"
            )
        self.selected_option_ids = selected
        self.eliminated_option_ids = eliminated
        self.draft_text = self.draft_text.strip()
        return self


class SaveCheckpointCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    current_question_id: str = Field(
        alias="currentQuestionId", min_length=1, max_length=240
    )
    answers: list[AnswerCheckpoint] = Field(min_length=1, max_length=100)
    total_elapsed_seconds: int = Field(alias="totalElapsedSeconds", ge=0, le=86_400)

    @model_validator(mode="after")
    def unique_questions(self) -> "SaveCheckpointCommand":
        ids = [answer.question_id for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("checkpoint answers must contain unique question IDs")
        return self


class SubmitTaskSessionCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    total_elapsed_seconds: int = Field(alias="totalElapsedSeconds", ge=0, le=86_400)


class ReflectionCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    raw_text: str = Field(alias="rawText", min_length=1, max_length=4_000)
    tags: list[str] = Field(min_length=1, max_length=12)
    strategy_note: str | None = Field(
        default=None, alias="strategyNote", max_length=2_000
    )
    doubt_note: str | None = Field(default=None, alias="doubtNote", max_length=2_000)

    @model_validator(mode="after")
    def normalize_user_voice(self) -> "ReflectionCommand":
        self.raw_text = self.raw_text.strip()
        tags = [tag.strip() for tag in self.tags]
        if (
            not self.raw_text
            or any(not tag for tag in tags)
            or len(tags) != len(set(tags))
        ):
            raise ValueError("reflection text and unique non-blank tags are required")
        self.tags = tags
        self.strategy_note = (self.strategy_note or "").strip() or None
        self.doubt_note = (self.doubt_note or "").strip() or None
        return self


class CauseDecisionCommand(XingceModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    decision: Literal["replace", "skip"]
    replacement_cause: str | None = Field(
        default=None, alias="replacementCause", max_length=2_000
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "CauseDecisionCommand":
        self.replacement_cause = (self.replacement_cause or "").strip() or None
        if self.decision == "replace" and self.replacement_cause is None:
            raise ValueError("replace requires replacementCause")
        if self.decision == "skip" and self.replacement_cause is not None:
            raise ValueError("skip cannot include replacementCause")
        return self


class SafeOption(XingceModel):
    option_id: str = Field(alias="optionId")
    label: str
    text: str
    html: str | None = None


class SafeAsset(XingceModel):
    asset_id: str = Field(alias="assetId")
    url: str
    alt_text: str = Field(alias="altText")


class SafeMaterial(XingceModel):
    material_id: str = Field(alias="materialId")
    title: str | None = None
    text: str | None = None
    html: str | None = None
    assets: list[SafeAsset]


class SafeQuestion(XingceModel):
    question_id: str = Field(alias="questionId")
    content_version_id: str = Field(alias="contentVersionId")
    material_id: str | None = Field(default=None, alias="materialId")
    module_name: str = Field(alias="moduleName")
    stem: str
    stem_html: str | None = Field(default=None, alias="stemHtml")
    options: list[SafeOption]


class SafeQuestionBundle(XingceModel):
    bundle_version_id: str = Field(
        alias="bundleVersionId", min_length=64, max_length=64
    )
    bundle_type: Literal["single", "shared_material"] = Field(alias="bundleType")
    materials: list[SafeMaterial]
    questions: list[SafeQuestion] = Field(min_length=1)


class XingceResultItem(XingceModel):
    question_id: str = Field(alias="questionId")
    result_code: Literal[
        "correct", "incorrect", "timeout", "guessed_correct", "skipped"
    ] = Field(alias="resultCode")
    selected_option_ids: list[str] = Field(alias="selectedOptionIds")
    correct_option_ids: list[str] = Field(alias="correctOptionIds")
    explanation: str | None = None
    explanation_source: str | None = Field(default=None, alias="explanationSource")


class XingceAttemptResult(XingceModel):
    attempt_id: UUID = Field(alias="attemptId")
    result_code: Literal[
        "correct", "incorrect", "timeout", "guessed_correct", "skipped", "mixed"
    ] = Field(alias="resultCode")
    correct_count: int = Field(alias="correctCount", ge=0)
    total_count: int = Field(alias="totalCount", ge=1)
    items: list[XingceResultItem]


class ReflectionVersionProjection(XingceModel):
    reflection_version_id: UUID = Field(alias="reflectionVersionId")
    raw_text: str = Field(alias="rawText")
    tags: list[str]
    strategy_note: str | None = Field(default=None, alias="strategyNote")
    doubt_note: str | None = Field(default=None, alias="doubtNote")
    version_no: int = Field(alias="versionNo", ge=1)


class CauseDecisionVersionProjection(XingceModel):
    decision_version_id: UUID = Field(alias="decisionVersionId")
    decision: Literal["replace", "skip"]
    replacement_cause: str | None = Field(default=None, alias="replacementCause")
    version_no: int = Field(alias="versionNo", ge=1)


class AICauseCandidateState(XingceModel):
    state: Literal["unavailable"] = "unavailable"
    reason: Literal["scenario_not_activated"] = "scenario_not_activated"
    charged: Literal[False] = False


class AttemptReviewProjection(XingceModel):
    review_id: UUID = Field(alias="reviewId")
    revision: int = Field(ge=1)
    state: Literal[
        "reflection_required",
        "cause_candidate_pending",
        "cause_decision_required",
        "immediate_verification_due",
    ]
    ai_candidate: AICauseCandidateState = Field(alias="aiCandidate")
    reflection: ReflectionVersionProjection | None = None
    cause_decision: CauseDecisionVersionProjection | None = Field(
        default=None,
        alias="causeDecision",
    )


class XingceCompletionReceipt(XingceModel):
    receipt_id: UUID = Field(alias="receiptId")
    settlement_status: Literal["completed", "late_evidence_linked"] = Field(
        alias="settlementStatus"
    )
    plan: TodayDashboard


class TaskSessionProjection(XingceModel):
    session_id: UUID = Field(alias="sessionId")
    plan_task_id: UUID = Field(alias="planTaskId")
    revision: int = Field(ge=1)
    state: Literal[
        "answering",
        "submitted",
        "reflection_required",
        "cause_decision_required",
        "completed",
    ]
    mode: Literal["practice", "targeted", "baseline", "verification"]
    bundle: SafeQuestionBundle
    current_question_id: str = Field(alias="currentQuestionId")
    answers: list[AnswerCheckpoint]
    total_elapsed_seconds: int = Field(alias="totalElapsedSeconds", ge=0)
    result: XingceAttemptResult | None = None
    review: AttemptReviewProjection | None = None
    completion: XingceCompletionReceipt | None = None
