from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ilink_channel.short_term_memory import ILinkShortTermMemory


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
USER_A = UUID("00000000-0000-0000-0000-00000000c101")
USER_B = UUID("00000000-0000-0000-0000-00000000c102")


def _insert_users(session: Session) -> None:
    for user_id in (USER_A, USER_B):
        session.execute(
            text(
                "insert into learner.users (user_id, display_name, account_status) "
                "values (:user_id, :display_name, 'active')"
            ),
            {"user_id": str(user_id), "display_name": f"memory-{user_id}"},
        )
    session.commit()


def test_short_term_memory_is_account_scoped_bounded_expiring_and_idempotent(
    db_session: Session,
) -> None:
    _insert_users(db_session)
    memory = ILinkShortTermMemory(db_session)
    memory.remember(
        user_id=USER_A,
        source_message_id="expired-message",
        user_text="昨天那个呢？",
        assistant_text="这条已经过期。",
        now=NOW - timedelta(hours=3),
    )
    for index in range(5):
        memory.remember(
            user_id=USER_A,
            source_message_id=f"message-{index}",
            user_text=f"用户问题 {index}",
            assistant_text=f"岸岸回答 {index}",
            now=NOW + timedelta(minutes=index),
        )
    memory.remember(
        user_id=USER_A,
        source_message_id="message-4",
        user_text="重复投递不应覆盖",
        assistant_text="重复投递不应新增",
        now=NOW + timedelta(minutes=10),
    )
    memory.remember(
        user_id=USER_B,
        source_message_id="user-b-message",
        user_text="另一个账号的问题",
        assistant_text="另一个账号的回答",
        now=NOW + timedelta(minutes=5),
    )
    db_session.commit()

    messages = memory.recent_messages(
        user_id=USER_A,
        now=NOW + timedelta(minutes=6),
    )

    assert [(message.role, message.content) for message in messages] == [
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
        message.content
        for message in memory.recent_messages(
            user_id=USER_B,
            now=NOW + timedelta(minutes=6),
        )
    ] == ["另一个账号的问题", "另一个账号的回答"]
    assert memory.recent_messages(
        user_id=USER_A,
        now=NOW + timedelta(hours=3),
    ) == ()
    assert db_session.execute(
        text(
            "select count(*) from messaging.ilink_short_term_memory_turns "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one() == 4


def test_account_disable_erases_short_term_memory(db_session: Session) -> None:
    _insert_users(db_session)
    memory = ILinkShortTermMemory(db_session)
    memory.remember(
        user_id=USER_A,
        source_message_id="message-before-disable",
        user_text="请记住当前话题",
        assistant_text="只会短期记住。",
        now=NOW,
    )
    db_session.commit()

    db_session.execute(
        text(
            "update learner.users set account_status = 'disabled' "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    )
    db_session.commit()

    assert db_session.execute(
        text(
            "select count(*) from messaging.ilink_short_term_memory_turns "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one() == 0
