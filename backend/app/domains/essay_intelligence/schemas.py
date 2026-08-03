from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.contracts.guokao_shenlun import (
    GuokaoShenlunQuestionType as QuestionType,
)
from app.domains.essay_intelligence.normalization import (
    NORMALIZATION_CONTRACT,
    normalize_grounding_text,
)


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
LifecycleState = Literal["draft", "published"]
RubricSource = Literal["official", "system_compiled"]
RubricVerification = Literal["verified", "unverified"]
ReviewStatus = Literal["completed", "blocked", "failed"]
ScoreConfidence = Literal["low", "medium"]


class EssayContractModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )


class EssayTaskVersion(EssayContractModel):
    task_version_id: UUID = Field(alias="taskVersionId")
    task_key: str = Field(alias="taskKey", min_length=1, max_length=160)
    version_no: int = Field(alias="versionNo", ge=1)
    question_id: str | None = Field(default=None, alias="questionId", min_length=1)
    question_type: QuestionType = Field(alias="questionType")
    prompt: str = Field(min_length=1, max_length=10000)
    prompt_sha256: Sha256 = Field(alias="promptSha256")
    word_limit: int = Field(alias="wordLimit", ge=1, le=5000)
    maximum_score: float = Field(alias="maximumScore", gt=0, le=100)
    state: LifecycleState
    supersedes_task_version_id: UUID | None = Field(
        default=None,
        alias="supersedesTaskVersionId",
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_prompt_hash(self) -> "EssayTaskVersion":
        _validate_text_hash(self.prompt, self.prompt_sha256, "promptSha256")
        if self.supersedes_task_version_id == self.task_version_id:
            raise ValueError("task version cannot supersede itself")
        return self


class EssayMaterialSegment(EssayContractModel):
    material_id: str = Field(alias="materialId", min_length=1, max_length=160)
    segment_id: str = Field(alias="segmentId", min_length=1, max_length=160)
    material_order: int = Field(alias="materialOrder", ge=1)
    segment_order: int = Field(alias="segmentOrder", ge=1)
    text: str = Field(min_length=1, max_length=50000)
    text_sha256: Sha256 = Field(alias="textSha256")

    @model_validator(mode="after")
    def validate_text_hash(self) -> "EssayMaterialSegment":
        _validate_text_hash(self.text, self.text_sha256, "textSha256")
        return self


class EssayMaterialBundleVersion(EssayContractModel):
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    task_version_id: UUID = Field(alias="taskVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    completeness: Literal["complete", "incomplete"]
    source_kind: Literal["own_bank", "official", "external_confirmed"] = Field(alias="sourceKind")
    content_sha256: Sha256 = Field(alias="contentSha256")
    segments: list[EssayMaterialSegment]
    supersedes_material_bundle_version_id: UUID | None = Field(
        default=None,
        alias="supersedesMaterialBundleVersionId",
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_segments(self) -> "EssayMaterialBundleVersion":
        if self.completeness == "complete" and not self.segments:
            raise ValueError("complete material bundle requires segments")
        segment_ids = [item.segment_id for item in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("material segment IDs must be unique")
        positions = [
            (item.material_id, item.material_order, item.segment_order) for item in self.segments
        ]
        if len(positions) != len(set(positions)):
            raise ValueError("material segment positions must be unique")
        if self.supersedes_material_bundle_version_id == self.material_bundle_version_id:
            raise ValueError("material bundle version cannot supersede itself")
        return self


class EssayRubricCriterion(EssayContractModel):
    criterion_code: str = Field(alias="criterionCode", min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    maximum: float = Field(gt=0, le=100)
    scoring_rule: str = Field(alias="scoringRule", min_length=1, max_length=2000)
    material_evidence_required: bool = Field(alias="materialEvidenceRequired")


class EssayRubricMaterialEvidence(EssayContractModel):
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    material_id: str = Field(alias="materialId", min_length=1, max_length=160)
    segment_id: str = Field(alias="segmentId", min_length=1, max_length=160)
    start_offset: int = Field(alias="startOffset", ge=0)
    end_offset: int = Field(alias="endOffset", gt=0)
    quote: str = Field(min_length=1, max_length=1000)
    quote_sha256: Sha256 = Field(alias="quoteSha256")

    @model_validator(mode="after")
    def validate_span(self) -> "EssayRubricMaterialEvidence":
        if self.end_offset <= self.start_offset:
            raise ValueError("rubric material evidence requires a positive text span")
        if self.end_offset - self.start_offset != len(self.quote):
            raise ValueError("rubric material evidence offsets must match quote length")
        _validate_text_hash(self.quote, self.quote_sha256, "quoteSha256")
        return self


class EssayRubricPoint(EssayContractModel):
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

    @model_validator(mode="after")
    def validate_material_segments(self) -> "EssayRubricPoint":
        if len(self.material_segment_ids) != len(set(self.material_segment_ids)):
            raise ValueError("rubric point material segment IDs must be unique")
        referenced_segments = {item.segment_id for item in self.material_evidence_refs}
        if referenced_segments != set(self.material_segment_ids):
            raise ValueError("rubric point materialSegmentIds must match material evidence")
        evidence_keys = [
            (
                item.material_bundle_version_id,
                item.material_id,
                item.segment_id,
                item.start_offset,
                item.end_offset,
                item.quote_sha256,
            )
            for item in self.material_evidence_refs
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("rubric point material evidence must be unique")
        return self


class EssayRubricVersion(EssayContractModel):
    rubric_version_id: UUID = Field(alias="rubricVersionId")
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID | None = Field(
        default=None,
        alias="materialBundleVersionId",
    )
    version_no: int = Field(alias="versionNo", ge=1)
    source_kind: RubricSource = Field(alias="sourceKind")
    verification_status: RubricVerification = Field(alias="verificationStatus")
    state: LifecycleState
    total_maximum: float = Field(alias="totalMaximum", gt=0, le=100)
    criteria: list[EssayRubricCriterion] = Field(min_length=1)
    points: list[EssayRubricPoint] = Field(min_length=1)
    content_sha256: Sha256 = Field(alias="contentSha256")
    supersedes_rubric_version_id: UUID | None = Field(
        default=None,
        alias="supersedesRubricVersionId",
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_rubric(self) -> "EssayRubricVersion":
        if self.source_kind == "system_compiled" and self.verification_status != "unverified":
            raise ValueError("system-compiled rubric must remain unverified")
        if self.source_kind == "system_compiled" and self.material_bundle_version_id is None:
            raise ValueError("system-compiled rubric must bind a material bundle version")
        criterion_codes = [item.criterion_code for item in self.criteria]
        if len(criterion_codes) != len(set(criterion_codes)):
            raise ValueError("rubric criterion codes must be unique")
        point_ids = [item.rubric_point_id for item in self.points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("rubric point IDs must be unique")
        known_criteria = set(criterion_codes)
        if any(item.criterion_code not in known_criteria for item in self.points):
            raise ValueError("rubric point references unknown criterion")
        normalized_point_labels = [
            (item.criterion_code, "".join(item.label.split()).casefold()) for item in self.points
        ]
        if len(normalized_point_labels) != len(set(normalized_point_labels)):
            raise ValueError("rubric point labels must be unique within criterion")
        criterion_total = sum(item.maximum for item in self.criteria)
        if abs(criterion_total - self.total_maximum) > 0.001:
            raise ValueError("criterion maximums must sum to rubric total")
        for criterion in self.criteria:
            point_total = sum(
                item.maximum_credit
                for item in self.points
                if item.criterion_code == criterion.criterion_code
            )
            if point_total > criterion.maximum + 0.001:
                raise ValueError("rubric point credits cannot exceed criterion maximum")
            if (
                criterion.material_evidence_required
                and abs(point_total - criterion.maximum) > 0.001
            ):
                raise ValueError(
                    "material-grounded criterion point credits must conserve its maximum"
                )
        for point in self.points:
            for item in point.material_evidence_refs:
                if (
                    self.material_bundle_version_id is not None
                    and item.material_bundle_version_id != self.material_bundle_version_id
                ):
                    raise ValueError("rubric evidence must reference its bound material bundle")
        if self.supersedes_rubric_version_id == self.rubric_version_id:
            raise ValueError("rubric version cannot supersede itself")
        return self


class EssayAttempt(EssayContractModel):
    attempt_id: UUID = Field(alias="attemptId")
    user_id: UUID = Field(alias="userId")
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    rubric_version_id: UUID = Field(alias="rubricVersionId")
    source_context: Literal["in_program", "paper", "external"] = Field(alias="sourceContext")
    timed_status: Literal["timed", "untimed", "unknown"] = Field(alias="timedStatus")
    elapsed_seconds: int | None = Field(default=None, alias="elapsedSeconds", ge=1)
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    submitted_at: AwareDatetime = Field(alias="submittedAt")

    @model_validator(mode="after")
    def validate_timing(self) -> "EssayAttempt":
        if self.timed_status == "timed" and self.elapsed_seconds is None:
            raise ValueError("timed attempt requires elapsedSeconds")
        return self


class EssayThinkingSnapshot(EssayContractModel):
    point_finding_text: str | None = Field(
        default=None,
        alias="pointFindingText",
        min_length=1,
        max_length=5000,
    )
    outline_text: str | None = Field(
        default=None,
        alias="outlineText",
        min_length=1,
        max_length=5000,
    )
    user_thought: str | None = Field(
        default=None,
        alias="userThought",
        min_length=1,
        max_length=5000,
    )

    @model_validator(mode="after")
    def validate_nonempty(self) -> "EssayThinkingSnapshot":
        if not any((self.point_finding_text, self.outline_text, self.user_thought)):
            raise ValueError("thinking snapshot requires at least one field")
        return self


class EssayAnswerVersion(EssayContractModel):
    answer_version_id: UUID = Field(alias="answerVersionId")
    attempt_id: UUID = Field(alias="attemptId")
    user_id: UUID = Field(alias="userId")
    task_version_id: UUID = Field(alias="taskVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    source_type: Literal["typed", "ocr_confirmed"] = Field(alias="sourceType")
    source_artifact_version_id: UUID | None = Field(
        default=None,
        alias="sourceArtifactVersionId",
    )
    recognition_revision_id: UUID | None = Field(
        default=None,
        alias="recognitionRevisionId",
    )
    answer_text: str = Field(alias="answerText", min_length=1, max_length=10000)
    answer_sha256: Sha256 = Field(alias="answerSha256")
    thinking: EssayThinkingSnapshot | None = None
    thinking_sha256: Sha256 | None = Field(default=None, alias="thinkingSha256")
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    supersedes_answer_version_id: UUID | None = Field(
        default=None,
        alias="supersedesAnswerVersionId",
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_thinking_hash(self) -> "EssayAnswerVersion":
        if (self.thinking is None) != (self.thinking_sha256 is None):
            raise ValueError("thinking snapshot and hash must appear together")
        _validate_text_hash(self.answer_text, self.answer_sha256, "answerSha256")
        if self.supersedes_answer_version_id == self.answer_version_id:
            raise ValueError("answer version cannot supersede itself")
        has_evidence = (
            self.source_artifact_version_id is not None and self.recognition_revision_id is not None
        )
        if self.source_type == "ocr_confirmed" and not has_evidence:
            raise ValueError("OCR answer version requires immutable source evidence")
        if self.source_type == "typed" and (
            self.source_artifact_version_id is not None or self.recognition_revision_id is not None
        ):
            raise ValueError("typed answer version cannot carry OCR source evidence")
        return self


class EssayReviewRequest(EssayContractModel):
    attempt_id: UUID = Field(alias="attemptId")
    task_version_id: UUID = Field(alias="taskVersionId")
    answer_version_id: UUID = Field(alias="answerVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    rubric_version_id: UUID = Field(alias="rubricVersionId")
    review_scope: Literal["grounded_full"] = Field(alias="reviewScope")


class EssayScoreRange(EssayContractModel):
    lower: float = Field(ge=0)
    recommended: float = Field(ge=0)
    upper: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "EssayScoreRange":
        if not self.lower <= self.recommended <= self.upper:
            raise ValueError("score range must be ordered")
        return self


class EssayScore(EssayContractModel):
    maximum: float = Field(gt=0, le=100)
    score_range: EssayScoreRange = Field(alias="scoreRange")
    confidence: ScoreConfidence
    confidence_reasons: list[str] = Field(alias="confidenceReasons", min_length=1)

    @model_validator(mode="after")
    def validate_maximum(self) -> "EssayScore":
        if self.score_range.upper > self.maximum:
            raise ValueError("score range cannot exceed maximum")
        return self


class MaterialEvidenceRef(EssayContractModel):
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    material_id: str = Field(alias="materialId", min_length=1, max_length=160)
    segment_id: str = Field(alias="segmentId", min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=500)
    quote_sha256: Sha256 = Field(alias="quoteSha256")
    normalization: Literal["nfkc_whitespace_v1"] | None = None
    normalized_start: int | None = Field(
        default=None,
        alias="normalizedStart",
        ge=0,
    )
    normalized_end: int | None = Field(
        default=None,
        alias="normalizedEnd",
        ge=1,
    )

    @model_validator(mode="after")
    def validate_quote_hash(self) -> "MaterialEvidenceRef":
        value = (
            normalize_grounding_text(self.quote)
            if self.normalization == NORMALIZATION_CONTRACT
            else self.quote
        )
        _validate_text_hash(value, self.quote_sha256, "quoteSha256")
        _validate_normalized_offsets(
            self.normalization,
            self.normalized_start,
            self.normalized_end,
        )
        return self


class AnswerEvidenceRef(EssayContractModel):
    answer_version_id: UUID = Field(alias="answerVersionId")
    segment_id: str = Field(alias="segmentId", min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=500)
    quote_sha256: Sha256 = Field(alias="quoteSha256")
    normalization: Literal["nfkc_whitespace_v1"] | None = None
    normalized_start: int | None = Field(
        default=None,
        alias="normalizedStart",
        ge=0,
    )
    normalized_end: int | None = Field(
        default=None,
        alias="normalizedEnd",
        ge=1,
    )

    @model_validator(mode="after")
    def validate_quote_hash(self) -> "AnswerEvidenceRef":
        value = (
            normalize_grounding_text(self.quote)
            if self.normalization == NORMALIZATION_CONTRACT
            else self.quote
        )
        _validate_text_hash(value, self.quote_sha256, "quoteSha256")
        _validate_normalized_offsets(
            self.normalization,
            self.normalized_start,
            self.normalized_end,
        )
        return self


class EssayDimensionReview(EssayContractModel):
    criterion_code: str = Field(alias="criterionCode", min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    maximum: float = Field(gt=0, le=100)
    score_range: EssayScoreRange = Field(alias="scoreRange")
    rationale: str = Field(min_length=1, max_length=1000)
    finding_ids: list[str] = Field(alias="findingIds")
    answer_evidence_refs: list[AnswerEvidenceRef] = Field(alias="answerEvidenceRefs")
    material_evidence_refs: list[MaterialEvidenceRef] = Field(alias="materialEvidenceRefs")

    @model_validator(mode="after")
    def validate_score(self) -> "EssayDimensionReview":
        if self.score_range.upper > self.maximum:
            raise ValueError("dimension score cannot exceed maximum")
        if len(self.finding_ids) != len(set(self.finding_ids)):
            raise ValueError("dimension finding IDs must be unique")
        _validate_unique_evidence_refs(
            self.answer_evidence_refs,
            self.material_evidence_refs,
        )
        return self


class EssayPointCoverage(EssayContractModel):
    rubric_point_id: str = Field(alias="rubricPointId", min_length=1, max_length=120)
    state: Literal["covered", "partial", "missing"]
    awarded_range: EssayScoreRange = Field(alias="awardedRange")
    explanation: str = Field(min_length=1, max_length=1000)
    answer_evidence_refs: list[AnswerEvidenceRef] = Field(alias="answerEvidenceRefs")
    material_evidence_refs: list[MaterialEvidenceRef] = Field(
        alias="materialEvidenceRefs",
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_evidence(self) -> "EssayPointCoverage":
        if self.state == "missing":
            if self.answer_evidence_refs or self.awarded_range.upper != 0:
                raise ValueError("missing point cannot cite answer or receive credit")
        elif not self.answer_evidence_refs:
            raise ValueError("covered or partial point requires answer evidence")
        _validate_unique_evidence_refs(
            self.answer_evidence_refs,
            self.material_evidence_refs,
        )
        return self


class EssayClaimEvidence(EssayContractModel):
    claim_id: str = Field(alias="claimId", min_length=1, max_length=120)
    claim_text: str = Field(alias="claimText", min_length=1, max_length=1000)
    verdict: Literal["supported", "partially_supported", "unsupported"]
    explanation: str = Field(min_length=1, max_length=1000)
    answer_evidence_refs: list[AnswerEvidenceRef] = Field(
        alias="answerEvidenceRefs",
        min_length=1,
    )
    material_evidence_refs: list[MaterialEvidenceRef] = Field(alias="materialEvidenceRefs")

    @model_validator(mode="after")
    def validate_support(self) -> "EssayClaimEvidence":
        if self.verdict != "unsupported" and not self.material_evidence_refs:
            raise ValueError("supported claim requires material evidence")
        _validate_unique_evidence_refs(
            self.answer_evidence_refs,
            self.material_evidence_refs,
        )
        return self


class EssayFinding(EssayContractModel):
    finding_id: str = Field(alias="findingId", min_length=1, max_length=120)
    type: Literal[
        "point_finding",
        "abstraction",
        "structure",
        "expression",
        "format",
        "unsupported_claim",
    ]
    severity: Literal["low", "medium", "high"]
    diagnosis: str = Field(min_length=1, max_length=1000)
    action: str = Field(min_length=1, max_length=1000)
    attribution: Literal[
        "not_found",
        "found_not_written",
        "written_imprecisely",
        "unknown",
    ]
    answer_evidence_refs: list[AnswerEvidenceRef] = Field(
        alias="answerEvidenceRefs",
        min_length=1,
    )
    material_evidence_refs: list[MaterialEvidenceRef] = Field(alias="materialEvidenceRefs")

    @model_validator(mode="after")
    def validate_grounding(self) -> "EssayFinding":
        grounded_types = {"point_finding", "abstraction", "unsupported_claim"}
        if self.type in grounded_types and not self.material_evidence_refs:
            raise ValueError("substantive finding requires material evidence")
        _validate_unique_evidence_refs(
            self.answer_evidence_refs,
            self.material_evidence_refs,
        )
        return self


class EssayRewriteConstraint(EssayContractModel):
    code: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)


class EssayRewriteStep(EssayContractModel):
    step_id: str = Field(alias="stepId", min_length=1, max_length=80)
    instruction: str = Field(min_length=1, max_length=1000)
    success_criterion: str = Field(alias="successCriterion", min_length=1, max_length=1000)


class EssayRewritePlan(EssayContractModel):
    priority_finding_ids: list[str] = Field(
        alias="priorityFindingIds",
        min_length=1,
        max_length=3,
    )
    target_point_ids: list[str] = Field(alias="targetPointIds")
    constraints: list[EssayRewriteConstraint]
    steps: list[EssayRewriteStep] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "EssayRewritePlan":
        for values, label in (
            (self.priority_finding_ids, "priority finding"),
            (self.target_point_ids, "target point"),
            ([item.step_id for item in self.steps], "rewrite step"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} IDs must be unique")
        return self


class EssayReviewReadiness(EssayContractModel):
    material_complete: bool = Field(alias="materialComplete")
    prompt_complete: bool = Field(alias="promptComplete")
    answer_complete: bool = Field(alias="answerComplete")
    ocr_confirmed: bool = Field(alias="ocrConfirmed")
    blocking_codes: list[str] = Field(alias="blockingCodes")

    @property
    def ready(self) -> bool:
        return (
            self.material_complete
            and self.prompt_complete
            and self.answer_complete
            and self.ocr_confirmed
            and not self.blocking_codes
        )


class EssayReviewAudit(EssayContractModel):
    contract_version: str = Field(alias="contractVersion", min_length=1)
    provider_schema_version: str = Field(alias="providerSchemaVersion", min_length=1)
    rubric_version: str = Field(alias="rubricVersion", min_length=1)
    rubric_source: RubricSource = Field(alias="rubricSource")
    rubric_verification: RubricVerification = Field(alias="rubricVerification")
    prompt_version: str = Field(alias="promptVersion", min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    output_fingerprint: Sha256 = Field(alias="outputFingerprint")
    generated_at: AwareDatetime = Field(alias="generatedAt")
    valid_until: AwareDatetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def validate_audit(self) -> "EssayReviewAudit":
        if self.valid_until <= self.generated_at:
            raise ValueError("validUntil must be after generatedAt")
        if self.rubric_source == "system_compiled" and self.rubric_verification != "unverified":
            raise ValueError("system-compiled rubric must remain unverified")
        return self


class EssayProviderReviewPayload(EssayContractModel):
    score: EssayScore
    dimensions: list[EssayDimensionReview] = Field(min_length=1)
    point_coverage: list[EssayPointCoverage] = Field(alias="pointCoverage", min_length=1)
    claim_evidence: list[EssayClaimEvidence] = Field(alias="claimEvidence")
    findings: list[EssayFinding] = Field(min_length=1)
    rewrite_plan: EssayRewritePlan = Field(alias="rewritePlan")

    @model_validator(mode="after")
    def validate_payload(self) -> "EssayProviderReviewPayload":
        _validate_review_body(
            score=self.score,
            dimensions=self.dimensions,
            point_coverage=self.point_coverage,
            claim_evidence=self.claim_evidence,
            findings=self.findings,
            rewrite_plan=self.rewrite_plan,
        )
        return self


class EssayReviewResponse(EssayContractModel):
    review_version_id: UUID = Field(alias="reviewVersionId")
    attempt_id: UUID = Field(alias="attemptId")
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    answer_version_id: UUID = Field(alias="answerVersionId")
    rubric_version_id: UUID = Field(alias="rubricVersionId")
    status: ReviewStatus
    review_scope: Literal["grounded_full"] = Field(alias="reviewScope")
    thinking_evidence_available: bool = Field(alias="thinkingEvidenceAvailable")
    readiness: EssayReviewReadiness
    score: EssayScore | None
    dimensions: list[EssayDimensionReview]
    point_coverage: list[EssayPointCoverage] = Field(alias="pointCoverage")
    claim_evidence: list[EssayClaimEvidence] = Field(alias="claimEvidence")
    findings: list[EssayFinding]
    rewrite_plan: EssayRewritePlan | None = Field(alias="rewritePlan")
    audit: EssayReviewAudit | None
    supersedes_review_version_id: UUID | None = Field(
        default=None,
        alias="supersedesReviewVersionId",
    )

    @model_validator(mode="after")
    def validate_response(self) -> "EssayReviewResponse":
        if self.status == "completed":
            if not self.readiness.ready:
                raise ValueError("completed grounded review requires complete inputs")
            if self.score is None or self.rewrite_plan is None or self.audit is None:
                raise ValueError("completed review requires score, rewrite plan, and audit")
            _validate_review_body(
                score=self.score,
                dimensions=self.dimensions,
                point_coverage=self.point_coverage,
                claim_evidence=self.claim_evidence,
                findings=self.findings,
                rewrite_plan=self.rewrite_plan,
            )
            self._validate_references()
            if not self.thinking_evidence_available and any(
                item.attribution != "unknown" for item in self.findings
            ):
                raise ValueError("finding attribution requires learner thinking evidence")
            if self.audit.rubric_source == "system_compiled" and self.score.confidence != "low":
                raise ValueError("unverified system rubric caps confidence at low")
        elif any(
            (
                self.score is not None,
                bool(self.dimensions),
                bool(self.point_coverage),
                bool(self.claim_evidence),
                bool(self.findings),
                self.rewrite_plan is not None,
            )
        ):
            raise ValueError("blocked or failed review cannot expose evaluation output")
        return self

    def _validate_references(self) -> None:
        answer_refs, material_refs = _all_references(
            self.dimensions,
            self.point_coverage,
            self.claim_evidence,
            self.findings,
        )
        if any(item.answer_version_id != self.answer_version_id for item in answer_refs):
            raise ValueError("answer evidence must reference reviewed answer version")
        if any(
            item.material_bundle_version_id != self.material_bundle_version_id
            for item in material_refs
        ):
            raise ValueError("material evidence must reference reviewed material bundle")


class EssayRewriteRequest(EssayContractModel):
    base_review_version_id: UUID = Field(alias="baseReviewVersionId")
    base_answer_version_id: UUID = Field(alias="baseAnswerVersionId")
    rewritten_answer_version_id: UUID = Field(alias="rewrittenAnswerVersionId")

    @model_validator(mode="after")
    def validate_versions(self) -> "EssayRewriteRequest":
        if self.base_answer_version_id == self.rewritten_answer_version_id:
            raise ValueError("rewrite must reference a new answer version")
        return self


class EssayPointCoverageDelta(EssayContractModel):
    rubric_point_id: str = Field(alias="rubricPointId", min_length=1)
    from_state: Literal["covered", "partial", "missing"] = Field(alias="fromState")
    to_state: Literal["covered", "partial", "missing"] = Field(alias="toState")


class EssayDimensionRangeDelta(EssayContractModel):
    criterion_code: str = Field(alias="criterionCode", min_length=1)
    before: EssayScoreRange
    after: EssayScoreRange

    @model_validator(mode="after")
    def validate_changed(self) -> "EssayDimensionRangeDelta":
        if self.before == self.after:
            raise ValueError("dimension delta requires a changed score range")
        return self


class EssayRewriteVersion(EssayContractModel):
    rewrite_version_id: UUID = Field(alias="rewriteVersionId")
    attempt_id: UUID = Field(alias="attemptId")
    task_version_id: UUID = Field(alias="taskVersionId")
    base_review_version_id: UUID = Field(alias="baseReviewVersionId")
    base_answer_version_id: UUID = Field(alias="baseAnswerVersionId")
    rewritten_answer_version_id: UUID = Field(alias="rewrittenAnswerVersionId")
    addressed_finding_ids: list[str] = Field(alias="addressedFindingIds")
    unresolved_finding_ids: list[str] = Field(alias="unresolvedFindingIds")
    regressions: list[str]
    point_coverage_delta: list[EssayPointCoverageDelta] = Field(alias="pointCoverageDelta")
    dimension_range_delta: list[EssayDimensionRangeDelta] = Field(alias="dimensionRangeDelta")
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    output_fingerprint: Sha256 = Field(alias="outputFingerprint")
    audit: EssayReviewAudit
    supersedes_rewrite_version_id: UUID | None = Field(
        default=None,
        alias="supersedesRewriteVersionId",
    )

    @model_validator(mode="after")
    def validate_rewrite(self) -> "EssayRewriteVersion":
        if self.base_answer_version_id == self.rewritten_answer_version_id:
            raise ValueError("rewrite must reference a new answer version")
        for values, label in (
            (self.addressed_finding_ids, "addressed finding"),
            (self.unresolved_finding_ids, "unresolved finding"),
            (self.regressions, "regression"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} IDs must be unique")
        if set(self.addressed_finding_ids) & set(self.unresolved_finding_ids):
            raise ValueError("a finding cannot be both addressed and unresolved")
        point_ids = [item.rubric_point_id for item in self.point_coverage_delta]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("point coverage delta IDs must be unique")
        if any(item.from_state == item.to_state for item in self.point_coverage_delta):
            raise ValueError("point coverage delta requires a changed state")
        criterion_codes = [item.criterion_code for item in self.dimension_range_delta]
        if len(criterion_codes) != len(set(criterion_codes)):
            raise ValueError("dimension range delta criterion codes must be unique")
        if self.audit.input_fingerprint != self.input_fingerprint:
            raise ValueError("rewrite audit input fingerprint does not match")
        if self.audit.output_fingerprint != self.output_fingerprint:
            raise ValueError("rewrite audit output fingerprint does not match")
        if self.supersedes_rewrite_version_id == self.rewrite_version_id:
            raise ValueError("rewrite version cannot supersede itself")
        return self


class EssayTransferVerificationRequest(EssayContractModel):
    source_review_version_id: UUID = Field(alias="sourceReviewVersionId")
    source_task_version_id: UUID = Field(alias="sourceTaskVersionId")
    transfer_attempt_id: UUID = Field(alias="transferAttemptId")
    transfer_task_version_id: UUID = Field(alias="transferTaskVersionId")
    transfer_answer_version_id: UUID = Field(alias="transferAnswerVersionId")

    @model_validator(mode="after")
    def validate_fresh_task(self) -> "EssayTransferVerificationRequest":
        if self.source_task_version_id == self.transfer_task_version_id:
            raise ValueError("transfer verification requires a different task")
        return self


class EssayTransferVerificationVersion(EssayContractModel):
    verification_version_id: UUID = Field(alias="verificationVersionId")
    user_id: UUID = Field(alias="userId")
    source_review_version_id: UUID = Field(alias="sourceReviewVersionId")
    source_attempt_id: UUID = Field(alias="sourceAttemptId")
    source_task_version_id: UUID = Field(alias="sourceTaskVersionId")
    transfer_attempt_id: UUID = Field(alias="transferAttemptId")
    transfer_task_version_id: UUID = Field(alias="transferTaskVersionId")
    transfer_answer_version_id: UUID = Field(alias="transferAnswerVersionId")
    fresh_material_bundle_version_id: UUID = Field(alias="freshMaterialBundleVersionId")
    status: Literal["verified", "not_verified", "inconclusive"]
    tested_criterion_codes: list[str] = Field(
        alias="testedCriterionCodes",
        min_length=1,
    )
    answer_evidence_refs: list[AnswerEvidenceRef] = Field(alias="answerEvidenceRefs")
    material_evidence_refs: list[MaterialEvidenceRef] = Field(alias="materialEvidenceRefs")
    input_fingerprint: Sha256 = Field(alias="inputFingerprint")
    output_fingerprint: Sha256 = Field(alias="outputFingerprint")
    audit: EssayReviewAudit
    supersedes_verification_version_id: UUID | None = Field(
        default=None,
        alias="supersedesVerificationVersionId",
    )

    @model_validator(mode="after")
    def validate_transfer(self) -> "EssayTransferVerificationVersion":
        if self.source_attempt_id == self.transfer_attempt_id:
            raise ValueError("transfer verification requires a different attempt")
        if self.source_task_version_id == self.transfer_task_version_id:
            raise ValueError("transfer verification requires a different task")
        if len(self.tested_criterion_codes) != len(set(self.tested_criterion_codes)):
            raise ValueError("tested criterion codes must be unique")
        if self.status == "verified" and (
            not self.answer_evidence_refs or not self.material_evidence_refs
        ):
            raise ValueError("verified transfer requires answer and material evidence")
        if any(
            item.answer_version_id != self.transfer_answer_version_id
            for item in self.answer_evidence_refs
        ):
            raise ValueError("transfer answer evidence references wrong answer version")
        if any(
            item.material_bundle_version_id != self.fresh_material_bundle_version_id
            for item in self.material_evidence_refs
        ):
            raise ValueError("transfer material evidence references wrong bundle")
        _validate_unique_evidence_refs(
            self.answer_evidence_refs,
            self.material_evidence_refs,
        )
        if self.audit.input_fingerprint != self.input_fingerprint:
            raise ValueError("transfer audit input fingerprint does not match")
        if self.audit.output_fingerprint != self.output_fingerprint:
            raise ValueError("transfer audit output fingerprint does not match")
        if self.supersedes_verification_version_id == self.verification_version_id:
            raise ValueError("transfer verification version cannot supersede itself")
        return self


def _validate_review_body(
    *,
    score: EssayScore,
    dimensions: list[EssayDimensionReview],
    point_coverage: list[EssayPointCoverage],
    claim_evidence: list[EssayClaimEvidence],
    findings: list[EssayFinding],
    rewrite_plan: EssayRewritePlan,
) -> None:
    dimension_codes = [item.criterion_code for item in dimensions]
    if len(dimension_codes) != len(set(dimension_codes)):
        raise ValueError("review criterion codes must be unique")
    point_ids = [item.rubric_point_id for item in point_coverage]
    if len(point_ids) != len(set(point_ids)):
        raise ValueError("review rubric point IDs must be unique")
    claim_ids = [item.claim_id for item in claim_evidence]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("review claim IDs must be unique")
    finding_ids = [item.finding_id for item in findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("review finding IDs must be unique")
    known_findings = set(finding_ids)
    if any(
        finding_id not in known_findings for item in dimensions for finding_id in item.finding_ids
    ):
        raise ValueError("dimension references unknown finding")
    if not set(rewrite_plan.priority_finding_ids) <= known_findings:
        raise ValueError("rewrite plan references unknown finding")
    if not set(rewrite_plan.target_point_ids) <= set(point_ids):
        raise ValueError("rewrite plan references unknown rubric point")
    maximum_total = sum(item.maximum for item in dimensions)
    if abs(maximum_total - score.maximum) > 0.001:
        raise ValueError("dimension maximums must sum to total maximum")
    for field in ("lower", "recommended", "upper"):
        dimension_total = sum(getattr(item.score_range, field) for item in dimensions)
        if abs(dimension_total - getattr(score.score_range, field)) > 0.001:
            raise ValueError("dimension score ranges must sum to total score range")


def _all_references(
    dimensions: list[EssayDimensionReview],
    point_coverage: list[EssayPointCoverage],
    claim_evidence: list[EssayClaimEvidence],
    findings: list[EssayFinding],
) -> tuple[list[AnswerEvidenceRef], list[MaterialEvidenceRef]]:
    items = [*dimensions, *point_coverage, *claim_evidence, *findings]
    return (
        [ref for item in items for ref in item.answer_evidence_refs],
        [ref for item in items for ref in item.material_evidence_refs],
    )


def _validate_text_hash(value: str, expected: str, field_name: str) -> None:
    actual = sha256(value.encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError(f"{field_name} does not match UTF-8 content")


def _validate_normalized_offsets(
    normalization: str | None,
    start: int | None,
    end: int | None,
) -> None:
    values_present = start is not None and end is not None
    if normalization is None and (start is not None or end is not None):
        raise ValueError("normalized offsets require a normalization contract")
    if normalization is not None and not values_present:
        raise ValueError("grounded quote requires both normalized offsets")
    if values_present and start >= end:
        raise ValueError("normalized quote offsets must be ordered")


def _validate_unique_evidence_refs(
    answer_refs: list[AnswerEvidenceRef],
    material_refs: list[MaterialEvidenceRef],
) -> None:
    answer_keys = [
        (item.answer_version_id, item.segment_id, item.quote_sha256) for item in answer_refs
    ]
    if len(answer_keys) != len(set(answer_keys)):
        raise ValueError("answer evidence references must be unique")
    material_keys = [
        (
            item.material_bundle_version_id,
            item.material_id,
            item.segment_id,
            item.quote_sha256,
        )
        for item in material_refs
    ]
    if len(material_keys) != len(set(material_keys)):
        raise ValueError("material evidence references must be unique")
