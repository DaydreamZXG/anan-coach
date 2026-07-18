from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


AI_GOVERNANCE_CONTRACT_VERSION = "ai-governance@v1"

AgentKey = Literal["position", "tutor", "planner", "recognition"]
ModelTier = Literal["small", "high"]
ScenarioKey = Literal[
    "position_explanation",
    "tutoring_explanation",
    "xingce_cause_analysis",
    "essay_grounded_review",
    "essay_rubric_compilation",
    "planning_explanation",
    "planning_strategic_deprioritization",
    "recognition_printed_ocr",
    "recognition_handwriting_ocr",
    "recognition_score_report",
    "external_question_answer",
]
CompanionIntent = Literal[
    "companion",
    "thinking",
    "reminder",
    "encouragement",
    "rest",
    "deprioritize",
]
SemanticState = Literal[
    "ready",
    "evidence_insufficient",
    "permission_denied",
    "security_blocked",
    "quota_exhausted",
    "provider_pending",
    "failed",
]


class GovernanceModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class GlobalAskObjectRef(GovernanceModel):
    type: Literal["position", "question", "material", "attempt", "plan", "report"]
    id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)


class GlobalAskContext(GovernanceModel):
    route_id: str = Field(alias="routeId", min_length=1, max_length=200)
    workspace: Literal["today", "training", "position", "profile"]
    object_refs: tuple[GlobalAskObjectRef, ...] = Field(
        default=(), alias="objectRefs", max_length=12
    )
    task_mode: Literal["practice", "retest", "mock", "essay"] | None = Field(
        default=None, alias="taskMode"
    )
    task_stage: Literal["reading", "answering", "submitted", "reviewing"] | None = Field(
        default=None, alias="taskStage"
    )
    learner_state_version: str = Field(
        alias="learnerStateVersion", min_length=1, max_length=200
    )

    @model_validator(mode="after")
    def unique_refs(self) -> "GlobalAskContext":
        identities = [(item.type, item.id, item.version) for item in self.object_refs]
        if len(identities) != len(set(identities)):
            raise ValueError("GlobalAskContext objectRefs must be unique")
        if (self.task_mode is None) != (self.task_stage is None):
            raise ValueError("taskMode and taskStage must be supplied together")
        return self


class IntentDecision(GovernanceModel):
    scenario_key: ScenarioKey = Field(alias="scenarioKey")
    responding_agent: AgentKey = Field(alias="respondingAgent")
    answer_type: Literal[
        "position_explanation",
        "learning_explanation",
        "cause_explanation",
        "plan_explanation",
        "strategic_deprioritization",
        "operation_help",
    ] = Field(alias="answerType")
    model_tier: ModelTier = Field(alias="modelTier")
    policy_decision: Literal[
        "answer",
        "operation_only",
        "post_submit_only",
        "evidence_required",
        "domain_forbidden",
        "security_blocked",
    ] = Field(alias="policyDecision")
    companion_intent: CompanionIntent = Field(alias="companionIntent")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes", min_length=1, max_length=8)


class EvidenceReference(GovernanceModel):
    kind: str = Field(min_length=1, max_length=80)
    source_id: str = Field(alias="sourceId", min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=200)
    anchor: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=200)


class AICallAttribution(GovernanceModel):
    contract_version: Literal["ai-governance@v1"] = Field(
        default=AI_GOVERNANCE_CONTRACT_VERSION, alias="contractVersion"
    )
    scenario_key: ScenarioKey = Field(alias="scenarioKey")
    operation: str = Field(min_length=1, max_length=160)
    responding_agent: AgentKey = Field(alias="respondingAgent")
    impact_level: Literal["low", "medium", "high"] = Field(alias="impactLevel")
    model_tier: ModelTier = Field(alias="modelTier")
    deployment_id: UUID | None = Field(default=None, alias="deploymentId")
    evaluation_state: Literal["passed", "local_unreleased"] = Field(alias="evaluationState")
    route_policy_version: str = Field(alias="routePolicyVersion", min_length=1)
    permission_policy_version: str = Field(alias="permissionPolicyVersion", min_length=1)
    injection_policy_version: str = Field(alias="injectionPolicyVersion", min_length=1)
    evidence_refs: tuple[EvidenceReference, ...] = Field(
        alias="evidenceRefs", min_length=1, max_length=50
    )
    retrieval_bundle_id: UUID | None = Field(default=None, alias="retrievalBundleId")
    reservation_id: UUID | None = Field(default=None, alias="reservationId")
    reservation_source: Literal["catalog", "community_reward"] | None = Field(
        default=None, alias="reservationSource"
    )
    entitlement_state: Literal["reserved", "not_applicable"] = Field(
        alias="entitlementState"
    )

    @model_validator(mode="after")
    def coherent(self) -> "AICallAttribution":
        if self.evaluation_state == "passed" and self.deployment_id is None:
            raise ValueError("passed attribution requires a deploymentId")
        if self.evaluation_state == "local_unreleased" and self.deployment_id is not None:
            raise ValueError("local_unreleased attribution cannot name a deployment")
        if self.entitlement_state == "reserved" and self.reservation_id is None:
            raise ValueError("reserved attribution requires reservationId")
        if self.entitlement_state == "reserved" and self.reservation_source is None:
            raise ValueError("reserved attribution requires reservationSource")
        if self.entitlement_state == "not_applicable" and (
            self.reservation_id is not None or self.reservation_source is not None
        ):
            raise ValueError("not_applicable attribution cannot name a reservation")
        identities = [
            (item.kind, item.source_id, item.version, item.anchor)
            for item in self.evidence_refs
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("AI call evidence references must be unique")
        return self

    @property
    def evidence_fingerprint(self) -> str:
        payload = [item.model_dump(by_alias=True, mode="json") for item in self.evidence_refs]
        return sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
