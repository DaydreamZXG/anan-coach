from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.domains.calibration.schemas import AbilityEstimate
from app.domains.calibration.service import CalibrationService
from app.domains.knowledge.service import KnowledgeService, WeightedPracticeSelection
from app.domains.memory.service import LearnerMemoryService
from app.domains.mock_exam.service import MOCK_DURATION_MINUTES, WEEKLY_MOCK_TASK_ID
from app.domains.study_budget import (
    FULLTIME_DEFAULT_DAILY_MINUTES,
    FULLTIME_MAX_TASK_MINUTES,
    FULLTIME_MIN_TASK_MINUTES,
    PARTTIME_DEFAULT_DAILY_MINUTES,
    PARTTIME_MAX_TASK_MINUTES,
)
from app.domains.taskstream.schemas import (
    PrimaryAction,
    TargetContext,
    TodayAlert,
    TodayCalibration,
    TodayTask,
    TodayTaskstream,
    TomorrowFocus,
    TomorrowPreview,
    TrackMixItem,
)

TODAY_XINGCE_TASK_ID = "today-xingce-guokao-weighted"
TODAY_ESSAY_TASK_ID = "today-shenlun-summary-rewrite"


class TaskstreamService:
    def __init__(
        self,
        *,
        knowledge_service: KnowledgeService,
        memory_service: LearnerMemoryService,
        calibration_service: CalibrationService,
    ) -> None:
        self._knowledge_service = knowledge_service
        self._memory_service = memory_service
        self._calibration_service = calibration_service

    def get_today(self, *, user_id: str) -> TodayTaskstream:
        profile = self._memory_service.get_required_profile(user_id=user_id)
        today = datetime.now(UTC).date()
        completed_task_ids = self._memory_service.list_completed_task_ids_for_date(
            user_id=user_id,
            completed_on=today,
        )
        calibration = self._calibration_service.get_ability_estimate(user_id=user_id)
        budget = _daily_budget(profile.daily_minutes, profile.study_mode)
        score_gap = _score_gap(
            profile.target_score,
            profile.baseline_xingce_score,
            profile.baseline_shenlun_score,
        )
        score_gap_available = (
            profile.target_score is not None
            and profile.baseline_xingce_score is not None
            and profile.baseline_shenlun_score is not None
        )
        unviewed_mock = self._memory_service.get_unviewed_mock_review(user_id=user_id)
        latest_mock_reason = self._memory_service.get_latest_mock_review_reason(user_id=user_id)
        week_start = _week_start(today)
        mock_created_this_week = self._memory_service.has_mock_exam_this_week(
            user_id=user_id,
            week_start=week_start,
        )

        if unviewed_mock is not None:
            tasks = [
                TodayTask(
                    id=f"{WEEKLY_MOCK_TASK_ID}-review",
                    title="查看上次模考复盘",
                    subtitle="复盘未查看前，不把模考任务计入完成。",
                    kind="mock_exam_review",
                    track="行测",
                    estimated_minutes=20,
                    reason=unviewed_mock.payload.get("nextWeekReason")
                    if isinstance(unviewed_mock.payload.get("nextWeekReason"), str)
                    else "模考复盘是下周计划的输入，必须先看完。",
                    source_grounding="learner.mock_exam_sessions.review_payload",
                    question_ids=[],
                    start_url=f"/api/v1/mock-exams/{unviewed_mock.payload['sessionId']}/review",
                )
            ]
            return _today_response(
                today=today,
                profile=profile,
                budget=budget,
                score_gap=score_gap,
                score_gap_available=score_gap_available,
                track_mix=[
                    TrackMixItem(
                        track="行测",
                        minutes=20,
                        reason="先完成模考复盘，planner 才能引用最新校准结果。",
                    )
                ],
                tasks=tasks,
                completed_task_ids=completed_task_ids,
                calibration=calibration,
                alerts=[
                    TodayAlert(
                        id="mock-review-unviewed",
                        severity="required",
                        title="模考复盘待查看",
                        subtitle="查看后才算完成本周模考闭环。",
                        action_url=f"/api/v1/mock-exams/{unviewed_mock.payload['sessionId']}/review",
                    )
                ],
                rationale="最近一次模考已生成复盘，今天先看复盘再继续训练。",
            )

        if not mock_created_this_week:
            tasks = [
                TodayTask(
                    id=WEEKLY_MOCK_TASK_ID,
                    title="国考行测 135 题整卷模考",
                    subtitle="严格 120 分钟，中途不反馈，交卷后生成复盘。",
                    kind="mock_exam",
                    track="行测",
                    estimated_minutes=MOCK_DURATION_MINUTES,
                    reason="每周模考用于高置信校准，不给全国分位，只看自己的趋势。",
                    source_grounding="product.questions + product.knowledge_map_nodes",
                    question_ids=[],
                    start_url="/api/v1/mock-exams/weekly",
                )
            ]
            return _today_response(
                today=today,
                profile=profile,
                budget=budget,
                score_gap=score_gap,
                score_gap_available=score_gap_available,
                track_mix=[
                    TrackMixItem(
                        track="行测",
                        minutes=MOCK_DURATION_MINUTES,
                        reason="本周还没有整卷模考记录，先用模考校准能力估计。",
                    )
                ],
                tasks=tasks,
                completed_task_ids=completed_task_ids,
                calibration=calibration,
                alerts=[],
                rationale=f"{calibration.data_line}，本周先补一条高置信模考校准记录。",
            )

        practice_selection = self._knowledge_service.select_weighted_xingce_practice(
            exam_scope="guokao",
            limit=3,
            module_name=_daily_rotation_module(today, calibration),
        )
        question_ids = [question.question_id for question in practice_selection.items]
        xingce_minutes = _xingce_minutes(budget.daily_minutes, budget.study_mode)
        headline = _headline(budget.daily_minutes, budget.study_mode, task_count=1)
        is_weekend = today.weekday() >= 5

        return TodayTaskstream(
            date=today.isoformat(),
            headline=headline,
            plan_basis="rules_v1",
            target=TargetContext(
                exam=profile.target_exam or "",
                position_group=profile.position_group or "",
                score_gap=score_gap,
                score_gap_available=score_gap_available,
            ),
            track_mix=[
                TrackMixItem(
                    track="行测",
                    minutes=xingce_minutes,
                    reason=_xingce_reason(
                        profile.baseline_source,
                        profile.baseline_xingce_score,
                        practice_selection,
                    ),
                ),
            ],
            primary_action=PrimaryAction(
                title="开始今日学习",
                subtitle="旧规则流仅保留行测兼容任务；申论由 v2 证据规划统一安排。",
            ),
            rationale=_rationale(
                budget.study_mode,
                profile.baseline_source,
                calibration,
                latest_mock_reason,
                is_weekend=is_weekend,
            ),
            calibration=_calibration_summary(calibration),
            alerts=[
                TodayAlert(
                    id="essay-v2-planning-owner",
                    severity="required",
                    title="申论任务已迁移至 v2 学习大脑",
                    subtitle="旧 taskstream 不再生成无评分规程、无版本证据的 AI 评阅或重写任务。",
                    action_url="/api/v2/plans/today",
                )
            ],
            tasks=[
                TodayTask(
                    id=TODAY_XINGCE_TASK_ID,
                    title=f"国考{practice_selection.module_name} 3 题",
                    subtitle=f"今天先做「{practice_selection.tag_value}」，做完立刻看来源和解析。",
                    kind="xingce_practice",
                    track="行测",
                    module_name=practice_selection.module_name,
                    estimated_minutes=xingce_minutes,
                    reason=practice_selection.basis_sentence,
                    source_grounding="product.knowledge_map_nodes + product.questions",
                    question_ids=question_ids,
                    start_url=f"/api/v1/practice/tasks/{TODAY_XINGCE_TASK_ID}",
                ),
            ],
            completed_task_ids=completed_task_ids,
        )

    def get_tomorrow(self, *, user_id: str) -> TomorrowPreview:
        """明日政务预览：不抽题、不落库，只给焦点模块与时长，作会话结束钩子。"""
        profile = self._memory_service.get_required_profile(user_id=user_id)
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        calibration = self._calibration_service.get_ability_estimate(user_id=user_id)
        budget = _daily_budget(profile.daily_minutes, profile.study_mode)
        score_gap = _score_gap(
            profile.target_score,
            profile.baseline_xingce_score,
            profile.baseline_shenlun_score,
        )
        score_gap_available = (
            profile.target_score is not None
            and profile.baseline_xingce_score is not None
            and profile.baseline_shenlun_score is not None
        )
        xingce_minutes = _xingce_minutes(budget.daily_minutes, budget.study_mode)
        essay_minutes = _essay_minutes(budget.daily_minutes, budget.study_mode, xingce_minutes)
        focus_module, focus_reason = _tomorrow_focus(calibration)
        if score_gap_available:
            headline = f"明日继续理政，距上岸还差 {score_gap:g} 分"
        else:
            headline = "明日继续理政，先定目标分"
        return TomorrowPreview(
            date=tomorrow.isoformat(),
            headline=headline,
            target=TargetContext(
                exam=profile.target_exam or "",
                position_group=profile.position_group or "",
                score_gap=score_gap,
                score_gap_available=score_gap_available,
            ),
            track_mix=[
                TrackMixItem(track="行测", minutes=xingce_minutes, reason=focus_reason),
                TrackMixItem(
                    track="申论",
                    minutes=essay_minutes,
                    reason=_essay_reason(profile.baseline_source, profile.baseline_shenlun_score),
                ),
            ],
            focus=TomorrowFocus(
                module=focus_module,
                question_count=3,
                reason=focus_reason,
            ),
            primary_action=PrimaryAction(
                title="明日政务已备好",
                subtitle="完成今日即可解锁，预览以明日实际为准。",
            ),
        )


def _tomorrow_focus(calibration: AbilityEstimate) -> tuple[str, str]:
    candidates = [
        m for m in calibration.module_estimates
        if m.sample_size and m.sample_size >= 10
    ]
    if candidates:
        weakest = min(candidates, key=lambda m: m.accuracy)
        raw = weakest.accuracy
        pct = round(raw * 100) if raw <= 1 else round(raw)
        return weakest.module_name, f"你{weakest.module_name}胜率约 {pct}%，明日主攻补弱。"
    return "行测高频", "继续近5年国考高频练习，先把基线坐实。"


def _today_response(
    *,
    today: date,
    profile: object,
    budget: _Budget,
    score_gap: float,
    score_gap_available: bool,
    track_mix: list[TrackMixItem],
    tasks: list[TodayTask],
    completed_task_ids: list[str],
    calibration: AbilityEstimate,
    alerts: list[TodayAlert],
    rationale: str,
) -> TodayTaskstream:
    return TodayTaskstream(
        date=today.isoformat(),
        headline=_headline(budget.daily_minutes, budget.study_mode, task_count=len(tasks)),
        plan_basis="rules_v1",
        target=TargetContext(
            exam=getattr(profile, "target_exam") or "",
            position_group=getattr(profile, "position_group") or "",
            score_gap=score_gap,
            score_gap_available=score_gap_available,
        ),
        track_mix=track_mix,
        primary_action=PrimaryAction(
            title="开始今日学习",
            subtitle="按顺序完成，做完我会重排明天。",
        ),
        rationale=rationale,
        calibration=_calibration_summary(calibration),
        alerts=alerts,
        tasks=tasks,
        completed_task_ids=completed_task_ids,
    )


class _Budget:
    def __init__(self, *, daily_minutes: int, study_mode: str) -> None:
        self.daily_minutes = daily_minutes
        self.study_mode = study_mode


def _daily_budget(daily_minutes: int | None, study_mode: str | None) -> _Budget:
    if study_mode == "fulltime":
        return _Budget(
            daily_minutes=daily_minutes or FULLTIME_DEFAULT_DAILY_MINUTES,
            study_mode="fulltime",
        )
    if study_mode == "parttime":
        return _Budget(
            daily_minutes=daily_minutes or PARTTIME_DEFAULT_DAILY_MINUTES,
            study_mode="parttime",
        )
    return _Budget(
        daily_minutes=daily_minutes or PARTTIME_DEFAULT_DAILY_MINUTES,
        study_mode="parttime",
    )


def _xingce_minutes(daily_minutes: int, study_mode: str) -> int:
    if study_mode == "parttime":
        return min(PARTTIME_MAX_TASK_MINUTES, max(10, round(daily_minutes * 0.20)))
    return min(
        daily_minutes,
        min(
            FULLTIME_MAX_TASK_MINUTES,
            max(FULLTIME_MIN_TASK_MINUTES, round(daily_minutes * 0.18)),
        ),
    )


def _essay_minutes(daily_minutes: int, study_mode: str, xingce_minutes: int) -> int:
    remaining = max(0, daily_minutes - xingce_minutes)
    if remaining == 0:
        return 0
    if study_mode == "parttime":
        return min(PARTTIME_MAX_TASK_MINUTES, max(10, remaining))
    return min(
        remaining,
        min(
            FULLTIME_MAX_TASK_MINUTES,
            max(FULLTIME_MIN_TASK_MINUTES, round(daily_minutes * 0.16)),
        ),
    )


def _score_gap(
    target_score: float | None,
    baseline_xingce_score: float | None,
    baseline_shenlun_score: float | None,
) -> float:
    if (
        target_score is None
        or baseline_xingce_score is None
        or baseline_shenlun_score is None
    ):
        return 0.0
    return round(target_score - (baseline_xingce_score + baseline_shenlun_score), 1)


def _headline(daily_minutes: int, study_mode: str, *, task_count: int) -> str:
    duration = _format_minutes_for_headline(daily_minutes)
    if study_mode == "fulltime":
        return f"今天 {duration}，按上午/下午/晚上三段先完成 {task_count} 个任务"
    return f"今天 {duration}，先完成 {task_count} 个任务"


def _format_minutes_for_headline(minutes: int) -> str:
    if minutes >= 60:
        hours = minutes / 60
        return f"{hours:g} 小时"
    return f"{minutes} 分钟"


def _xingce_reason(
    baseline_source: str | None,
    baseline_score: float | None,
    selection: WeightedPracticeSelection,
) -> str:
    if baseline_source == "self_report" and baseline_score is not None:
        return (
            f"基于你自报行测 {baseline_score:g} 分，"
            f"先练近5年国考高频「{selection.label}」。"
        )
    if baseline_source == "placement_test" and baseline_score is not None:
        return (
            f"基于摸底行测约 {baseline_score:g} 分，"
            f"先练近5年国考高频「{selection.label}」。"
        )
    return f"还没有你的行测基线，先按近5年国考频次练「{selection.label}」。"


def _essay_reason(baseline_source: str | None, baseline_score: float | None) -> str:
    if baseline_source == "self_report" and baseline_score is not None:
        return f"基于你自报申论 {baseline_score:g} 分，保留申论小题练习。"
    return "申论和行测同级；没有申论基线时，先做完整材料题的概括练习。"


def _calibration_summary(calibration: AbilityEstimate) -> TodayCalibration:
    return TodayCalibration(
        xingce_score=calibration.xingce_score,
        confidence=calibration.confidence,
        status_label=calibration.status_label,
        data_line=calibration.data_line,
    )


def _week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


def _rationale(
    study_mode: str,
    baseline_source: str | None,
    calibration: AbilityEstimate,
    latest_mock_reason: str | None,
    *,
    is_weekend: bool = False,
) -> str:
    source = {
        "self_report": "基于你的自报基线",
        "placement_test": "基于你的摸底结果",
    }.get(baseline_source or "", "还没有你的个人数据")
    calibration_line = f"{calibration.data_line}，{calibration.status_label}"
    if latest_mock_reason:
        calibration_line = f"{calibration_line}；{latest_mock_reason}"
    if is_weekend:
        return f"{source}，{calibration_line}，周末主攻弱项，把本周攒的短板补回来。"
    if study_mode == "fulltime":
        return f"{source}，{calibration_line}，今天用整块训练推进。"
    return f"{source}，{calibration_line}，今天只排短任务，先适配碎片时间。"


# MARK: - 每日轮换（按星期 + 弱点选行测模块，周末主攻最弱项）

_XINGCE_ROTATION = ["常识判断", "言语理解与表达", "数量关系", "判断推理", "资料分析"]


def _accuracy_pct(raw: float) -> float:
    """calibration 的 accuracy 可能是 0-1 或 0-100，统一归一到 0-1。"""
    return raw if raw <= 1 else raw / 100


def _daily_rotation_module(today: date, calibration: AbilityEstimate) -> str | None:
    """按星期轮换行测模块；周末主攻最弱项。返回 None 表示交由默认高频节点选择。

    - 周一~周五：依次轮换五大模块，保证每周各模块都练到。
    - 周六/周日：主攻校准出的最弱模块（样本≥10 且胜率<50%）；无弱项则回退 None。
    """
    weekday = today.weekday()  # 0=Mon … 6=Sun
    weak_modules = [
        m.module_name
        for m in calibration.module_estimates
        if (m.sample_size or 0) >= 10 and _accuracy_pct(m.accuracy) < 0.5
    ]
    if weekday >= 5:
        return weak_modules[0] if weak_modules else None
    return _XINGCE_ROTATION[weekday]
