from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QualificationBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


QualificationFactKey = Literal[
    "education",
    "degree",
    "major",
    "graduation_year",
    "fresh_graduate",
    "political_status",
    "work_years",
    "grassroots_years",
    "service_project",
    "hukou",
    "age",
    "certifications",
    "other_conditions",
]
QualificationFactValue: TypeAlias = str | int | bool | list[str]
FactVerificationStatus = Literal["reported", "confirmed", "conflicting"]
FactSourceType = Literal["learner_report", "document", "official_import", "operator"]


class FactEvidence(QualificationBaseModel):
    evidence_id: str = Field(alias="evidenceId")
    source_type: FactSourceType = Field(alias="sourceType")
    source_reference: str | None = Field(default=None, alias="sourceReference")
    recorded_at: datetime = Field(alias="recordedAt")


class QualificationFact(QualificationBaseModel):
    fact_id: str = Field(alias="factId")
    key: QualificationFactKey
    value: QualificationFactValue
    verification_status: FactVerificationStatus = Field(alias="verificationStatus")
    evidence: list[FactEvidence] = Field(default_factory=list)
    effective_at: datetime = Field(alias="effectiveAt")
    supersedes_fact_id: str | None = Field(default=None, alias="supersedesFactId")


RequirementOperator = Literal["equals", "one_of", "at_least", "contains_any", "manual_review"]
RequirementField = QualificationFactKey


class PositionRequirement(QualificationBaseModel):
    requirement_id: str = Field(alias="requirementId")
    field: RequirementField
    operator: RequirementOperator
    expected: QualificationFactValue | None = None
    hard_condition: bool = Field(default=True, alias="hardCondition")
    source_text: str = Field(alias="sourceText", min_length=1)
    source_reference: str = Field(alias="sourceReference", min_length=1)

    @model_validator(mode="after")
    def validate_expected_value(self) -> "PositionRequirement":
        if self.operator != "manual_review" and self.expected is None:
            raise ValueError(f"{self.operator} requires expected")
        if self.operator == "manual_review" and self.expected is not None:
            raise ValueError("manual_review cannot carry an expected value")
        return self


RequirementCheckStatus = Literal["met", "not_met", "needs_confirmation"]


class RequirementCheck(QualificationBaseModel):
    requirement_id: str = Field(alias="requirementId")
    status: RequirementCheckStatus
    reason_code: str = Field(alias="reasonCode")
    fact_id: str | None = Field(default=None, alias="factId")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    field: RequirementField | None = None
    operator: RequirementOperator | None = None
    expected: QualificationFactValue | None = None
    source_text: str | None = Field(default=None, alias="sourceText")
    source_reference: str | None = Field(default=None, alias="sourceReference")
    confirmation_id: str | None = Field(default=None, alias="confirmationId")


RequirementConfirmationResponse = Literal["satisfied", "not_satisfied", "uncertain"]


class RequirementConfirmation(QualificationBaseModel):
    confirmation_id: str = Field(alias="confirmationId")
    position_id: str = Field(alias="positionId")
    requirement_id: str = Field(alias="requirementId")
    profile_version: int = Field(alias="profileVersion", ge=1)
    response: RequirementConfirmationResponse
    statement: str | None = Field(default=None, max_length=500)
    confirmed_at: datetime = Field(alias="confirmedAt")
    supersedes_confirmation_id: str | None = Field(
        default=None,
        alias="supersedesConfirmationId",
    )


class RequirementConfirmationRequest(QualificationBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    response: RequirementConfirmationResponse
    statement: str | None = Field(default=None, max_length=500)


EligibilityStatus = Literal["eligible", "needs_confirmation", "ineligible"]


class EligibilityEvaluation(QualificationBaseModel):
    status: EligibilityStatus
    checks: list[RequirementCheck]
    missing_fact_keys: list[QualificationFactKey] = Field(alias="missingFactKeys")
    rule_set_version: str = Field(alias="ruleSetVersion")
    position_data_version: str = Field(alias="positionDataVersion")
    evaluated_at: datetime = Field(alias="evaluatedAt")


class EligibilitySnapshot(QualificationBaseModel):
    snapshot_id: str = Field(alias="snapshotId")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    facts: list[QualificationFact]
    evaluation: EligibilityEvaluation


class QualificationFactInput(QualificationBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    key: QualificationFactKey
    value: QualificationFactValue


class QualificationFactsUpdate(QualificationBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    facts: list[QualificationFactInput] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "QualificationFactsUpdate":
        keys = [fact.key for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError("qualification facts cannot repeat a key")
        return self


class QualificationProfile(QualificationBaseModel):
    profile_version: int = Field(alias="profileVersion", ge=1)
    facts: list[QualificationFact]


class PositionEligibilitySnapshot(QualificationBaseModel):
    snapshot_id: str = Field(alias="snapshotId")
    position_id: str = Field(alias="positionId")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    facts: list[QualificationFact]
    evaluation: EligibilityEvaluation


class RequirementConfirmationResult(QualificationBaseModel):
    confirmation: RequirementConfirmation
    eligibility: PositionEligibilitySnapshot


QualificationGapType = Literal[
    "missing_fact",
    "conflicting_fact",
    "position_confirmation",
]
QualificationGapAction = Literal["complete_profile", "review_position_requirement"]


class QualificationGap(QualificationBaseModel):
    fact_key: QualificationFactKey = Field(alias="factKey")
    gap_type: QualificationGapType = Field(alias="gapType")
    affected_position_count: int = Field(alias="affectedPositionCount", ge=1)
    sample_position_ids: list[str] = Field(alias="samplePositionIds", max_length=3)
    sample_position_codes: list[str] = Field(alias="samplePositionCodes", max_length=3)
    action: QualificationGapAction


class QualificationGapSummary(QualificationBaseModel):
    exam_year: int = Field(alias="examYear", ge=2000, le=2100)
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    rule_set_version: str = Field(alias="ruleSetVersion")
    position_data_version: str = Field(alias="positionDataVersion")
    total_affected_positions: int = Field(alias="totalAffectedPositions", ge=0)
    gaps: list[QualificationGap]
    complete: bool
