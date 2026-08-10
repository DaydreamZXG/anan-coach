from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.governance.contracts import GlobalAskContext
from app.domains.companion.short_term import ShortTermConversationMessage


SHORT_TERM_MEMORY_MAX_TURNS = 4
SHORT_TERM_MEMORY_TTL = timedelta(hours=2)
MAX_MEMORY_USER_CHARS = 800
MAX_MEMORY_ASSISTANT_CHARS = 2_000


def companion_context_scope_fingerprint(context: GlobalAskContext) -> str:
    object_refs = sorted(
        (
            {"type": item.type, "id": item.id, "version": item.version}
            for item in context.object_refs
        ),
        key=lambda item: (item["type"], item["id"], item["version"]),
    )
    payload = {
        "routeId": context.route_id.strip().lstrip("/"),
        "workspace": context.workspace,
        "objectRefs": object_refs,
        "taskMode": context.task_mode,
        "taskStage": context.task_stage,
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class CompanionShortTermMemory:
    """Small, expiring conversation window scoped to one authorized page context."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def recent_messages(
        self,
        *,
        user_id: str | UUID,
        context_scope_fingerprint: str,
        now: datetime | None = None,
    ) -> tuple[ShortTermConversationMessage, ...]:
        checked_at = now or datetime.now(UTC)
        rows = self._session.execute(
            text(
                """
                select user_text, assistant_text
                from (
                  select user_text, assistant_text, created_at, turn_id
                  from platform.companion_short_term_memory_turns
                  where user_id = cast(:user_id as uuid)
                    and context_scope_fingerprint = :context_scope_fingerprint
                    and expires_at > :checked_at
                  order by created_at desc, turn_id desc
                  limit :turn_limit
                ) recent
                order by created_at, turn_id
                """
            ),
            {
                "user_id": str(user_id),
                "context_scope_fingerprint": context_scope_fingerprint,
                "checked_at": checked_at,
                "turn_limit": SHORT_TERM_MEMORY_MAX_TURNS,
            },
        ).mappings()
        messages: list[ShortTermConversationMessage] = []
        for row in rows:
            messages.extend(
                (
                    ShortTermConversationMessage(
                        role="user",
                        content=str(row["user_text"]),
                    ),
                    ShortTermConversationMessage(
                        role="assistant",
                        content=str(row["assistant_text"]),
                    ),
                )
            )
        return tuple(messages)

    def remember(
        self,
        *,
        user_id: str | UUID,
        context_scope_fingerprint: str,
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
            (
                "anan-companion-short-term:"
                f"{user_id}:{context_scope_fingerprint}:{source_fingerprint}"
            ),
        )
        self._session.execute(
            text(
                "delete from platform.companion_short_term_memory_turns "
                "where expires_at <= :remembered_at"
            ),
            {"remembered_at": remembered_at},
        )
        self._session.execute(
            text(
                """
                insert into platform.companion_short_term_memory_turns (
                  turn_id, user_id, context_scope_fingerprint,
                  source_message_fingerprint, user_text, assistant_text,
                  created_at, expires_at
                ) values (
                  :turn_id, cast(:user_id as uuid), :context_scope_fingerprint,
                  :source_message_fingerprint, :user_text, :assistant_text,
                  :created_at, :expires_at
                )
                on conflict (
                  user_id, context_scope_fingerprint, source_message_fingerprint
                ) do nothing
                """
            ),
            {
                "turn_id": str(turn_id),
                "user_id": str(user_id),
                "context_scope_fingerprint": context_scope_fingerprint,
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
                delete from platform.companion_short_term_memory_turns
                where turn_id in (
                  select turn_id
                  from platform.companion_short_term_memory_turns
                  where user_id = cast(:user_id as uuid)
                    and context_scope_fingerprint = :context_scope_fingerprint
                  order by created_at desc, turn_id desc
                  offset :turn_limit
                )
                """
            ),
            {
                "user_id": str(user_id),
                "context_scope_fingerprint": context_scope_fingerprint,
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
