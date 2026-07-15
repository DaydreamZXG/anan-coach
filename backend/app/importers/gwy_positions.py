from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class GwyPositionImportError(ValueError):
    pass


@dataclass(frozen=True)
class GwyPositionImportResult:
    systems: int
    positions: int
    position_score_histories: int
    system_score_histories: int
    position_score_links: int
    position_tags: int


def import_gwy_positions(
    *,
    session: Session,
    source_path: Path,
    batch_size: int = 1000,
) -> GwyPositionImportResult:
    source = source_path.expanduser().resolve()
    if not source.exists():
        raise GwyPositionImportError(f"gwy.db not found: {source}")
    if batch_size < 1:
        raise GwyPositionImportError("batch_size must be positive")

    with sqlite3.connect(source) as sqlite_conn:
        sqlite_conn.row_factory = sqlite3.Row
        _require_source_tables(sqlite_conn)
        systems = _upsert_rows(
            session=session,
            rows=_read_rows(sqlite_conn, "select code, name_zh, paper_type, is_big_six from systems"),
            sql=_UPSERT_SYSTEM,
            batch_size=batch_size,
        )
        positions = _upsert_rows(
            session=session,
            rows=_read_rows(sqlite_conn, _POSITIONS_SQL),
            sql=_UPSERT_POSITION,
            batch_size=batch_size,
        )
        score_histories = _upsert_rows(
            session=session,
            rows=_read_rows(sqlite_conn, _POSITION_SCORE_SQL),
            sql=_UPSERT_POSITION_SCORE,
            batch_size=batch_size,
        )
        system_score_histories = _upsert_rows(
            session=session,
            rows=_read_rows(sqlite_conn, _SYSTEM_SCORE_SQL),
            sql=_UPSERT_SYSTEM_SCORE,
            batch_size=batch_size,
        )
        position_tags = _upsert_rows(
            session=session,
            rows=_read_rows(sqlite_conn, "select position_id, tag from position_tags"),
            sql=_UPSERT_POSITION_TAG,
            batch_size=batch_size,
        )

    links = _refresh_score_links(session)
    session.commit()
    return GwyPositionImportResult(
        systems=systems,
        positions=positions,
        position_score_histories=score_histories,
        system_score_histories=system_score_histories,
        position_score_links=links,
        position_tags=position_tags,
    )


def verify_gwy_position_import(session: Session, *, source_path: Path) -> dict[str, Any]:
    source = source_path.expanduser().resolve()
    if not source.exists():
        raise GwyPositionImportError(f"gwy.db not found: {source}")

    with sqlite3.connect(source) as sqlite_conn:
        source_counts = {
            "systems": _sqlite_count(sqlite_conn, "systems"),
            "positions": _sqlite_count(sqlite_conn, "positions"),
            "position_score_histories": _sqlite_count(sqlite_conn, "position_score_history"),
            "system_score_histories": _sqlite_count(sqlite_conn, "system_score_history"),
            "position_tags": _sqlite_count(sqlite_conn, "position_tags"),
        }
        source_link_count = sqlite_conn.execute(
            """
            select count(*)
            from positions p
            join position_score_history h
              on h.year = p.year
             and h.position_code = p.position_code
            """
        ).fetchone()[0]

    target_counts = {
        "systems": _pg_count(session, "product.position_systems"),
        "positions": _pg_count(session, "product.positions"),
        "position_score_histories": _pg_count(session, "product.position_score_histories"),
        "system_score_histories": _pg_count(session, "product.system_score_histories"),
        "position_tags": _pg_count(session, "product.position_tags"),
        "position_score_links": _pg_count(session, "product.position_score_links"),
    }
    mismatches = {
        key: {"source": source_counts[key], "target": target_counts[key]}
        for key in source_counts
        if source_counts[key] != target_counts[key]
    }
    if source_link_count != target_counts["position_score_links"]:
        mismatches["position_score_links"] = {
            "source": source_link_count,
            "target": target_counts["position_score_links"],
        }

    coverage = session.execute(
        text(
            """
            select
              p.year,
              count(*) as positions,
              count(l.score_history_id) as positions_with_score,
              round(100.0 * count(l.score_history_id) / nullif(count(*), 0), 1) as pct
            from product.positions p
            left join product.position_score_links l on l.position_id = p.position_id
            group by p.year
            order by p.year
            """
        )
    ).mappings().all()

    coverage_rows = []
    for row in coverage:
        item = dict(row)
        item["pct"] = float(item["pct"]) if item["pct"] is not None else None
        coverage_rows.append(item)

    return {
        "source_counts": source_counts,
        "target_counts": target_counts,
        "source_link_count": source_link_count,
        "mismatches": mismatches,
        "coverage_by_year": coverage_rows,
        "ok": not mismatches,
    }


def _require_source_tables(conn: sqlite3.Connection) -> None:
    found = {
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type in ('table', 'view')"
        ).fetchall()
    }
    required = {
        "systems",
        "positions",
        "position_score_history",
        "system_score_history",
        "position_tags",
    }
    missing = sorted(required - found)
    if missing:
        raise GwyPositionImportError(f"gwy.db missing table(s): {', '.join(missing)}")


def _read_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def _upsert_rows(
    *,
    session: Session,
    rows: list[dict[str, Any]],
    sql: str,
    batch_size: int,
) -> int:
    statement = text(sql)
    for start in range(0, len(rows), batch_size):
        session.execute(statement, rows[start : start + batch_size])
    return len(rows)


def _refresh_score_links(session: Session) -> int:
    session.execute(
        text(
            """
            insert into product.position_score_links (
              position_id,
              score_history_id,
              match_method,
              confidence,
              evidence
            )
            select
              p.position_id,
              h.score_history_id,
              'same_year_position_code',
              case
                when code_counts.position_count = 1 then 'direct_code_year'
                else 'shared_code_year'
              end,
              jsonb_build_object(
                'positionCode', p.position_code,
                'year', p.year,
                'sameCodePositionCount', code_counts.position_count
              )
            from product.positions p
            join product.position_score_histories h
              on h.year = p.year
             and h.position_code = p.position_code
            join (
              select year, position_code, count(*) as position_count
              from product.positions
              group by year, position_code
            ) code_counts
              on code_counts.year = p.year
             and code_counts.position_code = p.position_code
            on conflict (position_id, score_history_id) do update set
              match_method = excluded.match_method,
              confidence = excluded.confidence,
              evidence = excluded.evidence,
              updated_at = now()
            """
        )
    )
    return _pg_count(session, "product.position_score_links")


def _sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"select count(*) from {table}").fetchone()[0])


def _pg_count(session: Session, qualified_table: str) -> int:
    return int(session.execute(text(f"select count(*) from {qualified_table}")).scalar_one())


_POSITIONS_SQL = """
select
  id as source_id,
  year,
  position_code,
  name,
  department_full,
  system_code,
  region,
  city,
  paper_type,
  headcount,
  education_min,
  major_required,
  political_req,
  hukou_req,
  service_min_yrs,
  remark,
  raw_json
from positions
order by year, id
"""

_POSITION_SCORE_SQL = """
select
  id as source_id,
  position_code,
  year,
  entry_score_min,
  entry_score_max,
  admission_min,
  admission_max,
  signup_count,
  ratio,
  data_source
from position_score_history
order by year, id
"""

_SYSTEM_SCORE_SQL = """
select
  id as source_id,
  system_code,
  region,
  year,
  entry_score_min,
  entry_score_max,
  admission_min,
  admission_max,
  confidence,
  data_source
from system_score_history
order by year, id
"""

_UPSERT_SYSTEM = """
insert into product.position_systems (
  system_code,
  name_zh,
  paper_type,
  is_big_six,
  updated_at
) values (
  :code,
  :name_zh,
  :paper_type,
  cast(:is_big_six as integer)::boolean,
  now()
)
on conflict (system_code) do update set
  name_zh = excluded.name_zh,
  paper_type = excluded.paper_type,
  is_big_six = excluded.is_big_six,
  updated_at = now()
"""

_UPSERT_POSITION = """
insert into product.positions (
  source_id,
  year,
  position_code,
  name,
  department_full,
  system_code,
  region,
  city,
  paper_type,
  headcount,
  education_min,
  major_required,
  political_req,
  hukou_req,
  service_min_yrs,
  remark,
  raw_json,
  updated_at
) values (
  :source_id,
  :year,
  :position_code,
  :name,
  :department_full,
  :system_code,
  :region,
  :city,
  :paper_type,
  :headcount,
  :education_min,
  :major_required,
  :political_req,
  :hukou_req,
  :service_min_yrs,
  :remark,
  cast(:raw_json as jsonb),
  now()
)
on conflict (source_id) do update set
  year = excluded.year,
  position_code = excluded.position_code,
  name = excluded.name,
  department_full = excluded.department_full,
  system_code = excluded.system_code,
  region = excluded.region,
  city = excluded.city,
  paper_type = excluded.paper_type,
  headcount = excluded.headcount,
  education_min = excluded.education_min,
  major_required = excluded.major_required,
  political_req = excluded.political_req,
  hukou_req = excluded.hukou_req,
  service_min_yrs = excluded.service_min_yrs,
  remark = excluded.remark,
  raw_json = excluded.raw_json,
  updated_at = now()
"""

_UPSERT_POSITION_SCORE = """
insert into product.position_score_histories (
  source_id,
  position_code,
  year,
  entry_score_min,
  entry_score_max,
  admission_min,
  admission_max,
  signup_count,
  ratio,
  data_source,
  updated_at
) values (
  :source_id,
  :position_code,
  :year,
  :entry_score_min,
  :entry_score_max,
  :admission_min,
  :admission_max,
  :signup_count,
  :ratio,
  :data_source,
  now()
)
on conflict (source_id) do update set
  position_code = excluded.position_code,
  year = excluded.year,
  entry_score_min = excluded.entry_score_min,
  entry_score_max = excluded.entry_score_max,
  admission_min = excluded.admission_min,
  admission_max = excluded.admission_max,
  signup_count = excluded.signup_count,
  ratio = excluded.ratio,
  data_source = excluded.data_source,
  updated_at = now()
"""

_UPSERT_SYSTEM_SCORE = """
insert into product.system_score_histories (
  source_id,
  system_code,
  region,
  year,
  entry_score_min,
  entry_score_max,
  admission_min,
  admission_max,
  confidence,
  data_source,
  updated_at
) values (
  :source_id,
  :system_code,
  :region,
  :year,
  :entry_score_min,
  :entry_score_max,
  :admission_min,
  :admission_max,
  :confidence,
  :data_source,
  now()
)
on conflict (source_id) do update set
  system_code = excluded.system_code,
  region = excluded.region,
  year = excluded.year,
  entry_score_min = excluded.entry_score_min,
  entry_score_max = excluded.entry_score_max,
  admission_min = excluded.admission_min,
  admission_max = excluded.admission_max,
  confidence = excluded.confidence,
  data_source = excluded.data_source,
  updated_at = now()
"""

_UPSERT_POSITION_TAG = """
insert into product.position_tags (
  position_source_id,
  tag,
  updated_at
) values (
  :position_id,
  :tag,
  now()
)
on conflict (position_source_id, tag) do update set
  updated_at = now()
"""
