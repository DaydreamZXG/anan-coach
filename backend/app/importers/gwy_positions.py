from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.importers.guokao_official_positions import (
    OFFICIAL_POSITION_CONTRACT,
    GuokaoOfficialPositionError,
    load_official_position_catalog,
    merge_official_position_catalog,
)


class GwyPositionImportError(ValueError):
    pass


@dataclass(frozen=True)
class GwyPositionImportResult:
    systems: int
    positions: int
    official_positions_enriched: int
    position_score_histories: int
    system_score_histories: int
    position_score_links: int
    position_tags: int


def import_gwy_positions(
    *,
    session: Session,
    source_path: Path,
    batch_size: int = 1000,
    official_source_path: Path | None = None,
    official_exam_year: int | None = None,
    position_data_version: str | None = None,
) -> GwyPositionImportResult:
    source = source_path.expanduser().resolve()
    if not source.exists():
        raise GwyPositionImportError(f"gwy.db not found: {source}")
    if batch_size < 1:
        raise GwyPositionImportError("batch_size must be positive")
    version = _validated_position_data_version(position_data_version)

    with sqlite3.connect(source) as sqlite_conn:
        sqlite_conn.row_factory = sqlite3.Row
        _require_source_tables(sqlite_conn)
        position_rows = _read_rows(sqlite_conn, _POSITIONS_SQL)
        official_positions_enriched = 0
        if official_source_path is not None:
            if official_exam_year is None or not position_data_version:
                raise GwyPositionImportError(
                    "official_exam_year and position_data_version are required "
                    "with official_source_path"
                )
            try:
                catalog = load_official_position_catalog(
                    official_source_path,
                    exam_year=official_exam_year,
                    position_data_version=version,
                )
                position_rows = merge_official_position_catalog(
                    position_rows,
                    catalog=catalog,
                )
            except GuokaoOfficialPositionError as exc:
                raise GwyPositionImportError(str(exc)) from exc
            official_positions_enriched = len(catalog.rows)
        snapshot_rows = [
            _position_snapshot_row(row, position_data_version=version) for row in position_rows
        ]
        batch_source_sha256 = _position_batch_fingerprint(snapshot_rows)
        _ensure_import_batch(
            session,
            position_data_version=version,
            source_sha256=batch_source_sha256,
            position_count=len(snapshot_rows),
        )
        systems = _upsert_rows(
            session=session,
            rows=_read_rows(
                sqlite_conn, "select code, name_zh, paper_type, is_big_six from systems"
            ),
            sql=_UPSERT_SYSTEM,
            batch_size=batch_size,
        )
        positions = _upsert_rows(
            session=session,
            rows=snapshot_rows,
            sql=_INSERT_POSITION_IDENTITY,
            batch_size=batch_size,
        )
        _upsert_rows(
            session=session,
            rows=snapshot_rows,
            sql=_INSERT_POSITION_SNAPSHOT,
            batch_size=batch_size,
        )
        _verify_position_snapshot_rows(
            session,
            rows=snapshot_rows,
            position_data_version=version,
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
        official_positions_enriched=official_positions_enriched,
        position_score_histories=score_histories,
        system_score_histories=system_score_histories,
        position_score_links=links,
        position_tags=position_tags,
    )


def verify_gwy_position_import(
    session: Session,
    *,
    source_path: Path,
    official_source_path: Path | None = None,
    official_exam_year: int | None = None,
    position_data_version: str | None = None,
) -> dict[str, Any]:
    source = source_path.expanduser().resolve()
    if not source.exists():
        raise GwyPositionImportError(f"gwy.db not found: {source}")
    version = _validated_position_data_version(position_data_version)

    with sqlite3.connect(source) as sqlite_conn:
        sqlite_conn.row_factory = sqlite3.Row
        position_rows = _read_rows(sqlite_conn, _POSITIONS_SQL)
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

    official_verification: dict[str, Any] | None = None
    if official_source_path is not None:
        if official_exam_year is None or not position_data_version:
            raise GwyPositionImportError(
                "official_exam_year and position_data_version are required "
                "with official_source_path"
            )
        try:
            catalog = load_official_position_catalog(
                official_source_path,
                exam_year=official_exam_year,
                position_data_version=version,
            )
            position_rows = merge_official_position_catalog(
                position_rows,
                catalog=catalog,
            )
        except GuokaoOfficialPositionError as exc:
            raise GwyPositionImportError(str(exc)) from exc
        official_target = (
            session.execute(
                text(
                    """
                select
                  count(*) as total,
                  count(*) filter (
                    where snapshot.raw_json ->> 'contractVersion' = :contract_version
                      and snapshot.raw_json ->> 'positionDataVersion'
                            = :position_data_version
                      and snapshot.raw_json #>> '{source,sha256}' = :source_sha256
                  ) as verified
                from product.positions
                join product.position_snapshots snapshot
                  on snapshot.position_id = positions.position_id
                 and snapshot.position_data_version = :position_data_version
                where snapshot.year = :exam_year
                """
                ),
                {
                    "contract_version": OFFICIAL_POSITION_CONTRACT,
                    "position_data_version": catalog.position_data_version,
                    "source_sha256": catalog.source_sha256,
                    "exam_year": catalog.exam_year,
                },
            )
            .mappings()
            .one()
        )
        official_verification = {
            "exam_year": catalog.exam_year,
            "position_data_version": catalog.position_data_version,
            "source_sha256": catalog.source_sha256,
            "source_positions": len(catalog.rows),
            "target_positions": int(official_target["total"]),
            "verified_positions": int(official_target["verified"]),
        }
        if (
            official_verification["source_positions"] != official_verification["target_positions"]
            or official_verification["source_positions"]
            != official_verification["verified_positions"]
        ):
            mismatches["official_position_catalog"] = {
                "source": official_verification["source_positions"],
                "target": official_verification["target_positions"],
                "verified": official_verification["verified_positions"],
            }

    batch = (
        session.execute(
            text(
                """
            select source_sha256, position_count
            from product.position_import_batches
            where position_data_version = :position_data_version
            """
            ),
            {"position_data_version": version},
        )
        .mappings()
        .one_or_none()
    )
    expected_snapshot_rows = [
        _position_snapshot_row(row, position_data_version=version) for row in position_rows
    ]
    expected_batch_fingerprint = _position_batch_fingerprint(expected_snapshot_rows)
    if batch is None:
        mismatches["position_import_batch"] = {
            "source": expected_batch_fingerprint,
            "target": None,
        }
    elif batch["source_sha256"] != expected_batch_fingerprint or int(
        batch["position_count"]
    ) != len(expected_snapshot_rows):
        mismatches["position_import_batch"] = {
            "source": {
                "sha256": expected_batch_fingerprint,
                "positions": len(expected_snapshot_rows),
            },
            "target": {
                "sha256": batch["source_sha256"],
                "positions": int(batch["position_count"]),
            },
        }

    target_counts["positions"] = int(
        session.execute(
            text(
                """
                select count(*)
                from product.position_snapshots
                where position_data_version = :position_data_version
                """
            ),
            {"position_data_version": version},
        ).scalar_one()
    )
    mismatches.pop("positions", None)
    if source_counts["positions"] != target_counts["positions"]:
        mismatches["positions"] = {
            "source": source_counts["positions"],
            "target": target_counts["positions"],
        }

    coverage = (
        session.execute(
            text(
                """
            select
              snapshot.year,
              count(*) as positions,
              count(l.score_history_id) as positions_with_score,
              round(100.0 * count(l.score_history_id) / nullif(count(*), 0), 1) as pct
            from product.position_snapshots snapshot
            left join product.position_score_links l
              on l.position_id = snapshot.position_id
            where snapshot.position_data_version = :position_data_version
            group by snapshot.year
            order by snapshot.year
            """
            ),
            {"position_data_version": version},
        )
        .mappings()
        .all()
    )

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
        "official_catalog": official_verification,
        "position_data_version": version,
        "position_import_batch": dict(batch) if batch is not None else None,
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


def _validated_position_data_version(value: str | None) -> str:
    version = value.strip() if value is not None else ""
    if not version:
        raise GwyPositionImportError(
            "position_data_version is required for every immutable position import"
        )
    if len(version) > 200:
        raise GwyPositionImportError("position_data_version must be at most 200 characters")
    return version


def _position_snapshot_row(
    row: dict[str, Any],
    *,
    position_data_version: str,
) -> dict[str, Any]:
    normalized_raw_json = _normalized_json(row.get("raw_json"))
    payload = {
        "source_id": str(row["source_id"]),
        "year": int(row["year"]),
        "position_code": str(row["position_code"]),
        "name": str(row["name"]),
        "department_full": str(row["department_full"]),
        "system_code": str(row["system_code"]),
        "region": str(row["region"]),
        "city": row.get("city"),
        "paper_type": str(row["paper_type"]),
        "headcount": int(row["headcount"]),
        "education_min": row.get("education_min"),
        "major_required": row.get("major_required"),
        "political_req": row.get("political_req"),
        "hukou_req": row.get("hukou_req"),
        "service_min_yrs": row.get("service_min_yrs"),
        "has_extra_exam": bool(row.get("has_extra_exam", False)),
        "remark": row.get("remark"),
        "raw_json": normalized_raw_json,
    }
    fingerprint = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **payload,
        "raw_json": (
            json.dumps(normalized_raw_json, ensure_ascii=False, sort_keys=True)
            if normalized_raw_json is not None
            else None
        ),
        "position_data_version": position_data_version,
        "source_fingerprint": fingerprint,
    }


def _normalized_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise GwyPositionImportError("position raw_json is not valid JSON") from exc
    return value


def _position_batch_fingerprint(rows: list[dict[str, Any]]) -> str:
    if not rows:
        raise GwyPositionImportError("position import batch must not be empty")
    authority = [
        (str(row["source_id"]), str(row["source_fingerprint"]))
        for row in sorted(rows, key=lambda item: str(item["source_id"]))
    ]
    return sha256(
        json.dumps(
            authority,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _ensure_import_batch(
    session: Session,
    *,
    position_data_version: str,
    source_sha256: str,
    position_count: int,
) -> None:
    existing = (
        session.execute(
            text(
                """
            select source_sha256, position_count
            from product.position_import_batches
            where position_data_version = :position_data_version
            """
            ),
            {"position_data_version": position_data_version},
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        if (
            existing["source_sha256"] != source_sha256
            or int(existing["position_count"]) != position_count
        ):
            raise GwyPositionImportError(
                "position_data_version already identifies a different immutable import"
            )
        return
    session.execute(
        text(
            """
            insert into product.position_import_batches (
              position_data_version, source_sha256, position_count
            ) values (
              :position_data_version, :source_sha256, :position_count
            )
            """
        ),
        {
            "position_data_version": position_data_version,
            "source_sha256": source_sha256,
            "position_count": position_count,
        },
    )


def _verify_position_snapshot_rows(
    session: Session,
    *,
    rows: list[dict[str, Any]],
    position_data_version: str,
) -> None:
    stored = {
        str(row["source_id"]): str(row["source_fingerprint"])
        for row in session.execute(
            text(
                """
                select source_id, source_fingerprint
                from product.position_snapshots
                where position_data_version = :position_data_version
                """
            ),
            {"position_data_version": position_data_version},
        ).mappings()
    }
    expected = {str(row["source_id"]): str(row["source_fingerprint"]) for row in rows}
    if stored != expected:
        raise GwyPositionImportError(
            "stored position snapshot does not match the immutable import batch"
        )


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

_INSERT_POSITION_IDENTITY = """
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
  has_extra_exam,
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
  :has_extra_exam,
  now()
)
on conflict (source_id) do nothing
"""

_INSERT_POSITION_SNAPSHOT = """
insert into product.position_snapshots (
  position_id,
  position_data_version,
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
  has_extra_exam,
  remark,
  raw_json,
  source_fingerprint
)
select
  position.position_id,
  :position_data_version,
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
  :has_extra_exam,
  :remark,
  cast(:raw_json as jsonb),
  :source_fingerprint
from product.positions position
where position.source_id = :source_id
on conflict (position_id, position_data_version) do nothing
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
