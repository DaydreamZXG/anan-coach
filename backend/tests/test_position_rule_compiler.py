from __future__ import annotations

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


def _compile(source: RawPositionConditions):
    return compile_position_rules(
        source,
        rule_set_version="guokao-2026-v1",
        position_data_version="guokao-2026-fixture",
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
    compiled = _compile(
        _source(major_required="法学类", hukou_req="限江西省户籍或生源")
    )

    assert compiled.status == "needs_confirmation"
    by_field = {item.field: item for item in compiled.requirements}
    assert by_field["major"].operator == "manual_review"
    assert by_field["hukou"].operator == "manual_review"
    assert compiled.manual_review_count == 2
    assert "major_requires_catalog_confirmation" in compiled.warnings


def test_unrecognized_remark_creates_a_blocking_other_condition() -> None:
    compiled = _compile(_source(remark="须持有法律职业资格证书A证"))

    requirement = next(
        item for item in compiled.requirements if item.field == "other_conditions"
    )
    assert requirement.operator == "manual_review"
    assert requirement.source_text == "须持有法律职业资格证书A证"
    assert compiled.status == "needs_confirmation"


def test_unmapped_raw_fields_create_a_blocking_audit_rule() -> None:
    compiled = _compile(
        _source(raw_payload={"position_code": "3001", "minimum_degree": "学士"})
    )

    requirement = next(
        item for item in compiled.requirements if item.field == "other_conditions"
    )
    assert requirement.operator == "manual_review"
    assert "minimum_degree" in requirement.source_text
    assert "raw_fields_require_confirmation" in compiled.warnings


def test_official_payload_compiles_degree_without_treating_audit_fields_as_unknown() -> None:
    compiled = _compile(_source(raw_payload=_official_payload()))

    degree = next(item for item in compiled.requirements if item.field == "degree")
    assert degree.operator == "one_of"
    assert degree.expected == ["学士", "硕士", "博士"]
    assert "raw_fields_require_confirmation" not in compiled.warnings
    assert all(
        "未映射条件字段" not in item.source_text
        for item in compiled.requirements
    )


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
        requirement = next(
            item for item in compiled.requirements if item.field == "education"
        )
        assert requirement.expected == options


def test_exact_fresh_graduate_remark_is_deterministic() -> None:
    compiled = _compile(_source(remark="仅限应届高校毕业生"))

    requirement = next(
        item for item in compiled.requirements if item.field == "fresh_graduate"
    )
    assert requirement.operator == "equals"
    assert requirement.expected is True
    assert compiled.status == "deterministic"


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
        check
        for check in result.checks
        if check.reason_code == "source_conditions_incomplete"
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
