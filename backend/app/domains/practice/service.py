from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.domains.knowledge.schemas import (
    QuestionExplanation,
    QuestionOption,
)
from app.domains.knowledge.service import KnowledgeService
from app.domains.memory.service import LearnerMemoryService
from app.domains.practice.repository import FrozenPracticeQuestion, PracticeRepository
from app.domains.practice.schemas import (
    AnswerFeedback,
    PracticeQuestion,
    PracticeQuestionVersion,
    PracticeTask,
    PracticeTaskVersion,
)
from app.domains.taskstream.service import TODAY_XINGCE_TASK_ID


class PracticeTaskNotFoundError(LookupError):
    pass


class PracticeTaskVersionError(RuntimeError):
    pass


class PracticeService:
    def __init__(
        self,
        *,
        knowledge_service: KnowledgeService,
        memory_service: LearnerMemoryService,
        repository: PracticeRepository,
    ) -> None:
        self._knowledge_service = knowledge_service
        self._memory_service = memory_service
        self._repository = repository

    def get_task(self, task_id: str, *, user_id: str) -> PracticeTask:
        if task_id != TODAY_XINGCE_TASK_ID:
            raise PracticeTaskNotFoundError(task_id)

        self._memory_service.get_required_profile(user_id=user_id)
        selection = self._knowledge_service.select_weighted_xingce_practice(
            exam_scope="guokao",
            limit=3,
        )
        frozen = self._repository.frozen_questions(
            release_id=selection.release_id,
            node_id=selection.node_id,
            question_ids=[summary.question_id for summary in selection.items],
        )
        if len(frozen) != 3:
            raise PracticeTaskVersionError(
                "Practice selection has no complete immutable content bundle"
            )
        task_version = _task_version(
            task_id=task_id,
            release_id=selection.release_id,
            node_id=selection.node_id,
            questions=frozen,
        )
        return PracticeTask(
            task_id=task_id,
            title=f"国考{selection.module_name}·{selection.tag_value} 3 题",
            track="行测",
            question_count=len(frozen),
            task_version=task_version,
            questions=[_question_schema(question) for question in frozen],
        )

    def submit_answer(
        self,
        *,
        task_id: str,
        user_id: str,
        question_id: str,
        selected_answer: str,
        task_version: PracticeTaskVersion,
    ) -> AnswerFeedback:
        if task_id != TODAY_XINGCE_TASK_ID:
            raise PracticeTaskNotFoundError(task_id)

        self._memory_service.get_required_profile(user_id=user_id)
        frozen = self._repository.weighted_frozen_questions(
            release_id=task_version.release_id,
            node_id=task_version.node_id,
            limit=3,
        )
        if len(frozen) != 3:
            raise PracticeTaskVersionError(
                "Practice task has no complete immutable content bundle"
            )
        expected_version = _task_version(
            task_id=task_id,
            release_id=task_version.release_id,
            node_id=task_version.node_id,
            questions=frozen,
        )
        if expected_version != task_version:
            raise PracticeTaskVersionError(
                "Practice task version does not match its immutable release bundle"
            )
        question = next(
            (item for item in frozen if item.question_id == question_id),
            None,
        )
        if question is None:
            raise PracticeTaskVersionError(
                "Practice question is not part of the frozen task version"
            )
        snapshot = question.snapshot
        normalized_selected = selected_answer.strip().upper()
        answer = _optional_str(snapshot.get("answer"))
        correct_answer = answer.strip().upper() if answer else None
        correct = correct_answer is not None and normalized_selected == correct_answer
        explanation = _optional_str(snapshot.get("preferred_analysis"))
        explanation_source = _optional_str(snapshot.get("analysis_source"))
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
            source_count=question.occurrence_count,
            source_summary=_source_summary(question),
            next_action=_next_action(correct, question.tag_value),
        )


def _task_version(
    *,
    task_id: str,
    release_id: str,
    node_id: str,
    questions: list[FrozenPracticeQuestion],
) -> PracticeTaskVersion:
    question_versions = [
        PracticeQuestionVersion(
            questionId=question.question_id,
            contentVersionId=question.content_version_id,
        )
        for question in questions
    ]
    payload = {
        "taskId": task_id,
        "releaseId": release_id,
        "nodeId": node_id,
        "questions": [
            item.model_dump(by_alias=True, mode="json")
            for item in question_versions
        ],
    }
    bundle_version_id = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PracticeTaskVersion(
        releaseId=release_id,
        nodeId=node_id,
        bundleVersionId=bundle_version_id,
        questions=question_versions,
    )


def _question_schema(question: FrozenPracticeQuestion) -> PracticeQuestion:
    snapshot = question.snapshot
    options = [
        QuestionOption(
            optionIndex=index,
            label=str(option.get("label") or ""),
            optionText=str(option.get("option_text") or ""),
            optionHtml=_optional_str(option.get("option_html")),
        )
        for index, option in enumerate(_objects(snapshot.get("options")), start=1)
    ]
    explanation_text = _optional_str(snapshot.get("preferred_analysis"))
    explanation = (
        QuestionExplanation(
            preferredAnalysis=explanation_text,
            originalAnalysis=None,
            storedAnalysis=None,
            analysisSource=_optional_str(snapshot.get("analysis_source")),
            analysisStatus=_optional_str(snapshot.get("analysis_status")),
            preferAiAnalysis=bool(snapshot.get("prefer_ai_analysis")),
            needsReview=bool(snapshot.get("analysis_needs_review")),
        )
        if explanation_text is not None
        else None
    )
    return PracticeQuestion(
        questionId=question.question_id,
        contentVersionId=question.content_version_id,
        domain="objective",
        status="published",
        moduleName=_optional_str(snapshot.get("module_name")),
        questionType=_optional_str(snapshot.get("question_type")),
        displayLevel1=None,
        displayLevel2=None,
        displayLevel3=None,
        knowledgeFamily=None,
        teachingPoint=question.tag_value,
        microSkill=None,
        stem=str(snapshot.get("stem") or ""),
        stemHtml=_optional_str(snapshot.get("stem_html")),
        answer=_optional_str(snapshot.get("answer")),
        materialText=_optional_str(snapshot.get("material_text")),
        materialHtml=_optional_str(snapshot.get("material_html")),
        difficulty=None,
        visualRequired=question.visual_required,
        needsMultimodal=question.visual_required,
        sharedContextRequired=question.shared_context_required,
        occurrenceCount=question.occurrence_count,
        occurrenceProvinceCount=None,
        firstYear=None,
        latestYear=question.latest_year,
        sourceMemberCount=None,
        options=options,
        explanation=explanation,
        occurrences=[],
        tags=[],
        knowledgeChunks=[],
        assets=[],
    )


def _source_summary(question: FrozenPracticeQuestion) -> str:
    return (
        f"国考冻结发布 {question.release_version}，"
        f"收录 {question.occurrence_count} 条来源"
    )


def _objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _next_action(correct: bool, teaching_point: str | None) -> str:
    if correct:
        return "这题已通过。下一题继续保持先找论点和论据的节奏。"
    point = teaching_point or "本题核心考点"
    return f"先复盘 {point}，再做同类题；不要直接跳到下一模块。"
