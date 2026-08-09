from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal, Sequence


MODEL_VERSION = "historical-position-cohort-v5"
PRIMARY_REFERENCE_YEAR = 2026
MIN_REFERENCE_POSITIONS = 3
MAX_GUOKAO_TOTAL_SCORE = 200.0

Gender = Literal["男", "女"]
GenderRestriction = Literal["男", "女", "unrestricted"]
FallbackLevel = Literal[
    "latest_local_exact_major",
    "latest_local_major_family",
    "latest_national_exact_major",
    "older_local_exact_major",
]


@dataclass(frozen=True)
class LearnerTargetProfile:
    profile_version: int
    major: str
    major_family: tuple[str, ...]
    education: str
    preferred_regions: tuple[str, ...]
    gender: Gender | None = None
    political_status: str | None = None
    hukou: str | None = None
    certifications: tuple[str, ...] = ()
    grassroots_years: int = 0


@dataclass(frozen=True)
class HistoricalPositionSample:
    position_id: str
    position_year: int
    score_year: int
    entry_score: float
    name: str
    department: str
    region: str
    city: str | None
    education: str | None
    major_requirement: str | None
    political_requirement: str | None = None
    hukou_requirement: str | None = None
    certifications: tuple[str, ...] = ()
    gender_restriction: GenderRestriction = "unrestricted"
    grassroots_years: int = 0


@dataclass(frozen=True)
class ReferencePosition:
    position_id: str
    name: str
    department: str
    region: str
    score_year: int
    entry_score: float
    matched_reasons: tuple[str, ...]


@dataclass(frozen=True)
class LearnerTargetScore:
    version_id: str
    model_version: str
    profile_version: int
    reference_year: int
    target_year: int
    fallback_level: FallbackLevel
    historical_p80: float
    historical_p95: float
    cohort_maximum: float
    historical_maximum: float
    coverage_sample_count: int
    trend_uplift: float
    trend_reference_year: int | None
    trend_sample_count: int
    lower: float
    recommended: float
    upper: float
    sample_count: int
    reference_positions: tuple[ReferencePosition, ...]
    limitations: tuple[str, ...]


def derive_learner_target_score(
    *,
    profile: LearnerTargetProfile,
    samples: Sequence[HistoricalPositionSample],
    reference_year: int = PRIMARY_REFERENCE_YEAR,
) -> LearnerTargetScore | None:
    """Select an explainable historical cohort before deriving a score range.

    Gender is deliberately conditional: unrestricted positions are neutral for every
    learner; a restricted position is excluded only when it conflicts with a known
    learner gender.  The selector never blends a weaker fallback tier into a tier
    that already has enough positions.
    """

    eligible = [sample for sample in samples if _hard_conditions_match(profile, sample)]
    tiers: tuple[tuple[FallbackLevel, list[HistoricalPositionSample]], ...] = (
        (
            "latest_local_exact_major",
            [
                item
                for item in eligible
                if item.score_year == reference_year
                and item.region in profile.preferred_regions
                and _exact_major(profile.major, item.major_requirement)
            ],
        ),
        (
            "latest_local_major_family",
            [
                item
                for item in eligible
                if item.score_year == reference_year
                and item.region in profile.preferred_regions
                and _major_family_match(profile, item.major_requirement)
            ],
        ),
        (
            "latest_national_exact_major",
            [
                item
                for item in eligible
                if item.score_year == reference_year
                and _exact_major(profile.major, item.major_requirement)
            ],
        ),
        (
            "older_local_exact_major",
            [
                item
                for item in eligible
                if item.score_year < reference_year
                and item.region in profile.preferred_regions
                and _exact_major(profile.major, item.major_requirement)
            ],
        ),
    )
    selected_level: FallbackLevel | None = None
    selected: list[HistoricalPositionSample] = []
    for level, tier_samples in tiers:
        distinct = _latest_score_per_position(tier_samples)
        if len(distinct) >= MIN_REFERENCE_POSITIONS:
            selected_level = level
            selected = distinct
            break
    if selected_level is None:
        return None

    scores = sorted(item.entry_score for item in selected)
    historical_p80 = round(_quantile(scores, 0.8), 2)
    historical_p95 = round(_quantile(scores, 0.95), 2)
    cohort_maximum = round(max(scores), 2)
    coverage = _latest_score_per_position(
        [
            item
            for item in eligible
            if item.score_year == reference_year
            and _major_family_match(profile, item.major_requirement)
        ]
    )
    historical_maximum = round(
        max(item.entry_score for item in coverage) if coverage else cohort_maximum,
        2,
    )
    trend_uplift, trend_reference_year, trend_sample_count = _trend_uplift(
        profile=profile,
        eligible=eligible,
        selected_level=selected_level,
        selected_scores=scores,
        reference_year=reference_year,
    )
    lower = round(min(MAX_GUOKAO_TOTAL_SCORE, historical_p80 + trend_uplift), 2)
    recommended = round(min(MAX_GUOKAO_TOTAL_SCORE, historical_p95 + trend_uplift), 2)
    upper = round(min(MAX_GUOKAO_TOTAL_SCORE, historical_maximum + trend_uplift), 2)
    selected_references = sorted(
        selected,
        key=lambda row: (row.entry_score, row.position_id),
        reverse=True,
    )
    coverage_maximum = max(
        coverage or selected,
        key=lambda row: (row.entry_score, row.position_id),
    )
    reference_rows = [coverage_maximum]
    reference_rows.extend(
        item for item in selected_references if item.position_id != coverage_maximum.position_id
    )
    references = tuple(
        ReferencePosition(
            position_id=item.position_id,
            name=item.name,
            department=item.department,
            region=item.region,
            score_year=item.score_year,
            entry_score=item.entry_score,
            matched_reasons=_matched_reasons(profile, item),
        )
        for item in reference_rows[:8]
    )
    limitations = _limitations(
        selected_level,
        selected,
        reference_year,
        trend_uplift=trend_uplift,
        trend_reference_year=trend_reference_year,
        trend_sample_count=trend_sample_count,
    )
    version_payload = {
        "modelVersion": MODEL_VERSION,
        "profileVersion": profile.profile_version,
        "referenceYear": reference_year,
        "targetYear": reference_year + 1,
        "fallbackLevel": selected_level,
        "historicalP80": historical_p80,
        "historicalP95": historical_p95,
        "cohortMaximum": cohort_maximum,
        "historicalMaximum": historical_maximum,
        "coverageSamples": [
            [item.position_id, item.score_year, round(item.entry_score, 3)]
            for item in sorted(coverage, key=lambda row: (row.position_id, row.score_year))
        ],
        "trendUplift": trend_uplift,
        "trendReferenceYear": trend_reference_year,
        "samples": [
            [item.position_id, item.score_year, round(item.entry_score, 3)]
            for item in sorted(selected, key=lambda row: (row.position_id, row.score_year))
        ],
    }
    version_id = sha256(
        json.dumps(version_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return LearnerTargetScore(
        version_id=version_id,
        model_version=MODEL_VERSION,
        profile_version=profile.profile_version,
        reference_year=reference_year,
        target_year=reference_year + 1,
        fallback_level=selected_level,
        historical_p80=historical_p80,
        historical_p95=historical_p95,
        cohort_maximum=cohort_maximum,
        historical_maximum=historical_maximum,
        coverage_sample_count=len(coverage),
        trend_uplift=trend_uplift,
        trend_reference_year=trend_reference_year,
        trend_sample_count=trend_sample_count,
        lower=lower,
        recommended=recommended,
        upper=upper,
        sample_count=len(selected),
        reference_positions=references,
        limitations=limitations,
    )


def _trend_uplift(
    *,
    profile: LearnerTargetProfile,
    eligible: Sequence[HistoricalPositionSample],
    selected_level: FallbackLevel,
    selected_scores: Sequence[float],
    reference_year: int,
) -> tuple[float, int | None, int]:
    """Derive one transparent next-exam allowance from comparable high quantiles.

    A single extreme position must not determine the trend.  P80 and P95 preserve
    the high-score preparation intent while remaining comparable across cohorts.
    The whole-point floor deliberately avoids claiming decimal forecast precision
    from only one year-over-year comparison.
    """

    prior_year = reference_year - 1
    prior = _latest_score_per_position(
        _cohort_for_level(
            profile=profile,
            eligible=eligible,
            level=selected_level,
            score_year=prior_year,
        )
    )
    if len(prior) < MIN_REFERENCE_POSITIONS:
        return 0.0, None, 0
    prior_scores = sorted(item.entry_score for item in prior)
    p80_delta = _quantile(selected_scores, 0.8) - _quantile(prior_scores, 0.8)
    p95_delta = _quantile(selected_scores, 0.95) - _quantile(prior_scores, 0.95)
    uplift = float(max(0, int((p80_delta + p95_delta) / 2)))
    return uplift, prior_year, len(prior)


def _cohort_for_level(
    *,
    profile: LearnerTargetProfile,
    eligible: Sequence[HistoricalPositionSample],
    level: FallbackLevel,
    score_year: int,
) -> list[HistoricalPositionSample]:
    rows = [item for item in eligible if item.score_year == score_year]
    if level == "latest_local_exact_major":
        return [
            item
            for item in rows
            if item.region in profile.preferred_regions
            and _exact_major(profile.major, item.major_requirement)
        ]
    if level == "latest_local_major_family":
        return [
            item
            for item in rows
            if item.region in profile.preferred_regions
            and _major_family_match(profile, item.major_requirement)
        ]
    if level == "latest_national_exact_major":
        return [item for item in rows if _exact_major(profile.major, item.major_requirement)]
    return []


def _hard_conditions_match(
    profile: LearnerTargetProfile,
    sample: HistoricalPositionSample,
) -> bool:
    if sample.gender_restriction != "unrestricted":
        if profile.gender is None or sample.gender_restriction != profile.gender:
            return False
    if sample.grassroots_years > profile.grassroots_years:
        return False
    if sample.education and not _education_compatible(profile.education, sample.education):
        return False
    if sample.political_requirement and not _unrestricted(sample.political_requirement):
        if (
            not profile.political_status
            or profile.political_status not in sample.political_requirement
        ):
            return False
    if sample.hukou_requirement and not _unrestricted(sample.hukou_requirement):
        if not profile.hukou or profile.hukou not in sample.hukou_requirement:
            return False
    if sample.certifications and not set(sample.certifications).intersection(
        profile.certifications
    ):
        return False
    return True


def _matched_reasons(
    profile: LearnerTargetProfile,
    sample: HistoricalPositionSample,
) -> tuple[str, ...]:
    reasons = ["专业相符", "学历条件相符"]
    if sample.region in profile.preferred_regions:
        reasons.append("目标地区相符")
    if sample.gender_restriction != "unrestricted":
        reasons.append(f"同为限{sample.gender_restriction}岗位")
    if sample.political_requirement and not _unrestricted(sample.political_requirement):
        reasons.append("政治面貌条件相符")
    if sample.hukou_requirement and not _unrestricted(sample.hukou_requirement):
        reasons.append("户籍条件相符")
    if sample.certifications:
        reasons.append("证书条件相符")
    return tuple(reasons)


def _latest_score_per_position(
    samples: Sequence[HistoricalPositionSample],
) -> list[HistoricalPositionSample]:
    latest: dict[str, HistoricalPositionSample] = {}
    for sample in samples:
        current = latest.get(sample.position_id)
        if current is None or sample.score_year > current.score_year:
            latest[sample.position_id] = sample
    return list(latest.values())


def _exact_major(major: str, requirement: str | None) -> bool:
    if _unrestricted(requirement):
        return False
    normalized = (requirement or "").replace("专业", "")
    candidate = major.replace("专业", "")
    return candidate in normalized


def _major_family_match(profile: LearnerTargetProfile, requirement: str | None) -> bool:
    if _exact_major(profile.major, requirement):
        return True
    if _unrestricted(requirement):
        return False
    normalized = (requirement or "").replace("专业", "")
    return any(item.replace("专业", "") in normalized for item in profile.major_family)


def _unrestricted(value: str | None) -> bool:
    return value is None or not value.strip() or "不限" in value


def _education_compatible(learner: str, requirement: str) -> bool:
    ranks = {"专科": 1, "大专": 1, "本科": 2, "硕士": 3, "研究生": 3, "博士": 4}
    learner_rank = max((rank for label, rank in ranks.items() if label in learner), default=0)
    required_rank = max((rank for label, rank in ranks.items() if label in requirement), default=0)
    return required_rank == 0 or learner_rank >= required_rank


def _quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    if len(values) == 1:
        return float(values[0])
    index = (len(values) - 1) * quantile
    lower_index = int(index)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = index - lower_index
    return float(values[lower_index] + (values[upper_index] - values[lower_index]) * fraction)


def _limitations(
    level: FallbackLevel,
    samples: Sequence[HistoricalPositionSample],
    reference_year: int,
    *,
    trend_uplift: float,
    trend_reference_year: int | None,
    trend_sample_count: int,
) -> tuple[str, ...]:
    messages: list[str] = []
    if level == "latest_local_major_family":
        messages.append("本地同专业样本不足，已扩展到同专业类别。")
    elif level == "latest_national_exact_major":
        messages.append("目标省份样本不足，已使用全国同专业总体样本；未指定某个相邻省份。")
    elif level == "older_local_exact_major":
        messages.append("最近完整年度样本不足，已使用更早年度的本地同专业岗位。")
    if any(item.gender_restriction != "unrestricted" for item in samples):
        messages.append("限性别岗位仅与相同性别限制分层比较，不影响不限性别岗位。")
    years = sorted({item.score_year for item in samples})
    if years != [reference_year]:
        messages.append(f"实际引用分数年份：{', '.join(str(year) for year in years)}。")
    if trend_reference_year is None:
        messages.append("缺少至少 3 个同口径上年度样本，下一届目标暂未加入趋势上抛。")
    else:
        messages.append(
            f"对比 {trend_reference_year} 年同口径 {trend_sample_count} 个样本，"
            f"下一届目标上抛 {trend_uplift:g} 分；该趋势仅有一年对照，置信度有限。"
        )
    return tuple(messages)
