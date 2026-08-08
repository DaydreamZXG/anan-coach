from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.domains.ilink_channel import hub_bridge
from app.domains.ilink_channel.hub_bridge import HubBridgeEvent
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


class _NestedTransaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        del args


class _NestedSession:
    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction()


class _FailingMemory:
    def __init__(self, session: object) -> None:
        del session

    def recent_messages(self, *, user_id: UUID) -> tuple[()]:
        del user_id
        raise SQLAlchemyError("read failed")

    def remember(self, **kwargs: object) -> None:
        del kwargs
        raise SQLAlchemyError("write failed")


def test_ilink_memory_read_and_write_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub_bridge, "ILinkShortTermMemory", _FailingMemory)
    session = _NestedSession()

    history = hub_bridge._recent_short_term_history(  # type: ignore[arg-type]
        session,
        user_id=USER_A,
    )
    hub_bridge._remember_short_term_turn(  # type: ignore[arg-type]
        session,
        user_id=USER_A,
        source_message_id="message-fail-open",
        user_text="继续聊",
        assistant_text="继续。",
    )

    assert history == ()


def test_hub_bridge_remembers_only_after_text_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _Session:
        def __enter__(self) -> "_Session":
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def commit(self) -> None:
            events.append("commit")

        def rollback(self) -> None:
            events.append("rollback")

    sessions = [_Session(), _Session()]

    class _SessionFactory:
        def __call__(self) -> _Session:
            return sessions.pop(0)

    class _FactoryProvider:
        def __call__(self) -> _SessionFactory:
            return _SessionFactory()

    class _HubSettings:
        @classmethod
        def from_environment(cls) -> object:
            return object()

    class _HubClient:
        def __init__(self, settings: object) -> None:
            del settings

    class _HubRepository:
        def __init__(self, session: _Session) -> None:
            del session

        def for_bot(self, *, bot_id: str) -> dict[str, str]:
            del bot_id
            return {"user_id": str(USER_A)}

    class _ReceiptRepository:
        def __init__(self, session: _Session) -> None:
            del session

        def lock_command(self, *, command_id: str) -> None:
            del command_id

        def command_receipt(self, *, command_id: str) -> None:
            del command_id
            return None

        def save_command_receipt(self, **kwargs: object) -> None:
            del kwargs
            events.append("receipt")

    class _ControlPlane:
        def handle(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            return None

    class _Responder:
        def __init__(self, *, settings: object) -> None:
            del settings

        def reply(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            return "先做资料分析。"

    monkeypatch.setattr(hub_bridge, "get_settings", lambda: object())
    monkeypatch.setattr(hub_bridge, "OpenILinkHubSettings", _HubSettings)
    monkeypatch.setattr(hub_bridge, "OpenILinkHubClient", _HubClient)
    monkeypatch.setattr(hub_bridge, "get_session_factory", _FactoryProvider())
    monkeypatch.setattr(hub_bridge, "OpenILinkHubRepository", _HubRepository)
    monkeypatch.setattr(hub_bridge, "ILinkRepository", _ReceiptRepository)
    monkeypatch.setattr(hub_bridge, "ILinkControlPlane", _ControlPlane)
    monkeypatch.setattr(hub_bridge, "AccountScopedCompanionResponder", _Responder)
    monkeypatch.setattr(
        hub_bridge,
        "_recent_short_term_history",
        lambda *args, **kwargs: (),
    )
    monkeypatch.setattr(
        hub_bridge,
        "_send_with_retry",
        lambda **kwargs: events.append("send"),
    )
    monkeypatch.setattr(
        hub_bridge,
        "_remember_short_term_turn",
        lambda *args, **kwargs: events.append("remember"),
    )

    hub_bridge.process_hub_event(
        HubBridgeEvent(
            event_id="event-1",
            event_type="message.text",
            trace_id="trace-1",
            installation_id="installation-1",
            bot_id="bot-1",
            sender_id="sender-1",
            source_message_id="message-1",
            content="接下来做什么？",
        )
    )

    assert events.index("send") < events.index("remember") < events.index("receipt")
