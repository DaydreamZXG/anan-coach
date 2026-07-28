from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.domains.review_session.repository import (
    ReviewCardRecord,
    ReviewSessionRepository,
    ReviewVerdictCommandRecord,
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


class ReviewVerdictConflictError(RuntimeError):
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
        idempotency_key: str | None,
    ) -> ReviewVerdictResponse:
        selected_answer = request.selected_answer.strip().upper()
        command_id = _command_id(
            card_id=card_id,
            idempotency_key=idempotency_key,
        )
        request_fingerprint = _request_fingerprint(
            docket_id=docket_id,
            card_id=card_id,
            selected_answer=selected_answer,
            elapsed_seconds=request.elapsed_seconds,
            legacy=idempotency_key is None,
        )
        try:
            self._repository.lock_command(
                user_id=user_id,
                command_id=command_id,
            )
            existing_command = self._repository.get_command(
                user_id=user_id,
                command_id=command_id,
            )
            if existing_command is not None:
                return _replay_command(
                    command=existing_command,
                    card_id=card_id,
                    request_fingerprint=request_fingerprint,
                )

            card = self._repository.get_card_for_user(
                user_id=user_id,
                docket_id=docket_id,
                card_id=card_id,
                for_update=True,
            )
            if card is None:
                raise ReviewCardNotFoundError(card_id)
            if card.status == "completed" or self._repository.verdict_exists(
                user_id=user_id,
                card_id=card_id,
            ):
                raise ReviewVerdictConflictError("Review card verdict is already completed")
            if not card.correct_answer:
                raise ReviewCardUnanswerableError(card.question_id)

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
            response = ReviewVerdictResponse(
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
            self._repository.record_command(
                user_id=user_id,
                command_id=command_id,
                card_id=card_id,
                request_fingerprint=request_fingerprint,
                response_payload=json.loads(response.model_dump_json(by_alias=True)),
            )
            self._repository.commit()
            return response
        except Exception:
            self._repository.rollback()
            raise


def _command_id(*, card_id: str, idempotency_key: str | None) -> str:
    if idempotency_key is None:
        return f"review-verdict:legacy:{card_id}"
    return f"review-verdict:explicit:{idempotency_key}"


def _request_fingerprint(
    *,
    docket_id: str,
    card_id: str,
    selected_answer: str,
    elapsed_seconds: int | None,
    legacy: bool,
) -> str:
    canonical: dict[str, Any] = {
        "cardId": card_id,
        "docketId": docket_id,
        "selectedAnswer": selected_answer,
    }
    if not legacy:
        canonical["elapsedSeconds"] = elapsed_seconds
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return sha256(encoded).hexdigest()


def _replay_command(
    *,
    command: ReviewVerdictCommandRecord,
    card_id: str,
    request_fingerprint: str,
) -> ReviewVerdictResponse:
    if (
        command.command_type != "review_verdict"
        or command.target_id != card_id
        or command.request_fingerprint != request_fingerprint
    ):
        raise ReviewVerdictConflictError(
            "Idempotency key was already used with a different request"
        )
    return ReviewVerdictResponse.model_validate(command.response_payload)


def _companion_line(*, card: ReviewCardRecord, correct: bool) -> str:
    module_name = card.module_name or "此卷"
    if correct:
        return f"大人明断，{module_name}此案可盖准字，继续批下一封。"
    return f"大人，此处{module_name}有误，臣已标为驳，稍后归入错卷复盘。"
