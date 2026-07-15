from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.domains.positions.schemas import PositionSummary
from app.domains.positions.service import PositionService


PredictionConfidence = Literal["high", "medium", "low"]

MIN_PREDICTION_SAMPLE_COUNT = 2
PREDICTION_SAMPLE_LIMIT = 80
PREDICTION_MARGIN_LOW_THRESHOLD = 110.0
PREDICTION_MARGIN_HIGH_THRESHOLD = 130.0
PREDICTION_SAFETY_MARGIN = 3.0


@dataclass(frozen=True)
class ScorePrediction:
    target_score: float
    sample_count: int
    sample_position_count: int
    latest_score_year: int
    average_entry_score: float
    safety_margin: float
    confidence: PredictionConfidence
    matched_dimensions: list[str]
    evidence_summary: str


class PositionScorePredictor:
    def __init__(self, *, position_service: PositionService) -> None:
        self._position_service = position_service

    def predict(
        self,
        *,
        position: PositionSummary,
        year: int,
        education: str,
        major: str,
        political_status: str,
        grassroots_years: int,
    ) -> ScorePrediction | None:
        sample_year = year - 1
        tiers = _prediction_tiers(position)
        for tier in tiers:
            rows = self._position_service.list_score_prediction_points(
                year=year,
                sample_year=sample_year,
                target_position_id=position.position_id,
                region=position.region,
                city=tier.city,
                system_code=tier.system_code,
                education=education,
                major=major,
                political_status=political_status,
                grassroots_years=grassroots_years,
                limit=PREDICTION_SAMPLE_LIMIT,
            )
            if len(rows) >= MIN_PREDICTION_SAMPLE_COUNT:
                return _prediction_from_rows(
                    rows=rows,
                    sample_year=sample_year,
                    matched_dimensions=tier.matched_dimensions,
                    evidence_scope=tier.evidence_scope,
                )
        return None


@dataclass(frozen=True)
class _PredictionTier:
    city: str | None
    system_code: str | None
    matched_dimensions: list[str]
    evidence_scope: str


def _prediction_tiers(position: PositionSummary) -> list[_PredictionTier]:
    tiers: list[_PredictionTier] = []
    if position.city:
        tiers.append(
            _PredictionTier(
                city=position.city,
                system_code=position.system_code,
                matched_dimensions=[
                    "region",
                    "city",
                    "system",
                    "major",
                    "education",
                    "political",
                    "serviceYears",
                ],
                evidence_scope="上一年同地区同专业同城市同系统",
            )
        )
    tiers.append(
        _PredictionTier(
            city=None,
            system_code=position.system_code,
            matched_dimensions=[
                "region",
                "system",
                "major",
                "education",
                "political",
                "serviceYears",
            ],
            evidence_scope="上一年同地区同专业同系统",
        )
    )
    tiers.append(
        _PredictionTier(
            city=None,
            system_code=None,
            matched_dimensions=[
                "region",
                "major",
                "education",
                "political",
                "serviceYears",
            ],
            evidence_scope="上一年同地区同专业",
        )
    )
    return tiers


def _prediction_from_rows(
    *,
    rows: list[dict[str, object]],
    sample_year: int,
    matched_dimensions: list[str],
    evidence_scope: str,
) -> ScorePrediction:
    scores = [_float(row["entry_score_min"]) for row in rows]
    average = round(sum(scores) / len(scores), 2)
    safety_margin = _dynamic_safety_margin(average)
    target_score = round(average + safety_margin, 2)
    sample_position_count = len({str(row["position_id"]) for row in rows})
    sample_count = len(rows)
    return ScorePrediction(
        target_score=target_score,
        sample_count=sample_count,
        sample_position_count=sample_position_count,
        latest_score_year=sample_year,
        average_entry_score=average,
        safety_margin=safety_margin,
        confidence=_confidence(sample_count),
        matched_dimensions=matched_dimensions,
        evidence_summary=(
            f"{evidence_scope}样本均分 {average:g}，样本 {sample_count} 条，"
            f"安全边际 {safety_margin:g} 分。"
        ),
    )


def _dynamic_safety_margin(entry_score: float) -> float:
    if entry_score <= PREDICTION_MARGIN_LOW_THRESHOLD:
        return PREDICTION_SAFETY_MARGIN
    if entry_score >= PREDICTION_MARGIN_HIGH_THRESHOLD:
        return 0.0
    ratio = (PREDICTION_MARGIN_HIGH_THRESHOLD - entry_score) / (
        PREDICTION_MARGIN_HIGH_THRESHOLD - PREDICTION_MARGIN_LOW_THRESHOLD
    )
    return round(PREDICTION_SAFETY_MARGIN * ratio, 2)


def _confidence(sample_count: int) -> PredictionConfidence:
    if sample_count >= 8:
        return "high"
    if sample_count >= 4:
        return "medium"
    return "low"


def _float(value: object) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
