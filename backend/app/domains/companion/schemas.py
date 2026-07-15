from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.essay.schemas import AIQuotaState


class CompanionBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


CompanionScenario = Literal["why_wrong", "how_to_learn", "should_skip_module"]


class CompanionAskRequest(CompanionBaseModel):
    scenario: CompanionScenario
    question_text: str = Field(alias="questionText", min_length=4, max_length=800)
    node_id: str | None = Field(default=None, alias="nodeId")


class CompanionCitation(CompanionBaseModel):
    kind: Literal[
        "learner_memory",
        "essay_learning",
        "method_card",
        "target_position",
        "knowledge_map",
    ]
    title: str
    source_id: str | None = Field(default=None, alias="sourceId")
    summary: str


class CompanionAskResponse(CompanionBaseModel):
    scenario: CompanionScenario
    answer: str
    citations: list[CompanionCitation]
    next_actions: list[str] = Field(alias="nextActions")
    quota: AIQuotaState


class ExtractedTagCandidate(CompanionBaseModel):
    module_name: str = Field(alias="moduleName", min_length=1, max_length=80)
    tag_type: str = Field(alias="tagType", min_length=1, max_length=80)
    tag_value: str = Field(alias="tagValue", min_length=1, max_length=120)
    confidence: float = Field(ge=0, le=1)


class ExternalQuestionResolveRequest(CompanionBaseModel):
    stem: str = Field(min_length=8, max_length=4000)
    options: list[str] = Field(default_factory=list, max_length=12)
    selected_answer: str | None = Field(default=None, alias="selectedAnswer", max_length=20)
    correct_answer: str | None = Field(default=None, alias="correctAnswer", max_length=20)
    extracted_tags: list[ExtractedTagCandidate] = Field(alias="extractedTags", min_length=1)
    contains_image: bool = Field(default=False, alias="containsImage")
    extraction_confidence: float = Field(alias="extractionConfidence", ge=0, le=1)


class KnowledgeMapCandidate(CompanionBaseModel):
    node_id: str = Field(alias="nodeId")
    display_path: str = Field(alias="displayPath")
    module_name: str = Field(alias="moduleName")
    tag_type: str = Field(alias="tagType")
    tag_value: str = Field(alias="tagValue")
    support_status: str = Field(alias="supportStatus")
    confidence: float


class ExternalQuestionResolveResponse(CompanionBaseModel):
    intake_id: str = Field(alias="intakeId")
    status: Literal["pending_confirmation", "rejected", "confirmed"]
    reason: str | None = None
    candidates: list[KnowledgeMapCandidate]
    requires_user_confirmation: bool = Field(alias="requiresUserConfirmation")
    answer_preview: str | None = Field(default=None, alias="answerPreview")
    quota: AIQuotaState


class ExternalQuestionConfirmRequest(CompanionBaseModel):
    user_confirmed: bool = Field(alias="userConfirmed")
    error_summary: str = Field(alias="errorSummary", min_length=2, max_length=500)


class ExternalQuestionConfirmResponse(CompanionBaseModel):
    external_question_id: str = Field(alias="externalQuestionId")
    intake_id: str = Field(alias="intakeId")
    node_id: str = Field(alias="nodeId")
    memory_source: Literal["external"] = Field(alias="memorySource")
    status: Literal["confirmed"]
