from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.companion.channel import AccountScopedCompanionResponder
from app.domains.ilink_channel.material_intake import PRIVATE_READING_ROOM_PATH
from app.domains.ilink_channel.plan_projection import empty_plan_reply
from app.domains.ilink_channel.protocol import InboundMessage, MediaReference, ValidatedImage
from app.domains.ilink_channel.repository import ILinkRepository
from app.domains.ilink_channel.resolver import StoredImage
from app.domains.ilink_channel.schemas import AnswerProjection
from app.domains.ilink_channel.service import ILinkChannelService
from app.domains.ilink_channel.settings import ILinkSettings
from app.domains.ilink_channel.workflow import ILinkPhotoWorkflow


NOW = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
TODAY_SHANGHAI = date(2026, 7, 21)
USER_A = UUID("00000000-0000-0000-0000-000000009201")


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
    session.execute(
        text(
            "insert into learner.users (user_id, display_name, account_status) "
            "values (:user_id, :name, 'active')"
        ),
        {"user_id": str(USER_A), "name": "expansion-user"},
    )
    session.commit()


def bind(session: Session, *, user_id: UUID, sender: str) -> None:
    service = ILinkChannelService(settings=channel_settings(), clock=lambda: NOW)
    pending = service.issue(
        session, user_id=user_id, command_id=f"issue-{sender}", replace_pending=False
    )
    session.commit()
    assert pending.binding_code
    service.confirm_sender(
        session,
        code=pending.binding_code,
        sender_id=sender,
        source_message_id=f"confirm-{sender}",
    )
    session.commit()


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

    def stat(self, *, object_ref):
        return 4096


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
        return ValidatedImage(
            payload=b"valid-image", mime_type="image/png", width=100, height=100
        )

    def send_text(self, **kwargs):
        self.sent.append(kwargs)


class FakeCompanion:
    def __init__(self) -> None:
        self.calls = []

    def reply(self, _session, **kwargs):
        self.calls.append(kwargs)
        return "先复盘这周反复出现的资料分析耗时，再调整今天的训练顺序。"


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


def material_projection() -> AnswerProjection:
    return AnswerProjection(
        answerKind="ambiguous",
        answerText="当前无法唯一确定原题",
        explanation="讲义类图片没有唯一题目命中。",
        track="xingce",
        recognizedPayload={
            "contractVersion": "ilink-query-recognition@v1",
            "track": "xingce",
            "segments": [
                {
                    "segmentId": "seg-1",
                    "segmentKind": "question",
                    "text": "第一章 言语理解讲义要点",
                    "confidence": 0.9,
                },
                {
                    "segmentId": "seg-2",
                    "segmentKind": "material",
                    "text": "主旨题优先找转折词与结论词。",
                    "confidence": 0.8,
                },
            ],
            "segmentRefs": ["seg-1", "seg-2"],
        },
    )


def build_workflow(
    resolver: FakeResolver,
    *,
    companion: FakeCompanion | None = None,
) -> ILinkPhotoWorkflow:
    return ILinkPhotoWorkflow(
        core_settings=Settings(),
        channel_settings=channel_settings(),
        resolver=resolver,  # type: ignore[arg-type]
        companion_responder=companion,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )


def test_plain_text_uses_account_scoped_companion_after_binding(
    db_session: Session,
) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a")
    companion = FakeCompanion()
    protocol = FakeProtocol()
    workflow = build_workflow(FakeResolver(material_projection()), companion=companion)

    workflow.process(
        db_session,
        message=inbound("companion-1", sender="sender-a", text_value="我今天应该先补什么？"),
        client=protocol,  # type: ignore[arg-type]
    )

    assert protocol.sent[-1]["text"].startswith("先复盘这周")
    assert companion.calls == [
        {
            "user_id": USER_A,
            "source_message_id": "companion-1",
            "question": "我今天应该先补什么？",
        }
    ]


def test_account_scoped_companion_serializes_profile_uuid(
    db_session: Session,
    monkeypatch,
) -> None:
    channel_module = import_module("app.domains.companion.channel")
    profile_version_id = uuid4()
    captured = {}

    class FakeLearningBrainService:
        def get_or_create_profile(self, _session, *, user_id):
            assert user_id == str(USER_A)
            return SimpleNamespace(profile_version_id=profile_version_id)

    class FakeCompanionService:
        def ask(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(answer="先做资料分析。")

    monkeypatch.setattr(channel_module, "LearningBrainService", FakeLearningBrainService)
    monkeypatch.setattr(channel_module, "CompanionRepository", lambda _session: object())
    monkeypatch.setattr(channel_module, "AIAuditRepository", lambda _session: object())
    monkeypatch.setattr(channel_module, "CommerceRepository", lambda _session: object())
    monkeypatch.setattr(
        channel_module,
        "AppStoreSignedTransactionVerifier",
        lambda _settings: object(),
    )
    monkeypatch.setattr(channel_module, "CommerceService", lambda **_kwargs: object())
    monkeypatch.setattr(channel_module, "companion_provider", lambda _settings: object())
    monkeypatch.setattr(
        channel_module,
        "CompanionService",
        lambda **_kwargs: FakeCompanionService(),
    )

    reply = AccountScopedCompanionResponder(settings=Settings()).reply(
        db_session,
        user_id=USER_A,
        source_message_id="companion-profile-version-1",
        question="今天练什么？",
    )

    assert reply == "先做资料分析。"
    assert captured["request"].context.learner_state_version == str(profile_version_id)


def test_empty_message_does_not_call_companion_after_binding(
    db_session: Session,
) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a")
    companion = FakeCompanion()
    protocol = FakeProtocol()
    workflow = build_workflow(FakeResolver(material_projection()), companion=companion)

    workflow.process(
        db_session,
        message=inbound("empty-1", sender="sender-a", text_value="   "),
        client=protocol,  # type: ignore[arg-type]
    )

    assert "没有可读取的文字或图片" in protocol.sent[-1]["text"]
    assert companion.calls == []


def test_store_material_moves_image_into_pending_private_reading_room(
    db_session: Session,
) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a")
    resolver = FakeResolver(material_projection())
    protocol = FakeProtocol()
    workflow = build_workflow(resolver)
    workflow.process(
        db_session,
        message=inbound("photo-1", sender="sender-a", image=True),
        client=protocol,  # type: ignore[arg-type]
    )
    workflow.process(
        db_session,
        message=inbound("store-1", sender="sender-a", text_value="存资料"),
        client=protocol,  # type: ignore[arg-type]
    )
    reply = protocol.sent[-1]["text"]
    assert "已收进你的私有阅读室，去小程序核对识别结果" in reply
    assert PRIVATE_READING_ROOM_PATH in reply
    assert "不进入公共题库" in reply

    artifact = db_session.execute(
        text(
            "select * from learning.source_artifact_versions "
            "where owner_user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).mappings().one()
    assert artifact["source_kind"] == "camera_image"
    assert artifact["state"] == "available"
    assert artifact["content_sha256"] == "a" * 64
    assert artifact["byte_size"] == 4096

    recognition = db_session.execute(
        text(
            "select * from learning.recognition_revisions "
            "where owner_user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).mappings().one()
    assert recognition["state"] == "candidate"
    assert recognition["recognition_kind"] == "printed_ocr"
    payload = dict(recognition["candidate_payload"])
    assert [span["text"] for span in payload["textSpans"]] == [
        "第一章 言语理解讲义要点",
        "主旨题优先找转折词与结论词。",
    ]

    # 待核对前不产生任何私有源版本，也就没有检索片段。
    sources = db_session.execute(
        text(
            "select count(*) from learning.private_knowledge_source_versions "
            "where owner_user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one()
    assert sources == 0

    # 对象所有权已转移：查询侧引用清空，且不进入删除队列、未被删除。
    transient_ref = db_session.execute(
        text(
            "select transient_object_ref from messaging.ilink_photo_queries "
            "where user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one()
    assert transient_ref is None
    queue_count = db_session.execute(
        text("select count(*) from messaging.ilink_object_deletion_queue")
    ).scalar_one()
    assert queue_count == 0
    assert not resolver.storage.deleted
    assert str(artifact["object_ref"]).startswith("tos://bucket/private/ilink/")

    # 审计路径：命令回执 + 无内容事件。
    receipt = db_session.execute(
        text(
            "select * from messaging.ilink_command_receipts "
            "where command_type = 'store_material'"
        )
    ).mappings().one()
    assert str(receipt["user_id"]) == str(USER_A)
    event_count = db_session.execute(
        text(
            "select count(*) from messaging.ilink_channel_events "
            "where event_type = 'material_intake_transferred'"
        )
    ).scalar_one()
    assert event_count == 1


def test_store_material_replays_identically_for_repeat_replies(
    db_session: Session,
) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a")
    resolver = FakeResolver(material_projection())
    protocol = FakeProtocol()
    workflow = build_workflow(resolver)
    workflow.process(
        db_session,
        message=inbound("photo-1", sender="sender-a", image=True),
        client=protocol,  # type: ignore[arg-type]
    )
    workflow.process(
        db_session,
        message=inbound("store-1", sender="sender-a", text_value="存资料"),
        client=protocol,  # type: ignore[arg-type]
    )
    first_reply = protocol.sent[-1]["text"]
    workflow.process(
        db_session,
        message=inbound("store-2", sender="sender-a", text_value="存资料"),
        client=protocol,  # type: ignore[arg-type]
    )
    assert protocol.sent[-1]["text"] == first_reply
    artifacts = db_session.execute(
        text(
            "select count(*) from learning.source_artifact_versions "
            "where owner_user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one()
    recognitions = db_session.execute(
        text(
            "select count(*) from learning.recognition_revisions "
            "where owner_user_id = :user_id"
        ),
        {"user_id": str(USER_A)},
    ).scalar_one()
    assert (artifacts, recognitions) == (1, 1)


def test_store_material_rejects_unbound_sender(db_session: Session) -> None:
    install_schema(db_session)
    protocol = FakeProtocol()
    workflow = build_workflow(FakeResolver(material_projection()))
    workflow.process(
        db_session,
        message=inbound("store-x", sender="stranger", text_value="存资料"),
        client=protocol,  # type: ignore[arg-type]
    )
    assert "还没有连接到你的岸岸账户" in protocol.sent[-1]["text"]
    artifacts = db_session.execute(
        text("select count(*) from learning.source_artifact_versions")
    ).scalar_one()
    assert artifacts == 0


def test_store_material_window_closes_after_retention_sweep(
    db_session: Session,
) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a")
    resolver = FakeResolver(material_projection())
    protocol = FakeProtocol()
    workflow = build_workflow(resolver)
    workflow.process(
        db_session,
        message=inbound("photo-1", sender="sender-a", image=True),
        client=protocol,  # type: ignore[arg-type]
    )
    swept = ILinkRepository(db_session).expire_answered_transients(
        before=NOW + timedelta(seconds=901),
        limit=10,
        now=NOW + timedelta(seconds=901),
    )
    assert swept == 1
    db_session.commit()
    workflow.process(
        db_session,
        message=inbound("store-late", sender="sender-a", text_value="存资料"),
        client=protocol,  # type: ignore[arg-type]
    )
    assert "转存窗口" in protocol.sent[-1]["text"]
    artifacts = db_session.execute(
        text("select count(*) from learning.source_artifact_versions")
    ).scalar_one()
    assert artifacts == 0
    queued = db_session.execute(
        text("select count(*) from messaging.ilink_object_deletion_queue")
    ).scalar_one()
    assert queued == 1


def seed_today_plan(session: Session, *, user_id: UUID, minutes: int = 55) -> None:
    preference_id = uuid4()
    capacity_id = uuid4()
    plan_id = uuid4()
    session.execute(
        text(
            """
            insert into learning.weekly_study_preference_versions (
              preference_version_id, command_id, user_id, version_no, timezone,
              weekday_minutes, weekend_minutes, long_block_minutes,
              fragment_minutes, regular_rest_weekday, regular_mock_weekday,
              effective_from, source_type, confirmed, input_fingerprint
            ) values (
              :preference_id, :command_id, :user_id, 1, 'Asia/Shanghai',
              120, 180, 60, 15, 7, 6, :day, 'user_confirmed', true, :fingerprint
            )
            """
        ),
        {
            "preference_id": str(preference_id),
            "command_id": f"pref-{preference_id}",
            "user_id": str(user_id),
            "day": TODAY_SHANGHAI,
            "fingerprint": "1" * 64,
        },
    )
    session.execute(
        text(
            """
            insert into learning.daily_capacity_versions (
              capacity_version_id, command_id, user_id, local_date, version_no,
              available_minutes, energy_level, stress_level, confidence_level,
              confirmed, source_type, weekly_preference_version_id,
              input_fingerprint, recorded_at, xingce_only
            ) values (
              :capacity_id, :command_id, :user_id, :day, 1,
              90, 3, 3, 3, true, 'user_override', :preference_id,
              :fingerprint, :now, false
            )
            """
        ),
        {
            "capacity_id": str(capacity_id),
            "command_id": f"capacity-{capacity_id}",
            "user_id": str(user_id),
            "day": TODAY_SHANGHAI,
            "preference_id": str(preference_id),
            "fingerprint": "2" * 64,
            "now": NOW,
        },
    )
    session.execute(
        text(
            """
            insert into learning.daily_plan_versions (
              plan_version_id, command_id, user_id, local_date, version_no,
              capacity_version_id, weekly_preference_version_id, day_mode,
              headline, rationale, goal_gap, scheduled_minutes,
              due_reflection_count, due_verification_count, track_explanation,
              input_versions, evidence_refs, invalidation_conditions,
              replan_reason_codes, plan_basis, contract_version,
              algorithm_version, input_fingerprint, generated_at, valid_until
            ) values (
              :plan_id, :command_id, :user_id, :day, 1,
              :capacity_id, :preference_id, 'normal',
              :headline, '按目标分差与容量排序。',
              cast('{"readiness": "complete"}' as jsonb), :minutes,
              0, 0, cast('{}' as jsonb),
              cast('{}' as jsonb), cast('[{"owner": "planning"}]' as jsonb),
              cast('["daily_capacity_changed"]' as jsonb),
              cast('[]' as jsonb), 'evidence_priority_v2', 'today-dashboard@v3',
              'plan-algo@v2', :fingerprint, :now, :valid_until
            )
            """
        ),
        {
            "plan_id": str(plan_id),
            "command_id": f"plan-{plan_id}",
            "user_id": str(user_id),
            "day": TODAY_SHANGHAI,
            "capacity_id": str(capacity_id),
            "preference_id": str(preference_id),
            "headline": "今天双轨推进：行测 30 分钟，申论 25 分钟",
            "minutes": minutes,
            "fingerprint": "3" * 64,
            "now": NOW,
            "valid_until": NOW + timedelta(days=1),
        },
    )
    for order in (1, 2):
        session.execute(
            text(
                """
                insert into learning.daily_plan_tasks (
                  task_id, plan_version_id, user_id, local_date, plan_version_no,
                  candidate_key, work_item_key, task_order, track, stage, kind,
                  title, reason, estimated_minutes, mandatory, start_url,
                  priority_score, goal_gain, ability_gain, verification_urgency,
                  time_fit, load_penalty, track_balance_bonus, evidence_refs,
                  completion_contract, input_fingerprint
                ) values (
                  :task_id, :plan_id, :user_id, :day, 1,
                  :candidate_key, :work_item_key, :task_order, 'xingce',
                  'objective_practice', 'practice',
                  :title, '当前收益最高的闭环。', :estimated_minutes, false,
                  '/pages/workbench/index',
                  0.5, 0.5, 0.5, 0.5, 0.5, 0.1, 0.1,
                  cast('[{"owner": "planning"}]' as jsonb),
                  cast('{"objective": "完成练习"}' as jsonb), :fingerprint
                )
                """
            ),
            {
                "task_id": str(uuid4()),
                "plan_id": str(plan_id),
                "user_id": str(user_id),
                "day": TODAY_SHANGHAI,
                "candidate_key": f"candidate-{order}",
                "work_item_key": str(order) * 64,
                "task_order": order,
                "title": f"练习任务 {order}",
                "estimated_minutes": 30 if order == 1 else 25,
                "fingerprint": str(order + 4) * 64,
            },
        )
    session.commit()


def test_plan_query_returns_readonly_summary_without_writes(
    db_session: Session,
) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a")
    seed_today_plan(db_session, user_id=USER_A, minutes=55)
    protocol = FakeProtocol()
    workflow = build_workflow(FakeResolver(material_projection()))
    counts_before = _write_counts(db_session)
    workflow.process(
        db_session,
        message=inbound("plan-1", sender="sender-a", text_value="今天练什么"),
        client=protocol,  # type: ignore[arg-type]
    )
    reply = protocol.sent[-1]["text"]
    assert "今天双轨推进：行测 30 分钟，申论 25 分钟" in reply
    assert "2 个任务" in reply
    assert "55 分钟" in reply
    assert "打开小程序" in reply
    # 只读边界：不展示能力数据，不产生任何写入。
    assert "分差" not in reply
    assert _write_counts(db_session) == counts_before

    for alias in ("今天学什么", "今日安排"):
        workflow.process(
            db_session,
            message=inbound(f"plan-{alias}", sender="sender-a", text_value=alias),
            client=protocol,  # type: ignore[arg-type]
        )
        assert "2 个任务" in protocol.sent[-1]["text"]


def test_plan_query_empty_state_asks_for_goal_first(db_session: Session) -> None:
    install_schema(db_session)
    bind(db_session, user_id=USER_A, sender="sender-a")
    protocol = FakeProtocol()
    workflow = build_workflow(FakeResolver(material_projection()))
    workflow.process(
        db_session,
        message=inbound("plan-empty", sender="sender-a", text_value="今天练什么"),
        client=protocol,  # type: ignore[arg-type]
    )
    reply = protocol.sent[-1]["text"]
    assert "先定目标" in reply
    assert "打开小程序" in reply
    plans = db_session.execute(
        text("select count(*) from learning.daily_plan_versions")
    ).scalar_one()
    assert plans == 0


def test_empty_plan_copy_matches_plan_copy_three_branches() -> None:
    goal_branch = empty_plan_reply(goal_ready=False, capacity_confirmed=False)
    capacity_branch = empty_plan_reply(goal_ready=True, capacity_confirmed=False)
    pending_branch = empty_plan_reply(goal_ready=True, capacity_confirmed=True)
    assert "先定目标" in goal_branch
    assert "先确认今天能学多久" in capacity_branch
    assert "还没就绪" in pending_branch
    assert len({goal_branch, capacity_branch, pending_branch}) == 3
    for reply in (goal_branch, capacity_branch, pending_branch):
        assert "小程序" in reply


def _write_counts(session: Session) -> tuple[int, ...]:
    return tuple(
        session.execute(text(f"select count(*) from {table}")).scalar_one()
        for table in (
            "learning.daily_plan_versions",
            "learning.daily_capacity_versions",
            "messaging.ilink_command_receipts",
            "messaging.ilink_channel_events",
        )
    )
