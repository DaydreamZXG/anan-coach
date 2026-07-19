from __future__ import annotations

from app.domains.knowledge.service import KnowledgeService
from app.domains.memory.service import LearnerMemoryService
from app.domains.practice.schemas import AnswerFeedback, PracticeTask
from app.domains.taskstream.service import TODAY_XINGCE_TASK_ID


class PracticeTaskNotFoundError(LookupError):
    pass


class PracticeService:
    def __init__(
        self,
        *,
        knowledge_service: KnowledgeService,
        memory_service: LearnerMemoryService,
    ) -> None:
        self._knowledge_service = knowledge_service
        self._memory_service = memory_service

    def get_task(self, task_id: str, *, user_id: str) -> PracticeTask:
        if task_id != TODAY_XINGCE_TASK_ID:
            raise PracticeTaskNotFoundError(task_id)

        self._memory_service.get_required_profile(user_id=user_id)
        selection = self._knowledge_service.select_weighted_xingce_practice(
            exam_scope="guokao",
            limit=3,
        )
        questions = [
            self._knowledge_service.get_question(summary.question_id)
            for summary in selection.items
        ]
        return PracticeTask(
            task_id=task_id,
            title=f"国考{selection.module_name}·{selection.tag_value} 3 题",
            track="行测",
            question_count=len(questions),
            questions=questions,
        )

    def submit_answer(
        self,
        *,
        task_id: str,
        user_id: str,
        question_id: str,
        selected_answer: str,
    ) -> AnswerFeedback:
        if task_id != TODAY_XINGCE_TASK_ID:
            raise PracticeTaskNotFoundError(task_id)

        self._memory_service.get_required_profile(user_id=user_id)
        question = self._knowledge_service.get_question(question_id)
        normalized_selected = selected_answer.strip().upper()
        correct_answer = question.answer.strip().upper() if question.answer else None
        correct = correct_answer is not None and normalized_selected == correct_answer
        explanation = (
            question.explanation.preferred_analysis if question.explanation else None
        )
        explanation_source = (
            question.explanation.analysis_source if question.explanation else None
        )
        source_summary = _source_summary(question.occurrences)
        self._memory_service.record_practice_answer(
            user_id=user_id,
            task_id=task_id,
            question_id=question_id,
            selected_answer=normalized_selected,
            correct_answer=correct_answer,
            correct=correct,
        )

        return AnswerFeedback(
            task_id=task_id,
            question_id=question_id,
            selected_answer=normalized_selected,
            correct_answer=correct_answer,
            correct=correct,
            explanation=explanation,
            explanation_source=explanation_source,
            source_count=len(question.occurrences),
            source_summary=source_summary,
            next_action=_next_action(correct, question.teaching_point),
        )


def _source_summary(occurrences: object) -> str:
    provinces = []
    for occurrence in occurrences:
        province = getattr(occurrence, "province", None)
        if province and province not in provinces:
            provinces.append(province)
    if not provinces:
        return "暂无来源省份摘要"
    preview = "、".join(provinces[:4])
    suffix = "等" if len(provinces) > 4 else ""
    return f"命中 {preview}{suffix} {len(provinces)} 个来源"


def _next_action(correct: bool, teaching_point: str | None) -> str:
    if correct:
        return "这题已通过。下一题继续保持先找论点和论据的节奏。"
    point = teaching_point or "本题核心考点"
    return f"先复盘 {point}，再做同类题；不要直接跳到下一模块。"
