from __future__ import annotations

import pytest

from app.domains.qualification.compiler import (
    RawPositionConditions,
    compile_position_rules,
)
from app.domains.positions.source_contract import (
    OFFICIAL_POSITION_CONTRACT,
    OFFICIAL_POSITION_FIELD_NAMES,
)
from app.domains.qualification.schemas import QualificationFact
from app.domains.qualification.service import evaluate_eligibility
from datetime import UTC, datetime


NOW = datetime(2026, 7, 12, tzinfo=UTC)
TEST_MAJOR_CATALOG = frozenset({"法学", "汉语言文学", "人类学"})
TEST_MAJOR_CATALOG_VERSION = "test-major-catalog@2022-v1"


def _source(**changes: object) -> RawPositionConditions:
    values = {
        "position_id": "11111111-1111-1111-1111-111111111111",
        "education_min": "本科及以上",
        "major_required": "法学、汉语言文学",
        "political_req": "不限",
        "service_min_yrs": 0,
        "hukou_req": None,
        "remark": None,
    }
    values.update(changes)
    return RawPositionConditions(**values)  # type: ignore[arg-type]


def _compile(
    source: RawPositionConditions,
    *,
    major_catalog_names: frozenset[str] | None = TEST_MAJOR_CATALOG,
    major_catalog_version: str | None = TEST_MAJOR_CATALOG_VERSION,
):
    return compile_position_rules(
        source,
        rule_set_version="guokao-2026-v1",
        position_data_version="guokao-2026-fixture",
        major_catalog_names=major_catalog_names,
        major_catalog_version=major_catalog_version,
    )


def _fact(key: str, value: object) -> QualificationFact:
    return QualificationFact.model_validate(
        {
            "factId": f"fact-{key}",
            "key": key,
            "value": value,
            "verificationStatus": "confirmed",
            "effectiveAt": NOW,
        }
    )


def _official_payload(**field_changes: object) -> dict[str, object]:
    fields: dict[str, object] = {key: "" for key in OFFICIAL_POSITION_FIELD_NAMES}
    fields.update(
        {
            "degree": "与最高学历相对应的学位",
            "serviceProject": "无限制",
            **field_changes,
        }
    )
    return {
        "contractVersion": OFFICIAL_POSITION_CONTRACT,
        "positionDataVersion": "guokao-2026-official-v1",
        "source": {
            "fileName": "2026.xls",
            "sha256": "a" * 64,
            "sheetName": "中央党群机关",
            "rowNumber": 3,
        },
        "officialFields": fields,
    }


def test_compiles_known_education_exact_majors_and_service_years() -> None:
    compiled = _compile(_source(service_min_yrs=2))

    assert compiled.status == "deterministic"
    by_field = {item.field: item for item in compiled.requirements}
    assert by_field["education"].operator == "one_of"
    assert by_field["education"].expected == ["本科", "硕士", "博士"]
    assert by_field["major"].operator == "contains_any"
    assert by_field["major"].expected == ["法学", "汉语言文学"]
    assert by_field["grassroots_years"].operator == "at_least"
    assert by_field["grassroots_years"].expected == 2


def test_major_category_and_hukou_are_never_guessed() -> None:
    compiled = _compile(_source(major_required="法学类", hukou_req="限江西省户籍或生源"))

    assert compiled.status == "needs_confirmation"
    by_field = {item.field: item for item in compiled.requirements}
    assert by_field["major"].operator == "manual_review"
    assert by_field["hukou"].operator == "manual_review"
    assert compiled.manual_review_count == 2
    assert "major_requires_catalog_confirmation" in compiled.warnings


def test_exact_major_names_compile_without_treating_name_characters_as_categories() -> None:
    compiled = _compile(_source(major_required="仅限人类学专业、法学"))

    requirement = next(item for item in compiled.requirements if item.field == "major")
    assert requirement.operator == "contains_any"
    assert requirement.expected == ["人类学", "人类学专业", "法学"]
    assert compiled.status == "deterministic"

    result = evaluate_eligibility(
        facts=[_fact("education", "本科"), _fact("major", "人类学")],
        requirements=list(compiled.requirements),
        rule_set_version="guokao-2026-v1",
        position_data_version="guokao-2026-fixture",
        evaluated_at=NOW,
        requirements_authoritative=True,
    )
    assert result.status == "eligible"


@pytest.mark.parametrize(
    "major_required",
    [
        "男性",
        "汉族",
        "专业",
        "本科专业不限",
        "财会审计",
        "法学、男性",
    ],
)
def test_plain_chinese_tokens_outside_the_bound_catalog_remain_manual(
    major_required: str,
) -> None:
    compiled = _compile(_source(major_required=major_required))

    requirement = next(item for item in compiled.requirements if item.field == "major")
    assert requirement.operator == "manual_review"
    assert compiled.status == "needs_confirmation"
    assert "major_requires_catalog_confirmation" in compiled.warnings
    result = evaluate_eligibility(
        facts=[_fact("education", "本科"), _fact("major", major_required)],
        requirements=list(compiled.requirements),
        rule_set_version="guokao-2026-v1",
        position_data_version="guokao-2026-fixture",
        evaluated_at=NOW,
        requirements_authoritative=True,
    )
    assert result.status == "needs_confirmation"


def test_exact_major_fails_closed_without_a_versioned_catalog_snapshot() -> None:
    compiled = _compile(
        _source(major_required="法学"),
        major_catalog_names=None,
        major_catalog_version=None,
    )

    requirement = next(item for item in compiled.requirements if item.field == "major")
    assert requirement.operator == "manual_review"
    assert compiled.status == "needs_confirmation"
    assert "major_catalog_unavailable" in compiled.warnings


def test_major_catalog_snapshot_changes_the_source_fingerprint() -> None:
    source = _source(major_required="法学")
    first = _compile(source)
    expanded = _compile(
        source,
        major_catalog_names=TEST_MAJOR_CATALOG | {"社会学"},
        major_catalog_version="test-major-catalog@2022-v2",
    )
    unavailable = _compile(
        source,
        major_catalog_names=None,
        major_catalog_version=None,
    )

    assert first.source_fingerprint != expanded.source_fingerprint
    assert first.source_fingerprint != unavailable.source_fingerprint


@pytest.mark.parametrize(
    "major_required",
    [
        "社会学类",
        "法学及相关专业",
        "本科：法学；研究生：法律硕士",
        "法学（不含知识产权方向）",
        "法学（含知识产权专业）",
        "法学（限通过法律职业资格考试）",
        "英语（限英语专业八级）",
        "汉语言文学（师范）",
        "一级学科为法学",
        "所学专业为法学",
        "030101K法学",
        "法学和汉语言文学",
        "法学及汉语言文学",
        "法学等专业",
    ],
)
def test_complex_major_expressions_remain_manual_review(major_required: str) -> None:
    compiled = _compile(_source(major_required=major_required))

    requirement = next(item for item in compiled.requirements if item.field == "major")
    assert requirement.operator == "manual_review"
    assert compiled.status == "needs_confirmation"
    assert "major_requires_catalog_confirmation" in compiled.warnings


def test_unrecognized_certificate_remark_creates_a_blocking_other_condition() -> None:
    compiled = _compile(_source(remark="须持有注册会计师资格证书"))

    requirement = next(item for item in compiled.requirements if item.field == "other_conditions")
    assert requirement.operator == "manual_review"
    assert requirement.source_text == "须持有注册会计师资格证书"
    assert compiled.status == "needs_confirmation"


def test_unmapped_raw_fields_create_a_blocking_audit_rule() -> None:
    compiled = _compile(_source(raw_payload={"position_code": "3001", "minimum_degree": "学士"}))

    requirement = next(item for item in compiled.requirements if item.field == "other_conditions")
    assert requirement.operator == "manual_review"
    assert "minimum_degree" in requirement.source_text
    assert "raw_fields_require_confirmation" in compiled.warnings


def test_official_payload_compiles_degree_without_treating_audit_fields_as_unknown() -> None:
    compiled = _compile(_source(raw_payload=_official_payload()))

    degree = next(item for item in compiled.requirements if item.field == "degree")
    assert degree.operator == "one_of"
    assert degree.expected == ["学士", "硕士", "博士"]
    assert "raw_fields_require_confirmation" not in compiled.warnings
    assert all("未映射条件字段" not in item.source_text for item in compiled.requirements)


def test_official_service_project_labels_map_to_profile_facts() -> None:
    compiled = _compile(
        _source(
            raw_payload=_official_payload(
                serviceProject=(
                    "大学生村官、农村义务教育阶段学校教师特设岗位计划、"
                    "“三支一扶”计划、大学生志愿服务西部计划、"
                    "在军队服役5年（含）以上的高校毕业生退役士兵"
                )
            )
        )
    )

    service_project = next(
        item for item in compiled.requirements if item.field == "service_project"
    )
    assert service_project.operator == "one_of"
    assert service_project.expected == [
        "大学生村官",
        "特岗教师",
        "三支一扶",
        "西部计划",
        "高校毕业生退役士兵",
    ]


def test_official_education_variants_compile_to_canonical_profile_values() -> None:
    expected = {
        "仅限大专": ["大专"],
        "大专或本科": ["大专", "本科"],
        "本科或硕士研究生": ["本科", "硕士"],
        "仅限硕士研究生": ["硕士"],
        "仅限博士研究生": ["博士"],
    }

    for education, options in expected.items():
        compiled = _compile(
            _source(
                education_min=education,
                raw_payload=_official_payload(degree="无要求"),
            )
        )
        requirement = next(item for item in compiled.requirements if item.field == "education")
        assert requirement.expected == options


def test_exact_fresh_graduate_remark_is_deterministic() -> None:
    compiled = _compile(_source(remark="仅限应届高校毕业生"))

    requirement = next(item for item in compiled.requirements if item.field == "fresh_graduate")
    assert requirement.operator == "equals"
    assert requirement.expected is True
    assert compiled.status == "deterministic"


@pytest.mark.parametrize(
    ("remark", "gender"),
    [
        ("2026届高校毕业生，女性，在本单位最低服务年限为5年", "女"),
        ("应届高校毕业生，男性，本单位不提供宿舍，在本单位最低服务年限为5年", "男"),
    ],
)
def test_common_compound_remark_compiles_without_a_manual_gate(
    remark: str,
    gender: str,
) -> None:
    compiled = _compile(_source(remark=remark))

    by_field = {item.field: item for item in compiled.requirements}
    assert by_field["fresh_graduate"].expected is True
    assert by_field["gender"].expected == gender
    assert "other_conditions" not in by_field
    assert compiled.status == "deterministic"
    assert "remark_requires_confirmation" not in compiled.warnings


def test_non_blocking_job_disclosures_do_not_create_qualification_gates() -> None:
    compiled = _compile(_source(remark="本单位不提供宿舍，在本单位最低服务年限为5年"))

    assert all(item.field != "other_conditions" for item in compiled.requirements)
    assert compiled.status == "deterministic"


def test_structured_remark_keeps_only_the_unresolved_condition_manual() -> None:
    compiled = _compile(
        _source(
            remark=("2026届高校毕业生，女性，在本单位最低服务年限为5年，须持有注册会计师资格证书")
        )
    )

    by_field = {item.field: item for item in compiled.requirements}
    assert by_field["fresh_graduate"].expected is True
    assert by_field["gender"].expected == "女"
    assert by_field["other_conditions"].operator == "manual_review"
    assert by_field["other_conditions"].source_text == "须持有注册会计师资格证书"
    assert compiled.status == "needs_confirmation"


@pytest.mark.parametrize("year", [2025, 2026, 2027])
def test_fresh_graduate_remark_accepts_any_four_digit_recruitment_year(year: int) -> None:
    compiled = _compile(_source(remark=f"{year}届高校毕业生"))

    requirement = next(item for item in compiled.requirements if item.field == "fresh_graduate")
    assert requirement.expected is True
    assert compiled.status == "deterministic"


def test_common_certificate_requirements_compile_to_profile_tokens() -> None:
    english = _compile(
        _source(
            remark=(
                "大学英语四级60分（100分制）或425分（710分制）及以上"
                "（雅思成绩在6.5分以上或托福成绩在90分以上符合）"
            )
        )
    )
    legal = _compile(_source(remark="须持有法律职业资格证书A证"))

    english_requirement = next(
        item for item in english.requirements if item.field == "certifications"
    )
    assert english_requirement.operator == "contains_any"
    assert english_requirement.expected == [
        "大学英语四级425分及以上",
        "雅思6.5分及以上",
        "托福90分及以上",
    ]
    assert english.status == "deterministic"
    legal_requirement = next(item for item in legal.requirements if item.field == "certifications")
    assert legal_requirement.expected == ["法律职业资格证书A证"]
    assert legal.status == "deterministic"


def test_fingerprint_and_requirement_ids_are_stable_but_change_with_source() -> None:
    first = _compile(_source())
    repeated = _compile(_source())
    changed = _compile(_source(major_required="法学"))

    assert repeated.source_fingerprint == first.source_fingerprint
    assert [item.requirement_id for item in repeated.requirements] == [
        item.requirement_id for item in first.requirements
    ]
    assert changed.source_fingerprint != first.source_fingerprint


def test_incomplete_source_release_can_never_compile_as_eligible() -> None:
    source = _source()
    complete = compile_position_rules(
        source,
        rule_set_version="guokao-2026-v2",
        position_data_version="guokao-2026-fixture",
        source_conditions_complete=True,
    )
    incomplete = compile_position_rules(
        source,
        rule_set_version="guokao-2026-v2",
        position_data_version="guokao-2026-fixture",
        source_conditions_complete=False,
    )

    assert incomplete.status == "needs_confirmation"
    assert "source_conditions_incomplete" in incomplete.warnings
    assert incomplete.source_fingerprint != complete.source_fingerprint

    result = evaluate_eligibility(
        facts=[_fact("education", "本科"), _fact("major", "法学")],
        requirements=list(incomplete.requirements),
        rule_set_version="guokao-2026-v2",
        position_data_version="guokao-2026-fixture",
        evaluated_at=NOW,
        requirements_authoritative=True,
        source_conditions_complete=False,
    )
    assert result.status == "needs_confirmation"
    source_check = next(
        check for check in result.checks if check.reason_code == "source_conditions_incomplete"
    )
    assert source_check.requirement_id == "source-conditions-incomplete"


def test_authoritative_no_condition_compilation_can_be_eligible() -> None:
    compiled = _compile(
        _source(
            education_min="不限",
            major_required="不限",
            political_req="不限",
            service_min_yrs=0,
            hukou_req="不限",
            remark="无特殊要求",
        )
    )
    assert compiled.status == "no_conditions"
    assert compiled.requirements == ()

    result = evaluate_eligibility(
        facts=[],
        requirements=[],
        rule_set_version="guokao-2026-v1",
        position_data_version="guokao-2026-fixture",
        evaluated_at=NOW,
        requirements_authoritative=True,
    )
    assert result.status == "eligible"


def test_compiled_rules_replay_to_fact_evidence() -> None:
    compiled = _compile(_source())
    result = evaluate_eligibility(
        facts=[_fact("education", "本科"), _fact("major", "法学")],
        requirements=list(compiled.requirements),
        rule_set_version="guokao-2026-v1",
        position_data_version="guokao-2026-fixture",
        evaluated_at=NOW,
        requirements_authoritative=True,
    )
    assert result.status == "eligible"
    assert all(check.fact_id for check in result.checks)
