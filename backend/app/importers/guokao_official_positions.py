from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from openpyxl import load_workbook
import xlrd

from app.domains.positions.source_contract import (
    OFFICIAL_POSITION_CONTRACT,
    OFFICIAL_POSITION_FIELD_NAMES,
)

OFFICIAL_HEADERS = (
    "部门代码",
    "部门名称",
    "用人司局",
    "机构性质",
    "招考职位",
    "职位属性",
    "职位分布",
    "职位简介",
    "职位代码",
    "机构层级",
    "考试类别",
    "招考人数",
    "专业",
    "学历",
    "学位",
    "政治面貌",
    "基层工作最低年限",
    "服务基层项目工作经历",
    "是否在面试阶段组织专业能力测试",
    "面试人员比例",
    "工作地点",
    "落户地点",
    "备注",
    "部门网站",
    "咨询电话1",
    "咨询电话2",
    "咨询电话3",
)


class GuokaoOfficialPositionError(ValueError):
    pass


@dataclass(frozen=True)
class OfficialPositionRow:
    source_id: str
    exam_year: int
    position_code: str
    name: str
    department_full: str
    headcount: int
    education_min: str | None
    major_required: str | None
    political_req: str | None
    service_min_yrs: int | None
    remark: str | None
    raw_json: str
    source_sheet: str
    source_row: int


@dataclass(frozen=True)
class OfficialPositionCatalog:
    exam_year: int
    position_data_version: str
    source_sha256: str
    rows: tuple[OfficialPositionRow, ...]


def load_official_position_catalog(
    source_path: Path,
    *,
    exam_year: int,
    position_data_version: str,
) -> OfficialPositionCatalog:
    source = source_path.expanduser().resolve()
    if not source.exists():
        raise GuokaoOfficialPositionError(
            f"official position workbook not found: {source}"
        )
    if exam_year < 2000 or exam_year > 2100:
        raise GuokaoOfficialPositionError("exam_year must be a four-digit year")
    version = position_data_version.strip()
    if not version:
        raise GuokaoOfficialPositionError("position_data_version is required")
    source_fingerprint = sha256(source.read_bytes()).hexdigest()
    rows: list[OfficialPositionRow] = []
    sequence = 0
    for sheet_name, sheet_rows in _iter_workbook_sheets(source):
        iterator = iter(sheet_rows)
        notice = next(iterator, None)
        header_values = next(iterator, None)
        if notice is None or header_values is None:
            raise GuokaoOfficialPositionError(
                f"official sheet {sheet_name!r} does not contain two header rows"
            )
        headers = tuple(_cell_text(value) for value in header_values)
        if headers != OFFICIAL_HEADERS:
            raise GuokaoOfficialPositionError(
                f"official sheet {sheet_name!r} headers do not match the 2026 contract"
            )
        for source_row, values in enumerate(iterator, start=3):
            normalized = tuple(_cell_text(value) for value in values)
            if not any(normalized):
                continue
            if len(normalized) != len(OFFICIAL_HEADERS):
                raise GuokaoOfficialPositionError(
                    f"official sheet {sheet_name!r} row {source_row} has an invalid width"
                )
            fields = dict(zip(OFFICIAL_POSITION_FIELD_NAMES, normalized, strict=True))
            position_code = fields["positionCode"]
            if not position_code:
                raise GuokaoOfficialPositionError(
                    f"official sheet {sheet_name!r} row {source_row} has no position code"
                )
            sequence += 1
            source_id = f"gk-{exam_year}-{sequence:05d}"
            department_name = fields["departmentName"]
            agency_name = fields["agencyName"]
            department_full = (
                f"{department_name}{agency_name}"
                if agency_name and agency_name != department_name
                else department_name
            )
            headcount = _positive_integer(
                fields["headcount"],
                sheet_name=sheet_name,
                source_row=source_row,
            )
            official_payload = {
                "contractVersion": OFFICIAL_POSITION_CONTRACT,
                "positionDataVersion": version,
                "source": {
                    "fileName": source.name,
                    "sha256": source_fingerprint,
                    "sheetName": sheet_name,
                    "rowNumber": source_row,
                },
                "officialFields": {
                    **fields,
                    "headcount": headcount,
                },
            }
            rows.append(
                OfficialPositionRow(
                    source_id=source_id,
                    exam_year=exam_year,
                    position_code=position_code,
                    name=fields["positionName"],
                    department_full=department_full,
                    headcount=headcount,
                    education_min=_optional_text(fields["education"]),
                    major_required=_optional_text(fields["major"]),
                    political_req=_optional_text(fields["politicalStatus"]),
                    service_min_yrs=_parse_minimum_years(
                        fields["grassrootsMinimumYears"]
                    ),
                    remark=_optional_text(fields["remark"]),
                    raw_json=json.dumps(
                        official_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    source_sheet=sheet_name,
                    source_row=source_row,
                )
            )
    if not rows:
        raise GuokaoOfficialPositionError("official workbook contains no positions")
    return OfficialPositionCatalog(
        exam_year=exam_year,
        position_data_version=version,
        source_sha256=source_fingerprint,
        rows=tuple(rows),
    )


def merge_official_position_catalog(
    source_rows: Sequence[dict[str, Any]],
    *,
    catalog: OfficialPositionCatalog,
) -> list[dict[str, Any]]:
    by_source_id = {
        str(row["source_id"]): row
        for row in source_rows
        if int(row["year"]) == catalog.exam_year
    }
    if len(by_source_id) != len(catalog.rows):
        raise GuokaoOfficialPositionError(
            "official workbook and position database contain different position counts "
            f"for {catalog.exam_year}: {len(catalog.rows)} != {len(by_source_id)}"
        )
    mismatches: list[str] = []
    official_by_source_id = {row.source_id: row for row in catalog.rows}
    if set(by_source_id) != set(official_by_source_id):
        missing = sorted(set(official_by_source_id) - set(by_source_id))[:3]
        unexpected = sorted(set(by_source_id) - set(official_by_source_id))[:3]
        raise GuokaoOfficialPositionError(
            "official workbook and position database source identities differ; "
            f"missing={missing}, unexpected={unexpected}"
        )
    for source_id, official in official_by_source_id.items():
        existing = by_source_id[source_id]
        comparisons = {
            "position_code": (str(existing["position_code"]), official.position_code),
            "name": (str(existing["name"]), official.name),
            "department_full": (
                str(existing["department_full"]),
                official.department_full,
            ),
            "headcount": (int(existing["headcount"]), official.headcount),
        }
        for field, (actual, expected) in comparisons.items():
            if actual != expected:
                mismatches.append(
                    f"{source_id}.{field}: {actual!r} != {expected!r}"
                )
                if len(mismatches) >= 5:
                    break
        if len(mismatches) >= 5:
            break
    if mismatches:
        raise GuokaoOfficialPositionError(
            "official workbook does not match the imported position identity: "
            + "; ".join(mismatches)
        )

    merged: list[dict[str, Any]] = []
    for row in source_rows:
        official = official_by_source_id.get(str(row["source_id"]))
        if official is None:
            merged.append(dict(row))
            continue
        merged.append(
            {
                **row,
                "education_min": official.education_min,
                "major_required": official.major_required,
                "political_req": official.political_req,
                "service_min_yrs": official.service_min_yrs,
                "remark": official.remark,
                "raw_json": official.raw_json,
            }
        )
    return merged


def _iter_workbook_sheets(
    source: Path,
) -> Iterator[tuple[str, Iterable[Sequence[Any]]]]:
    suffix = source.suffix.casefold()
    if suffix == ".xlsx":
        workbook = load_workbook(source, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                yield sheet.title, sheet.iter_rows(values_only=True)
        finally:
            workbook.close()
        return
    if suffix == ".xls":
        workbook = xlrd.open_workbook(source, on_demand=True)
        try:
            for sheet in workbook.sheets():
                yield sheet.name, (
                    tuple(sheet.cell_value(row_index, column_index) for column_index in range(sheet.ncols))
                    for row_index in range(sheet.nrows)
                )
        finally:
            workbook.release_resources()
        return
    raise GuokaoOfficialPositionError(
        "official position workbook must be an .xls or .xlsx file"
    )


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\r\n?", "\n", str(value)).strip()


def _optional_text(value: str) -> str | None:
    return value or None


def _positive_integer(value: str, *, sheet_name: str, source_row: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise GuokaoOfficialPositionError(
            f"official sheet {sheet_name!r} row {source_row} has invalid headcount"
        ) from exc
    if parsed <= 0:
        raise GuokaoOfficialPositionError(
            f"official sheet {sheet_name!r} row {source_row} has invalid headcount"
        )
    return parsed


def _parse_minimum_years(value: str) -> int | None:
    normalized = re.sub(r"\s+", "", value)
    if normalized in {"", "不限", "无限制", "无要求", "不限制"}:
        return None
    match = re.search(r"(\d+)年", normalized)
    if match:
        return int(match.group(1))
    chinese_years = {
        "一年": 1,
        "二年": 2,
        "三年": 3,
        "四年": 4,
        "五年": 5,
    }
    for label, years in chinese_years.items():
        if label in normalized:
            return years
    raise GuokaoOfficialPositionError(
        f"unsupported grassroots minimum years: {value!r}"
    )
