from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domains.essay_intelligence.schemas import (
    EssayContractModel,
    EssayRubricCriterion,
    EssayRubricPoint,
)


RUBRIC_COMPILATION_CONTRACT_VERSION = "guokao-essay-rubric-v1"


class EssayRubricCompilationError(ValueError):
    def __init__(self, issues: list["RubricCompilationIssue"]) -> None:
        self.issues = tuple(issues)
        details = "; ".join(f"{item.code} ({item.message})" for item in issues)
        super().__init__(f"Essay rubric compilation failed: {details}")


@dataclass(frozen=True)
class RubricCompilationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class RubricSourceSegment:
    material_id: str
    segment_id: str
    text: str
    text_sha256: str


@dataclass(frozen=True)
class RubricCompilationSource:
    task_version_id: UUID
    task_key: str
    question_type: str
    prompt: str
    prompt_sha256: str
    maximum_score: float
    material_bundle_version_id: UUID
    material_content_sha256: str
    segments: tuple[RubricSourceSegment, ...]


class RubricAICompilationCallAudit(EssayContractModel):
    call_id: UUID = Field(alias="callId")
    prompt_version: str = Field(alias="promptVersion", min_length=1, max_length=160)
    schema_version: str = Field(alias="schemaVersion", min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=160)
    input_fingerprint: str = Field(
        alias="inputFingerprint",
        pattern=r"^[0-9a-f]{64}$",
    )
    output_fingerprint: str = Field(
        alias="outputFingerprint",
        pattern=r"^[0-9a-f]{64}$",
    )
    provider_request_id_sha256: str | None = Field(
        default=None,
        alias="providerRequestIdSha256",
        pattern=r"^[0-9a-f]{64}$",
    )


class RubricAIPipelineCompilationAudit(EssayContractModel):
    pipeline_version: str = Field(alias="pipelineVersion", min_length=1, max_length=160)
    generator: RubricAICompilationCallAudit
    critic: RubricAICompilationCallAudit
    critic_approved: Literal[True] = Field(alias="criticApproved")


class SystemRubricCompilationDraft(EssayContractModel):
    contract_version: Literal["guokao-essay-rubric-v1"] = Field(
        alias="contractVersion"
    )
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    prompt_sha256: str = Field(alias="promptSha256", pattern=r"^[0-9a-f]{64}$")
    material_content_sha256: str = Field(
        alias="materialContentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    compiler_version: str = Field(alias="compilerVersion", min_length=1, max_length=160)
    prompt_version: str = Field(alias="promptVersion", min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=160)
    input_fingerprint: str = Field(
        alias="inputFingerprint",
        pattern=r"^[0-9a-f]{64}$",
    )
    source_kind: Literal["system_compiled"] = Field(alias="sourceKind")
    verification_status: Literal["unverified"] = Field(alias="verificationStatus")
    total_maximum: float = Field(alias="totalMaximum", gt=0, le=100)
    criteria: list[EssayRubricCriterion] = Field(min_length=1)
    points: list[EssayRubricPoint] = Field(min_length=1)
    pipeline_audit: RubricAIPipelineCompilationAudit | None = Field(
        default=None,
        alias="pipelineAudit",
    )

    @model_validator(mode="after")
    def validate_audit_versions(self) -> "SystemRubricCompilationDraft":
        values = (
            self.compiler_version,
            self.prompt_version,
            self.provider,
            self.model,
        )
        if any(item != item.strip() for item in values):
            raise ValueError("rubric compilation audit values must be trimmed")
        return self


@dataclass(frozen=True)
class CompiledSystemRubric:
    task_version_id: UUID
    material_bundle_version_id: UUID
    source_kind: Literal["system_compiled"]
    verification_status: Literal["unverified"]
    total_maximum: float
    criteria: tuple[EssayRubricCriterion, ...]
    points: tuple[EssayRubricPoint, ...]
    input_fingerprint: str
    content_sha256: str
    compiler_version: str
    prompt_version: str
    provider: str
    model: str
    pipeline_audit: RubricAIPipelineCompilationAudit | None


def rubric_input_fingerprint(
    *,
    source: RubricCompilationSource,
    compiler_version: str,
    prompt_version: str,
    provider: str,
    model: str,
) -> str:
    return _canonical_hash(
        {
            "contractVersion": RUBRIC_COMPILATION_CONTRACT_VERSION,
            "compilerVersion": compiler_version,
            "promptVersion": prompt_version,
            "provider": provider,
            "model": model,
            "task": {
                "taskVersionId": str(source.task_version_id),
                "taskKey": source.task_key,
                "questionType": source.question_type,
                "promptSha256": source.prompt_sha256,
                "maximumScore": source.maximum_score,
            },
            "material": {
                "materialBundleVersionId": str(source.material_bundle_version_id),
                "contentSha256": source.material_content_sha256,
                "segments": [
                    {
                        "materialId": item.material_id,
                        "segmentId": item.segment_id,
                        "textSha256": item.text_sha256,
                    }
                    for item in source.segments
                ],
            },
        }
    )


def compile_system_rubric(
    *,
    source: RubricCompilationSource,
    draft: SystemRubricCompilationDraft,
) -> CompiledSystemRubric:
    issues: list[RubricCompilationIssue] = []
    _validate_source_integrity(source=source, issues=issues)
    if draft.task_version_id != source.task_version_id:
        issues.append(_issue("task_version_mismatch", "draft is not bound to the source task"))
    if draft.material_bundle_version_id != source.material_bundle_version_id:
        issues.append(
            _issue(
                "material_bundle_mismatch",
                "draft is not bound to the complete source material bundle",
            )
        )
    if draft.prompt_sha256 != source.prompt_sha256:
        issues.append(_issue("prompt_fingerprint_mismatch", "draft prompt hash is stale"))
    if draft.material_content_sha256 != source.material_content_sha256:
        issues.append(
            _issue("material_fingerprint_mismatch", "draft material hash is stale")
        )
    if abs(draft.total_maximum - source.maximum_score) > 0.001:
        issues.append(
            _issue(
                "total_maximum_mismatch",
                "rubric total must equal the published task maximum",
            )
        )

    expected_input = rubric_input_fingerprint(
        source=source,
        compiler_version=draft.compiler_version,
        prompt_version=draft.prompt_version,
        provider=draft.provider,
        model=draft.model,
    )
    if draft.input_fingerprint != expected_input:
        issues.append(
            _issue(
                "input_fingerprint_mismatch",
                "draft input fingerprint does not match the full pinned source",
            )
        )

    _validate_rubric_structure(draft=draft, issues=issues)
    segment_by_id = {item.segment_id: item for item in source.segments}
    for point in draft.points:
        for evidence in point.material_evidence_refs:
            segment = segment_by_id.get(evidence.segment_id)
            if segment is None:
                issues.append(
                    _issue(
                        "material_segment_unknown",
                        f"point {point.rubric_point_id} cites unknown segment "
                        f"{evidence.segment_id}",
                    )
                )
                continue
            if evidence.material_bundle_version_id != source.material_bundle_version_id:
                issues.append(
                    _issue(
                        "evidence_bundle_mismatch",
                        f"point {point.rubric_point_id} cites a different material bundle",
                    )
                )
            if evidence.material_id != segment.material_id:
                issues.append(
                    _issue(
                        "evidence_material_mismatch",
                        f"point {point.rubric_point_id} cites the wrong material ID",
                    )
                )
            if evidence.end_offset > len(segment.text):
                issues.append(
                    _issue(
                        "evidence_span_out_of_range",
                        f"point {point.rubric_point_id} evidence span exceeds its segment",
                    )
                )
                continue
            actual = segment.text[evidence.start_offset : evidence.end_offset]
            if actual != evidence.quote:
                issues.append(
                    _issue(
                        "evidence_quote_mismatch",
                        f"point {point.rubric_point_id} quote is not the exact source span",
                    )
                )

    if issues:
        raise EssayRubricCompilationError(issues)

    content = {
        "contractVersion": RUBRIC_COMPILATION_CONTRACT_VERSION,
        "taskVersionId": str(source.task_version_id),
        "materialBundleVersionId": str(source.material_bundle_version_id),
        "promptSha256": source.prompt_sha256,
        "materialContentSha256": source.material_content_sha256,
        "sourceKind": "system_compiled",
        "verificationStatus": "unverified",
        "totalMaximum": draft.total_maximum,
        "criteria": [item.model_dump(by_alias=True, mode="json") for item in draft.criteria],
        "points": [item.model_dump(by_alias=True, mode="json") for item in draft.points],
    }
    return CompiledSystemRubric(
        task_version_id=source.task_version_id,
        material_bundle_version_id=source.material_bundle_version_id,
        source_kind="system_compiled",
        verification_status="unverified",
        total_maximum=draft.total_maximum,
        criteria=tuple(draft.criteria),
        points=tuple(draft.points),
        input_fingerprint=draft.input_fingerprint,
        content_sha256=_canonical_hash(content),
        compiler_version=draft.compiler_version,
        prompt_version=draft.prompt_version,
        provider=draft.provider,
        model=draft.model,
        pipeline_audit=draft.pipeline_audit,
    )


def validate_rubric_compilation_source(source: RubricCompilationSource) -> None:
    issues: list[RubricCompilationIssue] = []
    _validate_source_integrity(source=source, issues=issues)
    if issues:
        raise EssayRubricCompilationError(issues)


def _validate_rubric_structure(
    *,
    draft: SystemRubricCompilationDraft,
    issues: list[RubricCompilationIssue],
) -> None:
    criterion_codes = [item.criterion_code for item in draft.criteria]
    normalized_labels = [_normalized(item.label) for item in draft.criteria]
    if len(criterion_codes) != len(set(criterion_codes)) or len(normalized_labels) != len(
        set(normalized_labels)
    ):
        issues.append(
            _issue("duplicate_criterion", "criterion codes and labels must be unique")
        )
    if abs(sum(item.maximum for item in draft.criteria) - draft.total_maximum) > 0.001:
        issues.append(
            _issue("criterion_total_mismatch", "criterion maximums must conserve rubric total")
        )

    point_ids = [item.rubric_point_id for item in draft.points]
    if len(point_ids) != len(set(point_ids)):
        issues.append(_issue("duplicate_point_id", "rubric point IDs must be unique"))
    point_labels = [
        (item.criterion_code, _normalized(item.label)) for item in draft.points
    ]
    if len(point_labels) != len(set(point_labels)):
        issues.append(
            _issue(
                "duplicate_point_label",
                "rubric point labels must be unique within a criterion",
            )
        )
    known_criteria = set(criterion_codes)
    for point in draft.points:
        if point.criterion_code not in known_criteria:
            issues.append(
                _issue(
                    "point_criterion_unknown",
                    f"point {point.rubric_point_id} references an unknown criterion",
                )
            )
    for criterion in draft.criteria:
        point_total = sum(
            item.maximum_credit
            for item in draft.points
            if item.criterion_code == criterion.criterion_code
        )
        if point_total > criterion.maximum + 0.001:
            issues.append(
                _issue(
                    "point_credit_exceeds_criterion",
                    f"points exceed criterion {criterion.criterion_code} maximum",
                )
            )
        if criterion.material_evidence_required and abs(
            point_total - criterion.maximum
        ) > 0.001:
            issues.append(
                _issue(
                    "point_credit_not_conserved",
                    f"material criterion {criterion.criterion_code} point credits "
                    "must equal its maximum",
                )
            )

    evidence_owner: dict[tuple[str, str, int, int, str], str] = {}
    for point in draft.points:
        for evidence in point.material_evidence_refs:
            key = (
                str(evidence.material_bundle_version_id),
                evidence.segment_id,
                evidence.start_offset,
                evidence.end_offset,
                evidence.quote_sha256,
            )
            owner = evidence_owner.setdefault(key, point.rubric_point_id)
            if owner != point.rubric_point_id:
                issues.append(
                    _issue(
                        "duplicate_point_evidence",
                        "one exact material span cannot award multiple rubric points",
                    )
                )


def _validate_source_integrity(
    *,
    source: RubricCompilationSource,
    issues: list[RubricCompilationIssue],
) -> None:
    if not source.task_key.startswith("guokao"):
        issues.append(
            _issue("task_scope_invalid", "rubric compiler accepts only 国考 tasks")
        )
    if not source.segments:
        issues.append(
            _issue("material_segments_missing", "complete material has no segments")
        )
    if _text_hash(source.prompt) != source.prompt_sha256:
        issues.append(
            _issue(
                "stored_prompt_hash_invalid",
                "stored prompt hash does not match text",
            )
        )
    segment_ids = [item.segment_id for item in source.segments]
    if len(segment_ids) != len(set(segment_ids)):
        issues.append(
            _issue(
                "material_segment_duplicate",
                "material segment IDs are not unique",
            )
        )
    for segment in source.segments:
        if _text_hash(segment.text) != segment.text_sha256:
            issues.append(
                _issue(
                    "stored_segment_hash_invalid",
                    f"stored material hash is invalid for {segment.segment_id}",
                )
            )


def _issue(code: str, message: str) -> RubricCompilationIssue:
    return RubricCompilationIssue(code=code, message=message)


def _normalized(value: str) -> str:
    return "".join(value.split()).casefold()


def _text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
