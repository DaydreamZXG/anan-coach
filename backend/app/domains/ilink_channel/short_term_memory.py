from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.companion.short_term import ShortTermConversationMessage


SHORT_TERM_MEMORY_MAX_TURNS = 4
SHORT_TERM_MEMORY_TTL = timedelta(hours=2)
MAX_MEMORY_USER_CHARS = 800
MAX_MEMORY_ASSISTANT_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class ILinkShortTermTurn:
    user_text: str
    assistant_text: str
    created_at: datetime

    def messages(self) -> tuple[ShortTermConversationMessage, ...]:
        return (
            ShortTermConversationMessage(role="user", content=self.user_text),
            ShortTermConversationMessage(role="assistant", content=self.assistant_text),
        )


class ILinkShortTermMemory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def recent_messages(
        self,
        *,
        user_id: UUID,
        now: datetime | None = None,
    ) -> tuple[ShortTermConversationMessage, ...]:
        checked_at = now or datetime.now(UTC)
        rows = self._session.execute(
            text(
                """
                select user_text, assistant_text, created_at
                from (
                  select user_text, assistant_text, created_at, turn_id
                  from messaging.ilink_short_term_memory_turns
                  where user_id = :user_id
                    and expires_at > :checked_at
                  order by created_at desc, turn_id desc
                  limit :turn_limit
                ) recent
                order by created_at, turn_id
                """
            ),
            {
                "user_id": str(user_id),
                "checked_at": checked_at,
                "turn_limit": SHORT_TERM_MEMORY_MAX_TURNS,
            },
        ).mappings()
        messages: list[ShortTermConversationMessage] = []
        for row in rows:
            turn = ILinkShortTermTurn(
                user_text=str(row["user_text"]),
                assistant_text=str(row["assistant_text"]),
                created_at=row["created_at"],
            )
            messages.extend(turn.messages())
        return tuple(messages)

    def remember(
        self,
        *,
        user_id: UUID,
        source_message_id: str,
        user_text: str,
        assistant_text: str,
        now: datetime | None = None,
    ) -> None:
        remembered_at = now or datetime.now(UTC)
        normalized_user = _bounded_text(user_text, MAX_MEMORY_USER_CHARS)
        normalized_assistant = _bounded_text(
            assistant_text,
            MAX_MEMORY_ASSISTANT_CHARS,
        )
        source_fingerprint = sha256(source_message_id.encode("utf-8")).hexdigest()
        turn_id = uuid5(
            NAMESPACE_URL,
            f"anan-ilink-short-term:{user_id}:{source_fingerprint}",
        )
        self._session.execute(
            text(
                "delete from messaging.ilink_short_term_memory_turns "
                "where expires_at <= :remembered_at"
            ),
            {"remembered_at": remembered_at},
        )
        self._session.execute(
            text(
                """
                insert into messaging.ilink_short_term_memory_turns (
                  turn_id, user_id, source_message_fingerprint,
                  user_text, assistant_text, created_at, expires_at
                ) values (
                  :turn_id, :user_id, :source_message_fingerprint,
                  :user_text, :assistant_text, :created_at, :expires_at
                )
                on conflict (user_id, source_message_fingerprint) do nothing
                """
            ),
            {
                "turn_id": str(turn_id),
                "user_id": str(user_id),
                "source_message_fingerprint": source_fingerprint,
                "user_text": normalized_user,
                "assistant_text": normalized_assistant,
                "created_at": remembered_at,
                "expires_at": remembered_at + SHORT_TERM_MEMORY_TTL,
            },
        )
        self._session.execute(
            text(
                """
                delete from messaging.ilink_short_term_memory_turns
                where turn_id in (
                  select turn_id
                  from messaging.ilink_short_term_memory_turns
                  where user_id = :user_id
                  order by created_at desc, turn_id desc
                  offset :turn_limit
                )
                """
            ),
            {
                "user_id": str(user_id),
                "turn_limit": SHORT_TERM_MEMORY_MAX_TURNS,
            },
        )


def _bounded_text(value: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("short-term memory text cannot be empty")
    if len(normalized) <= maximum:
        return normalized
    return normalized[: maximum - 1].rstrip() + "…"
