"""Deterministic, read-only teaching-package compilation."""

from app.domains.teaching_packages.compiler import compile_teaching_package
from app.domains.teaching_packages.contracts import (
    AnswerContext,
    CompileReasonCode,
    CompileState,
    CompileTeachingPackageInput,
    CompileTeachingPackageResult,
    CompiledTeachingPackage,
    ExamTargetScope,
    ReusableMethodAsset,
    SafeQuestionProjection,
    TeachingQuestionCandidate,
)
from app.domains.teaching_packages.structured_visual import (
    AttachStructuredVisualEvidenceInput,
    AttachStructuredVisualEvidenceResult,
    CompiledStructuredVisualSidecar,
    SafeStructuredVisualProjection,
    StructuredVisualAttachReasonCode,
    StructuredVisualAttachState,
    StructuredVisualEvidence,
    StructuredVisualFact,
    StructuredVisualFactKind,
    StructuredVisualReviewState,
    TeachingQuestionVisualBinding,
    attach_structured_visual_evidence,
)
from app.domains.teaching_packages.publication_reader import StructuredVisualEvidenceReader

__all__ = [
    "AnswerContext",
    "AttachStructuredVisualEvidenceInput",
    "AttachStructuredVisualEvidenceResult",
    "CompileReasonCode",
    "CompileState",
    "CompileTeachingPackageInput",
    "CompileTeachingPackageResult",
    "CompiledTeachingPackage",
    "CompiledStructuredVisualSidecar",
    "ExamTargetScope",
    "ReusableMethodAsset",
    "SafeQuestionProjection",
    "SafeStructuredVisualProjection",
    "StructuredVisualAttachReasonCode",
    "StructuredVisualAttachState",
    "StructuredVisualEvidence",
    "StructuredVisualEvidenceReader",
    "StructuredVisualFact",
    "StructuredVisualFactKind",
    "StructuredVisualReviewState",
    "TeachingQuestionCandidate",
    "TeachingQuestionVisualBinding",
    "attach_structured_visual_evidence",
    "compile_teaching_package",
]
