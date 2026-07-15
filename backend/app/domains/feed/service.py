from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.domains.memory.repository import LearnerProfileRecord, LearnerWorkbenchObjectRecord
from app.domains.memory.service import LearnerMemoryService
from app.domains.mock_exam.service import GUOKAO_MOCK_BLUEPRINT
from app.domains.taskstream.schemas import TodayTask, TodayTaskstream
from app.domains.taskstream.service import TaskstreamService

from .schemas import (
    CalibrationNoteCard,
    CalibrationNotePayload,
    CoachBriefCard,
    CoachBriefPayload,
    FeedCard,
    GoalGapCard,
    GoalGapPayload,
    MockExamDueCard,
    MockExamDuePayload,
    MockExamSessionSpec,
    MockReviewPendingCard,
    MockReviewPendingPayload,
    TaskEssayCard,
    TaskEssayPayload,
    TaskMethodCard,
    TaskMethodCardPayload,
    TaskPracticeCard,
    TaskPracticePayload,
    TodayFeed,
)

PLAN_BASIS = "rules_v1"

TASK_CARD_TYPES = {
    "task_practice",
    "task_essay",
    "task_method_card",
    "mock_exam_due",
    "mock_review_pending",
}


class FeedService:
    def __init__(
        self,
        *,
        taskstream_service: TaskstreamService,
        memory_service: LearnerMemoryService,
    ) -> None:
        self._taskstream_service = taskstream_service
        self._memory_service = memory_service
        self._rules: list[Callable[[_FeedContext], list[FeedCard]]] = [
            _mock_review_pending_rule,
            _coach_brief_rule,
            _goal_gap_rule,
            _task_cards_rule,
            _calibration_note_rule,
        ]

    def get_today(self, *, user_id: str) -> TodayFeed:
        profile = self._memory_service.get_required_profile(user_id=user_id)
        taskstream = self._taskstream_service.get_today(user_id=user_id)
        unviewed_mock = self._memory_service.get_unviewed_mock_review(user_id=user_id)
        context = _FeedContext(
            profile=profile,
            taskstream=taskstream,
            completed_task_ids=set(taskstream.completed_task_ids),
            unviewed_mock=unviewed_mock,
        )

        cards: list[FeedCard] = []
        for rule in self._rules:
            cards.extend(rule(context))
        _assert_task_budget_unchanged(taskstream=taskstream, cards=cards)

        return TodayFeed(
            generated_at=datetime.now(UTC).isoformat(),
            plan_basis=PLAN_BASIS,
            cards=sorted(cards, key=lambda card: (-card.priority, card.card_id)),
        )


class _FeedContext:
    def __init__(
        self,
        *,
        profile: LearnerProfileRecord,
        taskstream: TodayTaskstream,
        completed_task_ids: set[str],
        unviewed_mock: LearnerWorkbenchObjectRecord | None,
    ) -> None:
        self.profile = profile
        self.taskstream = taskstream
        self.completed_task_ids = completed_task_ids
        self.unviewed_mock = unviewed_mock


def _mock_review_pending_rule(context: _FeedContext) -> list[FeedCard]:
    task = _first_task_of_kind(context.taskstream, "mock_exam_review")
    if task is None:
        return []
    payload = _mock_review_payload(context=context, task=task)
    if payload is None:
        return []
    return [
        MockReviewPendingCard(
            card_id=f"{context.taskstream.date}:mock-review-pending:{payload.session_id}",
            priority=1000,
            reason=task.reason,
            data_line=task.source_grounding,
            payload=payload,
        )
    ]


def _coach_brief_rule(context: _FeedContext) -> list[FeedCard]:
    return [
        CoachBriefCard(
            card_id=f"{context.taskstream.date}:coach-brief",
            priority=800,
            reason="规则版今日主判断，来自旧 today planner 的 rationale。",
            data_line=context.taskstream.calibration.data_line,
            payload=CoachBriefPayload(
                text=context.taskstream.rationale,
                plan_basis=PLAN_BASIS,
            ),
        )
    ]


def _goal_gap_rule(context: _FeedContext) -> list[FeedCard]:
    target_score = context.profile.target_score
    estimated_score = context.taskstream.calibration.xingce_score
    return [
        GoalGapCard(
            card_id=f"{context.taskstream.date}:goal-gap",
            priority=700,
            reason=_goal_gap_reason(
                target_score=target_score,
                estimated_score=estimated_score,
            ),
            data_line=context.taskstream.calibration.data_line,
            payload=GoalGapPayload(
                target_score=target_score,
                estimated_score=estimated_score,
                days_left=None,
                position_name=context.taskstream.target.position_group or None,
            ),
        )
    ]


def _task_cards_rule(context: _FeedContext) -> list[FeedCard]:
    cards: list[FeedCard] = []
    for index, task in enumerate(context.taskstream.tasks):
        card = _task_to_card(context=context, task=task, index=index)
        if card is not None:
            cards.append(card)
    return cards


def _calibration_note_rule(context: _FeedContext) -> list[FeedCard]:
    calibration = context.taskstream.calibration
    return [
        CalibrationNoteCard(
            card_id=f"{context.taskstream.date}:calibration-note",
            priority=500,
            reason="能力估计状态有单独数据来源标注，供首页透明展示。",
            data_line=calibration.data_line,
            payload=CalibrationNotePayload(
                status_label=calibration.status_label,
                data_line=calibration.data_line,
                delta=None,
            ),
        )
    ]


def _task_to_card(
    *,
    context: _FeedContext,
    task: TodayTask,
    index: int,
) -> FeedCard | None:
    priority = 650 - index
    completed = task.id in context.completed_task_ids
    if task.kind == "xingce_practice":
        return TaskPracticeCard(
            card_id=f"{context.taskstream.date}:task:{task.id}",
            priority=priority,
            reason=task.reason,
            data_line=task.source_grounding,
            payload=TaskPracticePayload(
                task_id=task.id,
                title=task.title,
                subtitle=task.subtitle,
                module_name=_required_module_name(task),
                question_count=len(task.question_ids),
                est_minutes=task.estimated_minutes,
                start_url=task.start_url,
                completed=completed,
            ),
        )
    if task.kind == "essay_rewrite":
        return TaskEssayCard(
            card_id=f"{context.taskstream.date}:task:{task.id}",
            priority=priority,
            reason=task.reason,
            data_line=task.source_grounding,
            payload=TaskEssayPayload(
                task_id=task.id,
                title=task.title,
                subtitle=task.subtitle,
                question_type=_essay_question_type(task),
                est_minutes=task.estimated_minutes,
                start_url=task.start_url,
                completed=completed,
            ),
        )
    if task.kind == "method_card":
        return _method_card_task(context=context, task=task, priority=priority, completed=completed)
    if task.kind == "mock_exam":
        return MockExamDueCard(
            card_id=f"{context.taskstream.date}:task:{task.id}",
            priority=priority,
            reason=task.reason,
            data_line=task.source_grounding,
            payload=MockExamDuePayload(
                session_spec=MockExamSessionSpec(
                    task_id=task.id,
                    title=task.title,
                    start_url=task.start_url,
                    question_count=sum(count for _, count in GUOKAO_MOCK_BLUEPRINT),
                ),
                est_minutes=task.estimated_minutes,
                completed=completed,
            ),
        )
    if task.kind == "mock_exam_review":
        return None
    return None


def _method_card_task(
    *,
    context: _FeedContext,
    task: TodayTask,
    priority: int,
    completed: bool,
) -> FeedCard:
    return TaskMethodCard(
        card_id=f"{context.taskstream.date}:task:{task.id}",
        priority=priority,
        reason=task.reason,
        data_line=task.source_grounding,
        payload=TaskMethodCardPayload(
            task_id=task.id,
            card_id=task.question_ids[0] if task.question_ids else task.id,
            card_title=task.title,
            est_minutes=task.estimated_minutes,
            start_url=task.start_url,
            completed=completed,
        ),
    )


def _mock_review_payload(
    *,
    context: _FeedContext,
    task: TodayTask,
) -> MockReviewPendingPayload | None:
    if context.unviewed_mock is None:
        return None
    raw_session_id = context.unviewed_mock.payload.get("sessionId")
    if not isinstance(raw_session_id, str) or not raw_session_id:
        return None
    return MockReviewPendingPayload(
        task_id=task.id,
        session_id=raw_session_id,
        estimated_score=_optional_float(context.unviewed_mock.payload.get("estimatedScore")),
        review_url=task.start_url,
        completed=task.id in context.completed_task_ids,
    )


def _first_task_of_kind(taskstream: TodayTaskstream, kind: str) -> TodayTask | None:
    return next((task for task in taskstream.tasks if task.kind == kind), None)


def _goal_gap_reason(*, target_score: float | None, estimated_score: float | None) -> str:
    if target_score is None and estimated_score is None:
        return "还没有目标分和能力估计，暂不计算分差。"
    if target_score is None:
        return "还没有目标分，只展示当前能力估计，不计算分差。"
    if estimated_score is None:
        return "能力估计还没有足够数据，暂不计算目标分差。"
    gap = round(target_score - estimated_score, 1)
    if gap > 0:
        return f"当前估计与目标还差 {gap:g} 分。"
    return f"当前估计已达到目标 {abs(gap):g} 分，继续稳住正确率。"


def _required_module_name(task: TodayTask) -> str:
    if not task.module_name:
        raise RuntimeError(f"Task {task.id} is missing moduleName")
    return task.module_name


def _essay_question_type(task: TodayTask) -> str:
    if "概括" in task.title:
        return "概括题"
    return task.track


def _optional_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _assert_task_budget_unchanged(
    *,
    taskstream: TodayTaskstream,
    cards: list[FeedCard],
) -> None:
    old_task_minutes = sum(task.estimated_minutes for task in taskstream.tasks)
    feed_task_minutes = 0
    for card in cards:
        if card.type not in TASK_CARD_TYPES:
            continue
        payload = card.payload
        if isinstance(payload, MockExamDuePayload):
            feed_task_minutes += payload.est_minutes
        elif isinstance(
            payload,
            (
                TaskPracticePayload,
                TaskEssayPayload,
                TaskMethodCardPayload,
            ),
        ):
            feed_task_minutes += payload.est_minutes
        elif isinstance(payload, MockReviewPendingPayload):
            task = _first_task_of_kind(taskstream, "mock_exam_review")
            feed_task_minutes += task.estimated_minutes if task is not None else 0
    if feed_task_minutes != old_task_minutes:
        raise RuntimeError("Feed task cards must preserve taskstream scheduled minutes")
