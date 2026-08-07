from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MAX_SHORT_TERM_MESSAGES = 8
MAX_SHORT_TERM_MESSAGE_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class ShortTermConversationMessage:
    role: Literal["user", "assistant"]
    content: str


def short_term_history_payload(
    messages: tuple[ShortTermConversationMessage, ...],
) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for message in messages[-MAX_SHORT_TERM_MESSAGES:]:
        content = message.content.strip()
        if not content:
            continue
        payload.append(
            {
                "role": message.role,
                "content": content[:MAX_SHORT_TERM_MESSAGE_CHARS],
            }
        )
    return payload
