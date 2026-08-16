from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.companion.memory import (
    CompanionMemoryConflict,
    CompanionMemoryStore,
)


NOW = datetime(2026, 8, 10, 9, 30, tzinfo=UTC)
USER_A = UUID("00000000-0000-4000-8000-00000000d101")
USER_B = UUID("00000000-0000-4000-8000-00000000d102")


def _seed_users(session: Session) -> None:
    session.execute(
        text(
            "insert into learner.users (user_id, display_name, account_status) values "
            "(:user_a, '记忆测试 A', 'active'), (:user_b, '记忆测试 B', 'active')"
        ),
        {"user_a": str(USER_A), "user_b": str(USER_B)},
    )
    session.commit()


def test_explicit_memory_is_idempotent_revisioned_isolated_and_clearable(
    db_session: Session,
) -> None:
    _seed_users(db_session)
    memory = CompanionMemoryStore(db_session)

    first = memory.create(
        user_id=USER_A,
        idempotency_key="memory-create-0001",
        category="explanation_preference",
        content="资料分析先给列式，再讲心算。",
        now=NOW,
    )
    replay = memory.create(
        user_id=USER_A,
        idempotency_key="memory-create-0001",
        category="explanation_preference",
        content="资料分析先给列式，再讲心算。",
        now=NOW + timedelta(minutes=1),
    )
    db_session.commit()

    assert replay.memory_id == first.memory_id
    assert memory.state(user_id=USER_A, now=NOW).items == [first]
    assert memory.state(user_id=USER_B, now=NOW).items == []

    with pytest.raises(CompanionMemoryConflict):
        memory.create(
            user_id=USER_A,
            idempotency_key="memory-create-0001",
            category="study_constraint",
            content="工作日只有一小时。",
            now=NOW,
        )
    db_session.rollback()

    updated = memory.update(
        user_id=USER_A,
        memory_id=first.memory_id,
        revision=1,
        category="explanation_preference",
        content="资料分析先给列式，再说为什么。",
        now=NOW + timedelta(minutes=2),
    )
    db_session.commit()
    assert updated.revision == 2

    with pytest.raises(CompanionMemoryConflict):
        memory.update(
            user_id=USER_A,
            memory_id=first.memory_id,
            revision=1,
            category="personal_note",
            content="不应覆盖新内容。",
            now=NOW + timedelta(minutes=3),
        )
    db_session.rollback()
    assert memory.state(user_id=USER_A, now=NOW).items[0].content == updated.content

    assert memory.active_provider_items(user_id=USER_A) == (
        {
            "memoryId": str(first.memory_id),
            "category": "explanation_preference",
            "content": "资料分析先给列式，再说为什么。",
            "revision": 2,
        },
    )
    assert memory.state(user_id=USER_A, now=NOW).maximum_long_term_items is None
    memory.set_enabled(user_id=USER_A, enabled=False, now=NOW)
    db_session.commit()
    assert memory.active_provider_items(user_id=USER_A) == ()
    assert len(memory.state(user_id=USER_A, now=NOW).items) == 1

    assert memory.clear_long_term(user_id=USER_A) == 1
    assert memory.clear_long_term(user_id=USER_A) == 0
    db_session.commit()
    assert memory.state(user_id=USER_A, now=NOW).items == []


def test_short_term_clear_covers_both_channels_and_converges(db_session: Session) -> None:
    _seed_users(db_session)
    scope = sha256(b"scope").hexdigest()
    source = sha256(b"message").hexdigest()
    db_session.execute(
        text(
            """
            insert into platform.companion_short_term_memory_turns (
              turn_id, user_id, context_scope_fingerprint,
              source_message_fingerprint, user_text, assistant_text,
              created_at, expires_at
            ) values (
              cast(:turn_id as uuid), cast(:user_id as uuid), :scope,
              :source, '小程序问题', '小程序回答', :now, :expires_at
            )
            """
        ),
        {
            "turn_id": str(uuid4()),
            "user_id": str(USER_A),
            "scope": scope,
            "source": source,
            "now": NOW,
            "expires_at": NOW + timedelta(hours=2),
        },
    )
    db_session.execute(
        text(
            """
            insert into messaging.ilink_short_term_memory_turns (
              turn_id, user_id, source_message_fingerprint,
              user_text, assistant_text, created_at, expires_at
            ) values (
              cast(:turn_id as uuid), cast(:user_id as uuid), :source,
              '微信问题', '微信回答', :now, :expires_at
            )
            """
        ),
        {
            "turn_id": str(uuid4()),
            "user_id": str(USER_A),
            "source": source,
            "now": NOW,
            "expires_at": NOW + timedelta(hours=2),
        },
    )
    db_session.commit()

    memory = CompanionMemoryStore(db_session)
    assert memory.state(user_id=USER_A, now=NOW).short_term.turn_count == 2
    assert memory.clear_short_term(user_id=USER_A) == 2
    assert memory.clear_short_term(user_id=USER_A) == 0
    db_session.commit()
    assert memory.state(user_id=USER_A, now=NOW).short_term.turn_count == 0
