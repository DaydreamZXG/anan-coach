from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ilink_channel.material_intake import (
    ILinkMaterialIntake,
    STORE_MATERIAL_COMMAND,
)
from app.domains.ilink_channel.plan_projection import is_plan_query, today_plan_reply
from app.domains.ilink_channel.protocol import (
    ILinkProtocolClient,
    ILinkProtocolError,
    InboundMessage,
)
from app.domains.ilink_channel.repository import ILinkConflict, ILinkNotFound, ILinkRepository
from app.domains.ilink_channel.resolver import (
    PhotoQuestionResolver,
    PhotoResolutionError,
    StoredImage,
)
from app.domains.ilink_channel.service import (
    ILinkChannelService,
    query_view,
)
from app.domains.ilink_channel.settings import ILinkSettings


class ILinkPhotoWorkflow:
    def __init__(
        self,
        *,
        core_settings: Settings,
        channel_settings: ILinkSettings,
        resolver: PhotoQuestionResolver | None = None,
        clock=None,
    ) -> None:
        channel_settings.validate_worker()
        self._settings = channel_settings
        self._service = ILinkChannelService(settings=channel_settings, clock=clock)
        self._resolver = resolver or PhotoQuestionResolver(
            core_settings=core_settings,
            channel_settings=channel_settings,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._material_intake = ILinkMaterialIntake(
            settings=channel_settings,
            cipher=self._service.cipher,
            storage=self._resolver.storage,
            clock=self._clock,
        )

    def process(
        self,
        session: Session,
        *,
        message: InboundMessage,
        client: ILinkProtocolClient,
    ) -> None:
        sender_fingerprint = self._service.cipher.fingerprint(
            f"{self._settings.bot_account_id}\0{message.sender_id}", purpose="sender"
        )
        repository = ILinkRepository(session)
        link = repository.link_for_sender(
            bot_account_id=self._settings.bot_account_id,
            sender_fingerprint=sender_fingerprint,
        )
        text_command = message.text.strip()
        if link is None and _looks_like_binding_code(text_command):
            self._confirm_binding(session, message=message, client=client)
            return
        if link is None:
            self._send_direct(
                client,
                message=message,
                purpose="unbound",
                text="还没有连接到你的岸岸账户。请先打开小程序「拍照搜题」，生成连接码后把连接码发给我。",
            )
            return
        user_id = UUID(str(link["user_id"]))
        if text_command in {"收录", "重做", "删除"} and message.image is None:
            self._apply_query_command(
                session,
                user_id=user_id,
                message=message,
                client=client,
            )
            return
        if text_command == STORE_MATERIAL_COMMAND and message.image is None:
            self._store_material(
                session,
                user_id=user_id,
                message=message,
                client=client,
            )
            return
        if is_plan_query(text_command) and message.image is None:
            self._reply_plan_summary(
                session,
                user_id=user_id,
                message=message,
                client=client,
            )
            return
        if message.image is None:
            self._send_direct(
                client,
                message=message,
                purpose="help",
                text=(
                    "请发送一张已裁剪的单题图片，尽量包含完整题干、共享材料和全部选项。"
                    "查询不会写入能力画像；得到结果后可回复「收录」「重做」「删除」，"
                    "讲义或笔记类图片可回复「存资料」收进私有阅读室。"
                    "发送「今天练什么」可查看今日安排摘要。"
                ),
            )
            return
        self._process_photo(
            session,
            user_id=user_id,
            link_id=UUID(str(link["link_id"])),
            message=message,
            client=client,
        )

    def _confirm_binding(
        self,
        session: Session,
        *,
        message: InboundMessage,
        client: ILinkProtocolClient,
    ) -> None:
        try:
            self._service.confirm_sender(
                session,
                code=message.text,
                sender_id=message.sender_id,
                source_message_id=message.message_id,
            )
            session.commit()
            reply = "连接成功。现在可以直接发送裁剪好的单题图片。查询和收录都不会计入能力，只有之后独立作答才会。"
        except ILinkNotFound:
            session.rollback()
            reply = "连接码无效。请回到小程序「拍照搜题」重新生成。"
        except ILinkConflict as exc:
            session.rollback()
            reply = (
                "连接码已过期，请回到小程序重新生成。"
                if str(exc) == "code_expired"
                else "这个微信入口已连接到其他账户，请先在原账户解除连接。"
            )
        self._send_direct(client, message=message, purpose="binding", text=reply)

    def _apply_query_command(
        self,
        session: Session,
        *,
        user_id: UUID,
        message: InboundMessage,
        client: ILinkProtocolClient,
    ) -> None:
        try:
            view = self._service.command_latest(
                session,
                user_id=user_id,
                command=message.text.strip(),
                source_message_id=message.message_id,
            )
            session.commit()
            reply = view.reply_text
        except ILinkNotFound:
            session.rollback()
            reply = "还没有可操作的最近查询，请先发送一道题的图片。"
        except ILinkConflict:
            session.rollback()
            reply = "最近查询的状态已经变化，请重新发送题目或查看小程序里的个人收录。"
        self._send_direct(
            client,
            message=message,
            purpose=f"command:{message.text.strip()}",
            text=reply,
        )

    def _store_material(
        self,
        session: Session,
        *,
        user_id: UUID,
        message: InboundMessage,
        client: ILinkProtocolClient,
    ) -> None:
        try:
            reply = self._material_intake.store_latest(session, user_id=user_id)
            session.commit()
        except ILinkNotFound:
            session.rollback()
            reply = (
                "还没有可转存的图片。"
                "请先把讲义、笔记或错题卷拍照发给我，再回复「存资料」。"
            )
        except ILinkConflict as exc:
            session.rollback()
            reply = {
                "material_window_closed": (
                    "这张图片已超过转存窗口，原图已按约定删除。"
                    "请重新发送图片后再回复「存资料」。"
                ),
                "material_text_missing": (
                    "这张图片没有识别到可核对的文字。"
                    "请重拍更清晰的版本后再回复「存资料」。"
                ),
                "material_query_not_answered": (
                    "最近一次发图没有完成识别，无法转存。"
                    "请重新发送图片后再回复「存资料」。"
                ),
            }.get(
                str(exc),
                "这次没能转存，请重新发送图片后再回复「存资料」。",
            )
        except PhotoResolutionError:
            session.rollback()
            reply = "私有存储暂时不可用，这次没有改动任何数据；稍后再回复「存资料」重试。"
        self._send_direct(
            client,
            message=message,
            purpose="command:store-material",
            text=reply,
        )

    def _reply_plan_summary(
        self,
        session: Session,
        *,
        user_id: UUID,
        message: InboundMessage,
        client: ILinkProtocolClient,
    ) -> None:
        reply = today_plan_reply(session, user_id=user_id, now=self._clock())
        session.rollback()
        self._send_direct(
            client,
            message=message,
            purpose="plan-query",
            text=reply,
        )

    def _process_photo(
        self,
        session: Session,
        *,
        user_id: UUID,
        link_id: UUID,
        message: InboundMessage,
        client: ILinkProtocolClient,
    ) -> None:
        now = self._clock()
        source_message_fingerprint = self._service.cipher.fingerprint(
            f"{self._settings.bot_account_id}\0{message.message_id}",
            purpose="source-message",
        )
        query_id = uuid5(
            NAMESPACE_URL,
            f"ilink-query:{source_message_fingerprint}",
        )
        repository = ILinkRepository(session)
        query, replayed = repository.insert_query(
            query_id=query_id,
            link_id=link_id,
            user_id=user_id,
            bot_account_id=self._settings.bot_account_id,
            source_message_fingerprint=source_message_fingerprint,
            encrypted_context_token=self._service.cipher.encrypt(
                message.context_token, purpose=f"query-context:{query_id}"
            ),
            key_id=self._service.cipher.key_id,
            now=now,
        )
        session.commit()
        query = repository.query(query_id=query_id)
        if query is None:
            raise ILinkNotFound("query_not_found")
        if replayed and query["reply_state"] in {"sent", "uncertain"}:
            return
        if query["state"] in {"answered", "failed"}:
            self._send_query_reply(
                session,
                query=query,
                user_id=user_id,
                message=message,
                client=client,
            )
            return
        stored: StoredImage | None = None
        try:
            if query["state"] == "received":
                assert message.image is not None
                image = client.download_image(message.image)
                stored = self._resolver.storage.put(
                    user_id=user_id, query_id=query_id, image=image
                )
                repository.start_processing(
                    query_id=query_id,
                    object_ref=stored.object_ref,
                    source_fingerprint=stored.content_sha256,
                    mime_type=stored.mime_type,
                    now=self._clock(),
                )
                session.commit()
            else:
                if not query["transient_object_ref"] or not query["source_fingerprint"]:
                    raise PhotoResolutionError("processing_object_missing")
                stored = StoredImage(
                    object_ref=str(query["transient_object_ref"]),
                    content_sha256=str(query["source_fingerprint"]),
                    mime_type=str(query["mime_type"]),
                )
            projection = self._resolver.resolve(
                session,
                user_id=user_id,
                query_id=query_id,
                stored=stored,
                owner_scope_hash=self._service.cipher.fingerprint(
                    str(user_id), purpose="recognition-owner"
                ),
            )
            # 图片保留在私有前缀内一个转存窗口（ILINK_TRANSIENT_RETENTION_SECONDS），
            # 供用户回复「存资料」转入私有阅读室；窗口到期由维护任务送入可靠删除队列。
            repository.answer_query(
                query_id=query_id,
                answer_kind=projection.answer_kind,
                track=projection.track,
                recognized_payload=projection.recognized_payload,
                answer_payload={
                    "answerText": projection.answer_text,
                    "explanation": projection.explanation,
                },
                content_version_id=projection.content_version_id,
                authorization_snapshot_ref=projection.authorization_snapshot_ref,
                model_provenance=projection.model_provenance,
                now=self._clock(),
            )
            repository.event(
                aggregate_type="query",
                aggregate_id=str(query_id),
                user_id=user_id,
                event_type="answer_published",
                from_state="processing",
                to_state="answered",
                correlation_fingerprint=stored.content_sha256,
                details={"answerKind": projection.answer_kind, "abilityImpact": "none"},
                now=self._clock(),
            )
            session.commit()
        except (ILinkProtocolError, PhotoResolutionError, ILinkConflict) as exc:
            session.rollback()
            if stored is not None:
                try:
                    self._resolver.storage.delete(object_ref=stored.object_ref)
                except PhotoResolutionError:
                    session.rollback()
                    raise
            repository = ILinkRepository(session)
            current = repository.query(query_id=query_id)
            if current is not None and current["state"] in {"received", "processing"}:
                repository.fail_query(
                    query_id=query_id,
                    failure_code=getattr(exc, "code", "photo_query_failed"),
                    now=self._clock(),
                )
                repository.event(
                    aggregate_type="query",
                    aggregate_id=str(query_id),
                    user_id=user_id,
                    event_type="query_failed",
                    from_state=str(current["state"]),
                    to_state="failed",
                    correlation_fingerprint=None,
                    details={"failureCode": getattr(exc, "code", "photo_query_failed")},
                    now=self._clock(),
                )
                session.commit()
        query = ILinkRepository(session).query(query_id=query_id)
        if query is not None:
            self._send_query_reply(
                session,
                query=query,
                user_id=user_id,
                message=message,
                client=client,
            )

    def _send_query_reply(
        self,
        session: Session,
        *,
        query,
        user_id: UUID,
        message: InboundMessage,
        client: ILinkProtocolClient,
    ) -> None:
        repository = ILinkRepository(session)
        if query["reply_state"] in {"sent", "uncertain"}:
            return
        client_id = str(
            uuid5(NAMESPACE_URL, f"ilink-reply:{query['query_id']}:{query['version']}")
        )
        view = query_view(repository, query=query, user_id=user_id)
        encrypted_context = query["encrypted_context_token"]
        context_token = (
            self._service.cipher.decrypt(
                str(encrypted_context), purpose=f"query-context:{query['query_id']}"
            )
            if encrypted_context
            else message.context_token
        )
        repository.set_reply_state(
            query_id=UUID(str(query["query_id"])),
            client_id=client_id,
            state="sending",
            now=self._clock(),
        )
        session.commit()
        try:
            client.send_text(
                sender_id=message.sender_id,
                context_token=context_token,
                text=view.reply_text,
                client_id=client_id,
                run_id=message.run_id,
            )
        except ILinkProtocolError as exc:
            repository = ILinkRepository(session)
            repository.set_reply_state(
                query_id=UUID(str(query["query_id"])),
                client_id=client_id,
                state="uncertain" if exc.outcome_uncertain else "not_sent",
                now=self._clock(),
            )
            session.commit()
            return
        ILinkRepository(session).set_reply_state(
            query_id=UUID(str(query["query_id"])),
            client_id=client_id,
            state="sent",
            now=self._clock(),
        )
        session.commit()

    @staticmethod
    def _send_direct(
        client: ILinkProtocolClient,
        *,
        message: InboundMessage,
        purpose: str,
        text: str,
    ) -> None:
        client_id = str(
            uuid5(NAMESPACE_URL, f"ilink-direct:{message.message_id}:{purpose}")
        )
        client.send_text(
            sender_id=message.sender_id,
            context_token=message.context_token,
            text=text,
            client_id=client_id,
            run_id=message.run_id,
        )


def _looks_like_binding_code(value: str) -> bool:
    normalized = value.strip().upper().replace("-", "")
    return len(normalized) == 12 and normalized.isalnum()
