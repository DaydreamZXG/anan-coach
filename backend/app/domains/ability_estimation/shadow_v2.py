"""能力估计 v2 影子算法（ability-estimate-v2-shadow-1）。

铁律：
- v1 用户可见输出一字不改。本模块只读 v1 数据，唯一的写入目标是
  learning.ability_estimate_v2_shadow（append-only），不发 outbox 事件、
  不接任何小程序 API、不接调度器。
- 样本不足（每层 < MIN_SAMPLE_SIZE）时 theta/SE/置信区间显式缺省（null），
  不硬给数；EAP 先验本身也保证小样本时置信区间自然变宽。

算法概要：
1. 题目难度 b = logit(1 - 全国正确率)。全国正确率来自
   product.question_occurrences.occurrence_correct_ratio；历史题库中的
   0~100 百分数会在读取时归一化为 0~1。同一题多次出现时按
   occurrence_sample_size 做样本加权平均（缺样本量的出现按权重 1 计）。
   没有任何正确率数据的题目不参与 v2（显式排除，留痕在 strata 里）。
2. 用户水平 θ 用 Rasch 单参数模型估计，蒙猜校正采用 4 选 1 下界 c = 0.25 的
   3PL 简化：P(答对) = c + (1 - c) * sigmoid(θ - b)。
   实现选择 EAP（期望后验，N(0,1) 先验 + 数值网格积分）而不是 Newton MLE：
   全对/全错样本下 MLE 发散到 ±∞，EAP 始终有限，且小样本时后验标准差
   自然变宽，正好符合「样本不足不硬给数」的铁律。后验标准差即 SE，
   置信区间取 θ ± 1.96·SE。
3. 限时分层：作答用时（检查点 elapsedSeconds / 采集契约 v1 的毫秒时钟）
   超过单题建议用时（advisorySeconds）的作答归入 untimed 层，未超时归入
   timed 层；没有计时证据的作答无法证明限时环境，保守归入 untimed 层。
   θ_timed 与 θ_untimed 分开报。模考作答视为最高置信限时环境：进 timed 层
   且似然权重 ×1.5。
4. 与 v1 分数的换算先用线性锚定：θ=0 对应 v1 行测均值口径（占位常数），
   斜率为占位常数；两个常数都必须在首届内测拿到 v1/v2 配对数据后重新标定
   （历史依据见 docs/product/_archive/2026-07-27-cognitive-science-mapping.md 欠账清单）。
5. 对比留痕：每次计算同步快照 v1 当前行测分数区间并落 divergence 字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from math import exp, log, sqrt
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


ALGORITHM_VERSION_V2_SHADOW = "ability-estimate-v2-shadow-1"

# 4 选 1 蒙猜下界：θ → -∞ 时答对概率不会低于 0.25。
GUESS_FLOOR = 0.25
# 每层最小样本量口径，与 calibration 域知识点榜单的门槛保持一致（10）。
MIN_SAMPLE_SIZE = 10
# 全国正确率进 logit 前的截断，防止 0/1 正确率产生无穷难度。
RATIO_CLAMP_LOW = 0.02
RATIO_CLAMP_HIGH = 0.98
# 模考作答 = 最高置信限时环境，似然权重 ×1.5。
MOCK_ANSWER_WEIGHT = 1.5
# EAP 数值积分网格与先验（标准正态）。θ 截断在 ±4 个 logit。
THETA_GRID_MIN = -4.0
THETA_GRID_MAX = 4.0
THETA_GRID_STEP = 0.05
PRIOR_SD = 1.0
CONFIDENCE_Z = 1.96
# 线性锚定占位：θ=0 映射到 v1 行测分数口径的均值近似，斜率为每 logit 的
# 分数增量。两个常数均为占位值，待首届内测的 v1/v2 配对样本标定后替换。
V1_XINGCE_MEAN_ANCHOR = 55.0
V1_POINTS_PER_THETA = 10.0


class ShadowEstimateContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShadowObservation:
    """一条参与 v2 估计的作答观测。"""

    question_id: str
    correct: bool
    difficulty_b: float
    layer: str  # 'timed' | 'untimed'
    weight: float  # 练习 1.0，模考 MOCK_ANSWER_WEIGHT
    source: str  # 'practice' | 'mock_exam'


@dataclass(frozen=True)
class LayerEstimate:
    sample_size: int
    insufficient: bool
    theta: float | None
    theta_se: float | None
    ci_lower: float | None
    ci_upper: float | None


def difficulty_from_correct_ratio(correct_ratio: float) -> float:
    """难度锚定：b = logit(1 - 全国正确率)。

    正确率越高题越容易、b 越负；正确率 0.5 时 b = 0。
    截断到 [RATIO_CLAMP_LOW, RATIO_CLAMP_HIGH] 防 logit 发散。
    """
    ratio = min(max(float(correct_ratio), RATIO_CLAMP_LOW), RATIO_CLAMP_HIGH)
    return log((1.0 - ratio) / ratio)


def aggregate_question_difficulty(
    occurrence_rows: list[tuple[float | None, int | None]],
) -> float | None:
    """多次出现的题目按样本量加权平均正确率后再锚定难度。

    - 只有带 occurrence_correct_ratio 的出现参与；
    - occurrence_sample_size 缺失的出现按权重 1 计（无法声称更多样本）；
    - 一条可用数据都没有时返回 None：该题不参与 v2，不硬给数。
    """
    weighted_sum = 0.0
    weight_total = 0.0
    for ratio, sample_size in occurrence_rows:
        if ratio is None:
            continue
        normalized_ratio = _normalize_stored_correct_ratio(float(ratio))
        if normalized_ratio is None:
            continue
        weight = float(sample_size) if sample_size is not None and sample_size > 0 else 1.0
        weighted_sum += normalized_ratio * weight
        weight_total += weight
    if weight_total <= 0:
        return None
    return difficulty_from_correct_ratio(weighted_sum / weight_total)


def _normalize_stored_correct_ratio(value: float) -> float | None:
    if 0.0 <= value <= 1.0:
        return value
    if 1.0 < value <= 100.0:
        return value / 100.0
    return None


def answer_probability(theta: float, difficulty_b: float) -> float:
    """3PL 简化：P = c + (1-c)·σ(θ-b)，c 为 4 选 1 蒙猜下界。"""
    sigmoid = 1.0 / (1.0 + exp(-(theta - difficulty_b)))
    return GUESS_FLOOR + (1.0 - GUESS_FLOOR) * sigmoid


def classify_timing_layer(timing_evidence: Mapping[str, Any] | None) -> str:
    """按计时证据把一次作答归入 'timed' 或 'untimed' 层。

    数据来源是 learning.xingce_attempt_records.timing_evidence（提交时由
    检查点 elapsedSeconds 冻结；采集契约 v1 的毫秒时钟若在位则以
    questionElapsedMs 键出现）。规则：
    - 已有结论字段 timingStatus 直接采用（within_advisory / advisory_exceeded）；
    - 否则用 questionElapsedMs（优先，毫秒）或 questionElapsedSeconds 对比
      advisorySeconds；
    - 没有计时证据或证据不完整时无法证明限时环境，保守归入 untimed。
    """
    if timing_evidence is None:
        return "untimed"
    status = timing_evidence.get("timingStatus")
    if status == "within_advisory":
        return "timed"
    if status == "advisory_exceeded":
        return "untimed"
    advisory = timing_evidence.get("advisorySeconds")
    elapsed_ms = timing_evidence.get("questionElapsedMs")
    if elapsed_ms is not None:
        elapsed: float | None = float(elapsed_ms) / 1000.0
    else:
        raw_seconds = timing_evidence.get("questionElapsedSeconds")
        elapsed = float(raw_seconds) if raw_seconds is not None else None
    if advisory is None or elapsed is None:
        return "untimed"
    return "timed" if elapsed <= float(advisory) else "untimed"


def estimate_theta_eap(
    observations: list[ShadowObservation],
) -> tuple[float, float] | None:
    """EAP 估计 θ 与后验标准差（SE）。

    后验 ∝ N(0, PRIOR_SD²) × Π P^(w·x) (1-P)^(w·(1-x))，
    在 [-4, 4] 网格上数值积分。模考权重直接乘在对数似然上。
    观测为空时返回 None（由调用方落缺省）。
    """
    if not observations:
        return None
    grid: list[float] = []
    value = THETA_GRID_MIN
    while value <= THETA_GRID_MAX + 1e-9:
        grid.append(round(value, 6))
        value += THETA_GRID_STEP
    log_posteriors: list[float] = []
    for theta in grid:
        log_posterior = -0.5 * (theta / PRIOR_SD) ** 2
        for item in observations:
            probability = answer_probability(theta, item.difficulty_b)
            probability = min(max(probability, 1e-9), 1.0 - 1e-9)
            log_likelihood = log(probability) if item.correct else log(1.0 - probability)
            log_posterior += item.weight * log_likelihood
        log_posteriors.append(log_posterior)
    peak = max(log_posteriors)
    densities = [exp(item - peak) for item in log_posteriors]
    normalizer = sum(densities)
    mean = sum(theta * density for theta, density in zip(grid, densities)) / normalizer
    variance = (
        sum((theta - mean) ** 2 * density for theta, density in zip(grid, densities))
        / normalizer
    )
    return mean, sqrt(variance)


def map_theta_to_v1_score(theta: float) -> float:
    """线性锚定换算（占位，待内测标定）：θ=0 ↔ v1 均值口径。"""
    raw = V1_XINGCE_MEAN_ANCHOR + V1_POINTS_PER_THETA * theta
    return round(min(100.0, max(0.0, raw)), 2)


class AbilityEstimateV2ShadowService:
    """影子估计服务：内部触发（运维入口），不接调度器。"""

    def recompute_for_user(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """重算指定用户的 v2 影子估计并追加一行留痕。

        返回落库行的映射（供运维排查），不对外暴露任何 API。
        """
        computed_at = _as_utc(now or datetime.now(UTC))
        owner = session.execute(
            text("select user_id from learner.users where user_id = cast(:user_id as uuid)"),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if owner is None:
            raise ShadowEstimateContractError("Learner does not exist")

        practice_rows = self._practice_rows(session, user_id=user_id)
        mock_rows = self._mock_rows(session, user_id=user_id)
        question_ids = sorted(
            {str(row["question_id"]) for row in practice_rows}
            | {str(row["question_id"]) for row in mock_rows}
        )
        difficulties = self._question_difficulties(session, question_ids=question_ids)

        observations: list[ShadowObservation] = []
        excluded_no_difficulty = 0
        for row in practice_rows:
            question_id = str(row["question_id"])
            difficulty = difficulties.get(question_id)
            if difficulty is None:
                excluded_no_difficulty += 1
                continue
            timing = row["timing_evidence"]
            observations.append(
                ShadowObservation(
                    question_id=question_id,
                    correct=bool(row["correct"]),
                    difficulty_b=difficulty,
                    layer=classify_timing_layer(
                        dict(timing) if timing is not None else None
                    ),
                    weight=1.0,
                    source="practice",
                )
            )
        for row in mock_rows:
            question_id = str(row["question_id"])
            difficulty = difficulties.get(question_id)
            if difficulty is None:
                excluded_no_difficulty += 1
                continue
            observations.append(
                ShadowObservation(
                    question_id=question_id,
                    correct=str(row["selected_answer"]) == str(row["correct_answer"]),
                    difficulty_b=difficulty,
                    # 模考 = 最高置信限时环境。
                    layer="timed",
                    weight=MOCK_ANSWER_WEIGHT,
                    source="mock_exam",
                )
            )

        overall = _layer_estimate(observations)
        timed = _layer_estimate([item for item in observations if item.layer == "timed"])
        untimed = _layer_estimate(
            [item for item in observations if item.layer == "untimed"]
        )

        v1_snapshot = self._v1_current_snapshot(session, user_id=user_id)
        mapped_score = (
            map_theta_to_v1_score(overall.theta) if overall.theta is not None else None
        )
        divergence = _divergence(mapped_score=mapped_score, v1_snapshot=v1_snapshot)

        strata = {
            "timed": _layer_payload(
                timed,
                mock_answer_count=sum(
                    1 for item in observations
                    if item.layer == "timed" and item.source == "mock_exam"
                ),
            ),
            "untimed": _layer_payload(untimed, mock_answer_count=0),
            "policy": {
                "minSampleSize": MIN_SAMPLE_SIZE,
                "mockAnswerWeight": MOCK_ANSWER_WEIGHT,
                "guessFloor": GUESS_FLOOR,
                "timingRule": (
                    "advisory_exceeded 或无计时证据归 untimed；"
                    "within_advisory 与模考作答归 timed"
                ),
                "excludedNoDifficulty": excluded_no_difficulty,
            },
        }

        row = session.execute(
            text(
                """
                insert into learning.ability_estimate_v2_shadow (
                  shadow_estimate_id, user_id, subject,
                  theta, theta_se, ci_lower, ci_upper,
                  sample_size, insufficient_sample, strata,
                  mapped_v1_xingce_score, v1_snapshot, divergence,
                  algorithm_version, computed_at
                ) values (
                  cast(:shadow_estimate_id as uuid), cast(:user_id as uuid), 'xingce',
                  :theta, :theta_se, :ci_lower, :ci_upper,
                  :sample_size, :insufficient_sample, cast(:strata as jsonb),
                  :mapped_v1_xingce_score, cast(:v1_snapshot as jsonb),
                  cast(:divergence as jsonb),
                  :algorithm_version, :computed_at
                )
                returning *
                """
            ),
            {
                "shadow_estimate_id": str(uuid4()),
                "user_id": user_id,
                "theta": overall.theta,
                "theta_se": overall.theta_se,
                "ci_lower": overall.ci_lower,
                "ci_upper": overall.ci_upper,
                "sample_size": overall.sample_size,
                "insufficient_sample": overall.insufficient,
                "strata": json.dumps(strata, ensure_ascii=False, sort_keys=True),
                "mapped_v1_xingce_score": mapped_score,
                "v1_snapshot": (
                    json.dumps(v1_snapshot, ensure_ascii=False, sort_keys=True)
                    if v1_snapshot is not None
                    else None
                ),
                "divergence": json.dumps(divergence, ensure_ascii=False, sort_keys=True),
                "algorithm_version": ALGORITHM_VERSION_V2_SHADOW,
                "computed_at": computed_at,
            },
        ).mappings().one()
        return dict(row)

    def _practice_rows(self, session: Session, *, user_id: str) -> list[Mapping[str, Any]]:
        """练习作答 × 最近一次冻结的计时证据（可能为 null）。"""
        return list(
            session.execute(
                text(
                    """
                    select
                      pa.question_id,
                      pa.correct,
                      timing.timing_evidence
                    from learner.practice_answers pa
                    left join lateral (
                      select record.timing_evidence
                      from learning.xingce_attempt_records record
                      where record.user_id = pa.user_id
                        and record.question_id = pa.question_id
                      order by record.created_at desc
                      limit 1
                    ) timing on true
                    where pa.user_id = cast(:user_id as uuid)
                    order by pa.answered_at, pa.answer_id
                    """
                ),
                {"user_id": user_id},
            ).mappings().all()
        )

    def _mock_rows(self, session: Session, *, user_id: str) -> list[Mapping[str, Any]]:
        """已提交模考的逐题作答；正确性对照 product.questions.answer 的
        规范化口径（与 practice 域 submit_answer 的判定一致）。"""
        return list(
            session.execute(
                text(
                    """
                    select
                      answer_item ->> 'questionId' as question_id,
                      upper(trim(answer_item ->> 'selectedAnswer')) as selected_answer,
                      upper(trim(q.answer)) as correct_answer
                    from learner.mock_exam_sessions mock_session
                    cross join lateral jsonb_array_elements(mock_session.answers)
                      as answer_item
                    join product.questions q
                      on q.question_id = answer_item ->> 'questionId'
                    where mock_session.user_id = cast(:user_id as uuid)
                      and mock_session.status in ('submitted', 'reviewed')
                      and mock_session.answers is not null
                      and nullif(trim(answer_item ->> 'selectedAnswer'), '') is not null
                      and nullif(trim(q.answer), '') is not null
                    order by mock_session.submitted_at nulls last,
                             mock_session.created_at,
                             answer_item ->> 'questionId'
                    """
                ),
                {"user_id": user_id},
            ).mappings().all()
        )

    def _question_difficulties(
        self,
        session: Session,
        *,
        question_ids: list[str],
    ) -> dict[str, float]:
        if not question_ids:
            return {}
        rows = session.execute(
            text(
                """
                select question_id, occurrence_correct_ratio, occurrence_sample_size
                from product.question_occurrences
                where question_id = any(cast(:question_ids as text[]))
                  and occurrence_correct_ratio is not null
                """
            ),
            {"question_ids": question_ids},
        ).mappings().all()
        grouped: dict[str, list[tuple[float | None, int | None]]] = {}
        for row in rows:
            grouped.setdefault(str(row["question_id"]), []).append(
                (
                    float(row["occurrence_correct_ratio"]),
                    (
                        int(row["occurrence_sample_size"])
                        if row["occurrence_sample_size"] is not None
                        else None
                    ),
                )
            )
        difficulties: dict[str, float] = {}
        for question_id, occurrence_rows in grouped.items():
            difficulty = aggregate_question_difficulty(occurrence_rows)
            if difficulty is not None:
                difficulties[question_id] = difficulty
        return difficulties

    def _v1_current_snapshot(
        self,
        session: Session,
        *,
        user_id: str,
    ) -> dict[str, Any] | None:
        """只读快照 v1 当前分数区间（learning.ability_estimate_versions），
        用于 divergence 留痕；v1 数据与行为不做任何改动。"""
        row = session.execute(
            text(
                """
                select estimate_version_id::text, exam_code, exam_year,
                       xingce_lower, xingce_recommended, xingce_upper,
                       confidence, algorithm_version, generated_at
                from learning.ability_estimate_versions versions
                where user_id = cast(:user_id as uuid)
                  and not exists (
                    select 1
                    from learning.ability_estimate_versions successor
                    where successor.supersedes_estimate_version_id =
                          versions.estimate_version_id
                  )
                order by generated_at desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        return {
            "estimateVersionId": row["estimate_version_id"],
            "examCode": row["exam_code"],
            "examYear": int(row["exam_year"]),
            "xingceLower": _optional_float(row["xingce_lower"]),
            "xingceRecommended": _optional_float(row["xingce_recommended"]),
            "xingceUpper": _optional_float(row["xingce_upper"]),
            "confidence": row["confidence"],
            "algorithmVersion": row["algorithm_version"],
            "generatedAt": row["generated_at"].isoformat(),
        }


def recompute_user_shadow_estimate(
    session: Session,
    *,
    user_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """运维入口：内部触发重算指定用户的 v2 影子估计。

    对照现有域的 service 模式提供的模块级触发函数；不接调度器、
    不注册路由。运维需要时在受控环境（如管理脚本 / REPL）里调用。
    """
    return AbilityEstimateV2ShadowService().recompute_for_user(
        session, user_id=user_id, now=now
    )


def _layer_estimate(observations: list[ShadowObservation]) -> LayerEstimate:
    sample_size = len(observations)
    if sample_size < MIN_SAMPLE_SIZE:
        # 样本不足：显式缺省，不硬给数。
        return LayerEstimate(
            sample_size=sample_size,
            insufficient=True,
            theta=None,
            theta_se=None,
            ci_lower=None,
            ci_upper=None,
        )
    estimated = estimate_theta_eap(observations)
    assert estimated is not None  # sample_size >= MIN_SAMPLE_SIZE > 0
    theta, standard_error = estimated
    return LayerEstimate(
        sample_size=sample_size,
        insufficient=False,
        theta=round(theta, 4),
        theta_se=round(standard_error, 4),
        ci_lower=round(theta - CONFIDENCE_Z * standard_error, 4),
        ci_upper=round(theta + CONFIDENCE_Z * standard_error, 4),
    )


def _layer_payload(layer: LayerEstimate, *, mock_answer_count: int) -> dict[str, Any]:
    return {
        "sampleSize": layer.sample_size,
        "insufficientSample": layer.insufficient,
        "theta": layer.theta,
        "thetaSe": layer.theta_se,
        "ciLower": layer.ci_lower,
        "ciUpper": layer.ci_upper,
        "mockAnswerCount": mock_answer_count,
    }


def _divergence(
    *,
    mapped_score: float | None,
    v1_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    v1_recommended = (
        v1_snapshot.get("xingceRecommended") if v1_snapshot is not None else None
    )
    v1_lower = v1_snapshot.get("xingceLower") if v1_snapshot is not None else None
    v1_upper = v1_snapshot.get("xingceUpper") if v1_snapshot is not None else None
    if v1_snapshot is None or v1_recommended is None:
        basis = "v1_unavailable"
    else:
        basis = "score_evidence_interval"
    delta = (
        round(mapped_score - v1_recommended, 2)
        if mapped_score is not None and v1_recommended is not None
        else None
    )
    outside = (
        (mapped_score < v1_lower or mapped_score > v1_upper)
        if mapped_score is not None and v1_lower is not None and v1_upper is not None
        else None
    )
    return {
        "basis": basis,
        "mappedV2XingceScore": mapped_score,
        "v1Recommended": v1_recommended,
        "delta": delta,
        "outsideV1Range": outside,
    }


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
