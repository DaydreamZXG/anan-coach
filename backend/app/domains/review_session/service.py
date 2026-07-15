from __future__ import annotations

from app.domains.review_session.repository import (
    ReviewCardRecord,
    ReviewSessionRepository,
)
from app.domains.progression.service import ProgressionService
from app.domains.review_session.schemas import (
    ReviewVerdictRequest,
    ReviewVerdictResponse,
)


class ReviewCardNotFoundError(LookupError):
    pass


class ReviewCardUnanswerableError(RuntimeError):
    pass


class ReviewSessionService:
    def __init__(
        self,
        *,
        repository: ReviewSessionRepository,
        progression_service: ProgressionService,
    ) -> None:
        self._repository = repository
        self._progression_service = progression_service

    def submit_verdict(
        self,
        *,
        user_id: str,
        docket_id: str,
        card_id: str,
        request: ReviewVerdictRequest,
    ) -> ReviewVerdictResponse:
        card = self._repository.get_card_for_user(
            user_id=user_id,
            docket_id=docket_id,
            card_id=card_id,
        )
        if card is None:
            raise ReviewCardNotFoundError(card_id)
        if not card.correct_answer:
            raise ReviewCardUnanswerableError(card.question_id)

        selected_answer = request.selected_answer.strip().upper()
        correct_answer = card.correct_answer.strip().upper()
        correct = selected_answer == correct_answer
        verdict = self._repository.record_verdict(
            user_id=user_id,
            card=card,
            selected_answer=selected_answer,
            correct=correct,
            elapsed_seconds=request.elapsed_seconds,
        )
        progression = self._progression_service.record_verdict(
            user_id=user_id,
            correct=verdict.correct,
            module_name=card.module_name,
        )
        return ReviewVerdictResponse(
            verdict_id=verdict.verdict_id,
            docket_id=verdict.docket_id,
            card_id=verdict.card_id,
            question_id=verdict.question_id,
            selected_answer=verdict.selected_answer,
            correct=verdict.correct,
            stamp_text="准" if verdict.correct else "驳",
            companion_line=_companion_line(card=card, correct=verdict.correct),
            progression=progression,
            created_at=verdict.created_at,
        )


def _companion_line(*, card: ReviewCardRecord, correct: bool) -> str:
    module_name = card.module_name or "此卷"
    if correct:
        return f"大人明断，{module_name}此案可盖准字，继续批下一封。"
    return f"大人，此处{module_name}有误，臣已标为驳，稍后归入错卷复盘。"
