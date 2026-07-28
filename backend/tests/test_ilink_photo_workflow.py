from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ilink_channel.protocol import InboundMessage, MediaReference, ValidatedImage
from app.domains.ilink_channel.repository import ILinkConflict, ILinkRepository
from app.domains.ilink_channel.resolver import StoredImage
from app.domains.ilink_channel.schemas import AnswerProjection
from app.domains.ilink_channel.service import ILinkChannelService
from app.domains.ilink_channel.settings import ILinkSettings
from app.domains.ilink_channel.workflow import ILinkPhotoWorkflow


NOW = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
USER_A = UUID("00000000-0000-0000-0000-000000009101")
USER_B = UUID("00000000-0000-0000-0000-000000009102")


def channel_settings() -> ILinkSettings:
    return ILinkSettings(
        enabled=True,
        bot_account_id="anan-bot-1",
        bot_token="official-token-secret",
        base_url="https://ilinkai.weixin.qq.com",
        cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
        app_id="bot",
        app_client_version=132102,
        bot_agent="Anan/1.0",
        identity_key_id="ilink-key-v1",
        identity_encryption_key=base64.b64encode(b"e" * 32).decode(),
        identity_hmac_key="h" * 40,
        binding_ttl_seconds=600,
        long_poll_timeout_seconds=35,
        request_timeout_seconds=15,
        maximum_image_bytes=10 * 1024 * 1024,
        transient_retention_seconds=900,
        query_retention_days=30,
        recognition_route="photo-query-approved-v1",
    )


def install_schema(session: Session) -> None:
    if session.execute(
        text("select to_regclass('messaging.ilink_channel_links')")
    ).scalar_one() is None:
        migration = import_module(
            "migrations.versions.20260721_48_ilink_photo_channel"
        )
        for statement in migration._STATEMENTS:
            session.connection().exec_driver_sql(statement.replace("%", "%%"))
    for user_id in (USER_A, USER_B):
        session.execute(
            text(
                "insert into learner.users (user_id, display_name, account_status) "
                "values (:user_id, :name, 'active')"
            ),
            {"user_id": str(user_id), "name": f"user-{user_id}"},
        )
    session.commit()


def bind(
    session: Session,
    *,
    user_id: UUID,
    sender: str,
    command_suffix: str,
) -> None:
    service = ILinkChannelService(settings=channel_settings(), clock=lambda: NOW)
    pending = service.issue(
        session,
        user_id=user_id,
        command_id=f"issue-{command_suffix}",
        replace_pending=False,
    )
    session.commit()
    assert pending.binding_code
    service.confirm_sender(
        session,
        code=pending.binding_code,
        sender_id=sender,
        source_message_id=f"confirm-{command_suffix}",
    )
    session.commit()


def test_binding_is_unique_expiring_and_cross_account_isolated(db_session: Session) -> None:
    install_schema(db_session)
    service = ILinkChannelService(settings=channel_settings(), clock=lambda: NOW)
    first = service.issue(
        db_session,
        user_id=USER_A,
        command_id="issue-user-a",
        replace_pending=False,
    )
    second = service.issue(
        db_session,
        user_id=USER_B,
        command_id="issue-user-b",
        replace_pending=False,
    )
    db_session.commit()
    assert first.binding_code and second.binding_code
    service.confirm_sender(
        db_session,
        code=first.binding_code,
        sender_id="wechat-sender-1",
        source_message_id="confirm-a",
    )
    db_session.commit()
    try:
        service.confirm_sender(
            db_session,
            code=second.binding_code,
            sender_id="wechat-sender-1",
            source_message_id="confirm-b",
        )
    except ILinkConflict as exc:
        assert str(exc) == "sender_already_bound"
        db_session.rollback()
    else:
        raise AssertionError("one iLink sender must not bind two learners")

    cipher = service.cipher
    fingerprint = cipher.fingerprint("anan-bot-1\0wechat-sender-1", purpose="sender")
    linked = ILinkRepository(db_session).link_for_sender(
        bot_account_id="anan-bot-1", sender_fingerprint=fingerprint
    )
    assert UUID(str(linked["user_id"])) == USER_A
    assert "wechat-sender-1" not in str(dict(linked))

    expired_service = ILinkChannelService(
        settings=channel_settings(),
        clock=lambda: NOW + timedelta(seconds=601),
    )
    expired_view = expired_service.view(db_session, user_id=USER_B)
    assert expired_view.link_state == "pending"
    assert expired_view.binding_code is None
    with pytest.raises(ILinkConflict, match="code_expired"):
        expired_service.confirm_sender(
            db_session,
            code=second.binding_code,
            sender_id="wechat-sender-2",
            source_message_id="confirm-expired",
        )
    db_session.rollback()

    service.revoke(
        db_session,
        user_id=USER_A,
        expected_link_id=first.link_id,
        command_id="revoke-user-a",
    )
    db_session.commit()
    assert service.view(db_session, user_id=USER_A).link_state == "revoked"


class FakeStorage:
    def __init__(self) -> None:
        self.deleted = []

    def put(self, *, user_id, query_id, image):
        return StoredImage(
            object_ref=f"tos://bucket/private/ilink/{user_id}/{query_id}/image.png",
            content_sha256="a" * 64,
            mime_type=image.mime_type,
        )

    def delete(self, *, object_ref):
        self.deleted.append(object_ref)


class FakeResolver:
    def __init__(self, projection: AnswerProjection) -> None:
        self.storage = FakeStorage()
        self.projection = projection

    def resolve(self, _session, **_kwargs):
        return self.projection


class FakeProtocol:
    def __init__(self) -> None:
        self.sent = []

    def download_image(self, _media):
        return ValidatedImage(payload=b"valid-image", mime_type="image/png", width=100, height=100)

    def send_text(self, **kwargs):
        self.sent.append(kwargs)


def inbound(message_id: str, *, sender: str, text_value: str = "", image: bool = False):
    return InboundMessage(
        message_id=message_id,
        sender_id=sender,
        context_token=f"context-{message_id}",
        run_id=None,
        text=text_value,
        image=(
            MediaReference(
                encrypted_query_param="p",
                full_url="https://novac2c.cdn.weixin.qq.com/c2c/download?id=1",
                aes_key=base64.b64encode(b"k" * 16).decode(),
            )
            if image
            else None
        ),
    )


def test_query_collection_and_reattempt_never_write_ability(db_session: Session) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a", command_suffix="a")
    resolver = FakeResolver(
        AnswerProjection(
            answerKind="authorized_exact",
            answerText="B",
            explanation="授权解析",
            track="xingce",
            recognizedPayload={"segmentRefs": ["s1"], "prompt": "题目"},
            contentVersionId="content-v1",
            authorizationSnapshotRef="license-snapshot-v1",
        )
    )
    protocol = FakeProtocol()
    workflow = ILinkPhotoWorkflow(
        core_settings=Settings(),
        channel_settings=channel_settings(),
        resolver=resolver,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    before = db_session.execute(text("select count(*) from learning.ability_estimate_versions")).scalar_one()
    workflow.process(
        db_session,
        message=inbound("photo-1", sender="sender-a", image=True),
        client=protocol,  # type: ignore[arg-type]
    )
    stored_message_ref = db_session.execute(
        text(
            "select source_message_id from messaging.ilink_photo_queries "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one()
    assert stored_message_ref != "photo-1"
    assert len(stored_message_ref) == 64
    assert "来源：题库授权原题" in protocol.sent[-1]["text"]
    assert "已查询 · 未收录" in protocol.sent[-1]["text"]
    workflow.process(
        db_session,
        message=inbound("collect-1", sender="sender-a", text_value="收录"),
        client=protocol,  # type: ignore[arg-type]
    )
    assert "已收录 · 暂不计入能力" in protocol.sent[-1]["text"]
    workflow.process(
        db_session,
        message=inbound("redo-1", sender="sender-a", text_value="重做"),
        client=protocol,  # type: ignore[arg-type]
    )
    assert "已标记待重做 · 暂不计入能力" in protocol.sent[-1]["text"]
    after = db_session.execute(text("select count(*) from learning.ability_estimate_versions")).scalar_one()
    assert before == after
    collection_state = db_session.execute(
        text(
            "select state from messaging.ilink_photo_collections where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one()
    assert collection_state == "queued_for_reattempt"
    # 答复完成后原图保留一个「存资料」转存窗口，窗口到期由维护任务送入可靠删除队列。
    assert not resolver.storage.deleted
    transient_ref = db_session.execute(
        text(
            "select transient_object_ref from messaging.ilink_photo_queries "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one()
    assert transient_ref
    swept = ILinkRepository(db_session).expire_answered_transients(
        before=NOW + timedelta(seconds=901),
        limit=10,
        now=NOW + timedelta(seconds=901),
    )
    assert swept == 1
    db_session.commit()
    queued_state = db_session.execute(
        text(
            "select state from messaging.ilink_object_deletion_queue "
            "where object_ref = :object_ref"
        ),
        {"object_ref": transient_ref},
    ).scalar_one()
    assert queued_state == "pending"


def test_unmatched_and_material_required_remain_truthfully_labelled(db_session: Session) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a", command_suffix="a")
    protocol = FakeProtocol()
    resolver = FakeResolver(
        AnswerProjection(
            answerKind="material_required",
            answerText="请补充共享材料",
            explanation="当前图片不完整。",
            track="essay",
            recognizedPayload={"segmentRefs": ["s1"]},
        )
    )
    workflow = ILinkPhotoWorkflow(
        core_settings=Settings(),
        channel_settings=channel_settings(),
        resolver=resolver,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    workflow.process(
        db_session,
        message=inbound("photo-material", sender="sender-a", image=True),
        client=protocol,  # type: ignore[arg-type]
    )
    assert "来源：缺少共享材料" in protocol.sent[-1]["text"]
    assert "官方答案" not in protocol.sent[-1]["text"]


def test_explicit_delete_and_account_disable_erase_private_channel_data(db_session: Session) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a", command_suffix="a")
    resolver = FakeResolver(
        AnswerProjection(
            answerKind="ai_candidate",
            answerText="C",
            explanation="双模型一致候选",
            track="xingce",
            recognizedPayload={"segmentRefs": ["s1"]},
            modelProvenance={"policy": "dual-independent-agreement@v1"},
        )
    )
    workflow = ILinkPhotoWorkflow(
        core_settings=Settings(),
        channel_settings=channel_settings(),
        resolver=resolver,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    protocol = FakeProtocol()
    workflow.process(
        db_session,
        message=inbound("photo-delete", sender="sender-a", image=True),
        client=protocol,  # type: ignore[arg-type]
    )
    workflow.process(
        db_session,
        message=inbound("delete-1", sender="sender-a", text_value="删除"),
        client=protocol,  # type: ignore[arg-type]
    )
    assert "已删除" in protocol.sent[-1]["text"]
    # 「删除」把仍在转存窗口内的原图送入可靠删除队列，不留孤儿对象。
    queued = db_session.execute(
        text("select count(*) from messaging.ilink_object_deletion_queue")
    ).scalar_one()
    assert queued == 1
    db_session.execute(
        text(
            "update learner.users set account_status = 'disabled' where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    )
    db_session.commit()
    for table in (
        "ilink_channel_links",
        "ilink_photo_queries",
        "ilink_photo_collections",
        "ilink_channel_events",
    ):
        count = db_session.execute(
            text(f"select count(*) from messaging.{table} where user_id = :user_id"),
            {"user_id": str(USER_A)},
        ).scalar_one()
        assert count == 0


def test_retention_expires_stale_media_and_uncollected_query_content(
    db_session: Session,
) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a", command_suffix="a")
    service = ILinkChannelService(settings=channel_settings(), clock=lambda: NOW)
    link = ILinkRepository(db_session).link_for_user(user_id=USER_A)
    assert link is not None
    query_id = UUID("00000000-0000-0000-0000-000000009199")
    repository = ILinkRepository(db_session)
    repository.insert_query(
        query_id=query_id,
        link_id=UUID(str(link["link_id"])),
        user_id=USER_A,
        bot_account_id="anan-bot-1",
        source_message_fingerprint=service.cipher.fingerprint(
            "anan-bot-1\0stale-photo-1", purpose="source-message"
        ),
        encrypted_context_token=service.cipher.encrypt(
            "context-secret", purpose=f"query-context:{query_id}"
        ),
        key_id=service.cipher.key_id,
        now=NOW - timedelta(seconds=1000),
    )
    object_ref = f"tos://bucket/private/ilink/{USER_A}/{query_id}/image.png"
    repository.start_processing(
        query_id=query_id,
        object_ref=object_ref,
        source_fingerprint="f" * 64,
        mime_type="image/png",
        now=NOW - timedelta(seconds=1000),
    )
    assert repository.expire_stale_processing(
        before=NOW - timedelta(seconds=900), limit=10, now=NOW
    ) == 1
    db_session.commit()
    stale = ILinkRepository(db_session).query(query_id=query_id)
    assert stale["state"] == "failed"
    assert stale["failure_code"] == "processing_expired"
    assert stale["encrypted_context_token"] is None
    assert stale["transient_object_ref"] is None
    queued = db_session.execute(
        text(
            "select state from messaging.ilink_object_deletion_queue "
            "where object_ref = :object_ref"
        ),
        {"object_ref": object_ref},
    ).scalar_one()
    assert queued == "pending"

    db_session.execute(
        text(
            "update messaging.ilink_photo_queries set created_at = :created_at "
            "where query_id = :query_id"
        ),
        {
            "query_id": str(query_id),
            "created_at": NOW - timedelta(days=31),
        },
    )
    assert ILinkRepository(db_session).expire_uncollected_queries(
        before=NOW - timedelta(days=30), limit=10, now=NOW
    ) == 1
    db_session.commit()
    expired = ILinkRepository(db_session).query(query_id=query_id)
    assert expired["state"] == "deleted"
    assert expired["source_fingerprint"] is None
    assert expired["reply_client_id"] is None
