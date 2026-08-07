from __future__ import annotations

from datetime import UTC, datetime, timedelta
import secrets
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.domains.ilink_channel.crypto import ILinkIdentityCipher
from app.domains.ilink_channel.repository import (
    ILinkConflict,
    ILinkNotFound,
    ILinkRepository,
)
from app.domains.ilink_channel.schemas import ILinkChannelView, QueryView
from app.domains.ilink_channel.settings import ILinkSettings


_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class ILinkChannelService:
    def __init__(
        self,
        *,
        settings: ILinkSettings,
        clock=None,
    ) -> None:
        settings.validate_binding()
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cipher = ILinkIdentityCipher(
            encryption_key=settings.encryption_key_bytes(),
            hmac_key=settings.identity_hmac_key,
            key_id=settings.identity_key_id,
        )

    @property
    def cipher(self) -> ILinkIdentityCipher:
        return self._cipher

    def view(self, session: Session, *, user_id: UUID) -> ILinkChannelView:
        repository = ILinkRepository(session)
        row = repository.link_for_user(user_id=user_id)
        query_count, collection_count = repository.counts(user_id=user_id)
        if row is None:
            return self._view(
                state="unbound",
                row=None,
                binding_code=None,
                query_count=query_count,
                collection_count=collection_count,
            )
        code = None
        if (
            row["state"] == "pending"
            and row["encrypted_binding_code"]
            and row["code_expires_at"] > self._clock()
        ):
            code = self._cipher.decrypt(
                str(row["encrypted_binding_code"]),
                purpose=f"binding-code:{row['link_id']}",
            )
        return self._view(
            state=str(row["state"]),
            row=row,
            binding_code=code,
            query_count=query_count,
            collection_count=collection_count,
        )

    def issue(
        self,
        session: Session,
        *,
        user_id: UUID,
        command_id: str,
        replace_pending: bool,
    ) -> ILinkChannelView:
        now = self._clock()
        repository = ILinkRepository(session)
        request_fingerprint = self._cipher.fingerprint_payload(
            {"userId": str(user_id), "replacePending": replace_pending},
            purpose="issue-command",
        )
        replay = repository.command_receipt(command_id=command_id)
        if replay is not None:
            if replay["request_fingerprint"] != request_fingerprint:
                raise ILinkConflict("idempotency_conflict")
            return self.view(session, user_id=user_id)
        existing = repository.link_for_user(user_id=user_id, for_update=True)
        if existing is not None and existing["state"] == "bound":
            raise ILinkConflict("bound_link_must_be_revoked_first")
        if (
            existing is not None
            and existing["state"] == "pending"
            and existing["code_expires_at"] > now
            and not replace_pending
        ):
            result = self.view(session, user_id=user_id)
            repository.save_command_receipt(
                command_id=command_id,
                command_type="issue_code",
                user_id=user_id,
                request_fingerprint=request_fingerprint,
                result_payload=result.model_dump(by_alias=True, mode="json"),
                now=now,
            )
            return result
        link_id = UUID(str(existing["link_id"])) if existing else uuid4()
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
        expires_at = now + timedelta(seconds=self._settings.binding_ttl_seconds)
        row = repository.upsert_pending_link(
            link_id=link_id,
            user_id=user_id,
            encrypted_code=self._cipher.encrypt(
                code, purpose=f"binding-code:{link_id}"
            ),
            code_hash=self._cipher.fingerprint(code, purpose="binding-code"),
            expires_at=expires_at,
            key_id=self._cipher.key_id,
            command_id=command_id,
            now=now,
        )
        repository.event(
            aggregate_type="link",
            aggregate_id=str(link_id),
            user_id=user_id,
            event_type="binding_code_issued",
            from_state=str(existing["state"]) if existing else "unbound",
            to_state="pending",
            correlation_fingerprint=request_fingerprint,
            details={"expiresAt": expires_at.isoformat()},
            now=now,
        )
        query_count, collection_count = repository.counts(user_id=user_id)
        result = self._view(
            state="pending",
            row=row,
            binding_code=code,
            query_count=query_count,
            collection_count=collection_count,
        )
        repository.save_command_receipt(
            command_id=command_id,
            command_type="issue_code",
            user_id=user_id,
            request_fingerprint=request_fingerprint,
            result_payload=result.model_dump(by_alias=True, mode="json"),
            now=now,
        )
        return result

    def confirm_sender(
        self,
        session: Session,
        *,
        code: str,
        sender_id: str,
        source_message_id: str,
    ) -> UUID:
        now = self._clock()
        normalized = code.strip().upper().replace("-", "")
        if len(normalized) != 12 or any(char not in _CODE_ALPHABET for char in normalized):
            raise ILinkNotFound("code_not_found")
        repository = ILinkRepository(session)
        code_hash = self._cipher.fingerprint(normalized, purpose="binding-code")
        row = repository.link_for_code(code_hash=code_hash, for_update=True)
        if row is None:
            raise ILinkNotFound("code_not_found")
        if row["code_expires_at"] <= now:
            raise ILinkConflict("code_expired")
        user_id = UUID(str(row["user_id"]))
        sender_fingerprint = self._cipher.fingerprint(
            f"{self._settings.bot_account_id}\0{sender_id}", purpose="sender"
        )
        existing_sender = repository.link_for_sender(
            bot_account_id=self._settings.bot_account_id,
            sender_fingerprint=sender_fingerprint,
            for_update=True,
        )
        if existing_sender is not None and existing_sender["link_id"] != row["link_id"]:
            raise ILinkConflict("sender_already_bound")
        source_message_fingerprint = self._cipher.fingerprint(
            f"{self._settings.bot_account_id}\0{source_message_id}",
            purpose="source-message",
        )
        command_id = f"ilink-confirm:{source_message_fingerprint}"
        request_fingerprint = self._cipher.fingerprint_payload(
            {"linkId": str(row["link_id"]), "sender": sender_fingerprint},
            purpose="confirm-command",
        )
        replay = repository.command_receipt(command_id=command_id)
        if replay is not None:
            if replay["request_fingerprint"] != request_fingerprint:
                raise ILinkConflict("idempotency_conflict")
            return user_id
        repository.bind_link(
            link_id=UUID(str(row["link_id"])),
            bot_account_id=self._settings.bot_account_id,
            sender_fingerprint=sender_fingerprint,
            encrypted_sender_id=self._cipher.encrypt(
                sender_id, purpose=f"sender:{row['link_id']}"
            ),
            key_id=self._cipher.key_id,
            command_id=command_id,
            now=now,
        )
        repository.save_command_receipt(
            command_id=command_id,
            command_type="confirm_code",
            user_id=user_id,
            request_fingerprint=request_fingerprint,
            result_payload={"linkId": str(row["link_id"]), "state": "bound"},
            now=now,
        )
        repository.event(
            aggregate_type="link",
            aggregate_id=str(row["link_id"]),
            user_id=user_id,
            event_type="sender_bound",
            from_state="pending",
            to_state="bound",
            correlation_fingerprint=sender_fingerprint,
            details={},
            now=now,
        )
        return user_id

    def revoke(
        self,
        session: Session,
        *,
        user_id: UUID,
        expected_link_id: UUID,
        command_id: str,
    ) -> ILinkChannelView:
        now = self._clock()
        repository = ILinkRepository(session)
        row = repository.link_for_user(user_id=user_id, for_update=True)
        if row is None or UUID(str(row["link_id"])) != expected_link_id:
            raise ILinkNotFound("link_not_found")
        request_fingerprint = self._cipher.fingerprint_payload(
            {"userId": str(user_id), "linkId": str(expected_link_id)},
            purpose="revoke-command",
        )
        replay = repository.command_receipt(command_id=command_id)
        if replay is not None:
            if replay["request_fingerprint"] != request_fingerprint:
                raise ILinkConflict("idempotency_conflict")
            return self.view(session, user_id=user_id)
        if row["state"] != "revoked":
            repository.revoke_link(
                link_id=expected_link_id, command_id=command_id, now=now
            )
            repository.event(
                aggregate_type="link",
                aggregate_id=str(expected_link_id),
                user_id=user_id,
                event_type="link_revoked",
                from_state=str(row["state"]),
                to_state="revoked",
                correlation_fingerprint=request_fingerprint,
                details={},
                now=now,
            )
        result = self.view(session, user_id=user_id)
        repository.save_command_receipt(
            command_id=command_id,
            command_type="revoke",
            user_id=user_id,
            request_fingerprint=request_fingerprint,
            result_payload=result.model_dump(by_alias=True, mode="json"),
            now=now,
        )
        return result

    def command_latest(
        self,
        session: Session,
        *,
        user_id: UUID,
        command: str,
        source_message_id: str,
    ) -> QueryView:
        now = self._clock()
        repository = ILinkRepository(session)
        query = repository.latest_query(user_id=user_id, for_update=True)
        if query is None:
            raise ILinkNotFound("query_not_found")
        query_id = UUID(str(query["query_id"]))
        source_message_fingerprint = self._cipher.fingerprint(
            f"{self._settings.bot_account_id}\0{source_message_id}",
            purpose="source-message",
        )
        command_id = (
            f"ilink-query-command:{query_id}:{source_message_fingerprint}:{command}"
        )
        if command == "收录":
            repository.collect(
                query_id=query_id, user_id=user_id, command_id=command_id, now=now
            )
        elif command == "重做":
            repository.collect(
                query_id=query_id, user_id=user_id, command_id=command_id, now=now
            )
            repository.queue_reattempt(
                query_id=query_id,
                user_id=user_id,
                command_id=command_id + ":queue",
                now=now,
            )
        elif command == "删除":
            repository.delete_latest(
                query_id=query_id, user_id=user_id, command_id=command_id, now=now
            )
            query = repository.query(query_id=query_id)
        else:
            raise ILinkConflict("unsupported_command")
        if query is None:
            raise ILinkNotFound("query_not_found")
        return query_view(repository, query=query, user_id=user_id)

    def _view(
        self,
        *,
        state: str,
        row,
        binding_code: str | None,
        query_count: int,
        collection_count: int,
    ) -> ILinkChannelView:
        return ILinkChannelView(
            linkState=state,
            linkId=UUID(str(row["link_id"])) if row else None,
            bindingCode=binding_code,
            expiresAt=row["code_expires_at"] if row else None,
            queryCount=query_count,
            collectionCount=collection_count,
            usageCopy="在微信里直接问岸岸，或发送裁剪好的单题图片；发送「今天练什么」可查看当前安排。",
            evidenceCopy="查询、收录和重做请求都不计入能力；只有之后独立作答才会形成学习证据。",
        )


def query_view(repository: ILinkRepository, *, query, user_id: UUID) -> QueryView:
    collection_state = repository.collection_state(
        query_id=UUID(str(query["query_id"])), user_id=user_id
    )
    reply = render_query_reply(query=query, collection_state=collection_state)
    return QueryView(
        queryId=UUID(str(query["query_id"])),
        userId=user_id,
        linkId=(UUID(str(query["link_id"])) if query["link_id"] is not None else None),
        state=str(query["state"]),
        answerKind=str(query["answer_kind"]),
        collectionState=collection_state,
        replyText=reply,
    )


def render_query_reply(
    *, query, collection_state: str, include_material_command: bool = True
) -> str:
    if query["state"] == "deleted":
        return "已删除这次查询和个人收录内容。"
    if query["state"] == "failed":
        return "这次没有可靠识别完成。请重新裁剪，确保题干、材料和选项完整后再发。"
    payload = dict(query["answer_payload"] or {})
    answer = str(payload.get("answerText") or "当前没有可可靠展示的答案")
    explanation = str(payload.get("explanation") or "")
    provenance = dict(query["model_provenance"] or {})
    ai_source = (
        "AI 候选 · 尚未双模型复核 · 非官方答案"
        if provenance.get("policy") == "primary-candidate-secondary-unavailable@v1"
        else "AI 双模型候选 · 非官方答案"
    )
    source = {
        "authorized_exact": "题库授权原题",
        "similar_reference": "相似题参考",
        "ai_candidate": ai_source,
        "ambiguous": "匹配存在歧义",
        "material_required": "缺少共享材料",
    }.get(str(query["answer_kind"]), "来源未确认")
    status = {
        "not_collected": "已查询 · 未收录",
        "collected": "已收录 · 暂不计入能力",
        "queued_for_reattempt": "已收录 · 已标记待重做 · 暂不计入能力",
        "deleted": "已删除",
    }[collection_state]
    body = f"答案：{answer}\n\n解析：{explanation}\n\n来源：{source}\n状态：{status}"
    if collection_state == "not_collected":
        suffix = "\n回复「收录」「重做」「删除」"
        if include_material_command:
            suffix += "；讲义或笔记类图片可回复「存资料」收进私有阅读室"
        return body + suffix
    if collection_state == "collected":
        return body + "\n回复「重做」「删除」"
    if collection_state == "queued_for_reattempt":
        return body + "\n这是待重做标记；完成独立作答前不会改变能力画像。回复「删除」可移除。"
    return body
