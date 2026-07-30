from __future__ import annotations

from app.contracts.guokao_xingce import (
    GUOKAO_XINGCE_CATALOG_VERSION,
    GUOKAO_XINGCE_PLACEMENT_PLAN,
)
from app.domains.knowledge.schemas import QuestionDetail
from app.domains.knowledge.service import KnowledgeService
from app.domains.memory.repository import LearnerProfileRecord
from app.domains.onboarding.repository import (
    OnboardingProfileRecord,
    OnboardingRepository,
)
from app.domains.onboarding.schemas import (
    OnboardingProfileUpdate,
    PlacementModuleResult,
    PlacementQuestionSpec,
    PlacementSubmitResponse,
    PlacementTestTask,
)
from app.domains.positions.target_scores import PositionTargetScoreOwner
from sqlalchemy.orm import Session
from app.domains.study_budget import (
    FULLTIME_DEFAULT_DAILY_MINUTES,
    PARTTIME_DEFAULT_DAILY_MINUTES,
)


PLACEMENT_TASK_ID = f"placement-xingce-30:{GUOKAO_XINGCE_CATALOG_VERSION}"
PLACEMENT_DURATION_MINUTES = 35
PLACEMENT_PLAN = GUOKAO_XINGCE_PLACEMENT_PLAN
PLACEMENT_MATERIAL_TEXT_MAX_LENGTH = 5000


class PlacementAssemblyError(RuntimeError):
    pass


class OnboardingService:
    def __init__(
        self,
        *,
        session: Session,
        repository: OnboardingRepository,
        knowledge_service: KnowledgeService,
        target_score_owner: PositionTargetScoreOwner | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._knowledge_service = knowledge_service
        self._target_score_owner = target_score_owner or PositionTargetScoreOwner()

    def get_profile(self, *, user_id: str) -> LearnerProfileRecord:
        return _memory_profile(self._repository.get_profile(user_id=user_id))

    def update_profile(
        self,
        *,
        user_id: str,
        update: OnboardingProfileUpdate,
    ) -> LearnerProfileRecord:
        daily_minutes = update.daily_minutes
        if daily_minutes is None and update.study_mode:
            daily_minutes = (
                FULLTIME_DEFAULT_DAILY_MINUTES
                if update.study_mode == "fulltime"
                else PARTTIME_DEFAULT_DAILY_MINUTES
            )

        target_score = None
        target_position_data_version = None
        if update.target_position_id:
            release_context = self._target_score_owner.active_context_for_position(
                self._session,
                position_id=update.target_position_id,
            )
            target_position_data_version = release_context.release.position_data_version
            position_target_score = self._target_score_owner.range_for_position(
                self._session,
                position_id=update.target_position_id,
                context=release_context,
            )
            target_score = (
                position_target_score.recommended
                if position_target_score is not None
                else update.target_score
            )

        profile = self._repository.upsert_profile(
            user_id=user_id,
            education=update.education,
            major=update.major,
            fresh_graduate=update.fresh_graduate,
            political_status=update.political_status,
            grassroots_years=update.grassroots_years,
            study_mode=update.study_mode,
            daily_minutes=daily_minutes,
            target_position_id=update.target_position_id,
            target_score=target_score,
            target_position_data_version=target_position_data_version,
            baseline_source=update.baseline_source,
            baseline_xingce_score=update.baseline_xingce_score,
            baseline_shenlun_score=update.baseline_shenlun_score,
            baseline_weak_modules=update.baseline_weak_modules,
        )
        return _memory_profile(profile)

    def complete_onboarding(self, *, user_id: str) -> LearnerProfileRecord:
        return _memory_profile(self._repository.complete_onboarding(user_id=user_id))

    def get_placement_test(self) -> PlacementTestTask:
        questions: list[QuestionDetail] = []
        for module_name, count, _weight in PLACEMENT_PLAN:
            summaries = self._knowledge_service.search_questions(
                domain="objective",
                module_name=module_name,
                question_type=None,
                tag=None,
                province=None,
                year=None,
                requires_images=False,
                limit=count,
            ).items
            if len(summaries) != count:
                raise PlacementAssemblyError(
                    f"not enough questions for placement module {module_name}"
                )
            for summary in summaries:
                question = self._knowledge_service.get_question(summary.question_id)
                _truncate_placement_material(question)
                questions.append(question)
        return PlacementTestTask(
            task_id=PLACEMENT_TASK_ID,
            title="国考行测六模块 30 题能力基线",
            duration_minutes=PLACEMENT_DURATION_MINUTES,
            question_count=len(questions),
            module_plan=[
                PlacementQuestionSpec(module_name=module_name, count=count)
                for module_name, count, _weight in PLACEMENT_PLAN
            ],
            questions=questions,
        )

    def submit_placement_test(
        self,
        *,
        user_id: str,
        answers: dict[str, str],
    ) -> PlacementSubmitResponse:
        question_results: list[tuple[str, str, bool]] = []
        module_totals: dict[str, dict[str, int]] = {}
        for question_id, selected_answer in answers.items():
            question = self._knowledge_service.get_question(question_id)
            module = question.module_name or "未分类"
            expected = question.answer.strip().upper() if question.answer else None
            selected = selected_answer.strip().upper()
            correct = expected is not None and selected == expected
            question_results.append((question_id, selected, correct))
            module_totals.setdefault(module, {"correct": 0, "total": 0})
            module_totals[module]["total"] += 1
            if correct:
                module_totals[module]["correct"] += 1

        module_results = [
            PlacementModuleResult(
                module_name=module,
                correct=counts["correct"],
                total=counts["total"],
                accuracy=round(counts["correct"] / counts["total"], 4) if counts["total"] else 0.0,
            )
            for module, counts in sorted(module_totals.items())
        ]
        estimated = _estimate_xingce_score(module_results)
        self._repository.record_placement_attempt(
            user_id=user_id,
            task_id=PLACEMENT_TASK_ID,
            question_ids=[question_id for question_id, _selected, _correct in question_results],
            answers=[
                {
                    "questionId": question_id,
                    "selectedAnswer": selected,
                    "correct": str(correct).lower(),
                }
                for question_id, selected, correct in question_results
            ],
            module_snapshot=[result.model_dump(by_alias=True) for result in module_results],
            estimated_xingce_score=estimated,
        )
        return PlacementSubmitResponse(
            task_id=PLACEMENT_TASK_ID,
            estimated_xingce_score=estimated,
            baseline_source="placement_test",
            module_results=module_results,
        )


def _estimate_xingce_score(module_results: list[PlacementModuleResult]) -> float:
    by_module = {result.module_name: result.accuracy for result in module_results}
    score = 0.0
    for module_name, _count, weight in PLACEMENT_PLAN:
        score += by_module.get(module_name, 0.0) * weight
    return round(score, 2)


def _truncate_placement_material(question: QuestionDetail) -> None:
    material = question.material_text
    if material and len(material) > PLACEMENT_MATERIAL_TEXT_MAX_LENGTH:
        question.material_text = material[:PLACEMENT_MATERIAL_TEXT_MAX_LENGTH]


def _memory_profile(record: OnboardingProfileRecord) -> LearnerProfileRecord:
    return LearnerProfileRecord(
        target_exam=record.target_exam,
        target_region=record.target_region,
        position_group=record.position_group,
        daily_minutes=record.daily_minutes,
        education=record.education,
        major=record.major,
        fresh_graduate=record.fresh_graduate,
        political_status=record.political_status,
        grassroots_years=record.grassroots_years,
        study_mode=record.study_mode,
        target_position_id=record.target_position_id,
        target_score=record.target_score,
        target_position_data_version=record.target_position_data_version,
        baseline_source=record.baseline_source,
        baseline_xingce_score=record.baseline_xingce_score,
        baseline_shenlun_score=record.baseline_shenlun_score,
        baseline_weak_modules=record.baseline_weak_modules,
        baseline_module_snapshot=record.baseline_module_snapshot,
        onboarding_complete=record.onboarding_complete,
    )
