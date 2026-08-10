from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.governance.contracts import GlobalAskContext
from app.domains.companion.short_term_memory import (
    CompanionShortTermMemory,
    companion_context_scope_fingerprint,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-0000-0000-00000000c201")


def _insert_user(session: Session) -> None:
    session.execute(
        text(
            "insert into learner.users (user_id, display_name, account_status) "
            "values (:user_id, 'companion-memory', 'active')"
        ),
        {"user_id": str(USER_ID)},
    )
    session.commit()


def _context(*, question_id: str, learner_version: str = "brain-v1") -> GlobalAskContext:
    return GlobalAskContext(
        routeId="pages/capture-import/index",
        workspace="training",
        objectRefs=[
            {
                "type": "external_question",
                "id": question_id,
                "version": "00000000-0000-4000-8000-000000000210",
            }
        ],
        taskMode="practice",
        taskStage="reviewing",
        learnerStateVersion=learner_version,
    )


def test_context_scope_survives_learner_refresh_but_separates_questions() -> None:
    first = companion_context_scope_fingerprint(_context(question_id="a" * 64))
    refreshed = companion_context_scope_fingerprint(
        _context(question_id="a" * 64, learner_version="brain-v2")
    )
    another_question = companion_context_scope_fingerprint(
        _context(question_id="b" * 64)
    )

    assert first == refreshed
    assert first != another_question


def test_companion_memory_is_scoped_bounded_expiring_and_idempotent(
    db_session: Session,
) -> None:
    _insert_user(db_session)
    memory = CompanionShortTermMemory(db_session)
    first_scope = companion_context_scope_fingerprint(_context(question_id="a" * 64))
    second_scope = companion_context_scope_fingerprint(_context(question_id="b" * 64))
    for index in range(5):
        memory.remember(
            user_id=USER_ID,
            context_scope_fingerprint=first_scope,
            source_message_id=f"message-{index}",
            user_text=f"用户问题 {index}",
            assistant_text=f"岸岸回答 {index}",
            now=NOW + timedelta(minutes=index),
        )
    memory.remember(
        user_id=USER_ID,
        context_scope_fingerprint=first_scope,
        source_message_id="message-4",
        user_text="重复问题",
        assistant_text="重复回答",
        now=NOW + timedelta(minutes=10),
    )
    memory.remember(
        user_id=USER_ID,
        context_scope_fingerprint=second_scope,
        source_message_id="another-question",
        user_text="另一道题",
        assistant_text="另一段回答",
        now=NOW,
    )
    db_session.commit()

    messages = memory.recent_messages(
        user_id=USER_ID,
        context_scope_fingerprint=first_scope,
        now=NOW + timedelta(minutes=6),
    )

    assert [(item.role, item.content) for item in messages] == [
        ("user", "用户问题 1"),
        ("assistant", "岸岸回答 1"),
        ("user", "用户问题 2"),
        ("assistant", "岸岸回答 2"),
        ("user", "用户问题 3"),
        ("assistant", "岸岸回答 3"),
        ("user", "用户问题 4"),
        ("assistant", "岸岸回答 4"),
    ]
    assert [
        item.content
        for item in memory.recent_messages(
            user_id=USER_ID,
            context_scope_fingerprint=second_scope,
            now=NOW + timedelta(minutes=6),
        )
    ] == ["另一道题", "另一段回答"]
    assert memory.recent_messages(
        user_id=USER_ID,
        context_scope_fingerprint=first_scope,
        now=NOW + timedelta(hours=3),
    ) == ()


def test_account_disable_erases_companion_memory(db_session: Session) -> None:
    _insert_user(db_session)
    memory = CompanionShortTermMemory(db_session)
    scope = companion_context_scope_fingerprint(_context(question_id="a" * 64))
    memory.remember(
        user_id=USER_ID,
        context_scope_fingerprint=scope,
        source_message_id="before-disable",
        user_text="请短期记住",
        assistant_text="好。",
        now=NOW,
    )
    db_session.commit()

    db_session.execute(
        text(
            "update learner.users set account_status = 'disabled' "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_ID)},
    )
    db_session.commit()

    assert db_session.execute(
        text(
            "select count(*) from platform.companion_short_term_memory_turns "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_ID)},
    ).scalar_one() == 0
