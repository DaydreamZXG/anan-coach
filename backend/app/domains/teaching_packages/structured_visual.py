from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.domains.teaching_packages.contracts import (
    CompiledTeachingPackage,
    TeachingPackageModel,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class StructuredVisualReviewState(StrEnum):
    CANDIDATE = "candidate"
    NEEDS_REVIEW = "needs_review"
    MACHINE_VERIFIED = "machine_verified"
    VERIFIED = "verified"


class StructuredVisualFactKind(StrEnum):
    TITLE = "title"
    UNIT = "unit"
    AXIS = "axis"
    CATEGORY = "category"
    LEGEND = "legend"
    SERIES_VALUE = "series_value"
    TABLE_CELL = "table_cell"
    RELATIONSHIP = "relationship"
    ENTITY = "entity"
    LABEL = "label"
    FORMULA = "formula"
    SOURCE_ONLY = "source_only"


class StructuredVisualAudience(StrEnum):
    STUDENT = "student"
    VERIFIER = "verifier"


class StructuredVisualFact(TeachingPackageModel):
    """One answer-free literal transcribed from the current visual asset."""

    fact_ref: str = Field(alias="factRef", min_length=1, max_length=240)
    kind: StructuredVisualFactKind
    dimensions: tuple[str, ...] = Field(default=(), max_length=20)
    value: str = Field(min_length=1, max_length=2000)
    unit: str = Field(default="", max_length=120)


class StructuredVisualEvidence(TeachingPackageModel):
    """Reviewed evidence candidate with its complete immutable source identity."""

    evidence_ref: str = Field(alias="evidenceRef", min_length=1, max_length=240)
    question_ref: str = Field(alias="questionRef", min_length=1, max_length=240)
    content_version_id: str = Field(
        alias="contentVersionId", min_length=1, max_length=240
    )
    source_asset_sha256: str = Field(
        alias="sourceAssetSha256", pattern=r"^[0-9a-f]{64}$"
    )
    review_state: StructuredVisualReviewState = Field(alias="reviewState")
    chart_type: str = Field(alias="chartType", min_length=1, max_length=120)
    title: str = Field(default="", max_length=2000)
    unit: str = Field(default="", max_length=120)
    facts: tuple[StructuredVisualFact, ...] = Field(min_length=1, max_length=5000)
    evidence_kind: str = Field(
        alias="evidenceKind", default="data_visual", min_length=1, max_length=120
    )
    audiences: tuple[StructuredVisualAudience, ...] = Field(
        default=(StructuredVisualAudience.STUDENT, StructuredVisualAudience.VERIFIER),
        min_length=1,
        max_length=2,
    )
    source_roles: tuple[str, ...] = Field(
        alias="sourceRoles", default=(), max_length=20
    )

    @model_validator(mode="after")
    def validate_unique_fact_refs(self) -> Self:
        refs = tuple(item.fact_ref for item in self.facts)
        if len(refs) != len(set(refs)):
            raise ValueError("structured visual factRef values must be unique")
        if len(self.audiences) != len(set(self.audiences)):
            raise ValueError("structured visual audiences must be unique")
        if len(self.source_roles) != len(set(self.source_roles)):
            raise ValueError("structured visual sourceRoles must be unique")
        return self


class TeachingQuestionVisualBinding(TeachingPackageModel):
    """Resolved content-version binding for one selected teaching question."""

    question_ref: str = Field(alias="questionRef", min_length=1, max_length=240)
    content_version_id: str = Field(
        alias="contentVersionId", min_length=1, max_length=240
    )
    visual_required: bool = Field(alias="visualRequired")
    source_asset_sha256s: tuple[str, ...] = Field(
        alias="sourceAssetSha256s", default=(), max_length=100
    )
    evidence: tuple[StructuredVisualEvidence, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_source_hashes(self) -> Self:
        if any(_SHA256_PATTERN.fullmatch(value) is None for value in self.source_asset_sha256s):
            raise ValueError("sourceAssetSha256s must contain lowercase SHA-256 values")
        if len(self.source_asset_sha256s) != len(set(self.source_asset_sha256s)):
            raise ValueError("sourceAssetSha256s must be unique")
        refs = tuple(item.evidence_ref for item in self.evidence)
        if len(refs) != len(set(refs)):
            raise ValueError("evidenceRef values must be unique per question binding")
        return self


class SafeStructuredVisualProjection(TeachingPackageModel):
    """Only this literal, answer-free shape may enter a student prompt."""

    evidence_ref: str = Field(alias="evidenceRef")
    question_ref: str = Field(alias="questionRef")
    chart_type: str = Field(alias="chartType")
    title: str
    unit: str
    facts: tuple[StructuredVisualFact, ...]
    evidence_kind: str = Field(alias="evidenceKind", default="data_visual")
    audiences: tuple[StructuredVisualAudience, ...] = Field(
        default=(StructuredVisualAudience.STUDENT, StructuredVisualAudience.VERIFIER)
    )


class StructuredVisualAttachState(StrEnum):
    ATTACHABLE = "attachable"
    UNAVAILABLE = "unavailable"


class StructuredVisualAttachReasonCode(StrEnum):
    VISUAL_EVIDENCE_UNAVAILABLE = "VISUAL_EVIDENCE_UNAVAILABLE"
    VISUAL_EVIDENCE_UNVERIFIED = "VISUAL_EVIDENCE_UNVERIFIED"
    VISUAL_EVIDENCE_VERSION_MISMATCH = "VISUAL_EVIDENCE_VERSION_MISMATCH"
    VISUAL_EVIDENCE_BINDING_DUPLICATE = "VISUAL_EVIDENCE_BINDING_DUPLICATE"


class AttachStructuredVisualEvidenceInput(TeachingPackageModel):
    package: CompiledTeachingPackage
    bindings: tuple[TeachingQuestionVisualBinding, ...] = Field(
        min_length=2, max_length=2
    )


class CompiledStructuredVisualSidecar(TeachingPackageModel):
    """Immutable answer-free visual sidecar bound to one compiled v2 package."""

    contract_version: Literal["structured-visual-teaching-evidence@v2"] = Field(
        alias="contractVersion", default="structured-visual-teaching-evidence@v2"
    )
    package_fingerprint: str = Field(
        alias="packageFingerprint", pattern=r"^[0-9a-f]{64}$"
    )
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    student_visual_evidence: tuple[SafeStructuredVisualProjection, ...] = Field(
        alias="studentVisualEvidence", default=(), max_length=100
    )
    user_transfer_visual_evidence: tuple[SafeStructuredVisualProjection, ...] = Field(
        alias="userTransferVisualEvidence", default=(), max_length=100
    )
    student_verifier_visual_evidence: tuple[SafeStructuredVisualProjection, ...] = Field(
        alias="studentVerifierVisualEvidence", default=(), max_length=100
    )
    user_transfer_verifier_visual_evidence: tuple[SafeStructuredVisualProjection, ...] = Field(
        alias="userTransferVerifierVisualEvidence", default=(), max_length=100
    )


class AttachStructuredVisualEvidenceResult(TeachingPackageModel):
    state: StructuredVisualAttachState
    reason_codes: tuple[StructuredVisualAttachReasonCode, ...] = Field(
        alias="reasonCodes"
    )
    package_candidates: tuple[CompiledStructuredVisualSidecar, ...] = Field(
        alias="packageCandidates", max_length=1
    )

    @model_validator(mode="after")
    def validate_complete_or_unavailable(self) -> Self:
        if self.state == StructuredVisualAttachState.ATTACHABLE:
            if self.reason_codes or len(self.package_candidates) != 1:
                raise ValueError("attachable results require one sidecar and no reasons")
        elif not self.reason_codes or self.package_candidates:
            raise ValueError("unavailable results require reasons and no sidecar")
        return self


def _ordered_reasons(
    reasons: set[StructuredVisualAttachReasonCode],
) -> tuple[StructuredVisualAttachReasonCode, ...]:
    return tuple(reason for reason in StructuredVisualAttachReasonCode if reason in reasons)


def _safe_projection(
    evidence: StructuredVisualEvidence,
) -> SafeStructuredVisualProjection:
    return SafeStructuredVisualProjection(
        evidenceRef=evidence.evidence_ref,
        questionRef=evidence.question_ref,
        chartType=evidence.chart_type,
        title=evidence.title,
        unit=evidence.unit,
        facts=evidence.facts,
        evidenceKind=evidence.evidence_kind,
        audiences=evidence.audiences,
    )


def _for_audience(
    evidence: tuple[StructuredVisualEvidence, ...],
    audience: StructuredVisualAudience,
) -> tuple[SafeStructuredVisualProjection, ...]:
    return tuple(
        _safe_projection(item)
        for item in sorted(evidence, key=lambda value: value.evidence_ref)
        if audience in item.audiences
    )


def _binding_reasons(
    binding: TeachingQuestionVisualBinding,
) -> set[StructuredVisualAttachReasonCode]:
    evidence_items = binding.evidence
    if not evidence_items:
        if binding.visual_required:
            return {StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_UNAVAILABLE}
        return set()
    reasons: set[StructuredVisualAttachReasonCode] = set()
    for evidence in evidence_items:
        if evidence.review_state not in {
            StructuredVisualReviewState.MACHINE_VERIFIED,
            StructuredVisualReviewState.VERIFIED,
        }:
            reasons.add(StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_UNVERIFIED)
        if (
            evidence.question_ref != binding.question_ref
            or evidence.content_version_id != binding.content_version_id
            or evidence.source_asset_sha256 not in binding.source_asset_sha256s
        ):
            reasons.add(StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_VERSION_MISMATCH)
    return reasons


def _sidecar_fingerprint(
    package: CompiledTeachingPackage,
    bindings: tuple[TeachingQuestionVisualBinding, ...],
) -> str:
    canonical = {
        "packageFingerprint": package.fingerprint,
        "bindings": [
            item.model_dump(mode="json", by_alias=True)
            for item in sorted(bindings, key=lambda value: value.question_ref)
        ],
    }
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def attach_structured_visual_evidence(
    command: AttachStructuredVisualEvidenceInput,
) -> AttachStructuredVisualEvidenceResult:
    """Attach complete reviewed evidence without mutating the frozen v2 package."""

    refs = tuple(item.question_ref for item in command.bindings)
    if len(refs) != len(set(refs)):
        reasons = {StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_BINDING_DUPLICATE}
        return AttachStructuredVisualEvidenceResult(
            state=StructuredVisualAttachState.UNAVAILABLE,
            reasonCodes=_ordered_reasons(reasons),
            packageCandidates=(),
        )

    expected_refs = {
        command.package.student_question_ref,
        command.package.user_transfer_question_ref,
    }
    if set(refs) != expected_refs:
        reasons = {StructuredVisualAttachReasonCode.VISUAL_EVIDENCE_VERSION_MISMATCH}
        return AttachStructuredVisualEvidenceResult(
            state=StructuredVisualAttachState.UNAVAILABLE,
            reasonCodes=_ordered_reasons(reasons),
            packageCandidates=(),
        )

    reasons: set[StructuredVisualAttachReasonCode] = set()
    by_ref = {item.question_ref: item for item in command.bindings}
    for binding in command.bindings:
        reasons.update(_binding_reasons(binding))
    if reasons:
        return AttachStructuredVisualEvidenceResult(
            state=StructuredVisualAttachState.UNAVAILABLE,
            reasonCodes=_ordered_reasons(reasons),
            packageCandidates=(),
        )

    student = by_ref[command.package.student_question_ref]
    transfer = by_ref[command.package.user_transfer_question_ref]
    sidecar = CompiledStructuredVisualSidecar(
        packageFingerprint=command.package.fingerprint,
        fingerprint=_sidecar_fingerprint(command.package, command.bindings),
        studentVisualEvidence=_for_audience(
            student.evidence, StructuredVisualAudience.STUDENT
        ),
        userTransferVisualEvidence=_for_audience(
            transfer.evidence, StructuredVisualAudience.STUDENT
        ),
        studentVerifierVisualEvidence=_for_audience(
            student.evidence, StructuredVisualAudience.VERIFIER
        ),
        userTransferVerifierVisualEvidence=_for_audience(
            transfer.evidence, StructuredVisualAudience.VERIFIER
        ),
    )
    return AttachStructuredVisualEvidenceResult(
        state=StructuredVisualAttachState.ATTACHABLE,
        reasonCodes=(),
        packageCandidates=(sidecar,),
    )
