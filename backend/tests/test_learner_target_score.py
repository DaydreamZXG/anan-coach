from app.domains.position_decision.learner_target_score import (
    HistoricalPositionSample,
    LearnerTargetProfile,
    derive_learner_target_score,
)
from app.domains.qualification.compiler import certification_labels_from_text


def test_target_score_reuses_qualification_certificate_normalization() -> None:
    assert certification_labels_from_text("大学英语六级考试成绩达到425分以上") == (
        "大学英语六级425分及以上",
    )
    assert certification_labels_from_text("雅思6.5分以上") == ("雅思6.5分及以上",)


def _profile(*, gender: str = "男") -> LearnerTargetProfile:
    return LearnerTargetProfile(
        profile_version=7,
        major="汉语言文学",
        major_family=("中国语言文学类",),
        education="本科",
        preferred_regions=("山东",),
        gender=gender,  # type: ignore[arg-type]
        political_status="群众",
    )


def _sample(
    suffix: str,
    score: float,
    *,
    year: int = 2026,
    region: str = "山东",
    major: str = "汉语言文学",
    gender: str = "unrestricted",
) -> HistoricalPositionSample:
    return HistoricalPositionSample(
        position_id=f"p-{suffix}",
        position_year=year,
        score_year=year,
        entry_score=score,
        name=f"岗位{suffix}",
        department="某机关",
        region=region,
        city=None,
        education="本科及以上",
        major_requirement=major,
        gender_restriction=gender,  # type: ignore[arg-type]
    )


def test_unrestricted_positions_are_gender_neutral() -> None:
    samples = [_sample("1", 120), _sample("2", 126), _sample("3", 132)]

    male = derive_learner_target_score(profile=_profile(gender="男"), samples=samples)
    female = derive_learner_target_score(profile=_profile(gender="女"), samples=samples)

    assert male is not None and female is not None
    assert male.recommended == female.recommended
    assert male.version_id == female.version_id


def test_gender_restricted_positions_only_enter_matching_cohort() -> None:
    samples = [
        _sample("1", 120),
        _sample("2", 126),
        _sample("3", 132, gender="男"),
        _sample("4", 160, gender="女"),
    ]

    male = derive_learner_target_score(profile=_profile(gender="男"), samples=samples)
    female = derive_learner_target_score(profile=_profile(gender="女"), samples=samples)

    assert male is not None and female is not None
    assert {item.position_id for item in male.reference_positions} == {"p-1", "p-2", "p-3"}
    assert {item.position_id for item in female.reference_positions} == {"p-1", "p-2", "p-4"}


def test_sufficient_local_cohort_keeps_national_eligible_maximum_as_coverage_line() -> None:
    samples = [
        _sample("1", 120),
        _sample("2", 126),
        _sample("3", 132),
        _sample("old", 190, year=2025),
        _sample("national", 180, region="北京"),
    ]

    target = derive_learner_target_score(profile=_profile(), samples=samples)

    assert target is not None
    assert target.fallback_level == "latest_local_exact_major"
    assert target.sample_count == 3
    assert target.target_year == 2027
    assert target.lower <= target.recommended <= target.upper
    assert target.cohort_maximum == 132
    assert target.historical_maximum == 180
    assert target.upper == 180
    assert target.reference_positions[0].position_id == "p-national"
    assert target.reference_positions[0].region == "北京"


def test_next_exam_range_uses_high_quantiles_maximum_and_comparable_year_uplift() -> None:
    current = [
        _sample(f"current-{index}", score)
        for index, score in enumerate((110, 120, 130, 140, 150), start=1)
    ]
    prior = [
        _sample(f"prior-{index}", score, year=2025)
        for index, score in enumerate((100, 110, 120, 130, 140), start=1)
    ]

    target = derive_learner_target_score(profile=_profile(), samples=[*current, *prior])

    assert target is not None
    assert target.historical_p80 == 142
    assert target.historical_p95 == 148
    assert target.cohort_maximum == 150
    assert target.historical_maximum == 150
    assert target.coverage_sample_count == 5
    assert target.trend_reference_year == 2025
    assert target.trend_sample_count == 5
    assert target.trend_uplift == 10
    assert (target.lower, target.recommended, target.upper) == (152, 158, 160)
    assert [item.entry_score for item in target.reference_positions[:3]] == [150, 140, 130]
    assert "置信度有限" in "".join(target.limitations)


def test_next_exam_range_does_not_invent_uplift_without_comparable_prior_cohort() -> None:
    target = derive_learner_target_score(
        profile=_profile(),
        samples=[_sample("1", 120), _sample("2", 126), _sample("3", 132)],
    )

    assert target is not None
    assert target.trend_uplift == 0
    assert target.trend_reference_year is None
    assert target.upper == 132
    assert "暂未加入趋势上抛" in "".join(target.limitations)


def test_returns_unavailable_instead_of_inventing_score_for_too_few_samples() -> None:
    target = derive_learner_target_score(
        profile=_profile(),
        samples=[_sample("1", 120), _sample("2", 126)],
    )

    assert target is None


def test_falls_back_to_national_aggregate_instead_of_a_neighbor_province() -> None:
    target = derive_learner_target_score(
        profile=_profile(),
        samples=[
            _sample("local-1", 120),
            _sample("local-2", 126),
            _sample("national-1", 150, region="北京"),
            _sample("national-2", 155, region="江苏"),
            _sample("national-3", 160, region="浙江"),
        ],
    )

    assert target is not None
    assert target.fallback_level == "latest_national_exact_major"
    assert target.sample_count == 5
    assert "未指定某个相邻省份" in "".join(target.limitations)


def test_latest_national_aggregate_precedes_older_local_years() -> None:
    target = derive_learner_target_score(
        profile=_profile(),
        samples=[
            _sample("old-1", 120, year=2025),
            _sample("old-2", 126, year=2025),
            _sample("old-3", 132, year=2025),
            _sample("national-1", 150, region="北京"),
            _sample("national-2", 155, region="江苏"),
            _sample("national-3", 160, region="浙江"),
        ],
    )

    assert target is not None
    assert target.fallback_level == "latest_national_exact_major"
    assert {item.score_year for item in target.reference_positions} == {2026}
