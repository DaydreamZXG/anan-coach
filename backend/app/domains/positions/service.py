from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.domains.positions.repository import (
    PositionRepository,
    PositionSearchFilters,
    PositionScorePredictionFilters,
)
from app.domains.positions.schemas import (
    CohortAnnualScore,
    EntryScoreStats,
    MajorCategory,
    MajorItem,
    MajorSubCategory,
    MajorTreeResponse,
    PositionDetail,
    PositionSearchResponse,
    PositionSummary,
    PositionTargetScoreResponse,
    ProvinceInfo,
    ProvinceListResponse,
    CityInfo,
    DistrictInfo,
    TargetScoreBasis,
)


TARGET_SCORE_SAFETY_MARGIN = 3.0
TARGET_SCORE_MARGIN_LOW_THRESHOLD = 110.0
TARGET_SCORE_MARGIN_HIGH_THRESHOLD = 130.0


def dynamic_target_score_safety_margin(entry_score: float) -> float:
    if entry_score <= TARGET_SCORE_MARGIN_LOW_THRESHOLD:
        return TARGET_SCORE_SAFETY_MARGIN
    if entry_score >= TARGET_SCORE_MARGIN_HIGH_THRESHOLD:
        return 0.0
    ratio = (TARGET_SCORE_MARGIN_HIGH_THRESHOLD - entry_score) / (
        TARGET_SCORE_MARGIN_HIGH_THRESHOLD - TARGET_SCORE_MARGIN_LOW_THRESHOLD
    )
    return round(TARGET_SCORE_SAFETY_MARGIN * ratio, 2)


class PositionNotFoundError(LookupError):
    pass


class PositionService:
    def __init__(self, repository: PositionRepository) -> None:
        self._repository = repository

    def search_positions(
        self,
        *,
        year: int,
        education: str,
        major: str,
        fresh_graduate: bool | None,
        political_status: str,
        grassroots_years: int,
        region: str | None,
        city: str | None,
        has_extra_exam: bool | None,
        limit: int,
    ) -> PositionSearchResponse:
        filters = PositionSearchFilters(
            year=year,
            education=education,
            major=major,
            fresh_graduate=fresh_graduate,
            political_status=political_status,
            grassroots_years=grassroots_years,
            region=region,
            city=city,
            has_extra_exam=has_extra_exam,
            limit=limit,
        )
        rows = self._repository.list_positions(filters)
        unverified_conditions = []
        if fresh_graduate is not None:
            unverified_conditions.append("freshGraduate")
        return PositionSearchResponse(
            items=[
                _position_from_row(
                    row,
                    target_score_basis=self.target_score_basis_for_position(
                        position_id=str(row["position_id"])
                    ),
                )
                for row in rows
            ],
            limit=limit,
            unverified_conditions=unverified_conditions,
        )

    def list_regions(self) -> ProvinceListResponse:
        rows = self._repository.list_regions()
        return ProvinceListResponse(
            provinces=[
                ProvinceInfo(
                    name=str(row["name"]),
                    cities=[
                        CityInfo(
                            name=str(city["name"]),
                            districts=[
                                DistrictInfo(name=str(d["name"]))
                                for d in city.get("districts", [])
                            ],
                        )
                        for city in row.get("cities", [])
                    ],
                )
                for row in rows
            ]
        )

    def list_majors(self) -> MajorTreeResponse:
        items = self._repository.list_majors()
        return _assemble_major_tree(items)

    def get_position_detail(self, *, position_id: str) -> PositionDetail:
        row = self._repository.get_position_detail(position_id=position_id)
        if row is None:
            raise PositionNotFoundError(position_id)
        score_stats = self._repository.get_position_score_stats(
            position_id=position_id
        )
        target_basis = self.target_score_basis_for_position(position_id=position_id)
        return _position_detail_from_row(
            row,
            score_stats=score_stats,
            target_score_basis=target_basis,
        )

    def target_score_for_position(self, *, position_id: str) -> float | None:
        return self.target_score_basis_for_position(position_id=position_id).target_score

    def get_target_score_response(self, *, position_id: str) -> PositionTargetScoreResponse:
        return PositionTargetScoreResponse(
            position_id=position_id,
            target_score_basis=self.target_score_basis_for_position(position_id=position_id),
        )

    def list_score_prediction_points(
        self,
        *,
        year: int,
        sample_year: int,
        target_position_id: str,
        region: str,
        city: str | None,
        system_code: str | None,
        education: str,
        major: str,
        political_status: str,
        grassroots_years: int,
        limit: int,
    ) -> list[dict[str, object]]:
        return self._repository.list_score_prediction_points(
            PositionScorePredictionFilters(
                year=year,
                sample_year=sample_year,
                target_position_id=target_position_id,
                region=region,
                city=city,
                system_code=system_code,
                education=education,
                major=major,
                political_status=political_status,
                grassroots_years=grassroots_years,
                limit=limit,
            )
        )

    def target_score_basis_for_position(self, *, position_id: str) -> TargetScoreBasis:
        bases = self.target_score_bases_for_positions(position_ids=[position_id])
        if position_id not in bases:
            raise PositionNotFoundError(position_id)
        return bases[position_id]

    def target_score_bases_for_positions(
        self,
        *,
        position_ids: list[str],
    ) -> dict[str, TargetScoreBasis]:
        ordered_ids = list(dict.fromkeys(position_ids))
        contexts = self._repository.get_position_contexts(position_ids=ordered_ids)
        if not contexts:
            return {}

        direct_points = self._repository.list_target_score_points_for_positions(
            position_ids=list(contexts),
            basis_level="L1",
        )
        bases: dict[str, TargetScoreBasis] = {}
        for position_id, context in contexts.items():
            direct_basis = _target_score_basis_from_direct_data(
                score_points=direct_points.get(position_id, []),
                target_year=int(context["year"]),
            )
            if direct_basis is not None:
                bases[position_id] = direct_basis

        for basis_level in ("L2", "L3", "L4"):
            unresolved = [position_id for position_id in contexts if position_id not in bases]
            if not unresolved:
                break
            score_points = self._repository.list_target_score_points_for_positions(
                position_ids=unresolved,
                basis_level=basis_level,
            )
            for position_id in unresolved:
                basis = _target_score_basis_from_points(
                    score_points=score_points.get(position_id, []),
                    target_year=int(contexts[position_id]["year"]),
                    basis_level=basis_level,
                )
                if basis is not None:
                    bases[position_id] = basis

        for position_id in contexts:
            bases.setdefault(position_id, _unavailable_target_score_basis())
        return bases


def _position_from_row(
    row: dict[str, object],
    *,
    target_score_basis: TargetScoreBasis,
) -> PositionSummary:
    source_count = int(row["score_source_count"])
    average = row["average_entry_score"]
    latest = row["latest_entry_score"]
    return PositionSummary(
        position_id=str(row["position_id"]),
        year=int(row["year"]),
        position_code=str(row["position_code"]),
        name=str(row["name"]),
        department_full=str(row["department_full"]),
        system_code=str(row["system_code"]),
        region=str(row["region"]),
        city=row["city"] if isinstance(row["city"], str) else None,
        headcount=int(row["headcount"]),
        education_min=row["education_min"] if isinstance(row["education_min"], str) else None,
        major_required=row["major_required"] if isinstance(row["major_required"], str) else None,
        political_req=row["political_req"] if isinstance(row["political_req"], str) else None,
        service_min_yrs=(
            int(row["service_min_yrs"]) if row["service_min_yrs"] is not None else None
        ),
        remark=row["remark"] if isinstance(row["remark"], str) else None,
        has_extra_exam=bool(row.get("has_extra_exam") or False),
        entry_score=EntryScoreStats(
            years=[int(year) for year in row["score_years"]],
            source_count=source_count,
            average_entry_score=float(average) if average is not None else None,
            latest_entry_score=float(latest) if latest is not None else None,
            target_score=target_score_basis.target_score,
            target_score_basis=target_score_basis,
        ),
        eligibility_basis=[
            "education_min",
            "major_required",
            "political_req",
            "service_min_yrs",
        ],
    )


def _position_detail_from_row(
    row: dict[str, object],
    *,
    score_stats: dict[str, object] | None,
    target_score_basis: TargetScoreBasis,
) -> PositionDetail:
    if score_stats is not None and int(score_stats.get("source_count") or 0) > 0:
        years_raw = score_stats.get("years") or []
        years = [int(year) for year in years_raw] if years_raw else []
        source_count = int(score_stats.get("source_count") or 0)
        average = score_stats.get("average_entry_score")
        latest = score_stats.get("latest_entry_score")
    else:
        years = []
        source_count = 0
        average = None
        latest = None
    return PositionDetail(
        position_id=str(row["position_id"]),
        year=int(row["year"]),
        position_code=str(row["position_code"]),
        name=str(row["name"]),
        department_full=str(row["department_full"]),
        system_code=str(row["system_code"]),
        region=str(row["region"]),
        city=row["city"] if isinstance(row["city"], str) else None,
        paper_type=row["paper_type"] if isinstance(row["paper_type"], str) else None,
        headcount=int(row["headcount"]),
        education_min=row["education_min"] if isinstance(row["education_min"], str) else None,
        major_required=row["major_required"] if isinstance(row["major_required"], str) else None,
        political_req=row["political_req"] if isinstance(row["political_req"], str) else None,
        service_min_yrs=(
            int(row["service_min_yrs"]) if row["service_min_yrs"] is not None else None
        ),
        hukou_req=row["hukou_req"] if isinstance(row["hukou_req"], str) else None,
        remark=row["remark"] if isinstance(row["remark"], str) else None,
        has_extra_exam=bool(row.get("has_extra_exam") or False),
        entry_score=EntryScoreStats(
            years=years,
            source_count=source_count,
            average_entry_score=float(average) if average is not None else None,
            latest_entry_score=float(latest) if latest is not None else None,
            target_score=target_score_basis.target_score,
            target_score_basis=target_score_basis,
        ),
    )


def _target_score_basis_from_direct_data(
    *,
    score_points: list[dict[str, object]],
    target_year: int,
) -> TargetScoreBasis | None:
    if not score_points:
        return None

    by_year: dict[int, list[float]] = defaultdict(list)
    position_ids: set[str] = set()
    for point in score_points:
        year = int(point["score_year"])
        score = _float_score(point["entry_score_min"])
        by_year[year].append(score)
        position_ids.add(str(point["position_id"]))

    annual_scores = [
        CohortAnnualScore(
            year=year,
            average_entry_score=round(sum(scores) / len(scores), 2),
            sample_count=len(scores),
        )
        for year, scores in sorted(by_year.items())
    ]
    if not annual_scores:
        return None

    drift = _linear_trend_per_year(annual_scores) if len(annual_scores) >= 2 else 0.0
    latest = annual_scores[-1]
    safety_margin = dynamic_target_score_safety_margin(latest.average_entry_score)
    target_score = round(
        latest.average_entry_score + safety_margin,
        2,
    )
    return TargetScoreBasis(
        target_score=target_score,
        basis_level="direct",
        sample_position_count=len(position_ids),
        score_sample_count=len(score_points),
        annual_scores=annual_scores,
        drift_per_year=round(drift, 2) if len(annual_scores) >= 2 else None,
        latest_average_score=latest.average_entry_score,
        safety_margin=safety_margin,
    )


def _target_score_basis_from_points(
    *,
    score_points: list[dict[str, object]],
    target_year: int,
    basis_level: str,
) -> TargetScoreBasis | None:
    if basis_level == "L1":
        if len({int(point["score_year"]) for point in score_points}) < 2:
            return None
    elif len(score_points) < 3:
        return None

    by_year: dict[int, list[float]] = defaultdict(list)
    position_ids: set[str] = set()
    for point in score_points:
        year = int(point["score_year"])
        score = _float_score(point["entry_score_min"])
        by_year[year].append(score)
        position_ids.add(str(point["position_id"]))

    annual_scores = [
        CohortAnnualScore(
            year=year,
            average_entry_score=round(sum(scores) / len(scores), 2),
            sample_count=len(scores),
        )
        for year, scores in sorted(by_year.items())
    ]
    if not annual_scores:
        return None

    drift = _linear_trend_per_year(annual_scores)
    latest = annual_scores[-1]
    safety_margin = dynamic_target_score_safety_margin(latest.average_entry_score)
    target_score = round(
        latest.average_entry_score + safety_margin,
        2,
    )
    return TargetScoreBasis(
        target_score=target_score,
        basis_level=basis_level,  # type: ignore[arg-type]
        sample_position_count=len(position_ids),
        score_sample_count=len(score_points),
        annual_scores=annual_scores,
        drift_per_year=round(drift, 2),
        latest_average_score=latest.average_entry_score,
        safety_margin=safety_margin,
    )


def _unavailable_target_score_basis() -> TargetScoreBasis:
    return TargetScoreBasis(
        target_score=None,
        basis_level=None,
        sample_position_count=0,
        score_sample_count=0,
        annual_scores=[],
        drift_per_year=None,
        latest_average_score=None,
        safety_margin=TARGET_SCORE_SAFETY_MARGIN,
    )


def _float_score(value: object) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _linear_trend_per_year(annual_scores: list[CohortAnnualScore]) -> float:
    if len(annual_scores) < 2:
        return 0.0
    xs = [score.year for score in annual_scores]
    ys = [score.average_entry_score for score in annual_scores]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    return numerator / denominator


def _assemble_major_tree(items: list[dict[str, object]]) -> MajorTreeResponse:
    """从扁平的三列数据（门类/专业类/专业）组装成三级树。

    输入格式（由 repository.list_majors 返回）：
      {category_code, category_name, sub_code, sub_name, major_code, major_name}
    """
    categories: dict[str, dict[str, Any]] = {}
    sub_categories: dict[str, dict[str, Any]] = {}

    for item in items:
        cat_code = str(item["category_code"])
        cat_name = str(item["category_name"])
        sub_code = str(item["sub_code"])
        sub_name = str(item["sub_name"])
        major_code = str(item["major_code"])
        major_name = str(item["major_name"])

        if cat_code not in categories:
            categories[cat_code] = {
                "code": cat_code,
                "name": cat_name,
                "sub_categories": [],
            }
        if sub_code not in sub_categories:
            sub_categories[sub_code] = {
                "code": sub_code,
                "name": sub_name,
                "majors": [],
            }
            categories[cat_code]["sub_categories"].append(sub_categories[sub_code])
        sub_categories[sub_code]["majors"].append(
            {"code": major_code, "name": major_name}
        )

    sorted_categories = sorted(categories.values(), key=lambda c: c["code"])
    for cat in sorted_categories:
        cat["sub_categories"].sort(key=lambda s: s["code"])
        for sub in cat["sub_categories"]:
            sub["majors"].sort(key=lambda m: m["code"])

    return MajorTreeResponse(
        categories=[
            MajorCategory(
                code=cat["code"],
                name=cat["name"],
                sub_categories=[
                    MajorSubCategory(
                        code=sub["code"],
                        name=sub["name"],
                        majors=[
                            MajorItem(code=m["code"], name=m["name"])
                            for m in sub["majors"]
                        ],
                    )
                    for sub in cat["sub_categories"]
                ],
            )
            for cat in sorted_categories
        ]
    )
