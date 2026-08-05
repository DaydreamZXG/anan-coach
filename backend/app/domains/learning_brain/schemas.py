from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domains.ability_estimation.schemas import AbilityEstimateCurrent


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
BrainTrack = Literal["xingce", "essay"]
BrainReadiness = Literal["unavailable", "partial", "ready", "stale", "conflicted"]
EvidenceConfidence = Literal["unknown", "low", "medium", "high"]


class LearningBrainModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class BrainEvidenceRef(LearningBrainModel):
    owner: str = Field(min_length=1, max_length=80)
    entity_type: str = Field(alias="entityType", min_length=1, max_length=80)
    entity_id: str = Field(alias="entityId", min_length=1, max_length=180)
    version_id: str | None = Field(default=None, alias="versionId", max_length=180)
    occurred_at: datetime | None = Field(default=None, alias="occurredAt")
    role: str | None = Field(default=None, max_length=120)


class BrainEvidenceSummary(LearningBrainModel):
    score_evidence_count: int = Field(alias="scoreEvidenceCount", ge=0)
    xingce_attempt_count: int = Field(alias="xingceAttemptCount", ge=0)
    xingce_mastery_count: int = Field(alias="xingceMasteryCount", ge=0)
    essay_mastery_count: int = Field(alias="essayMasteryCount", ge=0)
    task_execution_count: int = Field(alias="taskExecutionCount", ge=0)
    external_answer_count: int = Field(default=0, alias="externalAnswerCount", ge=0)
    latest_evidence_at: datetime | None = Field(default=None, alias="latestEvidenceAt")

    @property
    def total_count(self) -> int:
        return (
            self.score_evidence_count
            + self.xingce_attempt_count
            + self.xingce_mastery_count
            + self.essay_mastery_count
            + self.task_execution_count
            + self.external_answer_count
        )


class BrainCandidateSummary(LearningBrainModel):
    awaiting_confirmation: int = Field(alias="awaitingConfirmation", ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    expired: int = Field(ge=0)


class BrainDecisionSummary(LearningBrainModel):
    active_strategy_count: int = Field(alias="activeStrategyCount", ge=0)
    review_due_count: int = Field(alias="reviewDueCount", ge=0)
    current_plan_version_id: UUID | None = Field(default=None, alias="currentPlanVersionId")
    current_weekly_plan_version_id: UUID | None = Field(
        default=None,
        alias="currentWeeklyPlanVersionId",
    )


class AbilityBand(LearningBrainModel):
    lower: float = Field(ge=0, le=100)
    recommended: float = Field(ge=0, le=100)
    upper: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_order(self) -> "AbilityBand":
        if not self.lower <= self.recommended <= self.upper:
            raise ValueError("ability band must be ordered")
        return self


class ModuleAbility(LearningBrainModel):
    track: BrainTrack
    module_key: str = Field(alias="moduleKey", min_length=1, max_length=160)
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    readiness: Literal["unavailable", "developing", "reliable", "stale", "conflicted"]
    score_band: AbilityBand | None = Field(default=None, alias="scoreBand")
    evidence_count: int = Field(alias="evidenceCount", ge=0)
    external_evidence_count: int = Field(default=0, alias="externalEvidenceCount", ge=0)
    correct_rate: float | None = Field(default=None, alias="correctRate", ge=0, le=1)
    average_seconds: float | None = Field(default=None, alias="averageSeconds", ge=0)
    stable_mastery_count: int = Field(alias="stableMasteryCount", ge=0)
    due_count: int = Field(alias="dueCount", ge=0)
    confidence: EvidenceConfidence
    freshness: Literal["current", "stale", "unavailable"]
    latest_evidence_at: datetime | None = Field(default=None, alias="latestEvidenceAt")
    conflict_codes: list[str] = Field(alias="conflictCodes")
    evidence_refs: list[BrainEvidenceRef] = Field(alias="evidenceRefs")

    @model_validator(mode="after")
    def validate_availability(self) -> "ModuleAbility":
        if self.readiness == "unavailable" and self.evidence_count != 0:
            raise ValueError("unavailable module cannot carry evidence")
        if self.evidence_count == 0 and self.readiness != "unavailable":
            raise ValueError("available module requires evidence")
        if self.correct_rate is not None and self.track != "xingce":
            raise ValueError("only Xingce module evidence exposes correctness")
        return self


class KnowledgePointWeakness(LearningBrainModel):
    """知识点级弱项投影（仅样本充足者，每模块最多 3 个）。

    数据来源为 calibration 域的知识点级正确率（practice_answers join
    product.question_tags），样本不足与未分类标签不进入本榜单。
    """

    module_name: str = Field(alias="moduleName", min_length=1, max_length=160)
    tag_value: str = Field(alias="tagValue", min_length=1, max_length=160)
    accuracy: float = Field(ge=0, le=1)
    sample_size: int = Field(alias="sampleSize", ge=1)


class ErrorPatternSummary(LearningBrainModel):
    pattern_id: Sha256 = Field(alias="patternId")
    track: BrainTrack
    module_key: str = Field(alias="moduleKey", min_length=1, max_length=160)
    cause_code: str = Field(alias="causeCode", min_length=1, max_length=160)
    display_name: str = Field(alias="displayName", min_length=1, max_length=240)
    status: Literal["confirmed", "counterevidence", "retired"]
    occurrence_count: int = Field(alias="occurrenceCount", ge=1)
    counterevidence_count: int = Field(alias="counterevidenceCount", ge=0)
    confidence: EvidenceConfidence
    latest_observed_at: datetime = Field(alias="latestObservedAt")
    evidence_refs: list[BrainEvidenceRef] = Field(alias="evidenceRefs", min_length=1)


class LearningBrainProfileVersion(LearningBrainModel):
    contract_version: Literal["learning-brain-profile@v1"] = Field(
        default="learning-brain-profile@v1",
        alias="contractVersion",
    )
    profile_version_id: UUID = Field(alias="profileVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    readiness: BrainReadiness
    ability: AbilityEstimateCurrent
    evidence_summary: BrainEvidenceSummary = Field(alias="evidenceSummary")
    candidate_summary: BrainCandidateSummary = Field(alias="candidateSummary")
    modules: list[ModuleAbility]
    error_patterns: list[ErrorPatternSummary] = Field(alias="errorPatterns")
    knowledge_point_weaknesses: list[KnowledgePointWeakness] | None = Field(
        default=None,
        alias="knowledgePointWeaknesses",
    )
    decision_summary: BrainDecisionSummary = Field(alias="decisionSummary")
    conflicts: list[str]
    source_refs: list[BrainEvidenceRef] = Field(alias="sourceRefs")
    input_versions: dict[str, str] = Field(alias="inputVersions")
    algorithm_version: Literal["learning-brain-profile@v1"] = Field(
        default="learning-brain-profile@v1",
        alias="algorithmVersion",
    )
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    supersedes_profile_version_id: UUID | None = Field(
        default=None,
        alias="supersedesProfileVersionId",
    )
    generated_at: datetime = Field(alias="generatedAt")
    valid_until: datetime = Field(alias="validUntil")


class ErrorPatternDetail(LearningBrainModel):
    pattern: ErrorPatternSummary
    supporting_evidence: list[BrainEvidenceRef] = Field(alias="supportingEvidence")
    counterevidence: list[BrainEvidenceRef]
    interpretation: str = Field(min_length=1, max_length=1200)
    limitations: list[str]


StrategyDecisionType = Literal[
    "exam_skip", "phase_deprioritize", "strategic_abandon", "baseline_guidance"
]
StrategyScopeType = Literal["track", "module", "question_type"]


class StrategyCandidateCommand(LearningBrainModel):
    based_on_profile_version_id: UUID = Field(alias="basedOnProfileVersionId")
    decision_type: StrategyDecisionType = Field(alias="decisionType")
    scope_type: StrategyScopeType = Field(alias="scopeType")
    scope_key: str = Field(alias="scopeKey", min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1000)
    effective_until: datetime = Field(alias="effectiveUntil")
    review_at: datetime = Field(alias="reviewAt")

    @model_validator(mode="after")
    def validate_window(self) -> "StrategyCandidateCommand":
        if self.review_at > self.effective_until:
            raise ValueError("reviewAt cannot be later than effectiveUntil")
        return self


class InferenceCandidateVersion(LearningBrainModel):
    candidate_version_id: UUID = Field(alias="candidateVersionId")
    candidate_id: UUID = Field(alias="candidateId")
    version_no: int = Field(alias="versionNo", ge=1)
    inference_type: Literal[
        "error_pattern",
        "module_interpretation",
        "strategy_recommendation",
        "strategy_directive",
        "planning_explanation",
    ] = Field(alias="inferenceType")
    status: Literal[
        "pending_validation",
        "awaiting_confirmation",
        "accepted",
        "rejected",
        "expired",
    ]
    producer_type: Literal["rule", "agent"] = Field(alias="producerType")
    producer_id: str = Field(alias="producerId")
    value: dict[str, object]
    evidence_refs: list[BrainEvidenceRef] = Field(alias="evidenceRefs", min_length=1)
    confidence: float = Field(ge=0, le=1)
    requires_user_confirmation: bool = Field(alias="requiresUserConfirmation")
    confirmation_status: Literal["not_required", "pending", "confirmed", "rejected"] = Field(
        alias="confirmationStatus"
    )
    validation_status: Literal["pending", "valid", "invalid"] = Field(
        alias="validationStatus"
    )
    policy_version: str = Field(alias="policyVersion")
    valid_from: datetime = Field(alias="validFrom")
    expires_at: datetime = Field(alias="expiresAt")
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    supersedes_candidate_version_id: UUID | None = Field(
        default=None,
        alias="supersedesCandidateVersionId",
    )
    created_at: datetime = Field(alias="createdAt")


class StrategyDecisionCommand(LearningBrainModel):
    based_on_candidate_version_id: UUID = Field(alias="basedOnCandidateVersionId")
    action: Literal["confirm", "reject"]


class StrategyDecisionVersion(LearningBrainModel):
    decision_version_id: UUID = Field(alias="decisionVersionId")
    decision_id: UUID = Field(alias="decisionId")
    version_no: int = Field(alias="versionNo", ge=1)
    candidate_version_id: UUID = Field(alias="candidateVersionId")
    decision_type: StrategyDecisionType = Field(alias="decisionType")
    status: Literal["active", "rejected", "expired", "review_due", "retired"]
    scope_type: StrategyScopeType = Field(alias="scopeType")
    scope_key: str = Field(alias="scopeKey")
    rationale: str
    benefit_evidence: dict[str, object] = Field(alias="benefitEvidence")
    evidence_refs: list[BrainEvidenceRef] = Field(alias="evidenceRefs", min_length=1)
    effective_from: datetime = Field(alias="effectiveFrom")
    effective_until: datetime = Field(alias="effectiveUntil")
    review_at: datetime = Field(alias="reviewAt")
    policy_version: Literal["strategy-decision@v1"] = Field(
        default="strategy-decision@v1",
        alias="policyVersion",
    )
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    created_at: datetime = Field(alias="createdAt")


class StrategyCandidateResult(LearningBrainModel):
    candidate: InferenceCandidateVersion


class StrategyDecisionResult(LearningBrainModel):
    candidate: InferenceCandidateVersion
    decision: StrategyDecisionVersion


class StrategyDirectiveEntry(LearningBrainModel):
    """一条策略指令快照：active 已生效 / awaiting_confirmation 待确认。

    供岸岸引用 rationale 解释「为什么这样排」。字段只带用户可理解的
    决策方向与理由，不暴露内部版本号。
    """

    status: Literal["active", "awaiting_confirmation"]
    decision_type: StrategyDecisionType = Field(alias="decisionType")
    scope_type: StrategyScopeType = Field(alias="scopeType")
    scope_key: str = Field(alias="scopeKey", min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1000)
    effective_until: datetime | None = Field(default=None, alias="effectiveUntil")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    generated_at: datetime = Field(alias="generatedAt")


class StrategyDirective(LearningBrainModel):
    """岸岸可引用的当前策略投影：已生效策略 + 待确认候选。

    由 learning_brain 的只读方法 ``current_strategy_directive`` 返回，
    不落库、不进 profile fingerprint。
    """

    active: StrategyDirectiveEntry | None = None
    pending: StrategyDirectiveEntry | None = None
