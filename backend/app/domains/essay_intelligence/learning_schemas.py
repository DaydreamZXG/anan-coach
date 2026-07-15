from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domains.essay_intelligence.schemas import (
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


class ReviewAcknowledgementPayload(ArtifactContractModel):
    schema_type: Literal["review_acknowledgement"] = Field(alias="schemaType")
    acknowledged_finding_ids: list[str] = Field(
        alias="acknowledgedFindingIds",
        min_length=1,
    )
    priority_finding_ids: list[str] = Field(
        alias="priorityFindingIds",
        min_length=1,
        max_length=3,
    )
    learner_understanding: str = Field(
        alias="learnerUnderstanding",
        min_length=1,
        max_length=3000,
    )

    @model_validator(mode="after")
    def validate_findings(self) -> "ReviewAcknowledgementPayload":
        acknowledged = self.acknowledged_finding_ids
        priorities = self.priority_finding_ids
        if len(acknowledged) != len(set(acknowledged)):
            raise ValueError("acknowledged finding IDs must be unique")
        if len(priorities) != len(set(priorities)):
            raise ValueError("priority finding IDs must be unique")
        if not set(priorities).issubset(acknowledged):
            raise ValueError("priority findings must be acknowledged")
        return self


class ReflectionPayload(ArtifactContractModel):
    schema_type: Literal["reflection"] = Field(alias="schemaType")
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
    current_review: "EssayWorkspaceReview | None" = Field(
        default=None,
        alias="currentReview",
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
