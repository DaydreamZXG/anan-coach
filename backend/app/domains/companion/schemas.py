from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.essay.schemas import AIQuotaState
from app.domains.ai.governance.contracts import GlobalAskContext


class CompanionBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CompanionAskRequest(CompanionBaseModel):
    question_text: str = Field(alias="questionText", min_length=1, max_length=800)
    context: GlobalAskContext

    @field_validator("question_text")
    @classmethod
    def meaningful_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if re.search(r"[\u3400-\u9fffA-Za-z0-9]", normalized) is None:
            raise ValueError("question must contain a meaningful word or number")
        return normalized


class CompanionCitation(CompanionBaseModel):
    kind: str = Field(min_length=1, max_length=80)
    title: str
    source_id: str = Field(alias="sourceId")
    version: str
    anchor: str
    summary: str


class CompanionAllowedAction(CompanionBaseModel):
    kind: Literal[
        "view_evidence",
        "add_reflection",
        "adjust_plan_preview",
        "supply_information",
        "open_training",
        "open_position_workspace",
        "refresh_context",
    ]
    label: str = Field(min_length=1, max_length=80)
    target_ref: str | None = Field(default=None, alias="targetRef", max_length=1024)
    requires_confirmation: bool = Field(alias="requiresConfirmation")


class CompanionHighImpactDecision(CompanionBaseModel):
    decision_type: Literal["position_explanation", "strategic_deprioritization"] = Field(
        alias="decisionType"
    )
    status: Literal["explain_only", "evidence_insufficient"]
    evidence_refs: list[str] = Field(alias="evidenceRefs", max_length=20)
    requires_confirmation: Literal[True] = Field(default=True, alias="requiresConfirmation")
    prohibited_mutations: list[Literal["qualification", "primary_goal", "strategy"]] = Field(
        alias="prohibitedMutations", min_length=1
    )

    @model_validator(mode="after")
    def explained_decision_has_evidence(self) -> "CompanionHighImpactDecision":
        if self.status == "explain_only" and not self.evidence_refs:
            raise ValueError("an explain-only high-impact decision requires evidence")
        return self


class CompanionAskResponse(CompanionBaseModel):
    answer_id: str = Field(alias="answerId")
    semantic_state: Literal[
        "ready",
        "evidence_insufficient",
        "permission_denied",
        "security_blocked",
        "quota_exhausted",
        "provider_pending",
        "failed",
    ] = Field(alias="semanticState")
    scenario_key: str = Field(alias="scenarioKey")
    responding_agent: Literal["position", "tutor", "planner"] = Field(alias="respondingAgent")
    answer_type: str = Field(alias="answerType")
    policy_decision: str = Field(alias="policyDecision")
    companion_intent: Literal[
        "companion",
        "thinking",
        "reminder",
        "encouragement",
        "rest",
        "deprioritize",
    ] = Field(alias="companionIntent")
    answer: str
    citations: list[CompanionCitation]
    confidence: Literal["low", "medium", "high"]
    limitations: list[str] = Field(max_length=8)
    knowledge_freshness: str = Field(alias="knowledgeFreshness")
    allowed_actions: list[CompanionAllowedAction] = Field(alias="allowedActions", max_length=3)
    high_impact_decision: CompanionHighImpactDecision | None = Field(
        default=None, alias="highImpactDecision"
    )
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
