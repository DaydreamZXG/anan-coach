from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class PositionSearchError(ValueError):
    pass


@dataclass(frozen=True)
class PositionSearchFilters:
    year: int
    education: str
    major: str
    fresh_graduate: bool | None
    political_status: str
    grassroots_years: int
    region: str | None
    city: str | None
    has_extra_exam: bool | None
    limit: int


@dataclass(frozen=True)
class PositionScorePredictionFilters:
    year: int
    sample_year: int
    target_position_id: str
    region: str
    city: str | None
    system_code: str | None
    education: str
    major: str
    political_status: str
    grassroots_years: int
    limit: int


class PositionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._has_extra_exam_column: bool | None = None

    def _extra_exam_column_available(self) -> bool:
        if self._has_extra_exam_column is None:
            row = self._session.execute(
                text(
                    """
                    select count(*) as count
                    from information_schema.columns
                    where table_schema = 'product'
                      and table_name = 'positions'
                      and column_name = 'has_extra_exam'
                    """
                )
            ).mappings().one()
            self._has_extra_exam_column = int(row["count"]) > 0
        return self._has_extra_exam_column

    def list_positions(self, filters: PositionSearchFilters) -> list[dict[str, Any]]:
        education_rank = _education_rank(filters.education)
        params: dict[str, Any] = {
            "year": filters.year,
            "major_like": f"%{filters.major}%",
            "grassroots_years": filters.grassroots_years,
            "education_rank": education_rank,
            "limit": filters.limit,
        }
        political_like_conditions = []
        for index, term in enumerate(_political_terms(filters.political_status)):
            key = f"political_like_{index}"
            params[key] = f"%{term}%"
            political_like_conditions.append(f"p.political_req like :{key}")
        where = [
            "p.year = :year",
            """
            (
              p.major_required is null
              or btrim(p.major_required) = ''
              or p.major_required like '%不限%'
              or p.major_required like :major_like
            )
            """,
            """
            (
              p.service_min_yrs is null
              or p.service_min_yrs <= :grassroots_years
            )
            """,
            """
            (
              p.education_min is null
              or btrim(p.education_min) = ''
              or p.education_min like '%不限%'
              or case
                   when p.education_min like '%博士%' then 4
                   when p.education_min like '%硕士%' or p.education_min like '%研究生%' then 3
                   when p.education_min like '%本科%' then 2
                   when p.education_min like '%大专%' or p.education_min like '%专科%' then 1
                   else 99
                 end <= :education_rank
            )
            """,
            """
            (
              p.political_req is null
              or btrim(p.political_req) = ''
              or p.political_req like '%不限%'
              or """ + " or ".join(political_like_conditions) + """
            )
            """,
        ]
        if filters.region:
            where.append("p.region = :region")
            params["region"] = filters.region
        if filters.city:
            where.append("p.city like :city_pattern")
            params["city_pattern"] = f"%{filters.city}%"
        if filters.has_extra_exam is not None and self._extra_exam_column_available():
            where.append("p.has_extra_exam = :has_extra_exam")
            params["has_extra_exam"] = filters.has_extra_exam

        extra_exam_select = (
            "p.has_extra_exam" if self._extra_exam_column_available() else "false"
        )

        rows = self._session.execute(
            text(
                f"""
                with score_rows as (
                  select
                    l.position_id,
                    e.year,
                    e.entry_score_min::numeric as entry_score_min,
                    e.entry_score_min::numeric as entry_score_max
                  from product.position_entry_score_links l
                  join product.position_entry_score_evidence e
                    on e.evidence_id = l.evidence_id
                  union all
                  select
                    l.position_id,
                    h.year,
                    h.entry_score_min::numeric as entry_score_min,
                    h.entry_score_max::numeric as entry_score_max
                  from product.position_score_links l
                  join product.position_score_histories h
                    on h.score_history_id = l.score_history_id
                  where h.entry_score_min is not null
                ),
                score_stats as (
                  select
                    position_id,
                    array_agg(year order by year) as years,
                    count(*) as source_count,
                    avg(entry_score_min) as average_entry_score,
                    avg(entry_score_max) as average_entry_score_max,
                    (array_agg(entry_score_min order by year desc))[1] as latest_entry_score,
                    (array_agg(entry_score_max order by year desc))[1] as latest_entry_score_max
                  from score_rows
                  group by position_id
                )
                select
                  p.position_id,
                  p.year,
                  p.position_code,
                  p.name,
                  p.department_full,
                  p.system_code,
                  p.region,
                  p.city,
                  p.headcount,
                  p.education_min,
                  p.major_required,
                  p.political_req,
                  p.service_min_yrs,
                  p.remark,
                  {extra_exam_select} as has_extra_exam,
                  coalesce(s.years, array[]::integer[]) as score_years,
                  coalesce(s.source_count, 0) as score_source_count,
                  s.average_entry_score,
                  s.average_entry_score_max,
                  s.latest_entry_score,
                  s.latest_entry_score_max
                from product.positions p
                left join score_stats s on s.position_id = p.position_id
                where {" and ".join(where)}
                order by
                  s.average_entry_score nulls last,
                  p.headcount desc,
                  p.region,
                  p.department_full,
                  p.position_code
                limit :limit
                """
            ),
            params,
        ).mappings().all()
        return [dict(row) for row in rows]

    def list_regions(self) -> list[dict[str, Any]]:
        rows = self._session.execute(
            text(
                """
                select
                  p.region as region,
                  p.city as city
                from product.positions p
                where p.region is not null
                  and btrim(p.region) <> ''
                group by p.region, p.city
                order by p.region, p.city
                """
            )
        ).mappings().all()
        provinces: dict[str, dict[str, dict[str, set[str]]]] = {}
        for row in rows:
            region = str(row["region"]) if row["region"] is not None else ""
            city_raw = str(row["city"]) if row["city"] is not None else ""
            if not region:
                continue
            province = provinces.setdefault(region, {})
            city_name, district_name = _parse_city_field(city_raw, region)
            if not city_name:
                continue
            city_entry = province.setdefault(city_name, {})
            if district_name:
                city_entry.setdefault("_districts", set()).add(district_name)
        result: list[dict[str, Any]] = []
        for region in sorted(provinces.keys()):
            cities = provinces[region]
            city_list: list[dict[str, Any]] = []
            for city_name in sorted(cities.keys()):
                districts = sorted(cities[city_name].get("_districts", set()))
                city_list.append(
                    {
                        "name": city_name,
                        "districts": [{"name": d} for d in districts],
                    }
                )
            result.append({"name": region, "cities": city_list})
        return result

    def list_majors(self) -> list[dict[str, Any]]:
        """从专业目录表读取三级层级数据，与岗位表 major_required 解耦。

        数据来源：product.major_categories / major_sub_categories / major_catalog
        """
        rows = self._session.execute(
            text(
                """
                select
                  c.code        as category_code,
                  c.name        as category_name,
                  s.code        as sub_code,
                  s.name        as sub_name,
                  m.code        as major_code,
                  m.name        as major_name
                from product.major_categories c
                join product.major_sub_categories s
                  on s.category_code = c.code
                join product.major_catalog m
                  on m.sub_category_code = s.code
                order by c.display_order, s.display_order, m.display_order
                """
            )
        ).mappings().all()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "category_code": str(row["category_code"]),
                    "category_name": str(row["category_name"]),
                    "sub_code": str(row["sub_code"]),
                    "sub_name": str(row["sub_name"]),
                    "major_code": str(row["major_code"]),
                    "major_name": str(row["major_name"]),
                }
            )
        return result

    def get_position_detail(self, *, position_id: str) -> dict[str, Any] | None:
        extra_exam_select = (
            "p.has_extra_exam" if self._extra_exam_column_available() else "false"
        )
        row = self._session.execute(
            text(
                f"""
                select
                  p.position_id,
                  p.year,
                  p.position_code,
                  p.name,
                  p.department_full,
                  p.system_code,
                  p.region,
                  p.city,
                  p.paper_type,
                  p.headcount,
                  p.education_min,
                  p.major_required,
                  p.political_req,
                  p.hukou_req,
                  p.service_min_yrs,
                  p.remark,
                  {extra_exam_select} as has_extra_exam
                from product.positions p
                where p.position_id = cast(:position_id as uuid)
                """
            ),
            {"position_id": position_id},
        ).mappings().first()
        return dict(row) if row else None

    def get_position_score_stats(self, *, position_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                with score_rows as (
                  select
                    e.year,
                    e.entry_score_min::numeric as entry_score_min
                  from product.position_entry_score_links l
                  join product.position_entry_score_evidence e
                    on e.evidence_id = l.evidence_id
                  where l.position_id = cast(:position_id as uuid)
                  union all
                  select
                    h.year,
                    h.entry_score_min::numeric as entry_score_min
                  from product.position_score_links l
                  join product.position_score_histories h
                    on h.score_history_id = l.score_history_id
                  where l.position_id = cast(:position_id as uuid)
                    and h.entry_score_min is not null
                )
                select
                  array_agg(year order by year) as years,
                  count(*) as source_count,
                  avg(entry_score_min) as average_entry_score,
                  (array_agg(entry_score_min order by year desc))[1] as latest_entry_score
                from score_rows
                """
            ),
            {"position_id": position_id},
        ).mappings().one()
        if int(row["source_count"]) == 0:
            return None
        return dict(row)

    def get_position_context(self, *, position_id: str) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select
                  position_id,
                  year,
                  position_code,
                  department_full,
                  system_code,
                  region,
                  city,
                  education_min,
                  major_required,
                  political_req,
                  service_min_yrs
                from product.positions
                where position_id = cast(:position_id as uuid)
                """
            ),
            {"position_id": position_id},
        ).mappings().first()
        return dict(row) if row else None

    def get_position_contexts(
        self,
        *,
        position_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not position_ids:
            return {}
        rows = self._session.execute(
            text(
                """
                select
                  position_id,
                  year,
                  position_code,
                  department_full,
                  system_code,
                  region,
                  city,
                  education_min,
                  major_required,
                  political_req,
                  service_min_yrs
                from product.positions
                where position_id = any(cast(:position_ids as uuid[]))
                """
            ),
            {"position_ids": position_ids},
        ).mappings().all()
        return {str(row["position_id"]): dict(row) for row in rows}

    def list_target_score_points(
        self,
        *,
        position_id: str,
        basis_level: str,
    ) -> list[dict[str, Any]]:
        conditions = _target_score_basis_condition(basis_level)

        rows = self._session.execute(
            text(
                f"""
                with target_position as (
                  select *
                  from product.positions
                  where position_id = cast(:position_id as uuid)
                ),
                raw_score_rows as (
                  select
                    l.position_id,
                    e.year,
                    e.entry_score_min::numeric as entry_score_min
                  from product.position_entry_score_links l
                  join product.position_entry_score_evidence e
                    on e.evidence_id = l.evidence_id
                  union all
                  select
                    l.position_id,
                    h.year,
                    h.entry_score_min::numeric as entry_score_min
                  from product.position_score_links l
                  join product.position_score_histories h
                    on h.score_history_id = l.score_history_id
                  where h.entry_score_min is not null
                ),
                score_points as (
                  select
                    p.position_id,
                    p.year as position_year,
                    p.system_code,
                    p.region,
                    p.city,
                    p.education_min,
                    p.major_required,
                    p.political_req,
                    p.service_min_yrs,
                    s.year as score_year,
                    avg(s.entry_score_min) as entry_score_min
                  from product.positions p
                  join raw_score_rows s on s.position_id = p.position_id
                  group by
                    p.position_id,
                    p.year,
                    p.system_code,
                    p.region,
                    p.city,
                    p.education_min,
                    p.major_required,
                    p.political_req,
                    p.service_min_yrs,
                    s.year
                )
                select
                  sp.position_id,
                  sp.score_year,
                  sp.entry_score_min
                from score_points sp
                cross join target_position t
                where {conditions}
                order by sp.score_year, sp.position_id
                """
            ),
            {"position_id": position_id},
        ).mappings().all()
        return [dict(row) for row in rows]

    def list_target_score_points_for_positions(
        self,
        *,
        position_ids: list[str],
        basis_level: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Load one cohort level for many targets without an N+1 query path."""

        if not position_ids:
            return {}
        conditions = _target_score_basis_condition(basis_level)
        rows = self._session.execute(
            text(
                f"""
                with target_positions as (
                  select *
                  from product.positions
                  where position_id = any(cast(:position_ids as uuid[]))
                ),
                raw_score_rows as (
                  select
                    l.position_id,
                    e.year,
                    e.entry_score_min::numeric as entry_score_min
                  from product.position_entry_score_links l
                  join product.position_entry_score_evidence e
                    on e.evidence_id = l.evidence_id
                  union all
                  select
                    l.position_id,
                    h.year,
                    h.entry_score_min::numeric as entry_score_min
                  from product.position_score_links l
                  join product.position_score_histories h
                    on h.score_history_id = l.score_history_id
                  where h.entry_score_min is not null
                ),
                score_points as (
                  select
                    p.position_id,
                    p.year as position_year,
                    p.system_code,
                    p.region,
                    p.city,
                    p.education_min,
                    p.major_required,
                    p.political_req,
                    p.service_min_yrs,
                    s.year as score_year,
                    avg(s.entry_score_min) as entry_score_min
                  from product.positions p
                  join raw_score_rows s on s.position_id = p.position_id
                  group by
                    p.position_id,
                    p.year,
                    p.system_code,
                    p.region,
                    p.city,
                    p.education_min,
                    p.major_required,
                    p.political_req,
                    p.service_min_yrs,
                    s.year
                )
                select
                  t.position_id as target_position_id,
                  sp.position_id,
                  sp.score_year,
                  sp.entry_score_min
                from target_positions t
                join score_points sp on {conditions}
                order by t.position_id, sp.score_year, sp.position_id
                """
            ),
            {"position_ids": position_ids},
        ).mappings().all()
        result: dict[str, list[dict[str, Any]]] = {
            position_id: [] for position_id in position_ids
        }
        for row in rows:
            result[str(row["target_position_id"])].append(
                {
                    "position_id": row["position_id"],
                    "score_year": row["score_year"],
                    "entry_score_min": row["entry_score_min"],
                }
            )
        return result

    def list_score_prediction_points(
        self,
        filters: PositionScorePredictionFilters,
    ) -> list[dict[str, Any]]:
        education_rank = _education_rank(filters.education)
        params: dict[str, Any] = {
            "year": filters.year,
            "sample_year": filters.sample_year,
            "target_position_id": filters.target_position_id,
            "region": filters.region,
            "major_like": f"%{filters.major}%",
            "education_rank": education_rank,
            "grassroots_years": filters.grassroots_years,
            "limit": filters.limit,
        }
        where = [
            "s.year = :sample_year",
            "p.year <= :year",
            "p.position_id <> cast(:target_position_id as uuid)",
            "p.region = :region",
            """
            (
              p.major_required is null
              or btrim(p.major_required) = ''
              or p.major_required like '%不限%'
              or p.major_required like :major_like
            )
            """,
            """
            (
              p.service_min_yrs is null
              or p.service_min_yrs <= :grassroots_years
            )
            """,
            """
            (
              p.education_min is null
              or btrim(p.education_min) = ''
              or p.education_min like '%不限%'
              or case
                   when p.education_min like '%博士%' then 4
                   when p.education_min like '%硕士%' or p.education_min like '%研究生%' then 3
                   when p.education_min like '%本科%' then 2
                   when p.education_min like '%大专%' or p.education_min like '%专科%' then 1
                   else 99
                 end <= :education_rank
            )
            """,
        ]
        political_like_conditions = []
        for index, term in enumerate(_political_terms(filters.political_status)):
            key = f"political_like_{index}"
            params[key] = f"%{term}%"
            political_like_conditions.append(f"p.political_req like :{key}")
        where.append(
            """
            (
              p.political_req is null
              or btrim(p.political_req) = ''
              or p.political_req like '%不限%'
              or """
            + " or ".join(political_like_conditions)
            + """
            )
            """
        )
        if filters.city:
            where.append("p.city = :city")
            params["city"] = filters.city
        if filters.system_code:
            where.append("p.system_code = :system_code")
            params["system_code"] = filters.system_code

        rows = self._session.execute(
            text(
                f"""
                with raw_score_rows as (
                  select
                    l.position_id,
                    e.year,
                    e.entry_score_min::numeric as entry_score_min
                  from product.position_entry_score_links l
                  join product.position_entry_score_evidence e
                    on e.evidence_id = l.evidence_id
                  union all
                  select
                    l.position_id,
                    h.year,
                    h.entry_score_min::numeric as entry_score_min
                  from product.position_score_links l
                  join product.position_score_histories h
                    on h.score_history_id = l.score_history_id
                  where h.entry_score_min is not null
                ),
                score_points as (
                  select
                    p.position_id,
                    p.system_code,
                    p.region,
                    p.city,
                    p.education_min,
                    p.major_required,
                    p.political_req,
                    p.service_min_yrs,
                    s.year as score_year,
                    avg(s.entry_score_min) as entry_score_min
                  from product.positions p
                  join raw_score_rows s on s.position_id = p.position_id
                  where {" and ".join(where)}
                  group by
                    p.position_id,
                    p.system_code,
                    p.region,
                    p.city,
                    p.education_min,
                    p.major_required,
                    p.political_req,
                    p.service_min_yrs,
                    s.year
                )
                select
                  position_id,
                  score_year,
                  entry_score_min
                from score_points
                order by entry_score_min, position_id
                limit :limit
                """
            ),
            params,
        ).mappings().all()
        return [dict(row) for row in rows]


def _target_score_basis_condition(basis_level: str) -> str:
    try:
        return {
            "L1": "sp.position_id = t.position_id",
            "L2": """
                sp.system_code = t.system_code
                and sp.region = t.region
                and (
                  (sp.city is not null and t.city is not null and sp.city = t.city)
                  or (sp.city is null and t.city is null)
                )
                and (
                  case
                    when sp.education_min is null or btrim(sp.education_min) = '' or sp.education_min like '%不限%' then 'unlimited'
                    when sp.education_min like '%博士%' then 'doctoral'
                    when sp.education_min like '%硕士%' or sp.education_min like '%研究生%' then 'master'
                    when sp.education_min like '%本科%' then 'bachelor'
                    when sp.education_min like '%大专%' or sp.education_min like '%专科%' then 'associate'
                    else btrim(sp.education_min)
                  end
                ) = (
                  case
                    when t.education_min is null or btrim(t.education_min) = '' or t.education_min like '%不限%' then 'unlimited'
                    when t.education_min like '%博士%' then 'doctoral'
                    when t.education_min like '%硕士%' or t.education_min like '%研究生%' then 'master'
                    when t.education_min like '%本科%' then 'bachelor'
                    when t.education_min like '%大专%' or t.education_min like '%专科%' then 'associate'
                    else btrim(t.education_min)
                  end
                )
                and (
                  coalesce(nullif(btrim(sp.major_required), ''), '不限') like '%不限%'
                  or coalesce(nullif(btrim(t.major_required), ''), '不限') like '%不限%'
                  or (
                    sp.major_required is not null
                    and t.major_required is not null
                    and (
                      sp.major_required = t.major_required
                      or sp.major_required like concat('%', t.major_required, '%')
                      or t.major_required like concat('%', sp.major_required, '%')
                    )
                  )
                )
                and (
                  coalesce(nullif(btrim(sp.political_req), ''), '不限') like '%不限%'
                  or coalesce(nullif(btrim(t.political_req), ''), '不限') like '%不限%'
                  or sp.political_req = t.political_req
                )
                and coalesce(sp.service_min_yrs, 0) = coalesce(t.service_min_yrs, 0)
            """,
            "L3": """
                sp.system_code = t.system_code
                and sp.region = t.region
                and sp.city is not null
                and t.city is not null
                and sp.city = t.city
            """,
            "L4": """
                sp.system_code = t.system_code
                and sp.region = t.region
            """,
        }[basis_level]
    except KeyError as exc:
        raise ValueError(f"unsupported basis level: {basis_level}") from exc


def _education_rank(value: str) -> int:
    if "博士" in value:
        return 4
    if "硕士" in value or "研究生" in value:
        return 3
    if "本科" in value:
        return 2
    if "大专" in value or "专科" in value:
        return 1
    raise PositionSearchError("education must contain 博士/硕士/研究生/本科/大专/专科")


def _political_terms(value: str) -> list[str]:
    if "党员" in value:
        return ["党员", "中共党员", "不限"]
    if "团员" in value:
        return ["团员", "共青团员", "不限"]
    if "群众" in value:
        return ["群众", "不限"]
    return [value, "不限"]


def _parse_city_field(city: str, region: str) -> tuple[str, str | None]:
    """Parse a city string into (city_name, district_name|None).

    Examples:
      ("江西省赣州市兴国县", "江西") -> ("赣州市", "兴国县")
      ("云南省临沧市", "云南")       -> ("临沧市", None)
      ("上海市嘉定区", "上海")       -> ("市辖区", "嘉定区")
      ("上海市", "上海")             -> ("市辖区", None)
      ("赣州市", "江西")             -> ("赣州市", None)
      ("北京市", "北京")             -> ("市辖区", None)
    """
    text = city.strip()
    if not text:
        return "", None
    if region and text.startswith(region):
        text = text[len(region):]
    if text.startswith("省"):
        text = text[1:]
    if text.startswith("市"):
        rest = text[1:]
        if not rest:
            return "市辖区", None
        return "市辖区", rest
    idx = text.find("市")
    if idx == -1:
        return text, None
    city_name = text[: idx + 1]
    rest = text[idx + 1:]
    if not rest:
        return city_name, None
    return city_name, rest
