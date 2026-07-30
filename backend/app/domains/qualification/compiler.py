from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from app.domains.positions.source_contract import (
    OFFICIAL_POSITION_CONTRACT,
    OFFICIAL_POSITION_FIELD_NAMES,
)
from app.domains.qualification.schemas import PositionRequirement


COMPILER_VERSION = "guokao-position-rules-v3"
SOURCE_CONDITIONS_INCOMPLETE_WARNING = "source_conditions_incomplete"
UNRESTRICTED = {"", "不限", "无", "无要求", "不限制", "专业不限", "无特殊要求"}
EDUCATION_OPTIONS = {
    "大专及以上": ["大专", "本科", "硕士", "博士"],
    "专科及以上": ["大专", "本科", "硕士", "博士"],
    "本科及以上": ["本科", "硕士", "博士"],
    "大学本科及以上": ["本科", "硕士", "博士"],
    "硕士研究生及以上": ["硕士", "博士"],
    "研究生及以上": ["硕士", "博士"],
    "本科或硕士研究生": ["本科", "硕士"],
    "大专或本科": ["大专", "本科"],
    "大专": ["大专"],
    "专科": ["大专"],
    "仅限大专": ["大专"],
    "本科": ["本科"],
    "大学本科": ["本科"],
    "仅限本科": ["本科"],
    "硕士研究生": ["硕士"],
    "仅限硕士研究生": ["硕士"],
    "博士研究生": ["博士"],
    "仅限博士研究生": ["博士"],
}
KNOWN_POLITICAL = {"中共党员", "中共预备党员", "共青团员", "群众", "民主党派"}
FRESH_GRADUATE_REMARKS = {
    "仅限应届高校毕业生",
    "限应届高校毕业生",
    "仅限应届毕业生",
    "限应届毕业生",
}
SERVICE_PROJECT_LABELS = {
    "大学生村官": "大学生村官",
    "农村义务教育阶段学校教师特设岗位计划": "特岗教师",
    "“三支一扶”计划": "三支一扶",
    '"三支一扶"计划': "三支一扶",
    "三支一扶计划": "三支一扶",
    "大学生志愿服务西部计划": "西部计划",
    "在军队服役5年（含）以上的高校毕业生退役士兵": "高校毕业生退役士兵",
}
MAJOR_COMPLEX_MARKERS = (
    "相关专业",
    "相近专业",
    "专业目录",
    "学科目录",
    "一级学科",
    "二级学科",
    "专业代码",
    "所学专业",
    "专业为",
    "专业要求",
    "详见",
    "及以上",
    "以上专业",
    "以下专业",
    "除",
    "不含",
    "包含",
    "含",
    "包括",
    "方向",
    "须",
    "取得",
    "通过",
    "资格",
    "且",
    "和",
    "及",
    "并且",
    "以及",
)
MAJOR_EXACT_TOKEN = re.compile(r"[\u3400-\u9fff·]+")
MAJOR_CATEGORY = re.compile(r"(?:专业|学科|门)?类(?:[（(]|$)")


@dataclass(frozen=True)
class RawPositionConditions:
    position_id: str
    education_min: str | None
    major_required: str | None
    political_req: str | None
    service_min_yrs: int | None
    hukou_req: str | None
    remark: str | None
    raw_payload: object | None = None


CompilationStatus = Literal["deterministic", "needs_confirmation", "no_conditions"]


@dataclass(frozen=True)
class CompiledPositionRules:
    requirements: tuple[PositionRequirement, ...]
    status: CompilationStatus
    warnings: tuple[str, ...]
    source_fingerprint: str

    @property
    def manual_review_count(self) -> int:
        return sum(item.operator == "manual_review" for item in self.requirements)


def compile_position_rules(
    source: RawPositionConditions,
    *,
    rule_set_version: str,
    position_data_version: str,
    source_conditions_complete: bool = True,
    major_catalog_names: frozenset[str] | None = None,
    major_catalog_version: str | None = None,
) -> CompiledPositionRules:
    requirements: list[PositionRequirement] = []
    warnings: list[str] = []
    normalized_major_catalog = (
        frozenset(normalized for name in major_catalog_names if (normalized := _normalize(name)))
        if major_catalog_names
        else None
    )
    major_catalog_fingerprint = _major_catalog_fingerprint(
        names=normalized_major_catalog,
        version=major_catalog_version,
    )
    if major_catalog_fingerprint is None:
        normalized_major_catalog = None

    _compile_education(source, requirements, warnings, rule_set_version, position_data_version)
    _compile_major(
        source,
        requirements,
        warnings,
        rule_set_version,
        position_data_version,
        major_catalog_names=normalized_major_catalog,
    )
    _compile_political(source, requirements, warnings, rule_set_version, position_data_version)
    _compile_official_payload(
        source,
        requirements,
        warnings,
        rule_set_version,
        position_data_version,
    )
    if source.service_min_yrs and source.service_min_yrs > 0:
        requirements.append(
            _requirement(
                source,
                field="grassroots_years",
                operator="at_least",
                expected=source.service_min_yrs,
                source_text=f"基层工作最低年限：{source.service_min_yrs} 年",
                source_field="service_min_yrs",
                rule_set_version=rule_set_version,
                position_data_version=position_data_version,
            )
        )
    if not _unrestricted(source.hukou_req):
        requirements.append(
            _manual_requirement(
                source,
                field="hukou",
                source_text=source.hukou_req or "",
                source_field="hukou_req",
                rule_set_version=rule_set_version,
                position_data_version=position_data_version,
            )
        )
        warnings.append("hukou_requires_confirmation")
    _compile_remark(source, requirements, warnings, rule_set_version, position_data_version)
    _compile_unmapped_raw_fields(
        source,
        requirements,
        warnings,
        rule_set_version,
        position_data_version,
    )
    if not source_conditions_complete:
        warnings.append(SOURCE_CONDITIONS_INCOMPLETE_WARNING)

    manual_count = sum(item.operator == "manual_review" for item in requirements)
    status: CompilationStatus
    if not source_conditions_complete:
        status = "needs_confirmation"
    elif not requirements:
        status = "no_conditions"
    elif manual_count:
        status = "needs_confirmation"
    else:
        status = "deterministic"
    return CompiledPositionRules(
        requirements=tuple(requirements),
        status=status,
        warnings=tuple(dict.fromkeys(warnings)),
        source_fingerprint=_fingerprint(
            source,
            source_conditions_complete=source_conditions_complete,
            major_catalog_version=major_catalog_version,
            major_catalog_fingerprint=major_catalog_fingerprint,
        ),
    )


def _compile_education(
    source: RawPositionConditions,
    requirements: list[PositionRequirement],
    warnings: list[str],
    rule_set_version: str,
    position_data_version: str,
) -> None:
    value = _normalize(source.education_min)
    if _unrestricted(value):
        return
    options = EDUCATION_OPTIONS.get(value)
    if options is None:
        requirements.append(
            _manual_requirement(
                source,
                field="education",
                source_text=value,
                source_field="education_min",
                rule_set_version=rule_set_version,
                position_data_version=position_data_version,
            )
        )
        warnings.append("education_text_unrecognized")
        return
    requirements.append(
        _requirement(
            source,
            field="education",
            operator="one_of",
            expected=options,
            source_text=value,
            source_field="education_min",
            rule_set_version=rule_set_version,
            position_data_version=position_data_version,
        )
    )


def _compile_major(
    source: RawPositionConditions,
    requirements: list[PositionRequirement],
    warnings: list[str],
    rule_set_version: str,
    position_data_version: str,
    *,
    major_catalog_names: frozenset[str] | None,
) -> None:
    value = _normalize(source.major_required)
    if _unrestricted(value):
        return
    parts = _parse_exact_major_options(
        value,
        major_catalog_names=major_catalog_names,
    )
    if parts is None:
        requirements.append(
            _manual_requirement(
                source,
                field="major",
                source_text=value,
                source_field="major_required",
                rule_set_version=rule_set_version,
                position_data_version=position_data_version,
            )
        )
        warnings.append("major_requires_catalog_confirmation")
        if major_catalog_names is None:
            warnings.append("major_catalog_unavailable")
        return
    requirements.append(
        _requirement(
            source,
            field="major",
            operator="contains_any",
            expected=parts,
            source_text=value,
            source_field="major_required",
            rule_set_version=rule_set_version,
            position_data_version=position_data_version,
        )
    )


def _parse_exact_major_options(
    value: str,
    *,
    major_catalog_names: frozenset[str] | None,
) -> list[str] | None:
    """Return only exact major names whose OR semantics are mechanically safe.

    Official category, catalogue, direction and exclusion expressions remain
    manual-review requirements.  A terminal ``专业`` and the explicit wrappers
    ``限``/``仅限`` are accepted as lexical aliases rather than category guesses.
    """

    # Without one immutable catalogue snapshot, even a plain Chinese token
    # cannot be proven to be a major rather than an unrelated condition.
    if not major_catalog_names:
        return None
    if any(char in value for char in "（）()") or re.search(r"\d", value):
        return None
    if any(marker in value for marker in MAJOR_COMPLEX_MARKERS):
        return None
    if MAJOR_CATEGORY.search(value) or re.search(r"(?:等|等专业)$", value):
        return None

    raw_parts = _split_options(value)
    if not raw_parts:
        return None

    options: list[str] = []
    for raw_part in raw_parts:
        part = raw_part
        if part.startswith("仅限"):
            part = part[2:]
        elif part.startswith("限"):
            part = part[1:]
        if not part or MAJOR_EXACT_TOKEN.fullmatch(part) is None:
            return None

        if part in major_catalog_names:
            canonical = part
        elif part.endswith("专业") and len(part) > 2 and part[:-2] in major_catalog_names:
            canonical = part[:-2]
        else:
            return None
        if MAJOR_CATEGORY.search(canonical):
            return None
        options.append(canonical)
        if canonical != part:
            options.append(part)
    return list(dict.fromkeys(options))


def _compile_political(
    source: RawPositionConditions,
    requirements: list[PositionRequirement],
    warnings: list[str],
    rule_set_version: str,
    position_data_version: str,
) -> None:
    value = _normalize(source.political_req)
    if _unrestricted(value):
        return
    parts = _split_options(value)
    if not parts or not set(parts).issubset(KNOWN_POLITICAL):
        requirements.append(
            _manual_requirement(
                source,
                field="political_status",
                source_text=value,
                source_field="political_req",
                rule_set_version=rule_set_version,
                position_data_version=position_data_version,
            )
        )
        warnings.append("political_status_text_unrecognized")
        return
    requirements.append(
        _requirement(
            source,
            field="political_status",
            operator="one_of",
            expected=parts,
            source_text=value,
            source_field="political_req",
            rule_set_version=rule_set_version,
            position_data_version=position_data_version,
        )
    )


def _compile_remark(
    source: RawPositionConditions,
    requirements: list[PositionRequirement],
    warnings: list[str],
    rule_set_version: str,
    position_data_version: str,
) -> None:
    value = _normalize(source.remark)
    if _unrestricted(value):
        return
    if value in FRESH_GRADUATE_REMARKS:
        requirements.append(
            _requirement(
                source,
                field="fresh_graduate",
                operator="equals",
                expected=True,
                source_text=value,
                source_field="remark",
                rule_set_version=rule_set_version,
                position_data_version=position_data_version,
            )
        )
        return
    requirements.append(
        _manual_requirement(
            source,
            field="other_conditions",
            source_text=value,
            source_field="remark",
            rule_set_version=rule_set_version,
            position_data_version=position_data_version,
        )
    )
    warnings.append("remark_requires_confirmation")


def _compile_unmapped_raw_fields(
    source: RawPositionConditions,
    requirements: list[PositionRequirement],
    warnings: list[str],
    rule_set_version: str,
    position_data_version: str,
) -> None:
    if source.raw_payload in (None, {}, ""):
        return
    if _official_fields(source.raw_payload) is not None:
        return
    known_keys = {
        "id",
        "source_id",
        "year",
        "position_code",
        "name",
        "department_full",
        "system_code",
        "region",
        "city",
        "paper_type",
        "headcount",
        "education_min",
        "major_required",
        "political_req",
        "hukou_req",
        "service_min_yrs",
        "remark",
    }
    if isinstance(source.raw_payload, dict):
        unknown = sorted(
            str(key)
            for key, value in source.raw_payload.items()
            if key not in known_keys and value not in (None, "", [], {})
        )
    else:
        unknown = ["unstructured_payload"]
    if not unknown:
        return
    if not any(item.field == "other_conditions" for item in requirements):
        requirements.append(
            _manual_requirement(
                source,
                field="other_conditions",
                source_text=f"原始岗位数据含未映射条件字段：{', '.join(unknown)}",
                source_field="raw_json",
                rule_set_version=rule_set_version,
                position_data_version=position_data_version,
            )
        )
    warnings.append("raw_fields_require_confirmation")


def _compile_official_payload(
    source: RawPositionConditions,
    requirements: list[PositionRequirement],
    warnings: list[str],
    rule_set_version: str,
    position_data_version: str,
) -> None:
    fields = _official_fields(source.raw_payload)
    if fields is None:
        return
    _compile_degree(
        source,
        fields,
        requirements,
        warnings,
        rule_set_version,
        position_data_version,
    )
    _compile_service_project(
        source,
        fields,
        requirements,
        warnings,
        rule_set_version,
        position_data_version,
    )


def _official_fields(raw_payload: object | None) -> dict[str, object] | None:
    if not isinstance(raw_payload, dict):
        return None
    if raw_payload.get("contractVersion") != OFFICIAL_POSITION_CONTRACT:
        return None
    fields = raw_payload.get("officialFields")
    source = raw_payload.get("source")
    position_data_version = raw_payload.get("positionDataVersion")
    if (
        not isinstance(fields, dict)
        or not isinstance(source, dict)
        or not isinstance(position_data_version, str)
        or not position_data_version.strip()
    ):
        return None
    if set(fields) != set(OFFICIAL_POSITION_FIELD_NAMES):
        return None
    return fields


def _compile_degree(
    source: RawPositionConditions,
    fields: dict[str, object],
    requirements: list[PositionRequirement],
    warnings: list[str],
    rule_set_version: str,
    position_data_version: str,
) -> None:
    degree = _normalize(_string_field(fields, "degree"))
    if _unrestricted(degree):
        return
    expected: list[str] | None
    if degree in {"学士", "硕士", "博士"}:
        expected = [degree]
    elif degree == "与最高学历相对应的学位":
        education = _normalize(source.education_min)
        education_options = EDUCATION_OPTIONS.get(education)
        expected = (
            list(
                dict.fromkeys(
                    mapped
                    for option in education_options or []
                    if (mapped := {"本科": "学士", "硕士": "硕士", "博士": "博士"}.get(option))
                )
            )
            or None
        )
    else:
        expected = None
    if expected is None:
        requirements.append(
            _manual_requirement(
                source,
                field="degree",
                source_text=degree,
                source_field="raw_json.officialFields.degree",
                rule_set_version=rule_set_version,
                position_data_version=position_data_version,
            )
        )
        warnings.append("degree_requires_confirmation")
        return
    requirements.append(
        _requirement(
            source,
            field="degree",
            operator="one_of",
            expected=expected,
            source_text=degree,
            source_field="raw_json.officialFields.degree",
            rule_set_version=rule_set_version,
            position_data_version=position_data_version,
        )
    )


def _compile_service_project(
    source: RawPositionConditions,
    fields: dict[str, object],
    requirements: list[PositionRequirement],
    warnings: list[str],
    rule_set_version: str,
    position_data_version: str,
) -> None:
    value = _normalize(_string_field(fields, "serviceProject"))
    if _unrestricted(value) or value == "无限制":
        return
    normalized = value.replace("，", "、").replace(",", "、")
    official_labels = [part for part in normalized.split("、") if part]
    expected: list[str] = []
    for label in official_labels:
        mapped = SERVICE_PROJECT_LABELS.get(label)
        if mapped is None:
            requirements.append(
                _manual_requirement(
                    source,
                    field="service_project",
                    source_text=value,
                    source_field="raw_json.officialFields.serviceProject",
                    rule_set_version=rule_set_version,
                    position_data_version=position_data_version,
                )
            )
            warnings.append("service_project_requires_confirmation")
            return
        expected.append(mapped)
    if not expected:
        return
    requirements.append(
        _requirement(
            source,
            field="service_project",
            operator="one_of",
            expected=list(dict.fromkeys(expected)),
            source_text=value,
            source_field="raw_json.officialFields.serviceProject",
            rule_set_version=rule_set_version,
            position_data_version=position_data_version,
        )
    )


def _string_field(fields: dict[str, object], key: str) -> str:
    value = fields.get(key)
    return value if isinstance(value, str) else ""


def _manual_requirement(
    source: RawPositionConditions,
    *,
    field: str,
    source_text: str,
    source_field: str,
    rule_set_version: str,
    position_data_version: str,
) -> PositionRequirement:
    return _requirement(
        source,
        field=field,
        operator="manual_review",
        expected=None,
        source_text=source_text,
        source_field=source_field,
        rule_set_version=rule_set_version,
        position_data_version=position_data_version,
    )


def _requirement(
    source: RawPositionConditions,
    *,
    field: str,
    operator: str,
    expected: object,
    source_text: str,
    source_field: str,
    rule_set_version: str,
    position_data_version: str,
) -> PositionRequirement:
    identity = f"{source.position_id}:{rule_set_version}:{position_data_version}:{field}"
    return PositionRequirement.model_validate(
        {
            "requirementId": str(uuid5(NAMESPACE_URL, identity)),
            "field": field,
            "operator": operator,
            "expected": expected,
            "hardCondition": True,
            "sourceText": source_text,
            "sourceReference": (
                "product.position_snapshots:"
                f"{position_data_version}:{source.position_id}:{source_field}"
            ),
        }
    )


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").strip("；;，,")


def _unrestricted(value: str | None) -> bool:
    return _normalize(value) in UNRESTRICTED


def _split_options(value: str) -> list[str]:
    normalized = re.sub(r"(?:或者|或)", "、", value)
    return [part for part in re.split(r"[、,，;/；]", normalized) if part]


def _major_catalog_fingerprint(
    *,
    names: frozenset[str] | None,
    version: str | None,
) -> str | None:
    normalized_version = (version or "").strip()
    if not names or not normalized_version:
        return None
    encoded = json.dumps(
        {
            "version": normalized_version,
            "names": sorted(names),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _fingerprint(
    source: RawPositionConditions,
    *,
    source_conditions_complete: bool,
    major_catalog_version: str | None,
    major_catalog_fingerprint: str | None,
) -> str:
    payload = {
        "positionId": source.position_id,
        "educationMin": source.education_min,
        "majorRequired": source.major_required,
        "politicalReq": source.political_req,
        "serviceMinYrs": source.service_min_yrs,
        "hukouReq": source.hukou_req,
        "remark": source.remark,
        "rawPayload": source.raw_payload,
        "sourceConditionsComplete": source_conditions_complete,
        "majorCatalogVersion": (
            major_catalog_version if major_catalog_fingerprint is not None else None
        ),
        "majorCatalogFingerprint": major_catalog_fingerprint,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()
