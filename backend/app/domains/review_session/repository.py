from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
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
    status: str


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


@dataclass(frozen=True)
class ReviewVerdictCommandRecord:
    command_type: str
    target_id: str | None
    request_fingerprint: str
    response_payload: dict[str, Any]


class ReviewSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_command(self, *, user_id: str, command_id: str) -> None:
        self._session.execute(
            text(
                """
                select pg_advisory_xact_lock(
                  hashtextextended(:lock_identity, 0)
                )
                """
            ),
            {"lock_identity": f"review-verdict:{user_id}:{command_id}"},
        )

    def get_command(
        self,
        *,
        user_id: str,
        command_id: str,
    ) -> ReviewVerdictCommandRecord | None:
        row = (
            self._session.execute(
                text(
                    """
                select
                  command_type,
                  target_id::text as target_id,
                  request_fingerprint,
                  response_payload
                from learning.xingce_commands
                where user_id = cast(:user_id as uuid)
                  and command_id = :command_id
                """
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return ReviewVerdictCommandRecord(
            command_type=str(row["command_type"]),
            target_id=str(row["target_id"]) if row["target_id"] is not None else None,
            request_fingerprint=str(row["request_fingerprint"]),
            response_payload=dict(row["response_payload"]),
        )

    def get_card_for_user(
        self,
        *,
        user_id: str,
        docket_id: str,
        card_id: str,
        for_update: bool = False,
    ) -> ReviewCardRecord | None:
        locking_clause = "for update of dc" if for_update else ""
        row = (
            self._session.execute(
                text(
                    f"""
                select
                  d.docket_id::text as docket_id,
                  dc.card_id::text as card_id,
                  dc.question_id,
                  dc.module_name,
                  q.answer as correct_answer,
                  dc.status
                from learner.dockets d
                join learner.docket_cards dc on dc.docket_id = d.docket_id
                join product.questions q on q.question_id = dc.question_id
                where d.user_id = cast(:user_id as uuid)
                  and d.docket_id = cast(:docket_id as uuid)
                  and dc.card_id = cast(:card_id as uuid)
                limit 1
                {locking_clause}
                """
                ),
                {
                    "user_id": user_id,
                    "docket_id": docket_id,
                    "card_id": card_id,
                },
            )
            .mappings()
            .first()
        )
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
            status=str(row["status"]),
        )

    def verdict_exists(self, *, user_id: str, card_id: str) -> bool:
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1
                      from learner.verdicts
                      where user_id = cast(:user_id as uuid)
                        and card_id = cast(:card_id as uuid)
                    )
                    """
                ),
                {"user_id": user_id, "card_id": card_id},
            ).scalar_one()
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
        row = (
            self._session.execute(
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
            )
            .mappings()
            .one()
        )
        self._session.execute(
            text(
                """
                update learner.docket_cards
                set status = 'completed',
                    updated_at = now()
                where docket_id = cast(:docket_id as uuid)
                  and card_id = cast(:card_id as uuid)
                """
            ),
            {"docket_id": card.docket_id, "card_id": card.card_id},
        )
        return VerdictRecord(
            verdict_id=str(row["verdict_id"]),
            docket_id=str(row["docket_id"]),
            card_id=str(row["card_id"]),
            question_id=str(row["question_id"]),
            selected_answer=str(row["selected_answer"]),
            correct=bool(row["correct"]),
            elapsed_seconds=(
                int(row["elapsed_seconds"]) if row["elapsed_seconds"] is not None else None
            ),
            created_at=row["created_at"],
        )

    def record_command(
        self,
        *,
        user_id: str,
        command_id: str,
        card_id: str,
        request_fingerprint: str,
        response_payload: dict[str, Any],
    ) -> None:
        self._session.execute(
            text(
                """
                insert into learning.xingce_commands (
                  user_id,
                  command_id,
                  command_type,
                  target_id,
                  request_fingerprint,
                  response_payload,
                  created_at
                ) values (
                  cast(:user_id as uuid),
                  :command_id,
                  'review_verdict',
                  cast(:card_id as uuid),
                  :request_fingerprint,
                  cast(:response_payload as jsonb),
                  now()
                )
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "card_id": card_id,
                "request_fingerprint": request_fingerprint,
                "response_payload": json.dumps(
                    response_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        )

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
