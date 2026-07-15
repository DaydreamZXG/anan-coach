from __future__ import annotations

from datetime import UTC, datetime
from math import ceil

from app.domains.position_decision.schemas import (
    DecisionBucket,
    DecisionRiskLevel,
    PositionDecisionBucket,
    PositionDecisionItem,
    PositionDecisionResponse,
    PositionDecisionScoreBasis,
    RiskPreference,
)
from app.domains.position_decision.score_prediction import (
    MIN_PREDICTION_SAMPLE_COUNT,
    PositionScorePredictor,
    ScorePrediction,
)
from app.domains.positions.schemas import PositionSummary
from app.domains.positions.service import PositionService


BUCKET_TITLES: dict[DecisionBucket, str] = {
    "safe": "保底岗位",
    "balanced": "稳妥岗位",
    "stretch": "冲刺岗位",
}
BUCKET_SUBTITLES: dict[DecisionBucket, str] = {
    "safe": "当前分数接近或覆盖目标分，适合作为低风险保底选择。",
    "balanced": "存在可训练弥补的分差，适合作为主报候选。",
    "stretch": "分差较大或风险较高，只适合作为冲刺选项。",
}


class PositionDecisionService:
    def __init__(self, *, position_service: PositionService) -> None:
        self._position_service = position_service
        self._score_predictor = PositionScorePredictor(
            position_service=position_service
        )

    def recommend_positions(
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
        current_score: float,
        risk_preference: RiskPreference,
        limit: int,
    ) -> PositionDecisionResponse:
        search = self._position_service.search_positions(
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
        items = [
            _decision_item(
                position=position,
                year=year,
                education=education,
                major=major,
                political_status=political_status,
                grassroots_years=grassroots_years,
                current_score=current_score,
                risk_preference=risk_preference,
                score_predictor=self._score_predictor,
            )
            for position in search.items
        ]
        buckets = _bucket_items(items)
        return PositionDecisionResponse(
            generated_at=datetime.now(UTC),
            current_score=current_score,
            risk_preference=risk_preference,
            summary=_summary(buckets, current_score=current_score),
            recommendation_buckets=[
                PositionDecisionBucket(
                    bucket=bucket,
                    title=BUCKET_TITLES[bucket],
                    subtitle=BUCKET_SUBTITLES[bucket],
                    items=buckets[bucket],
                )
                for bucket in ("safe", "balanced", "stretch")
            ],
            unverified_conditions=search.unverified_conditions,
            next_actions=_next_actions(buckets),
        )


def _decision_item(
    *,
    position: PositionSummary,
    year: int,
    education: str,
    major: str,
    political_status: str,
    grassroots_years: int,
    current_score: float,
    risk_preference: RiskPreference,
    score_predictor: PositionScorePredictor,
) -> PositionDecisionItem:
    decision_score_basis = _position_score_basis(position)
    if _needs_regional_prediction(decision_score_basis):
        prediction = score_predictor.predict(
            position=position,
            year=year,
            education=education,
            major=major,
            political_status=political_status,
            grassroots_years=grassroots_years,
        )
        if prediction is not None:
            decision_score_basis = _prediction_score_basis(prediction)
    target_score = (
        decision_score_basis.target_score
        if decision_score_basis is not None
        else None
    )
    score_gap = (
        round(target_score - current_score, 2)
        if target_score is not None
        else None
    )
    bucket = _bucket_for_gap(score_gap, risk_preference=risk_preference)
    risk_tags = _risk_tags(
        position=position,
        score_gap=score_gap,
        decision_score_basis=decision_score_basis,
    )
    risk_level = _risk_level(score_gap=score_gap, risk_tags=risk_tags)
    chance_score = _chance_score(
        position=position,
        score_gap=score_gap,
        risk_level=risk_level,
        decision_score_basis=decision_score_basis,
    )
    return PositionDecisionItem(
        position=position,
        bucket=bucket,
        decision_label=BUCKET_TITLES[bucket],
        decision_target_score=target_score,
        decision_score_basis=decision_score_basis,
        score_gap=score_gap,
        admission_chance_score=chance_score,
        chance_label=_chance_label(chance_score),
        risk_level=risk_level,
        risk_tags=risk_tags,
        reasons=_reasons(
            position=position,
            current_score=current_score,
            score_gap=score_gap,
            decision_score_basis=decision_score_basis,
        ),
        next_action=_next_action(bucket=bucket, score_gap=score_gap),
    )


def _bucket_for_gap(
    score_gap: float | None,
    *,
    risk_preference: RiskPreference,
) -> DecisionBucket:
    if score_gap is None:
        return "balanced"
    safe_threshold, balanced_threshold = {
        "conservative": (-2.0, 6.0),
        "balanced": (3.0, 10.0),
        "aggressive": (6.0, 14.0),
    }[risk_preference]
    if score_gap <= safe_threshold:
        return "safe"
    if score_gap <= balanced_threshold:
        return "balanced"
    return "stretch"


def _risk_tags(
    *,
    position: PositionSummary,
    score_gap: float | None,
    decision_score_basis: PositionDecisionScoreBasis | None,
) -> list[str]:
    tags: list[str] = []
    if decision_score_basis is None:
        tags.append("no_target_score")
    elif decision_score_basis.sample_count < 2:
        tags.append("low_score_sample")
    if decision_score_basis is not None:
        if decision_score_basis.basis_type == "regional_major_prediction":
            tags.append("predicted_score")
        if decision_score_basis.confidence == "low":
            tags.append("low_prediction_sample")
    if position.headcount <= 1:
        tags.append("low_headcount")
    if position.has_extra_exam:
        tags.append("extra_exam")
    if score_gap is not None and score_gap > 10:
        tags.append("high_score_gap")
    elif score_gap is not None and score_gap > 0:
        tags.append("score_gap")
    if _is_broad_condition(position.major_required):
        tags.append("broad_major_condition")
    if _is_broad_condition(position.political_req):
        tags.append("broad_political_condition")
    return tags


def _risk_level(
    *,
    score_gap: float | None,
    risk_tags: list[str],
) -> DecisionRiskLevel:
    if score_gap is None:
        return "unknown"
    high_tags = {"no_target_score", "low_score_sample", "low_headcount", "high_score_gap"}
    if score_gap > 10 or len(high_tags.intersection(risk_tags)) >= 2:
        return "high"
    if score_gap > 0 or high_tags.intersection(risk_tags):
        return "medium"
    return "low"


def _chance_score(
    *,
    position: PositionSummary,
    score_gap: float | None,
    risk_level: DecisionRiskLevel,
    decision_score_basis: PositionDecisionScoreBasis | None,
) -> int:
    if score_gap is None:
        base = 35.0
    else:
        base = 72.0 - score_gap * 3.2
    base += _basis_bonus(decision_score_basis)
    base += min(8, max(0, position.headcount - 1) * 2)
    if position.has_extra_exam:
        base -= 6
    if risk_level == "high":
        base -= 8
    elif risk_level == "unknown":
        base -= 10
    return max(5, min(95, int(round(base))))


def _basis_bonus(basis: PositionDecisionScoreBasis | None) -> float:
    if basis is None:
        return -8.0
    if basis.confidence == "high":
        return 6.0
    if basis.confidence == "medium":
        return 3.0
    if basis.basis_type == "regional_major_prediction":
        return -1.0
    return 1.0


def _chance_label(score: int) -> str:
    if score >= 75:
        return "较高"
    if score >= 55:
        return "中等"
    if score >= 35:
        return "偏低"
    return "较低"


def _reasons(
    *,
    position: PositionSummary,
    current_score: float,
    score_gap: float | None,
    decision_score_basis: PositionDecisionScoreBasis | None,
) -> list[str]:
    target = (
        decision_score_basis.target_score
        if decision_score_basis is not None
        else None
    )
    reasons = []
    if target is None or score_gap is None:
        reasons.append("该岗位缺少足够进面分样本，暂不能可靠估算目标分。")
    else:
        reasons.append(
            f"目标分约 {target:g}，当前估分 {current_score:g}，分差 {score_gap:+g}。"
        )
    if decision_score_basis is not None:
        reasons.append(decision_score_basis.evidence_summary)
    if position.headcount <= 1:
        reasons.append("招录人数为 1，竞争波动风险更高。")
    elif position.headcount >= 3:
        reasons.append(f"招录人数 {position.headcount}，相对单岗更抗波动。")
    if position.has_extra_exam:
        reasons.append("该岗位含专业/加试要求，需要额外确认备考成本。")
    return reasons


def _next_action(*, bucket: DecisionBucket, score_gap: float | None) -> str:
    if bucket == "safe":
        return "加入保底池，并继续寻找 1 个限制条件更强的稳妥岗位。"
    if bucket == "balanced":
        if score_gap is None:
            return "先补充分数样本或改用同系统同地区岗位估算目标分。"
        return f"作为主报候选，围绕 {ceil(max(score_gap, 0))} 分分差安排训练计划。"
    return "只放入冲刺池；报名时必须同时保留稳妥和保底岗位。"


def _position_score_basis(
    position: PositionSummary,
) -> PositionDecisionScoreBasis | None:
    target_score = position.entry_score.target_score
    basis = position.entry_score.target_score_basis
    if target_score is None or basis is None:
        return None
    latest_score_year = (
        max(score.year for score in basis.annual_scores)
        if basis.annual_scores
        else None
    )
    return PositionDecisionScoreBasis(
        basis_type="position_target_score",
        target_score=target_score,
        sample_count=basis.score_sample_count,
        sample_position_count=basis.sample_position_count,
        latest_score_year=latest_score_year,
        average_entry_score=basis.latest_average_score,
        safety_margin=basis.safety_margin,
        confidence=_position_basis_confidence(
            basis_level=basis.basis_level,
            sample_count=basis.score_sample_count,
        ),
        matched_dimensions=_position_basis_dimensions(basis.basis_level),
        evidence_summary=(
            f"岗位分数依据为 {basis.basis_level or 'unknown'}，"
            f"样本 {basis.score_sample_count} 条，安全边际 {basis.safety_margin:g} 分。"
        ),
    )


def _needs_regional_prediction(
    basis: PositionDecisionScoreBasis | None,
) -> bool:
    if basis is None:
        return True
    return (
        basis.basis_type == "position_target_score"
        and basis.sample_count < MIN_PREDICTION_SAMPLE_COUNT
    )


def _prediction_score_basis(
    prediction: ScorePrediction,
) -> PositionDecisionScoreBasis:
    return PositionDecisionScoreBasis(
        basis_type="regional_major_prediction",
        target_score=prediction.target_score,
        sample_count=prediction.sample_count,
        sample_position_count=prediction.sample_position_count,
        latest_score_year=prediction.latest_score_year,
        average_entry_score=prediction.average_entry_score,
        safety_margin=prediction.safety_margin,
        confidence=prediction.confidence,
        matched_dimensions=prediction.matched_dimensions,
        evidence_summary=prediction.evidence_summary,
    )


def _position_basis_confidence(
    *,
    basis_level: str | None,
    sample_count: int,
) -> str:
    if basis_level in {"direct", "L1"} and sample_count >= 2:
        return "high"
    if sample_count >= 8:
        return "high"
    if sample_count >= 3 or basis_level in {"direct", "L1", "L2"}:
        return "medium"
    return "low"


def _position_basis_dimensions(basis_level: str | None) -> list[str]:
    if basis_level in {"direct", "L1"}:
        return ["positionCode"]
    if basis_level == "L2":
        return [
            "system",
            "region",
            "city",
            "education",
            "major",
            "political",
            "serviceYears",
        ]
    if basis_level == "L3":
        return ["system", "region", "city"]
    if basis_level == "L4":
        return ["system", "region"]
    return []


def _bucket_items(
    items: list[PositionDecisionItem],
) -> dict[DecisionBucket, list[PositionDecisionItem]]:
    buckets: dict[DecisionBucket, list[PositionDecisionItem]] = {
        "safe": [],
        "balanced": [],
        "stretch": [],
    }
    for item in sorted(
        items,
        key=lambda i: (
            _bucket_sort_order(i.bucket),
            -i.admission_chance_score,
            i.score_gap if i.score_gap is not None else 999,
        ),
    ):
        if len(buckets[item.bucket]) < 5:
            buckets[item.bucket].append(item)
    return buckets


def _bucket_sort_order(bucket: DecisionBucket) -> int:
    return {"safe": 0, "balanced": 1, "stretch": 2}[bucket]


def _summary(
    buckets: dict[DecisionBucket, list[PositionDecisionItem]],
    *,
    current_score: float,
) -> str:
    safe_count = len(buckets["safe"])
    balanced_count = len(buckets["balanced"])
    stretch_count = len(buckets["stretch"])
    if safe_count and balanced_count:
        return (
            f"当前估分 {current_score:g}，先保底 {safe_count} 个，再从 "
            f"{balanced_count} 个稳妥岗位里选主报，冲刺池保留 {stretch_count} 个。"
        )
    if balanced_count:
        return (
            f"当前估分 {current_score:g}，暂未形成保底池，先从 "
            f"{balanced_count} 个稳妥岗位中选择主报并补 1 个低风险备选。"
        )
    return (
        f"当前估分 {current_score:g}，匹配岗位整体偏冲刺，建议放宽地区或增强限制条件。"
    )


def _next_actions(buckets: dict[DecisionBucket, list[PositionDecisionItem]]) -> list[str]:
    actions = [
        "先收藏 1 个稳妥岗位和 1 个保底岗位，不要只看冲刺岗位。",
        "用最近一次摸底或模考更新当前估分，重新生成岗位组合。",
    ]
    if not buckets["safe"]:
        actions.append("保底池为空时，优先放宽地区或寻找限制条件更多的岗位。")
    if buckets["stretch"]:
        actions.append("冲刺岗位只作为报名备选，必须配套稳妥/保底方案。")
    return actions


def _is_broad_condition(value: str | None) -> bool:
    if value is None:
        return True
    text = value.strip()
    return not text or "不限" in text
