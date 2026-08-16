from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


_SCOPE_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class TeachingPackageModel(BaseModel):
    """Closed immutable base model for compiler inputs and outputs."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        str_strip_whitespace=True,
    )


class CompileState(StrEnum):
    TEACHABLE = "teachable"
    UNAVAILABLE = "unavailable"


class CompileReasonCode(StrEnum):
    METHOD_EXCLUDED_FOR_EXAM = "METHOD_EXCLUDED_FOR_EXAM"
    QUESTION_EXPOSED = "QUESTION_EXPOSED"
    ANSWER_UNAVAILABLE = "ANSWER_UNAVAILABLE"
    EXPLANATION_UNAVAILABLE = "EXPLANATION_UNAVAILABLE"
    INSUFFICIENT_DISTINCT_QUESTIONS = "INSUFFICIENT_DISTINCT_QUESTIONS"


class ExamTargetScope(TeachingPackageModel):
    """Exact exam identity. No dimension is optional and no wildcard is supported."""

    exam_code: Literal["guokao", "shengkao", "sydw"] = Field(alias="examCode")
    region_code: str = Field(alias="regionCode", min_length=1, max_length=80)
    year: int = Field(ge=2000, le=2100)
    category_code: str = Field(alias="categoryCode", min_length=1, max_length=80)
    subject_code: Literal["xingce", "zhice"] = Field(alias="subjectCode")

    @model_validator(mode="after")
    def validate_scope_tokens(self) -> Self:
        for field_name, value in (
            ("regionCode", self.region_code),
            ("categoryCode", self.category_code),
        ):
            if not _SCOPE_TOKEN_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must be a normalized scope token")
        return self

    @computed_field(alias="scopeKey", return_type=str)
    @property
    def scope_key(self) -> str:
        return ":".join(
            (
                self.exam_code,
                self.region_code,
                str(self.year),
                self.category_code,
                self.subject_code,
            )
        )


class ReusableMethodAsset(TeachingPackageModel):
    """Existing objective-test method, reusable unless an exam is explicitly excluded."""

    method_card_ref: str = Field(alias="methodCardRef", min_length=1, max_length=240)
    excluded_exam_codes: tuple[Literal["guokao", "shengkao", "sydw"], ...] = Field(
        alias="excludedExamCodes", default=(), max_length=3
    )
    micro_skill_id: str = Field(alias="microSkillId", min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=500)
    recognition: str = Field(min_length=1, max_length=4000)
    method_steps: tuple[str, ...] = Field(alias="methodSteps", min_length=1, max_length=100)
    applicability: tuple[str, ...] = Field(min_length=1, max_length=100)
    exclusions: tuple[str, ...] = Field(default=(), max_length=100)
    pitfalls: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_unique_exam_exclusions(self) -> Self:
        if len(self.excluded_exam_codes) != len(set(self.excluded_exam_codes)):
            raise ValueError("excludedExamCodes must be unique")
        return self


class TeachingQuestionCandidate(TeachingPackageModel):
    """Resolved immutable question plus its server-only answer authority."""

    question_ref: str = Field(alias="questionRef", min_length=1, max_length=240)
    source_scope_key: str = Field(alias="sourceScopeKey", min_length=1, max_length=400)
    micro_skill_id: str = Field(alias="microSkillId", min_length=1, max_length=240)
    exposure_state: Literal["unseen", "seen"] = Field(alias="exposureState")
    stem: str = Field(min_length=1, max_length=20_000)
    options: tuple[str, ...] = Field(min_length=2, max_length=20)
    answer_key: tuple[str, ...] = Field(alias="answerKey", min_length=1, max_length=20)
    explanation: str = Field(max_length=50_000)


class CompileTeachingPackageInput(TeachingPackageModel):
    """Complete read-only material resolved before compilation."""

    target_scope_key: str = Field(alias="targetScopeKey", min_length=1, max_length=400)
    micro_skill_id: str = Field(alias="microSkillId", min_length=1, max_length=240)
    source_question_ref: str = Field(alias="sourceQuestionRef", min_length=1, max_length=240)
    method_asset: ReusableMethodAsset = Field(alias="methodAsset")
    question_candidates: tuple[TeachingQuestionCandidate, ...] = Field(
        alias="questionCandidates", min_length=2, max_length=1000
    )

    @model_validator(mode="after")
    def validate_target_scope_key(self) -> Self:
        parts = self.target_scope_key.split(":")
        if len(parts) != 5:
            raise ValueError("targetScopeKey must contain exactly five dimensions")
        exam_code, region_code, year, category_code, subject_code = parts
        ExamTargetScope(
            examCode=exam_code,
            regionCode=region_code,
            year=year,
            categoryCode=category_code,
            subjectCode=subject_code,
        )
        if self.method_asset.micro_skill_id != self.micro_skill_id:
            raise ValueError("methodAsset microSkillId must match the requested microSkillId")
        return self


class SafeQuestionProjection(TeachingPackageModel):
    """Only this answer-free shape may enter a student or user question prompt."""

    question_ref: str = Field(alias="questionRef")
    stem: str
    options: tuple[str, ...]


class AnswerContext(TeachingPackageModel):
    """Server-only authority, deliberately separate from the safe projection."""

    question_ref: str = Field(alias="questionRef")
    answer_key: tuple[str, ...] = Field(alias="answerKey")
    explanation: str


class CompiledTeachingPackage(TeachingPackageModel):
    """Internal immutable package candidate; callers must use its safe projections."""

    contract_version: Literal["multi-exam-teaching-package@v2"] = Field(
        alias="contractVersion", default="multi-exam-teaching-package@v2"
    )
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_scope_key: str = Field(alias="targetScopeKey")
    micro_skill_id: str = Field(alias="microSkillId")
    method_card_ref: str = Field(alias="methodCardRef")
    student_question_ref: str = Field(alias="studentQuestionRef")
    student_stem: str = Field(alias="studentStem")
    student_options: tuple[str, ...] = Field(alias="studentOptions")
    student_answer_key: tuple[str, ...] = Field(alias="studentAnswerKey")
    student_explanation: str = Field(alias="studentExplanation")
    user_transfer_question_ref: str = Field(alias="userTransferQuestionRef")
    user_transfer_stem: str = Field(alias="userTransferStem")
    user_transfer_options: tuple[str, ...] = Field(alias="userTransferOptions")
    user_transfer_answer_key: tuple[str, ...] = Field(alias="userTransferAnswerKey")
    user_transfer_explanation: str = Field(alias="userTransferExplanation")

    @property
    def student_question(self) -> SafeQuestionProjection:
        return SafeQuestionProjection(
            questionRef=self.student_question_ref,
            stem=self.student_stem,
            options=self.student_options,
        )

    @property
    def student_answer_context(self) -> AnswerContext:
        return AnswerContext(
            questionRef=self.student_question_ref,
            answerKey=self.student_answer_key,
            explanation=self.student_explanation,
        )

    @property
    def user_transfer_question(self) -> SafeQuestionProjection:
        return SafeQuestionProjection(
            questionRef=self.user_transfer_question_ref,
            stem=self.user_transfer_stem,
            options=self.user_transfer_options,
        )

    @property
    def user_transfer_answer_context(self) -> AnswerContext:
        return AnswerContext(
            questionRef=self.user_transfer_question_ref,
            answerKey=self.user_transfer_answer_key,
            explanation=self.user_transfer_explanation,
        )


class CompileTeachingPackageResult(TeachingPackageModel):
    """Complete package or closed failure report; partial packages are invalid."""

    state: CompileState
    reason_codes: tuple[CompileReasonCode, ...] = Field(alias="reasonCodes")
    package_candidates: tuple[CompiledTeachingPackage, ...] = Field(
        alias="packageCandidates", max_length=1
    )

    @model_validator(mode="after")
    def validate_complete_or_unavailable(self) -> Self:
        if self.state == CompileState.TEACHABLE:
            if self.reason_codes or len(self.package_candidates) != 1:
                raise ValueError("teachable results require one complete package and no reasons")
        elif not self.reason_codes or self.package_candidates:
            raise ValueError("unavailable results require reasons and no package")
        return self
