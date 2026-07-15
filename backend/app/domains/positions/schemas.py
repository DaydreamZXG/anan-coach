from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.ability_estimation.schemas import AbilityEstimateCurrent


class PositionsBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CohortAnnualScore(PositionsBaseModel):
    year: int
    average_entry_score: float = Field(alias="averageEntryScore")
    sample_count: int = Field(alias="sampleCount")


class TargetScoreBasis(PositionsBaseModel):
    target_score: float | None = Field(default=None, alias="targetScore")
    basis_level: Literal["direct", "L1", "L2", "L3", "L4"] | None = Field(
        default=None,
        alias="basisLevel",
    )
    sample_position_count: int = Field(alias="samplePositionCount")
    score_sample_count: int = Field(alias="scoreSampleCount")
    annual_scores: list[CohortAnnualScore] = Field(alias="annualScores")
    drift_per_year: float | None = Field(default=None, alias="driftPerYear")
    latest_average_score: float | None = Field(default=None, alias="latestAverageScore")
    safety_margin: float = Field(default=3.0, alias="safetyMargin")


class TargetScoreRange(PositionsBaseModel):
    """Canonical public target-score contract owned by the positions domain."""

    lower_bound: float = Field(alias="lowerBound", ge=0, le=300)
    recommended: float = Field(ge=0, le=300)
    upper_bound: float = Field(alias="upperBound", ge=0, le=300)
    sample_count: int = Field(alias="sampleCount", ge=0)
    basis_level: Literal["direct", "L1", "L2", "L3", "L4"] = Field(
        alias="basisLevel"
    )
    evidence_level: Literal["direct", "strong", "limited", "unavailable"] = Field(
        alias="evidenceLevel"
    )
    generated_at: datetime = Field(alias="generatedAt")
    valid_until: datetime = Field(alias="validUntil")

    @model_validator(mode="after")
    def validate_target_score_contract(self) -> "TargetScoreRange":
        if not self.lower_bound <= self.recommended <= self.upper_bound:
            raise ValueError("target score range must be ordered")
        if self.evidence_level == "unavailable" and self.sample_count != 0:
            raise ValueError("unavailable target score cannot claim samples")
        if self.evidence_level != "unavailable" and self.sample_count == 0:
            raise ValueError("available target score requires samples")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generatedAt must include a timezone")
        if self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None:
            raise ValueError("validUntil must include a timezone")
        if self.valid_until <= self.generated_at:
            raise ValueError("validUntil must be later than generatedAt")
        return self


class PositionTargetScoreResponse(PositionsBaseModel):
    position_id: str = Field(alias="positionId")
    target_score_basis: TargetScoreBasis = Field(alias="targetScoreBasis")


class PositionTargetScoreContractResponse(PositionsBaseModel):
    position_id: str = Field(alias="positionId")
    exam_year: int = Field(alias="examYear")
    rule_set_version: str = Field(alias="ruleSetVersion")
    position_data_version: str = Field(alias="positionDataVersion")
    target_score: TargetScoreRange | None = Field(alias="targetScore")


class EntryScoreStats(PositionsBaseModel):
    years: list[int]
    source_count: int = Field(alias="sourceCount")
    average_entry_score: float | None = Field(default=None, alias="averageEntryScore")
    latest_entry_score: float | None = Field(default=None, alias="latestEntryScore")
    target_score: float | None = Field(default=None, alias="targetScore")
    target_score_basis: TargetScoreBasis | None = Field(
        default=None,
        alias="targetScoreBasis",
    )


class PositionSummary(PositionsBaseModel):
    position_id: str = Field(alias="positionId")
    year: int
    position_code: str = Field(alias="positionCode")
    name: str
    department_full: str = Field(alias="departmentFull")
    system_code: str = Field(alias="systemCode")
    region: str
    city: str | None = None
    headcount: int
    education_min: str | None = Field(default=None, alias="educationMin")
    major_required: str | None = Field(default=None, alias="majorRequired")
    political_req: str | None = Field(default=None, alias="politicalReq")
    service_min_yrs: int | None = Field(default=None, alias="serviceMinYrs")
    remark: str | None = None
    has_extra_exam: bool = Field(default=False, alias="hasExtraExam")
    entry_score: EntryScoreStats = Field(alias="entryScore")
    eligibility_basis: list[str] = Field(alias="eligibilityBasis")


class PositionDetail(PositionsBaseModel):
    position_id: str = Field(alias="positionId")
    year: int
    position_code: str = Field(alias="positionCode")
    name: str
    department_full: str = Field(alias="departmentFull")
    system_code: str = Field(alias="systemCode")
    region: str
    city: str | None = None
    paper_type: str | None = Field(default=None, alias="paperType")
    headcount: int
    education_min: str | None = Field(default=None, alias="educationMin")
    major_required: str | None = Field(default=None, alias="majorRequired")
    political_req: str | None = Field(default=None, alias="politicalReq")
    service_min_yrs: int | None = Field(default=None, alias="serviceMinYrs")
    hukou_req: str | None = Field(default=None, alias="hukouReq")
    remark: str | None = None
    has_extra_exam: bool = Field(default=False, alias="hasExtraExam")
    entry_score: EntryScoreStats = Field(alias="entryScore")


class PositionSearchResponse(PositionsBaseModel):
    items: list[PositionSummary]
    limit: int
    unverified_conditions: list[str] = Field(alias="unverifiedConditions")


class PositionDiscoveryItem(PositionsBaseModel):
    position_id: str = Field(alias="positionId")
    position_code: str = Field(alias="positionCode")
    name: str
    department_full: str = Field(alias="departmentFull")
    system_code: str = Field(alias="systemCode")
    region: str
    city: str | None = None
    headcount: int
    has_extra_exam: bool = Field(alias="hasExtraExam")
    eligibility_status: Literal["eligible", "needs_confirmation", "ineligible"] = Field(
        alias="eligibilityStatus"
    )
    met_count: int = Field(alias="metCount", ge=0)
    pending_count: int = Field(alias="pendingCount", ge=0)
    not_met_count: int = Field(alias="notMetCount", ge=0)
    pending_requirement_ids: list[str] = Field(alias="pendingRequirementIds")
    not_met_requirement_ids: list[str] = Field(alias="notMetRequirementIds")
    reason_codes: list[str] = Field(alias="reasonCodes")
    target_score: TargetScoreRange | None = Field(
        alias="targetScore",
    )
    is_primary_goal: bool = Field(alias="isPrimaryGoal")


class PositionDiscoveryResponse(PositionsBaseModel):
    exam_year: int = Field(alias="examYear")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    rule_set_version: str = Field(alias="ruleSetVersion")
    position_data_version: str = Field(alias="positionDataVersion")
    items: list[PositionDiscoveryItem]
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    has_more: bool = Field(alias="hasMore")


PositionFitStatus = Literal[
    "conservative",
    "overlap",
    "below_target",
    "evidence_unavailable",
]
PositionFitEvidenceReason = Literal[
    "ability_estimate_unavailable",
    "ability_estimate_partial",
    "ability_estimate_stale",
    "ability_estimate_exam_year_mismatch",
    "target_score_unavailable",
]
PositionFitEvidenceStatus = Literal["unavailable", "partial", "stale", "ready"]


class PositionScoreGapRange(PositionsBaseModel):
    lower: float
    recommended: float
    upper: float
    unit: Literal["points"]
    direction: Literal["positive_means_target_above_current"]

    @model_validator(mode="after")
    def validate_gap_order(self) -> "PositionScoreGapRange":
        if not self.lower <= self.recommended <= self.upper:
            raise ValueError("position score gap range must be ordered")
        return self


class PositionFitBurden(PositionsBaseModel):
    additional_exam_required: bool = Field(alias="additionalExamRequired")
    pending_requirement_count: int = Field(alias="pendingRequirementCount", ge=0)
    hard_condition_not_met_count: int = Field(
        alias="hardConditionNotMetCount",
        ge=0,
    )
    pending_requirement_ids: list[str] = Field(alias="pendingRequirementIds")
    hard_condition_not_met_requirement_ids: list[str] = Field(
        alias="hardConditionNotMetRequirementIds"
    )

    @model_validator(mode="after")
    def validate_requirement_references(self) -> "PositionFitBurden":
        if self.pending_requirement_count != len(self.pending_requirement_ids):
            raise ValueError("pending requirement count must match its references")
        if self.hard_condition_not_met_count != len(
            self.hard_condition_not_met_requirement_ids
        ):
            raise ValueError("not-met requirement count must match its references")
        if len(set(self.pending_requirement_ids)) != len(self.pending_requirement_ids):
            raise ValueError("pending requirement references must be unique")
        if len(set(self.hard_condition_not_met_requirement_ids)) != len(
            self.hard_condition_not_met_requirement_ids
        ):
            raise ValueError("not-met requirement references must be unique")
        return self


class PositionFitAssessment(PositionsBaseModel):
    status: PositionFitStatus
    evidence_status: PositionFitEvidenceStatus = Field(alias="evidenceStatus")
    band: Literal["conservative", "overlap", "below_target"] | None
    score_gap: PositionScoreGapRange | None = Field(alias="scoreGap")
    requirements_burden: PositionFitBurden = Field(alias="requirementsBurden")
    evidence_reason_codes: list[PositionFitEvidenceReason] = Field(
        alias="evidenceReasonCodes"
    )

    @model_validator(mode="after")
    def validate_evidence_contract(self) -> "PositionFitAssessment":
        if self.status == "evidence_unavailable":
            if (
                self.evidence_status == "ready"
                or self.band is not None
                or self.score_gap is not None
                or not self.evidence_reason_codes
            ):
                raise ValueError(
                    "unavailable position fit requires non-ready evidence and no band/gap"
                )
        elif (
            self.evidence_status != "ready"
            or self.band != self.status
            or self.score_gap is None
            or self.evidence_reason_codes
        ):
            raise ValueError(
                "classified position fit requires ready evidence, matching band, and gap"
            )
        return self


class PositionWorkspaceItem(PositionDiscoveryItem):
    fit: PositionFitAssessment


class PositionWorkspaceResponse(PositionsBaseModel):
    exam_year: int = Field(alias="examYear")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    rule_set_version: str = Field(alias="ruleSetVersion")
    position_data_version: str = Field(alias="positionDataVersion")
    ability_estimate: AbilityEstimateCurrent = Field(alias="abilityEstimate")
    items: list[PositionWorkspaceItem]
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    has_more: bool = Field(alias="hasMore")


class PositionComparisonRequest(PositionsBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    position_ids: list[UUID] = Field(alias="positionIds", min_length=2, max_length=3)

    @model_validator(mode="after")
    def validate_unique_positions(self) -> "PositionComparisonRequest":
        if len(self.position_ids) != len(set(self.position_ids)):
            raise ValueError("position comparison cannot contain duplicate positions")
        return self


class PositionComparisonItem(PositionDiscoveryItem):
    paper_type: str | None = Field(default=None, alias="paperType")


class PositionComparisonHighlights(PositionsBaseModel):
    confirmed_eligible_position_ids: list[str] = Field(
        alias="confirmedEligiblePositionIds"
    )
    largest_headcount_position_ids: list[str] = Field(alias="largestHeadcountPositionIds")
    lowest_target_score_position_ids: list[str] = Field(
        alias="lowestTargetScorePositionIds"
    )
    primary_goal_position_id: str | None = Field(default=None, alias="primaryGoalPositionId")


class PositionComparisonResponse(PositionsBaseModel):
    exam_year: int = Field(alias="examYear")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    rule_set_version: str = Field(alias="ruleSetVersion")
    position_data_version: str = Field(alias="positionDataVersion")
    items: list[PositionComparisonItem] = Field(min_length=2, max_length=3)
    highlights: PositionComparisonHighlights


class PositionDiscoveryDetail(PositionComparisonItem):
    exam_year: int = Field(alias="examYear")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    rule_set_version: str = Field(alias="ruleSetVersion")
    position_data_version: str = Field(alias="positionDataVersion")
    ability_estimate: AbilityEstimateCurrent = Field(alias="abilityEstimate")
    fit: PositionFitAssessment


class PositionFitResponse(PositionsBaseModel):
    exam_year: int = Field(alias="examYear")
    learner_profile_version: int = Field(alias="learnerProfileVersion", ge=1)
    rule_set_version: str = Field(alias="ruleSetVersion")
    position_data_version: str = Field(alias="positionDataVersion")
    position: PositionComparisonItem
    ability_estimate: AbilityEstimateCurrent = Field(alias="abilityEstimate")
    fit: PositionFitAssessment


class RegionWithCities(PositionsBaseModel):
    name: str
    cities: list[str] = Field(default_factory=list)


class RegionListResponse(PositionsBaseModel):
    regions: list[RegionWithCities]


class MajorListResponse(PositionsBaseModel):
    majors: list[str]


class MajorItem(PositionsBaseModel):
    code: str
    name: str


class MajorSubCategory(PositionsBaseModel):
    code: str
    name: str
    majors: list[MajorItem]


class MajorCategory(PositionsBaseModel):
    code: str
    name: str
    sub_categories: list[MajorSubCategory] = Field(alias="subCategories")


class MajorTreeResponse(PositionsBaseModel):
    categories: list[MajorCategory]


class DistrictInfo(PositionsBaseModel):
    name: str


class CityInfo(PositionsBaseModel):
    name: str
    districts: list[DistrictInfo]


class ProvinceInfo(PositionsBaseModel):
    name: str
    cities: list[CityInfo]


class ProvinceListResponse(PositionsBaseModel):
    provinces: list[ProvinceInfo]
