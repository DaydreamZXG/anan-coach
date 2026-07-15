from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import text
from sqlalchemy.orm import Session


class GuokaoEntryScoreImportError(ValueError):
    pass


DEFAULT_SOURCE_NAME = "gemu.cn mirror of 2024 central civil service interview list"
DEFAULT_SOURCE_URL = "https://www.gemu.cn/zhaokao/103448.html"


@dataclass(frozen=True)
class GuokaoEntryScoreRecord:
    year: int
    department_code: str
    position_code: str
    agency: str
    bureau: str
    position_title: str
    entry_score_min: Decimal
    candidate_count: int


@dataclass(frozen=True)
class GuokaoEntryScoreImportResult:
    evidence_rows: int
    linked_positions: int
    exact_links: int
    containment_links: int
    unlinked_evidence: int
    source_file_sha256: str


def import_guokao_entry_scores(
    *,
    session: Session,
    source_path: Path,
    year: int = 2024,
    source_name: str = DEFAULT_SOURCE_NAME,
    source_url: str | None = DEFAULT_SOURCE_URL,
    batch_size: int = 1000,
) -> GuokaoEntryScoreImportResult:
    if batch_size < 1:
        raise GuokaoEntryScoreImportError("batch_size must be positive")

    source = source_path.expanduser().resolve()
    if not source.exists():
        raise GuokaoEntryScoreImportError(f"entry score workbook not found: {source}")

    source_file_sha256 = _file_sha256(source)
    records = _read_workbook_records(source=source, year=year)
    _upsert_evidence(
        session=session,
        records=records,
        source_name=source_name,
        source_url=source_url,
        source_file_sha256=source_file_sha256,
        batch_size=batch_size,
    )
    _refresh_entry_score_links(
        session=session,
        year=year,
        source_file_sha256=source_file_sha256,
    )
    session.commit()
    verification = verify_guokao_entry_score_import(
        session,
        year=year,
        source_file_sha256=source_file_sha256,
    )
    return GuokaoEntryScoreImportResult(
        evidence_rows=len(records),
        linked_positions=verification["linked_positions"],
        exact_links=verification["link_counts_by_method"].get("exact_department_title_code", 0),
        containment_links=verification["link_counts_by_method"].get(
            "unique_department_containment_title_code",
            0,
        ),
        unlinked_evidence=verification["unlinked_evidence"],
        source_file_sha256=source_file_sha256,
    )


def verify_guokao_entry_score_import(
    session: Session,
    *,
    year: int = 2024,
    source_file_sha256: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"year": year}
    source_filter = ""
    if source_file_sha256:
        params["source_file_sha256"] = source_file_sha256
        source_filter = "and e.source_file_sha256 = :source_file_sha256"

    evidence_count = int(
        session.execute(
            text(
                f"""
                select count(*)
                from product.position_entry_score_evidence e
                where e.year = :year
                {source_filter}
                """
            ),
            params,
        ).scalar_one()
    )
    linked_positions = int(
        session.execute(
            text(
                f"""
                select count(distinct l.position_id)
                from product.position_entry_score_links l
                join product.position_entry_score_evidence e
                  on e.evidence_id = l.evidence_id
                where e.year = :year
                {source_filter}
                """
            ),
            params,
        ).scalar_one()
    )
    link_rows = session.execute(
        text(
            f"""
            select l.match_method, count(*) as count
            from product.position_entry_score_links l
            join product.position_entry_score_evidence e
              on e.evidence_id = l.evidence_id
            where e.year = :year
            {source_filter}
            group by l.match_method
            order by l.match_method
            """
        ),
        params,
    ).mappings().all()
    link_counts_by_method = {row["match_method"]: int(row["count"]) for row in link_rows}
    unlinked_evidence = int(
        session.execute(
            text(
                f"""
                select count(*)
                from product.position_entry_score_evidence e
                where e.year = :year
                  {source_filter}
                  and not exists (
                    select 1
                    from product.position_entry_score_links l
                    where l.evidence_id = e.evidence_id
                  )
                """
            ),
            params,
        ).scalar_one()
    )
    stale_links = int(
        session.execute(
            text(
                f"""
                select count(*)
                from product.position_entry_score_links l
                join product.position_entry_score_evidence e
                  on e.evidence_id = l.evidence_id
                join product.positions p
                  on p.position_id = l.position_id
                where e.year = :year
                  {source_filter}
                  and (
                    p.year <> e.year
                    or p.position_code <> e.position_code
                    or p.name <> e.position_title
                    or (
                      l.match_method = 'exact_department_title_code'
                      and p.department_full <> e.agency || e.bureau
                    )
                    or (
                      l.match_method = 'unique_department_containment_title_code'
                      and not (
                        regexp_replace(coalesce(p.department_full, ''), '\\s+', '', 'g')
                          like '%' || regexp_replace(e.agency || e.bureau, '\\s+', '', 'g') || '%'
                        or regexp_replace(e.agency || e.bureau, '\\s+', '', 'g')
                          like '%' || regexp_replace(coalesce(p.department_full, ''), '\\s+', '', 'g') || '%'
                      )
                    )
                  )
                """
            ),
            params,
        ).scalar_one()
    )
    coverage_rows = session.execute(
        text(
            """
            select
              p.year,
              count(*) as positions,
              count(distinct l.position_id) as positions_with_entry_score,
              round(100.0 * count(distinct l.position_id) / nullif(count(*), 0), 1) as pct
            from product.positions p
            left join product.position_entry_score_links l
              on l.position_id = p.position_id
            group by p.year
            order by p.year
            """
        )
    ).mappings().all()
    coverage_by_year = []
    for row in coverage_rows:
        item = dict(row)
        item["pct"] = float(item["pct"]) if item["pct"] is not None else None
        coverage_by_year.append(item)

    return {
        "year": year,
        "source_file_sha256": source_file_sha256,
        "evidence_count": evidence_count,
        "linked_positions": linked_positions,
        "link_counts_by_method": link_counts_by_method,
        "unlinked_evidence": unlinked_evidence,
        "stale_links": stale_links,
        "coverage_by_year": coverage_by_year,
        "ok": evidence_count > 0 and stale_links == 0,
    }


def _read_workbook_records(*, source: Path, year: int) -> list[GuokaoEntryScoreRecord]:
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration as exc:
            raise GuokaoEntryScoreImportError("entry score workbook is empty") from exc

        indexes = _header_indexes(header)
        records: dict[tuple[str, str], GuokaoEntryScoreRecord] = {}
        for row_number, row in enumerate(rows, start=2):
            if not row or all(value is None or str(value).strip() == "" for value in row):
                continue
            department_code = _normalize_code(
                _cell(row, indexes["部门代码"]),
                width=6,
                column="部门代码",
                row_number=row_number,
            )
            position_code = _normalize_code(
                _cell(row, indexes["职位代码"]),
                width=12,
                column="职位代码",
                row_number=row_number,
            )
            record = GuokaoEntryScoreRecord(
                year=year,
                department_code=department_code,
                position_code=position_code,
                agency=_normalize_required_text(
                    _cell(row, indexes["招录机关"]),
                    column="招录机关",
                    row_number=row_number,
                ),
                bureau=_normalize_required_text(
                    _cell(row, indexes["用人司局"]),
                    column="用人司局",
                    row_number=row_number,
                ),
                position_title=_normalize_required_text(
                    _cell(row, indexes["招考职位"]),
                    column="招考职位",
                    row_number=row_number,
                ),
                entry_score_min=_parse_score(
                    _cell(row, indexes["最低面试分数"]),
                    row_number=row_number,
                ),
                candidate_count=1,
            )
            key = (department_code, position_code)
            existing = records.get(key)
            if existing is None:
                records[key] = record
                continue
            comparable_existing = (
                existing.agency,
                existing.bureau,
                existing.position_title,
                existing.entry_score_min,
            )
            comparable_record = (
                record.agency,
                record.bureau,
                record.position_title,
                record.entry_score_min,
            )
            if comparable_existing != comparable_record:
                raise GuokaoEntryScoreImportError(
                    "conflicting entry score evidence for "
                    f"department_code={department_code}, position_code={position_code} "
                    f"at row {row_number}"
                )
            records[key] = GuokaoEntryScoreRecord(
                year=existing.year,
                department_code=existing.department_code,
                position_code=existing.position_code,
                agency=existing.agency,
                bureau=existing.bureau,
                position_title=existing.position_title,
                entry_score_min=existing.entry_score_min,
                candidate_count=existing.candidate_count + 1,
            )
    finally:
        workbook.close()

    if not records:
        raise GuokaoEntryScoreImportError("entry score workbook contains no usable rows")
    return [records[key] for key in sorted(records)]


def _header_indexes(header: tuple[Any, ...]) -> dict[str, int]:
    names = {str(value).strip(): index for index, value in enumerate(header) if value is not None}
    required = ("招录机关", "部门代码", "用人司局", "招考职位", "职位代码", "最低面试分数")
    missing = [name for name in required if name not in names]
    if missing:
        raise GuokaoEntryScoreImportError(
            f"entry score workbook missing column(s): {', '.join(missing)}"
        )
    return {name: names[name] for name in required}


def _cell(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if index < len(row) else None


def _normalize_required_text(value: Any, *, column: str, row_number: int) -> str:
    if value is None:
        raise GuokaoEntryScoreImportError(f"row {row_number} missing {column}")
    normalized = str(value).strip()
    if not normalized:
        raise GuokaoEntryScoreImportError(f"row {row_number} missing {column}")
    return normalized


def _normalize_code(value: Any, *, width: int, column: str, row_number: int) -> str:
    normalized = _normalize_required_text(value, column=column, row_number=row_number)
    if isinstance(value, float) and value.is_integer():
        normalized = str(int(value))
    elif isinstance(value, int):
        normalized = str(value)
    elif normalized.endswith(".0") and normalized[:-2].isdigit():
        normalized = normalized[:-2]
    if not normalized.isdigit():
        raise GuokaoEntryScoreImportError(f"row {row_number} has non-numeric {column}: {normalized}")
    return normalized.zfill(width)


def _parse_score(value: Any, *, row_number: int) -> Decimal:
    if value is None or str(value).strip() == "":
        raise GuokaoEntryScoreImportError(f"row {row_number} missing 最低面试分数")
    try:
        score = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise GuokaoEntryScoreImportError(
            f"row {row_number} has invalid 最低面试分数: {value}"
        ) from exc
    if score <= 0:
        raise GuokaoEntryScoreImportError(
            f"row {row_number} has non-positive 最低面试分数: {value}"
        )
    return score


def _upsert_evidence(
    *,
    session: Session,
    records: list[GuokaoEntryScoreRecord],
    source_name: str,
    source_url: str | None,
    source_file_sha256: str,
    batch_size: int,
) -> None:
    statement = text(_UPSERT_ENTRY_SCORE_EVIDENCE)
    for start in range(0, len(records), batch_size):
        batch = [
            {
                "year": record.year,
                "department_code": record.department_code,
                "position_code": record.position_code,
                "agency": record.agency,
                "bureau": record.bureau,
                "position_title": record.position_title,
                "entry_score_min": str(record.entry_score_min),
                "source_name": source_name,
                "source_url": source_url,
                "source_file_sha256": source_file_sha256,
                "candidate_count": record.candidate_count,
                "raw_json": json.dumps(
                    {
                        "sourceColumns": [
                            "招录机关",
                            "部门代码",
                            "用人司局",
                            "招考职位",
                            "职位代码",
                            "最低面试分数",
                        ],
                        "privacy": "candidate names and admission ticket numbers intentionally not stored",
                    },
                    ensure_ascii=False,
                ),
            }
            for record in records[start : start + batch_size]
        ]
        session.execute(statement, batch)


def _refresh_entry_score_links(
    *,
    session: Session,
    year: int,
    source_file_sha256: str,
) -> None:
    params = {"year": year, "source_file_sha256": source_file_sha256}
    session.execute(text(_UPSERT_EXACT_ENTRY_SCORE_LINKS), params)
    session.execute(text(_UPSERT_CONTAINMENT_ENTRY_SCORE_LINKS), params)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_UPSERT_ENTRY_SCORE_EVIDENCE = """
insert into product.position_entry_score_evidence (
  year,
  department_code,
  position_code,
  agency,
  bureau,
  position_title,
  entry_score_min,
  source_name,
  source_url,
  source_file_sha256,
  candidate_count,
  raw_json,
  updated_at
) values (
  :year,
  :department_code,
  :position_code,
  :agency,
  :bureau,
  :position_title,
  cast(:entry_score_min as numeric),
  :source_name,
  :source_url,
  :source_file_sha256,
  :candidate_count,
  cast(:raw_json as jsonb),
  now()
)
on conflict (year, department_code, position_code) do update set
  agency = excluded.agency,
  bureau = excluded.bureau,
  position_title = excluded.position_title,
  entry_score_min = excluded.entry_score_min,
  source_name = excluded.source_name,
  source_url = excluded.source_url,
  source_file_sha256 = excluded.source_file_sha256,
  candidate_count = excluded.candidate_count,
  raw_json = excluded.raw_json,
  updated_at = now()
"""

_UPSERT_EXACT_ENTRY_SCORE_LINKS = """
insert into product.position_entry_score_links (
  position_id,
  evidence_id,
  match_method,
  confidence,
  evidence,
  updated_at
)
select
  p.position_id,
  e.evidence_id,
  'exact_department_title_code',
  'high',
  jsonb_build_object(
    'year', e.year,
    'departmentCode', e.department_code,
    'positionCode', e.position_code,
    'departmentFull', p.department_full,
    'agency', e.agency,
    'bureau', e.bureau
  ),
  now()
from product.positions p
join product.position_entry_score_evidence e
  on e.year = p.year
 and e.position_code = p.position_code
 and e.position_title = p.name
 and p.department_full = e.agency || e.bureau
where e.year = :year
  and e.source_file_sha256 = :source_file_sha256
on conflict (position_id, evidence_id) do update set
  match_method = excluded.match_method,
  confidence = excluded.confidence,
  evidence = excluded.evidence,
  updated_at = now()
"""

_UPSERT_CONTAINMENT_ENTRY_SCORE_LINKS = """
with candidates as (
  select
    p.position_id,
    e.evidence_id,
    p.department_full,
    e.year,
    e.department_code,
    e.position_code,
    e.agency,
    e.bureau,
    count(*) over (partition by p.position_id) as position_match_count,
    count(*) over (partition by e.evidence_id) as evidence_match_count
  from product.positions p
  join product.position_entry_score_evidence e
    on e.year = p.year
   and e.position_code = p.position_code
   and e.position_title = p.name
  where e.year = :year
    and e.source_file_sha256 = :source_file_sha256
    and p.department_full <> e.agency || e.bureau
    and (
      regexp_replace(coalesce(p.department_full, ''), '\\s+', '', 'g')
        like '%' || regexp_replace(e.agency || e.bureau, '\\s+', '', 'g') || '%'
      or regexp_replace(e.agency || e.bureau, '\\s+', '', 'g')
        like '%' || regexp_replace(coalesce(p.department_full, ''), '\\s+', '', 'g') || '%'
    )
    and not exists (
      select 1
      from product.position_entry_score_links exact_position
      join product.position_entry_score_evidence exact_evidence
        on exact_evidence.evidence_id = exact_position.evidence_id
      where exact_position.position_id = p.position_id
        and exact_position.match_method = 'exact_department_title_code'
        and exact_evidence.year = e.year
        and exact_evidence.source_file_sha256 = e.source_file_sha256
    )
    and not exists (
      select 1
      from product.position_entry_score_links exact_evidence_link
      join product.position_entry_score_evidence exact_evidence
        on exact_evidence.evidence_id = exact_evidence_link.evidence_id
      where exact_evidence_link.evidence_id = e.evidence_id
        and exact_evidence_link.match_method = 'exact_department_title_code'
        and exact_evidence.year = e.year
        and exact_evidence.source_file_sha256 = e.source_file_sha256
    )
)
insert into product.position_entry_score_links (
  position_id,
  evidence_id,
  match_method,
  confidence,
  evidence,
  updated_at
)
select
  position_id,
  evidence_id,
  'unique_department_containment_title_code',
  'audited_text_match',
  jsonb_build_object(
    'year', year,
    'departmentCode', department_code,
    'positionCode', position_code,
    'departmentFull', department_full,
    'agency', agency,
    'bureau', bureau,
    'positionMatchCount', position_match_count,
    'evidenceMatchCount', evidence_match_count
  ),
  now()
from candidates
where position_match_count = 1
  and evidence_match_count = 1
on conflict (position_id, evidence_id) do update set
  match_method = excluded.match_method,
  confidence = excluded.confidence,
  evidence = excluded.evidence,
  updated_at = now()
"""
