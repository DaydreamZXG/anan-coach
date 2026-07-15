from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from app.domains.qualification.schemas import PositionRequirement


COMPILER_VERSION = "guokao-position-rules-v1"
UNRESTRICTED = {"", "不限", "无", "无要求", "不限制", "专业不限", "无特殊要求"}
EDUCATION_OPTIONS = {
    "大专及以上": ["大专", "本科", "硕士", "博士"],
    "专科及以上": ["大专", "本科", "硕士", "博士"],
    "本科及以上": ["本科", "硕士", "博士"],
    "大学本科及以上": ["本科", "硕士", "博士"],
    "硕士研究生及以上": ["硕士", "博士"],
    "研究生及以上": ["硕士", "博士"],
    "大专": ["大专"],
    "专科": ["大专"],
    "本科": ["本科"],
    "大学本科": ["本科"],
    "仅限本科": ["本科"],
    "硕士研究生": ["硕士"],
    "博士研究生": ["博士"],
}
KNOWN_POLITICAL = {"中共党员", "中共预备党员", "共青团员", "群众", "民主党派"}
FRESH_GRADUATE_REMARKS = {
    "仅限应届高校毕业生",
    "限应届高校毕业生",
    "仅限应届毕业生",
    "限应届毕业生",
}


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
) -> CompiledPositionRules:
    requirements: list[PositionRequirement] = []
    warnings: list[str] = []

    _compile_education(source, requirements, warnings, rule_set_version, position_data_version)
    _compile_major(source, requirements, warnings, rule_set_version, position_data_version)
    _compile_political(source, requirements, warnings, rule_set_version, position_data_version)
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

    manual_count = sum(item.operator == "manual_review" for item in requirements)
    if not requirements:
        status: CompilationStatus = "no_conditions"
    elif manual_count:
        status = "needs_confirmation"
    else:
        status = "deterministic"
    return CompiledPositionRules(
        requirements=tuple(requirements),
        status=status,
        warnings=tuple(dict.fromkeys(warnings)),
        source_fingerprint=_fingerprint(source),
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
) -> None:
    value = _normalize(source.major_required)
    if _unrestricted(value):
        return
    risky = ("类", "相关", "相近", "目录", "代码", "以", "详见", "等专业")
    parts = _split_options(value)
    if not parts or any(term in value for term in risky):
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
    identity = (
        f"{source.position_id}:{rule_set_version}:{position_data_version}:{field}"
    )
    return PositionRequirement.model_validate(
        {
            "requirementId": str(uuid5(NAMESPACE_URL, identity)),
            "field": field,
            "operator": operator,
            "expected": expected,
            "hardCondition": True,
            "sourceText": source_text,
            "sourceReference": f"product.positions:{source.position_id}:{source_field}",
        }
    )


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").strip("；;，,")


def _unrestricted(value: str | None) -> bool:
    return _normalize(value) in UNRESTRICTED


def _split_options(value: str) -> list[str]:
    normalized = re.sub(r"(?:或者|或)", "、", value)
    return [part for part in re.split(r"[、,，;/；]", normalized) if part]


def _fingerprint(source: RawPositionConditions) -> str:
    payload = {
        "positionId": source.position_id,
        "educationMin": source.education_min,
        "majorRequired": source.major_required,
        "politicalReq": source.political_req,
        "serviceMinYrs": source.service_min_yrs,
        "hukouReq": source.hukou_req,
        "remark": source.remark,
        "rawPayload": source.raw_payload,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()
