from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domains.essay_intelligence.schemas import (
    EssayAnswerVersion,
    EssayFinding,
    EssayRubricMaterialEvidence,
)
from app.domains.planning.schemas import TaskExecutionCommandResult
from app.domains.essay_intelligence.workflow_schemas import EssayReviewIntentView


ArtifactType = Literal[
    "material_annotation",
    "point_extraction",
    "outline",
    "review_acknowledgement",
    "reflection",
]
UuidText = Annotated[
    str,
    Field(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    ),
]


class ArtifactContractModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )


class MaterialParagraphAnnotation(ArtifactContractModel):
    segment_id: str = Field(alias="segmentId", min_length=1, max_length=160)
    paragraph_summary: str = Field(alias="paragraphSummary", min_length=1, max_length=500)
    subjects: list[str] = Field(min_length=1, max_length=8)
    evidence_quote: str | None = Field(
        default=None,
        alias="evidenceQuote",
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_subjects(self) -> "MaterialParagraphAnnotation":
        values = [item.strip() for item in self.subjects]
        if any(not item for item in values) or len(values) != len(set(values)):
            raise ValueError("subjects must be non-blank and unique")
        self.subjects = values
        return self


class MaterialAnnotationPayload(ArtifactContractModel):
    schema_type: Literal["material_annotation"] = Field(alias="schemaType")
    annotations: list[MaterialParagraphAnnotation] = Field(min_length=1)
    overall_theme: str = Field(alias="overallTheme", min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_segments(self) -> "MaterialAnnotationPayload":
        segment_ids = [item.segment_id for item in self.annotations]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("material annotation segment IDs must be unique")
        return self


class ExtractedPoint(ArtifactContractModel):
    point_id: str = Field(alias="pointId", min_length=1, max_length=120)
    category: Literal[
        "problem",
        "cause",
        "impact",
        "countermeasure",
        "example",
        "theme",
    ]
    statement: str = Field(min_length=1, max_length=1000)
    source_segment_ids: list[str] = Field(
        alias="sourceSegmentIds",
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_sources(self) -> "ExtractedPoint":
        if len(self.source_segment_ids) != len(set(self.source_segment_ids)):
            raise ValueError("point source segment IDs must be unique")
        return self


class PointExtractionPayload(ArtifactContractModel):
    schema_type: Literal["point_extraction"] = Field(alias="schemaType")
    source_material_annotation_version_id: UuidText = Field(
        alias="sourceMaterialAnnotationVersionId"
    )
    points: list[ExtractedPoint] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def validate_points(self) -> "PointExtractionPayload":
        point_ids = [item.point_id for item in self.points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("extracted point IDs must be unique")
        return self


class OutlineSection(ArtifactContractModel):
    section_id: str = Field(alias="sectionId", min_length=1, max_length=120)
    heading: str = Field(min_length=1, max_length=300)
    argument: str = Field(min_length=1, max_length=1000)
    source_point_ids: list[str] = Field(
        alias="sourcePointIds",
        min_length=1,
        max_length=30,
    )
    planned_words: int = Field(alias="plannedWords", ge=1, le=5000)

    @model_validator(mode="after")
    def validate_point_ids(self) -> "OutlineSection":
        if len(self.source_point_ids) != len(set(self.source_point_ids)):
            raise ValueError("outline source point IDs must be unique")
        return self


class OutlinePayload(ArtifactContractModel):
    schema_type: Literal["outline"] = Field(alias="schemaType")
    source_point_extraction_version_id: UuidText = Field(
        alias="sourcePointExtractionVersionId"
    )
    thesis: str | None = Field(default=None, min_length=1, max_length=1000)
    sections: list[OutlineSection] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_sections(self) -> "OutlinePayload":
        section_ids = [item.section_id for item in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("outline section IDs must be unique")
        return self


class EssayFindingDecision(ArtifactContractModel):
    finding_id: str = Field(alias="findingId", min_length=1, max_length=120)
    decision: Literal["accepted", "rejected", "uncertain"]
    learner_reason: str | None = Field(
        default=None,
        alias="learnerReason",
        min_length=1,
        max_length=2000,
    )
    follow_up_question: str | None = Field(
        default=None,
        alias="followUpQuestion",
        min_length=1,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_decision_evidence(self) -> "EssayFindingDecision":
        if self.decision == "rejected" and self.learner_reason is None:
            raise ValueError("rejected finding requires learnerReason")
        if self.decision == "uncertain" and (
            self.learner_reason is None or self.follow_up_question is None
        ):
            raise ValueError(
                "uncertain finding requires learnerReason and followUpQuestion"
            )
        return self


class ReviewAcknowledgementPayload(ArtifactContractModel):
    schema_type: Literal["review_acknowledgement"] = Field(alias="schemaType")
    contract_version: Literal["essay-finding-decision@v1"] = Field(
        default="essay-finding-decision@v1",
        alias="contractVersion",
    )
    decisions: list[EssayFindingDecision] = Field(min_length=1)
    priority_finding_ids: list[str] = Field(
        alias="priorityFindingIds",
        default_factory=list,
        max_length=3,
    )
    learner_understanding: str = Field(
        alias="learnerUnderstanding",
        min_length=1,
        max_length=3000,
    )

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_acknowledgement(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "decisions" in value:
            return value
        acknowledged = value.get("acknowledgedFindingIds")
        if not isinstance(acknowledged, list):
            return value
        upgraded = dict(value)
        upgraded.pop("acknowledgedFindingIds", None)
        upgraded["contractVersion"] = "essay-finding-decision@v1"
        upgraded["decisions"] = [
            {"findingId": finding_id, "decision": "accepted"}
            for finding_id in acknowledged
        ]
        return upgraded

    @model_validator(mode="after")
    def validate_findings(self) -> "ReviewAcknowledgementPayload":
        finding_ids = [item.finding_id for item in self.decisions]
        priorities = self.priority_finding_ids
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding decision IDs must be unique")
        if len(priorities) != len(set(priorities)):
            raise ValueError("priority finding IDs must be unique")
        accepted = {
            item.finding_id for item in self.decisions if item.decision == "accepted"
        }
        if not set(priorities).issubset(accepted):
            raise ValueError("priority findings must be accepted")
        return self

    @property
    def accepted_finding_ids(self) -> list[str]:
        return [
            item.finding_id for item in self.decisions if item.decision == "accepted"
        ]


class ReflectionPayload(ArtifactContractModel):
    schema_type: Literal["reflection"] = Field(alias="schemaType")
    voice_artifact_version_ids: list[UuidText] = Field(
        default_factory=list,
        alias="voiceArtifactVersionIds",
        max_length=3,
    )
    cause_codes: list[
        Literal[
            "missed_material",
            "wrong_abstraction",
            "weak_structure",
            "unsupported_claim",
            "expression",
            "time_management",
            "format",
        ]
    ] = Field(alias="causeCodes", min_length=1, max_length=7)
    what_i_thought: str = Field(alias="whatIThought", min_length=1, max_length=3000)
    what_i_will_change: str = Field(
        alias="whatIWillChange",
        min_length=1,
        max_length=3000,
    )
    verification_plan: str = Field(
        alias="verificationPlan",
        min_length=1,
        max_length=2000,
    )

    @model_validator(mode="after")
    def validate_causes(self) -> "ReflectionPayload":
        if len(self.cause_codes) != len(set(self.cause_codes)):
            raise ValueError("reflection cause codes must be unique")
        if len(self.voice_artifact_version_ids) != len(
            set(self.voice_artifact_version_ids)
        ):
            raise ValueError("reflection voice artifact versions must be unique")
        return self


ArtifactPayload = Annotated[
    MaterialAnnotationPayload
    | PointExtractionPayload
    | OutlinePayload
    | ReviewAcknowledgementPayload
    | ReflectionPayload,
    Field(discriminator="schema_type"),
]


class EssayLearningArtifactCommand(ArtifactContractModel):
    artifact_type: ArtifactType = Field(alias="artifactType")
    task_version_id: UuidText = Field(alias="taskVersionId")
    material_bundle_version_id: UuidText = Field(alias="materialBundleVersionId")
    rubric_version_id: UuidText | None = Field(default=None, alias="rubricVersionId")
    review_version_id: UuidText | None = Field(default=None, alias="reviewVersionId")
    answer_version_id: UuidText | None = Field(default=None, alias="answerVersionId")
    based_on_artifact_version_id: UuidText | None = Field(
        default=None,
        alias="basedOnArtifactVersionId",
    )
    payload: ArtifactPayload

    @model_validator(mode="after")
    def validate_binding_shape(self) -> "EssayLearningArtifactCommand":
        if self.payload.schema_type != self.artifact_type:
            raise ValueError("payload schemaType must match artifactType")
        if self.artifact_type == "material_annotation":
            if any((self.rubric_version_id, self.review_version_id, self.answer_version_id)):
                raise ValueError("material annotation binds only task and material")
        elif self.artifact_type in {"point_extraction", "outline"}:
            if self.rubric_version_id is None:
                raise ValueError(f"{self.artifact_type} requires rubricVersionId")
            if self.review_version_id is not None or self.answer_version_id is not None:
                raise ValueError(f"{self.artifact_type} cannot bind review or answer")
        elif self.artifact_type == "review_acknowledgement":
            if self.rubric_version_id is None or self.review_version_id is None:
                raise ValueError("review acknowledgement requires rubric and review")
            if self.answer_version_id is not None:
                raise ValueError("review acknowledgement derives its answer from review")
        elif self.artifact_type == "reflection":
            if self.rubric_version_id is None or self.answer_version_id is None:
                raise ValueError("reflection requires rubric and answer")
        return self


class EssayLearningArtifactVersion(ArtifactContractModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    artifact_type: ArtifactType = Field(alias="artifactType")
    scope_key: str = Field(alias="scopeKey", pattern=r"^[0-9a-f]{64}$")
    version_no: int = Field(alias="versionNo", ge=1)
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    rubric_version_id: UUID | None = Field(default=None, alias="rubricVersionId")
    review_version_id: UUID | None = Field(default=None, alias="reviewVersionId")
    answer_version_id: UUID | None = Field(default=None, alias="answerVersionId")
    payload: ArtifactPayload
    input_fingerprint: str = Field(
        alias="inputFingerprint",
        pattern=r"^[0-9a-f]{64}$",
    )
    supersedes_artifact_version_id: UUID | None = Field(
        default=None,
        alias="supersedesArtifactVersionId",
    )
    created_at: AwareDatetime = Field(alias="createdAt")


class EssayArtifactPlanTaskCompletionCommand(ArtifactContractModel):
    artifact: EssayLearningArtifactCommand
    plan_task_id: UuidText = Field(alias="planTaskId")
    elapsed_minutes: int = Field(alias="elapsedMinutes", ge=0, le=1440)
    based_on_execution_version_id: UuidText | None = Field(
        default=None,
        alias="basedOnExecutionVersionId",
    )


class EssayArtifactPlanTaskCompletionResult(ArtifactContractModel):
    artifact: EssayLearningArtifactVersion
    completion: TaskExecutionCommandResult


class EssayTaskArtifactState(ArtifactContractModel):
    task_version_id: UUID = Field(alias="taskVersionId")
    current_by_type: dict[ArtifactType, EssayLearningArtifactVersion] = Field(
        alias="currentByType"
    )


class EssayWorkspaceMaterialScope(ArtifactContractModel):
    mode: Literal["explicit", "all", "fallback_all"]
    material_orders: list[int] = Field(alias="materialOrders", min_length=1)


class EssayWorkspaceTask(ArtifactContractModel):
    task_version_id: UUID = Field(alias="taskVersionId")
    task_key: str = Field(alias="taskKey", min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=10000)
    requirements: str | None = Field(default=None, min_length=1, max_length=5000)
    question_type: Literal[
        "summary",
        "comprehensive_analysis",
        "countermeasure",
        "implementation",
        "composition",
    ] = Field(alias="questionType")
    word_limit: int = Field(alias="wordLimit", ge=1, le=5000)
    maximum_score: float = Field(alias="maximumScore", gt=0, le=100)
    material_scope: EssayWorkspaceMaterialScope = Field(alias="materialScope")


class EssayWorkspaceMaterialSegment(ArtifactContractModel):
    material_id: str = Field(alias="materialId", min_length=1, max_length=160)
    segment_id: str = Field(alias="segmentId", min_length=1, max_length=160)
    material_order: int = Field(alias="materialOrder", ge=1)
    segment_order: int = Field(alias="segmentOrder", ge=1)
    text: str = Field(min_length=1, max_length=50000)


class EssayWorkspaceMaterial(ArtifactContractModel):
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    source_kind: Literal["own_bank", "official", "external_confirmed"] = Field(
        alias="sourceKind"
    )
    segments: list[EssayWorkspaceMaterialSegment] = Field(min_length=1)


class EssayWorkspaceRubricCriterion(ArtifactContractModel):
    criterion_code: str = Field(alias="criterionCode", min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    maximum: float = Field(gt=0, le=100)
    scoring_rule: str = Field(alias="scoringRule", min_length=1, max_length=2000)
    material_evidence_required: bool = Field(alias="materialEvidenceRequired")


class EssayWorkspaceRubricPoint(ArtifactContractModel):
    rubric_point_id: str = Field(alias="rubricPointId", min_length=1, max_length=120)
    criterion_code: str = Field(alias="criterionCode", min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=500)
    maximum_credit: float = Field(alias="maximumCredit", gt=0, le=100)
    material_segment_ids: list[str] = Field(
        alias="materialSegmentIds",
        min_length=1,
    )
    material_evidence_refs: list[EssayRubricMaterialEvidence] = Field(
        alias="materialEvidenceRefs",
        min_length=1,
    )


class EssayWorkspaceRubric(ArtifactContractModel):
    rubric_version_id: UUID = Field(alias="rubricVersionId")
    source_kind: Literal["official", "system_compiled"] = Field(alias="sourceKind")
    verification_status: Literal["verified", "unverified"] = Field(
        alias="verificationStatus"
    )
    total_maximum: float = Field(alias="totalMaximum", gt=0, le=100)
    criteria: list[EssayWorkspaceRubricCriterion] = Field(min_length=1)
    points: list[EssayWorkspaceRubricPoint] = Field(min_length=1)


class EssayWorkspaceRubricAvailability(ArtifactContractModel):
    status: Literal["published", "pending"]
    rubric: EssayWorkspaceRubric | None

    @model_validator(mode="after")
    def validate_availability(self) -> "EssayWorkspaceRubricAvailability":
        if (self.status == "published") != (self.rubric is not None):
            raise ValueError("published rubric status must carry rubric content")
        return self


class EssayWorkspaceProgression(ArtifactContractModel):
    attempt_id: UUID | None = Field(default=None, alias="attemptId")
    answer_version_id: UUID | None = Field(default=None, alias="answerVersionId")
    review_version_id: UUID | None = Field(default=None, alias="reviewVersionId")
    review_job_id: UUID | None = Field(default=None, alias="reviewJobId")
    review_intent: EssayReviewIntentView | None = Field(
        default=None,
        alias="reviewIntent",
    )
    review_status: Literal["completed", "blocked", "failed"] | None = Field(
        default=None,
        alias="reviewStatus",
    )
    rewrite_version_id: UUID | None = Field(default=None, alias="rewriteVersionId")
    transfer_verification_version_id: UUID | None = Field(
        default=None,
        alias="transferVerificationVersionId",
    )
    transfer_status: Literal["verified", "not_verified", "inconclusive"] | None = Field(
        default=None,
        alias="transferStatus",
    )
    mastery_state: Literal["rewrite_due", "transfer_due", "stable_mastered"] | None = Field(
        default=None,
        alias="masteryState",
    )
    mastery_version_id: UUID | None = Field(default=None, alias="masteryVersionId")
    mastery_due_at: AwareDatetime | None = Field(default=None, alias="masteryDueAt")
    current_review: "EssayWorkspaceReview | None" = Field(
        default=None,
        alias="currentReview",
    )
    current_answer: EssayAnswerVersion | None = Field(
        default=None,
        alias="currentAnswer",
    )


class EssayWorkspaceReview(ArtifactContractModel):
    review_version_id: UUID = Field(alias="reviewVersionId")
    status: Literal["completed", "blocked", "failed"]
    findings: list[EssayFinding]


class EssayTaskWorkspace(ArtifactContractModel):
    task: EssayWorkspaceTask
    material: EssayWorkspaceMaterial
    rubric_availability: EssayWorkspaceRubricAvailability = Field(
        alias="rubricAvailability"
    )
    current_artifacts: dict[ArtifactType, EssayLearningArtifactVersion] = Field(
        alias="currentArtifacts"
    )
    progression: EssayWorkspaceProgression
