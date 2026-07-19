from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ReviewCardRecord:
    docket_id: str
    card_id: str
    question_id: str
    module_name: str | None
    correct_answer: str | None


@dataclass(frozen=True)
class VerdictRecord:
    verdict_id: str
    docket_id: str
    card_id: str
    question_id: str
    selected_answer: str
    correct: bool
    elapsed_seconds: int | None
    created_at: datetime


class ReviewSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_card_for_user(
        self,
        *,
        user_id: str,
        docket_id: str,
        card_id: str,
    ) -> ReviewCardRecord | None:
        row = self._session.execute(
            text(
                """
                select
                  d.docket_id::text as docket_id,
                  dc.card_id::text as card_id,
                  dc.question_id,
                  dc.module_name,
                  q.answer as correct_answer
                from learner.dockets d
                join learner.docket_cards dc on dc.docket_id = d.docket_id
                join product.questions q on q.question_id = dc.question_id
                where d.user_id = cast(:user_id as uuid)
                  and d.docket_id = cast(:docket_id as uuid)
                  and dc.card_id = cast(:card_id as uuid)
                limit 1
                """
            ),
            {
                "user_id": user_id,
                "docket_id": docket_id,
                "card_id": card_id,
            },
        ).mappings().first()
        if row is None:
            return None
        return ReviewCardRecord(
            docket_id=str(row["docket_id"]),
            card_id=str(row["card_id"]),
            question_id=str(row["question_id"]),
            module_name=row["module_name"] if isinstance(row["module_name"], str) else None,
            correct_answer=(
                row["correct_answer"] if isinstance(row["correct_answer"], str) else None
            ),
        )

    def record_verdict(
        self,
        *,
        user_id: str,
        card: ReviewCardRecord,
        selected_answer: str,
        correct: bool,
        elapsed_seconds: int | None,
    ) -> VerdictRecord:
        row = self._session.execute(
            text(
                """
                insert into learner.verdicts (
                  verdict_id,
                  user_id,
                  docket_id,
                  card_id,
                  question_id,
                  selected_answer,
                  correct,
                  elapsed_seconds,
                  created_at
                ) values (
                  cast(:verdict_id as uuid),
                  cast(:user_id as uuid),
                  cast(:docket_id as uuid),
                  cast(:card_id as uuid),
                  :question_id,
                  :selected_answer,
                  :correct,
                  :elapsed_seconds,
                  now()
                )
                on conflict (user_id, card_id)
                do update set
                  selected_answer = excluded.selected_answer,
                  correct = excluded.correct,
                  elapsed_seconds = excluded.elapsed_seconds,
                  created_at = now()
                returning
                  verdict_id::text as verdict_id,
                  docket_id::text as docket_id,
                  card_id::text as card_id,
                  question_id,
                  selected_answer,
                  correct,
                  elapsed_seconds,
                  created_at
                """
            ),
            {
                "verdict_id": str(uuid4()),
                "user_id": user_id,
                "docket_id": card.docket_id,
                "card_id": card.card_id,
                "question_id": card.question_id,
                "selected_answer": selected_answer,
                "correct": correct,
                "elapsed_seconds": elapsed_seconds,
            },
        ).mappings().one()
        self._session.execute(
            text(
                """
                update learner.docket_cards
                set status = 'completed',
                    updated_at = now()
                where card_id = cast(:card_id as uuid)
                """
            ),
            {"card_id": card.card_id},
        )
        self._session.commit()
        return VerdictRecord(
            verdict_id=str(row["verdict_id"]),
            docket_id=str(row["docket_id"]),
            card_id=str(row["card_id"]),
            question_id=str(row["question_id"]),
            selected_answer=str(row["selected_answer"]),
            correct=bool(row["correct"]),
            elapsed_seconds=(
                int(row["elapsed_seconds"])
                if row["elapsed_seconds"] is not None
                else None
            ),
            created_at=row["created_at"],
        )
