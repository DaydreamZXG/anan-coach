from __future__ import annotations

from datetime import date

from app.domains.memory.repository import (
    LearnerMemoryRecord,
    LearnerMemoryRepository,
    LearnerProfileRecord,
    LearnerWorkbenchObjectRecord,
    LearnerWorkbenchSectionRecord,
)
from app.domains.memory.schemas import LearnerProfileUpdate


class LearnerProfileRequiredError(RuntimeError):
    pass


class LearnerMemoryService:
    def __init__(self, repository: LearnerMemoryRepository) -> None:
        self._repository = repository

    def get_memory(self, *, user_id: str) -> LearnerMemoryRecord:
        return self._repository.get_memory(user_id=user_id)

    def get_required_profile(self, *, user_id: str) -> LearnerProfileRecord:
        memory = self.get_memory(user_id=user_id)
        if not memory.profile.onboarding_complete:
            raise LearnerProfileRequiredError(user_id)
        return memory.profile

    def update_profile(
        self,
        *,
        user_id: str,
        update: LearnerProfileUpdate,
    ) -> LearnerProfileRecord:
        return self._repository.update_profile(
            user_id=user_id,
            target_exam=update.target_exam,
            target_region=update.target_region,
            position_group=update.position_group,
            daily_minutes=update.daily_minutes,
        )

    def record_practice_answer(
        self,
        *,
        user_id: str,
        task_id: str,
        question_id: str,
        selected_answer: str,
        correct_answer: str | None,
        correct: bool,
    ) -> None:
        self._repository.record_practice_answer(
            user_id=user_id,
            task_id=task_id,
            question_id=question_id,
            selected_answer=selected_answer,
            correct_answer=correct_answer,
            correct=correct,
        )

    def list_completed_task_ids_for_date(
        self,
        *,
        user_id: str,
        completed_on: date,
    ) -> list[str]:
        return self._repository.list_completed_task_ids_for_date(
            user_id=user_id,
            completed_on=completed_on,
        )

    def list_workbench_sections(
        self,
        *,
        user_id: str,
    ) -> list[LearnerWorkbenchSectionRecord]:
        return self._repository.list_workbench_sections(user_id=user_id)

    def has_mock_exam_this_week(
        self,
        *,
        user_id: str,
        week_start: date,
    ) -> bool:
        return self._repository.has_mock_exam_this_week(
            user_id=user_id,
            week_start=week_start,
        )

    def get_unviewed_mock_review(
        self,
        *,
        user_id: str,
    ) -> LearnerWorkbenchObjectRecord | None:
        return self._repository.get_unviewed_mock_review(user_id=user_id)

    def get_latest_mock_review_reason(
        self,
        *,
        user_id: str,
    ) -> str | None:
        return self._repository.get_latest_mock_review_reason(user_id=user_id)
