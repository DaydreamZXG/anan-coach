from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.platform.jobs import job_registration


class ILinkConflict(RuntimeError):
    pass


class ILinkNotFound(LookupError):
    pass


ILINK_OBJECT_DELETION_JOB_TYPE = "ilink_object_deletion_v1"
_ILINK_DELETION_REGISTRATION = job_registration(
    job_type=ILINK_OBJECT_DELETION_JOB_TYPE,
    queue_name="messaging.ilink_object_deletion_queue",
)


@dataclass(frozen=True, slots=True)
class ILinkObjectDeletionClaim:
    object_ref: str
    claim_token: UUID
    claimed_at: datetime
    lease_expires_at: datetime


class ILinkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_command(self, *, command_id: str) -> None:
        """Serialize one idempotent command until the surrounding transaction commits."""
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:command_id, 0))"),
            {"command_id": command_id},
        )

    def link_for_user(self, *, user_id: UUID, for_update: bool = False):
        suffix = " for update" if for_update else ""
        return self._session.execute(
            text(
                "select * from messaging.ilink_channel_links "
                "where user_id = :user_id" + suffix
            ),
            {"user_id": str(user_id)},
        ).mappings().one_or_none()

    def link_for_code(self, *, code_hash: str, for_update: bool = False):
        suffix = " for update" if for_update else ""
        return self._session.execute(
            text(
                "select * from messaging.ilink_channel_links "
                "where state = 'pending' and binding_code_hash = :code_hash" + suffix
            ),
            {"code_hash": code_hash},
        ).mappings().one_or_none()

    def link_for_sender(
        self, *, bot_account_id: str, sender_fingerprint: str, for_update: bool = False
    ):
        suffix = " for update" if for_update else ""
        return self._session.execute(
            text(
                "select * from messaging.ilink_channel_links "
                "where state = 'bound' and bot_account_id = :bot_account_id "
                "and sender_fingerprint = :sender_fingerprint" + suffix
            ),
            {
                "bot_account_id": bot_account_id,
                "sender_fingerprint": sender_fingerprint,
            },
        ).mappings().one_or_none()

    def command_receipt(self, *, command_id: str):
        return self._session.execute(
            text(
                "select * from messaging.ilink_command_receipts "
                "where command_id = :command_id"
            ),
            {"command_id": command_id},
        ).mappings().one_or_none()

    def save_command_receipt(
        self,
        *,
        command_id: str,
        command_type: str,
        user_id: UUID | None,
        request_fingerprint: str,
        result_payload: dict[str, object],
        now: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into messaging.ilink_command_receipts (
                  command_id, command_type, user_id, request_fingerprint,
                  result_payload, created_at
                ) values (
                  :command_id, :command_type, :user_id, :request_fingerprint,
                  cast(:result_payload as jsonb), :now
                )
                """
            ),
            {
                "command_id": command_id,
                "command_type": command_type,
                "user_id": str(user_id) if user_id else None,
                "request_fingerprint": request_fingerprint,
                "result_payload": json.dumps(result_payload, sort_keys=True),
                "now": now,
            },
        )

    def pending_material_delete(self, *, user_id: UUID, code: str):
        return self._session.execute(
            text(
                """
                select * from messaging.ilink_command_receipts
                where user_id = :user_id
                  and command_type = 'control_material_delete_pending'
                  and result_payload ->> 'code' = :code
                order by created_at desc
                limit 1
                """
            ),
            {"user_id": str(user_id), "code": code},
        ).mappings().one_or_none()

    def material_delete_consumed(self, *, pending_command_id: str) -> bool:
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1 from messaging.ilink_command_receipts
                      where command_type = 'control_material_delete_consumed'
                        and result_payload ->> 'pendingCommandId' = :pending_command_id
                    )
                    """
                ),
                {"pending_command_id": pending_command_id},
            ).scalar_one()
        )

    def upsert_pending_link(
        self,
        *,
        link_id: UUID,
        user_id: UUID,
        encrypted_code: str,
        code_hash: str,
        expires_at: datetime,
        key_id: str,
        command_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        row = self.link_for_user(user_id=user_id, for_update=True)
        if row is None:
            return dict(
                self._session.execute(
                    text(
                        """
                        insert into messaging.ilink_channel_links (
                          link_id, user_id, state, encrypted_binding_code,
                          encryption_key_id, binding_code_hash, code_expires_at,
                          revision, last_command_id, created_at, updated_at
                        ) values (
                          :link_id, :user_id, 'pending', :encrypted_code, :key_id,
                          :code_hash, :expires_at, 1, :command_id, :now, :now
                        ) returning *
                        """
                    ),
                    {
                        "link_id": str(link_id),
                        "user_id": str(user_id),
                        "encrypted_code": encrypted_code,
                        "key_id": key_id,
                        "code_hash": code_hash,
                        "expires_at": expires_at,
                        "command_id": command_id,
                        "now": now,
                    },
                ).mappings().one()
            )
        return dict(
            self._session.execute(
                text(
                    """
                    update messaging.ilink_channel_links set
                      state = 'pending', bot_account_id = null,
                      sender_fingerprint = null, encrypted_sender_id = null,
                      encrypted_binding_code = :encrypted_code,
                      encryption_key_id = :key_id,
                      binding_code_hash = :code_hash,
                      code_expires_at = :expires_at, code_consumed_at = null,
                      revision = revision + 1, last_command_id = :command_id,
                      updated_at = :now, revoked_at = null
                    where link_id = :link_id returning *
                    """
                ),
                {
                    "link_id": str(row["link_id"]),
                    "encrypted_code": encrypted_code,
                    "key_id": key_id,
                    "code_hash": code_hash,
                    "expires_at": expires_at,
                    "command_id": command_id,
                    "now": now,
                },
            ).mappings().one()
        )

    def bind_link(
        self,
        *,
        link_id: UUID,
        bot_account_id: str,
        sender_fingerprint: str,
        encrypted_sender_id: str,
        key_id: str,
        command_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        try:
            row = self._session.execute(
                text(
                    """
                    update messaging.ilink_channel_links set
                      state = 'bound', bot_account_id = :bot_account_id,
                      sender_fingerprint = :sender_fingerprint,
                      encrypted_sender_id = :encrypted_sender_id,
                      encrypted_binding_code = null, binding_code_hash = null,
                      code_expires_at = null, code_consumed_at = :now,
                      encryption_key_id = :key_id, revision = revision + 1,
                      last_command_id = :command_id, updated_at = :now,
                      revoked_at = null
                    where link_id = :link_id and state = 'pending'
                    returning *
                    """
                ),
                {
                    "link_id": str(link_id),
                    "bot_account_id": bot_account_id,
                    "sender_fingerprint": sender_fingerprint,
                    "encrypted_sender_id": encrypted_sender_id,
                    "key_id": key_id,
                    "command_id": command_id,
                    "now": now,
                },
            ).mappings().one_or_none()
        except IntegrityError as exc:
            raise ILinkConflict("sender_already_bound") from exc
        if row is None:
            raise ILinkConflict("link_state_changed")
        return dict(row)

    def revoke_link(
        self, *, link_id: UUID, command_id: str, now: datetime
    ) -> dict[str, Any]:
        row = self._session.execute(
            text(
                """
                update messaging.ilink_channel_links set
                  state = 'revoked', sender_fingerprint = null,
                  encrypted_sender_id = null, encrypted_binding_code = null,
                  binding_code_hash = null, code_expires_at = null,
                  revision = revision + 1, last_command_id = :command_id,
                  updated_at = :now, revoked_at = :now
                where link_id = :link_id and state in ('pending', 'bound')
                returning *
                """
            ),
            {"link_id": str(link_id), "command_id": command_id, "now": now},
        ).mappings().one_or_none()
        if row is None:
            raise ILinkConflict("link_state_changed")
        return dict(row)

    def counts(self, *, user_id: UUID) -> tuple[int, int]:
        row = self._session.execute(
            text(
                """
                select
                  (select count(*) from messaging.ilink_photo_queries
                   where user_id = :user_id and state <> 'deleted') as query_count,
                  (select count(*) from messaging.ilink_photo_collections
                   where user_id = :user_id and state <> 'deleted') as collection_count
                """
            ),
            {"user_id": str(user_id)},
        ).mappings().one()
        return int(row["query_count"]), int(row["collection_count"])

    def insert_query(
        self,
        *,
        query_id: UUID,
        link_id: UUID | None,
        hub_authorization_id: UUID | None = None,
        user_id: UUID,
        bot_account_id: str,
        source_message_fingerprint: str,
        encrypted_context_token: str | None,
        key_id: str,
        now: datetime,
    ):
        if (link_id is None) == (hub_authorization_id is None):
            raise ValueError("photo query must have exactly one channel authorization")
        existing = self.query_for_message(
            bot_account_id=bot_account_id,
            source_message_fingerprint=source_message_fingerprint,
        )
        if existing is not None:
            if UUID(str(existing["user_id"])) != user_id:
                raise ILinkConflict("message_replay_cross_account")
            return existing, True
        row = self._session.execute(
            text(
                """
                insert into messaging.ilink_photo_queries (
                  query_id, link_id, hub_authorization_id, user_id,
                  bot_account_id, source_message_id,
                  state, answer_kind, encrypted_context_token, encryption_key_id,
                  version, created_at, updated_at
                ) values (
                  :query_id, :link_id, :hub_authorization_id, :user_id,
                  :bot_account_id, :source_message_id,
                  'received', 'none', :context_token, :key_id, 1, :now, :now
                ) returning *
                """
            ),
            {
                "query_id": str(query_id),
                "link_id": str(link_id) if link_id is not None else None,
                "hub_authorization_id": (
                    str(hub_authorization_id)
                    if hub_authorization_id is not None
                    else None
                ),
                "user_id": str(user_id),
                "bot_account_id": bot_account_id,
                "source_message_id": source_message_fingerprint,
                "context_token": encrypted_context_token,
                "key_id": key_id,
                "now": now,
            },
        ).mappings().one()
        self.event(
            aggregate_type="query",
            aggregate_id=str(query_id),
            user_id=user_id,
            event_type="photo_received",
            from_state="absent",
            to_state="received",
            correlation_fingerprint=None,
            details={},
            now=now,
        )
        return row, False

    def query_for_message(
        self, *, bot_account_id: str, source_message_fingerprint: str
    ):
        return self._session.execute(
            text(
                "select * from messaging.ilink_photo_queries "
                "where bot_account_id = :bot and source_message_id = :message"
            ),
            {"bot": bot_account_id, "message": source_message_fingerprint},
        ).mappings().one_or_none()

    def query(self, *, query_id: UUID, for_update: bool = False):
        suffix = " for update" if for_update else ""
        return self._session.execute(
            text(
                "select * from messaging.ilink_photo_queries "
                "where query_id = :query_id" + suffix
            ),
            {"query_id": str(query_id)},
        ).mappings().one_or_none()

    def latest_query(self, *, user_id: UUID, for_update: bool = False):
        suffix = " for update" if for_update else ""
        return self._session.execute(
            text(
                "select * from messaging.ilink_photo_queries "
                "where user_id = :user_id and state in ('answered', 'failed') "
                "order by created_at desc limit 1" + suffix
            ),
            {"user_id": str(user_id)},
        ).mappings().one_or_none()

    def start_processing(
        self, *, query_id: UUID, object_ref: str, source_fingerprint: str, mime_type: str, now: datetime
    ) -> None:
        changed = self._session.execute(
            text(
                """
                update messaging.ilink_photo_queries set
                  state = 'processing', transient_object_ref = :object_ref,
                  source_fingerprint = :source_fingerprint, mime_type = :mime_type,
                  version = version + 1, updated_at = :now
                where query_id = :query_id and state = 'received'
                """
            ),
            {
                "query_id": str(query_id),
                "object_ref": object_ref,
                "source_fingerprint": source_fingerprint,
                "mime_type": mime_type,
                "now": now,
            },
        ).rowcount
        if changed != 1:
            raise ILinkConflict("query_state_changed")

    def answer_query(
        self,
        *,
        query_id: UUID,
        answer_kind: str,
        track: str,
        recognized_payload: dict[str, object],
        answer_payload: dict[str, object],
        content_version_id: str | None,
        authorization_snapshot_ref: str | None,
        model_provenance: dict[str, object] | None,
        now: datetime,
    ) -> None:
        changed = self._session.execute(
            text(
                """
                update messaging.ilink_photo_queries set
                  state = 'answered', answer_kind = :answer_kind, track = :track,
                  recognized_payload = cast(:recognized as jsonb),
                  answer_payload = cast(:answer as jsonb),
                  content_version_id = :content_version_id,
                  authorization_snapshot_ref = :authorization_snapshot_ref,
                  model_provenance = cast(:model_provenance as jsonb),
                  answered_at = :now,
                  version = version + 1, updated_at = :now
                where query_id = :query_id and state = 'processing'
                """
            ),
            {
                "query_id": str(query_id),
                "answer_kind": answer_kind,
                "track": track,
                "recognized": json.dumps(recognized_payload, ensure_ascii=False),
                "answer": json.dumps(answer_payload, ensure_ascii=False),
                "content_version_id": content_version_id,
                "authorization_snapshot_ref": authorization_snapshot_ref,
                "model_provenance": json.dumps(model_provenance) if model_provenance else None,
                "now": now,
            },
        ).rowcount
        if changed != 1:
            raise ILinkConflict("query_state_changed")

    def fail_query(self, *, query_id: UUID, failure_code: str, now: datetime) -> None:
        changed = self._session.execute(
            text(
                """
                update messaging.ilink_photo_queries set
                  state = 'failed', failure_code = :failure_code,
                  transient_object_ref = null,
                  version = version + 1, updated_at = :now
                where query_id = :query_id and state in ('received', 'processing')
                """
            ),
            {"query_id": str(query_id), "failure_code": failure_code, "now": now},
        ).rowcount
        if changed != 1:
            raise ILinkConflict("query_state_changed")

    def expire_stale_processing(
        self, *, before: datetime, limit: int, now: datetime
    ) -> int:
        if limit < 1 or limit > 1000:
            raise ValueError("stale processing limit is outside policy")
        rows = self._session.execute(
            text(
                """
                with candidates as (
                  select query_id, transient_object_ref
                  from messaging.ilink_photo_queries
                  where state in ('received', 'processing') and updated_at < :before
                  order by updated_at
                  for update skip locked
                  limit :limit
                ), queued as (
                  insert into messaging.ilink_object_deletion_queue (
                    object_ref, state, attempt_count, created_at, updated_at
                  )
                  select transient_object_ref, 'pending', 0, :now, :now
                  from candidates where transient_object_ref is not null
                  on conflict (object_ref) do nothing
                  returning object_ref
                )
                update messaging.ilink_photo_queries query set
                  state = 'failed', failure_code = 'processing_expired',
                  encrypted_context_token = null, transient_object_ref = null,
                  recognized_payload = null, answer_payload = null,
                  model_provenance = null, version = version + 1,
                  updated_at = :now
                from candidates
                where query.query_id = candidates.query_id
                returning query.query_id
                """
            ),
            {"before": before, "limit": limit, "now": now},
        ).scalars()
        return len(tuple(rows))

    def expire_uncollected_queries(
        self, *, before: datetime, limit: int, now: datetime
    ) -> int:
        if limit < 1 or limit > 1000:
            raise ValueError("query expiry limit is outside policy")
        rows = self._session.execute(
            text(
                """
                with candidates as (
                  select query.query_id, query.transient_object_ref
                  from messaging.ilink_photo_queries query
                  where query.state in ('answered', 'failed')
                    and query.created_at < :before
                    and not exists (
                      select 1 from messaging.ilink_photo_collections collection
                      where collection.query_id = query.query_id
                        and collection.state <> 'deleted'
                    )
                  order by query.created_at
                  for update skip locked
                  limit :limit
                ), queued as (
                  insert into messaging.ilink_object_deletion_queue (
                    object_ref, state, attempt_count, created_at, updated_at
                  )
                  select transient_object_ref, 'pending', 0, :now, :now
                  from candidates where transient_object_ref is not null
                  on conflict (object_ref) do nothing
                  returning object_ref
                )
                update messaging.ilink_photo_queries query set
                  state = 'deleted', source_fingerprint = null, mime_type = null,
                  recognized_payload = null, answer_payload = null,
                  content_version_id = null, authorization_snapshot_ref = null,
                  model_provenance = null, failure_code = null,
                  encrypted_context_token = null, transient_object_ref = null,
                  reply_client_id = null, deleted_at = :now,
                  version = version + 1, updated_at = :now
                from candidates
                where query.query_id = candidates.query_id
                returning query.query_id
                """
            ),
            {"before": before, "limit": limit, "now": now},
        ).scalars()
        return len(tuple(rows))

    def expire_answered_transients(
        self, *, before: datetime, limit: int, now: datetime
    ) -> int:
        if limit < 1 or limit > 1000:
            raise ValueError("answered transient expiry limit is outside policy")
        rows = self._session.execute(
            text(
                """
                with candidates as (
                  select query_id, transient_object_ref
                  from messaging.ilink_photo_queries
                  where state = 'answered'
                    and transient_object_ref is not null
                    and answered_at < :before
                  order by answered_at
                  for update skip locked
                  limit :limit
                ), queued as (
                  insert into messaging.ilink_object_deletion_queue (
                    object_ref, state, attempt_count, created_at, updated_at
                  )
                  select transient_object_ref, 'pending', 0, :now, :now
                  from candidates
                  on conflict (object_ref) do nothing
                  returning object_ref
                )
                update messaging.ilink_photo_queries query set
                  transient_object_ref = null,
                  version = version + 1, updated_at = :now
                from candidates
                where query.query_id = candidates.query_id
                returning query.query_id
                """
            ),
            {"before": before, "limit": limit, "now": now},
        ).scalars()
        return len(tuple(rows))

    def release_transient_for_material(
        self, *, query_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        changed = self._session.execute(
            text(
                """
                update messaging.ilink_photo_queries set
                  transient_object_ref = null,
                  version = version + 1, updated_at = :now
                where query_id = :query_id and user_id = :user_id
                  and state = 'answered' and transient_object_ref is not null
                """
            ),
            {"query_id": str(query_id), "user_id": str(user_id), "now": now},
        ).rowcount
        if changed != 1:
            raise ILinkConflict("query_state_changed")

    def set_reply_state(
        self, *, query_id: UUID, client_id: str, state: str, now: datetime
    ) -> None:
        clear_token = state in {"sent", "uncertain"}
        self._session.execute(
            text(
                """
                update messaging.ilink_photo_queries set
                  reply_client_id = :client_id, reply_state = :state,
                  encrypted_context_token = case when :clear_token
                    then null else encrypted_context_token end,
                  updated_at = :now
                where query_id = :query_id
                """
            ),
            {
                "query_id": str(query_id),
                "client_id": client_id,
                "state": state,
                "clear_token": clear_token,
                "now": now,
            },
        )

    def collect(self, *, query_id: UUID, user_id: UUID, command_id: str, now: datetime):
        query = self.query(query_id=query_id, for_update=True)
        if query is None or UUID(str(query["user_id"])) != user_id:
            raise ILinkNotFound("query_not_found")
        if query["state"] != "answered":
            raise ILinkConflict("query_not_answered")
        row = self._session.execute(
            text(
                """
                insert into messaging.ilink_photo_collections (
                  collection_id, query_id, user_id, state, command_id,
                  collected_at, updated_at
                ) values (
                  :collection_id, :query_id, :user_id, 'collected', :command_id,
                  :now, :now
                ) on conflict (user_id, query_id) do update set
                  state = case when messaging.ilink_photo_collections.state = 'deleted'
                    then 'collected' else messaging.ilink_photo_collections.state end,
                  command_id = excluded.command_id,
                  deleted_at = null,
                  updated_at = excluded.updated_at
                returning *
                """
            ),
            {
                "collection_id": str(uuid4()),
                "query_id": str(query_id),
                "user_id": str(user_id),
                "command_id": command_id,
                "now": now,
            },
        ).mappings().one()
        return row

    def queue_reattempt(
        self, *, query_id: UUID, user_id: UUID, command_id: str, now: datetime
    ):
        row = self._session.execute(
            text(
                """
                update messaging.ilink_photo_collections set
                  state = 'queued_for_reattempt', command_id = :command_id,
                  reattempt_requested_at = :now, updated_at = :now
                where query_id = :query_id and user_id = :user_id
                  and state in ('collected', 'queued_for_reattempt')
                returning *
                """
            ),
            {
                "query_id": str(query_id),
                "user_id": str(user_id),
                "command_id": command_id,
                "now": now,
            },
        ).mappings().one_or_none()
        if row is None:
            raise ILinkConflict("collection_required")
        return row

    def delete_latest(self, *, query_id: UUID, user_id: UUID, command_id: str, now: datetime) -> None:
        self._session.execute(
            text(
                """
                insert into messaging.ilink_object_deletion_queue (
                  object_ref, state, attempt_count, created_at, updated_at
                )
                select transient_object_ref, 'pending', 0, :now, :now
                from messaging.ilink_photo_queries
                where query_id = :query_id and user_id = :user_id
                  and transient_object_ref is not null
                on conflict (object_ref) do nothing
                """
            ),
            {"query_id": str(query_id), "user_id": str(user_id), "now": now},
        )
        self._session.execute(
            text(
                """
                update messaging.ilink_photo_collections set
                  state = 'deleted', command_id = :command_id,
                  reattempt_requested_at = null, deleted_at = :now, updated_at = :now
                where query_id = :query_id and user_id = :user_id
                  and state <> 'deleted'
                """
            ),
            {
                "query_id": str(query_id),
                "user_id": str(user_id),
                "command_id": command_id,
                "now": now,
            },
        )
        changed = self._session.execute(
            text(
                """
                update messaging.ilink_photo_queries set
                  state = 'deleted', source_fingerprint = null, mime_type = null,
                  recognized_payload = null,
                  answer_payload = null, model_provenance = null,
                  authorization_snapshot_ref = null, content_version_id = null,
                  encrypted_context_token = null, transient_object_ref = null,
                  reply_client_id = null,
                  deleted_at = :now, version = version + 1, updated_at = :now
                where query_id = :query_id and user_id = :user_id
                  and state in ('answered', 'failed')
                """
            ),
            {"query_id": str(query_id), "user_id": str(user_id), "now": now},
        ).rowcount
        if changed != 1:
            raise ILinkConflict("query_state_changed")

    def collection_state(self, *, query_id: UUID, user_id: UUID) -> str:
        value = self._session.execute(
            text(
                "select state from messaging.ilink_photo_collections "
                "where query_id = :query_id and user_id = :user_id"
            ),
            {"query_id": str(query_id), "user_id": str(user_id)},
        ).scalar_one_or_none()
        return str(value) if value is not None else "not_collected"

    def export(self, *, user_id: UUID) -> dict[str, object]:
        link = self.link_for_user(user_id=user_id)
        queries = self._session.execute(
            text(
                """
                select query_id, state, answer_kind, track, recognized_payload,
                       answer_payload, content_version_id, failure_code,
                       created_at, answered_at, deleted_at
                from messaging.ilink_photo_queries where user_id = :user_id
                order by created_at
                """
            ),
            {"user_id": str(user_id)},
        ).mappings()
        collections = self._session.execute(
            text(
                """
                select collection_id, query_id, state, collected_at,
                       reattempt_requested_at, deleted_at
                from messaging.ilink_photo_collections where user_id = :user_id
                order by collected_at
                """
            ),
            {"user_id": str(user_id)},
        ).mappings()
        return {
            "link": (
                {
                    "linkId": str(link["link_id"]),
                    "state": str(link["state"]),
                    "createdAt": link["created_at"],
                    "updatedAt": link["updated_at"],
                }
                if link
                else None
            ),
            "queries": [dict(row) for row in queries],
            "collections": [dict(row) for row in collections],
        }

    def delete_user_data(self, *, user_id: UUID) -> tuple[int, int, int, tuple[str, ...]]:
        refs = tuple(
            str(value)
            for value in self._session.execute(
                text(
                    "select transient_object_ref from messaging.ilink_photo_queries "
                    "where user_id = :user_id and transient_object_ref is not null"
                ),
                {"user_id": str(user_id)},
            ).scalars()
        )
        collections = self._session.execute(
            text("delete from messaging.ilink_photo_collections where user_id = :user_id"),
            {"user_id": str(user_id)},
        ).rowcount
        queries = self._session.execute(
            text("delete from messaging.ilink_photo_queries where user_id = :user_id"),
            {"user_id": str(user_id)},
        ).rowcount
        self._session.execute(
            text("delete from messaging.ilink_command_receipts where user_id = :user_id"),
            {"user_id": str(user_id)},
        )
        self._session.execute(
            text("delete from messaging.ilink_channel_events where user_id = :user_id"),
            {"user_id": str(user_id)},
        )
        links = self._session.execute(
            text("delete from messaging.ilink_channel_links where user_id = :user_id"),
            {"user_id": str(user_id)},
        ).rowcount
        return int(links or 0), int(queries or 0), int(collections or 0), refs

    def cursor(self, *, bot_account_id: str):
        return self._session.execute(
            text(
                "select * from messaging.ilink_worker_cursors "
                "where bot_account_id = :bot"
            ),
            {"bot": bot_account_id},
        ).mappings().one_or_none()

    def save_cursor(
        self, *, bot_account_id: str, encrypted_buf: str, key_id: str, now: datetime
    ) -> None:
        self._session.execute(
            text(
                """
                insert into messaging.ilink_worker_cursors (
                  bot_account_id, encrypted_updates_buf, encryption_key_id,
                  revision, updated_at
                ) values (:bot, :buf, :key_id, 1, :now)
                on conflict (bot_account_id) do update set
                  encrypted_updates_buf = excluded.encrypted_updates_buf,
                  encryption_key_id = excluded.encryption_key_id,
                  revision = messaging.ilink_worker_cursors.revision + 1,
                  updated_at = excluded.updated_at
                """
            ),
            {"bot": bot_account_id, "buf": encrypted_buf, "key_id": key_id, "now": now},
        )

    def clear_cursor(self, *, bot_account_id: str) -> None:
        self._session.execute(
            text("delete from messaging.ilink_worker_cursors where bot_account_id = :bot"),
            {"bot": bot_account_id},
        )

    def recover_stale_object_deletions(self, *, now: datetime) -> int:
        result = self._session.execute(
            text(
                """
                update messaging.ilink_object_deletion_queue set
                  state = 'pending',
                  attempt_count = greatest(attempt_count - 1, 0),
                  failure_code = 'stale_claim_recovered',
                  claim_token = null,
                  claimed_at = null,
                  lease_expires_at = null,
                  updated_at = :now
                where state = 'deleting'
                  and (
                    lease_expires_at <= :now
                    or lease_expires_at is null
                  )
                """
            ),
            {"now": now},
        )
        return int(result.rowcount or 0)

    def claim_object_deletions(
        self, *, limit: int, now: datetime
    ) -> tuple[ILinkObjectDeletionClaim, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("object deletion claim limit is outside policy")
        self.recover_stale_object_deletions(now=now)
        lease_expires_at = now + _ILINK_DELETION_REGISTRATION.lease_duration
        rows = self._session.execute(
            text(
                """
                with candidates as (
                  select object_ref from messaging.ilink_object_deletion_queue
                  where state in ('pending', 'failed') and attempt_count < 10
                  order by created_at
                  for update skip locked
                  limit :limit
                )
                update messaging.ilink_object_deletion_queue queue set
                  state = 'deleting', attempt_count = attempt_count + 1,
                  failure_code = null,
                  claim_token = gen_random_uuid(),
                  claimed_at = :now,
                  lease_expires_at = :lease_expires_at,
                  updated_at = :now
                from candidates where queue.object_ref = candidates.object_ref
                returning queue.object_ref, queue.claim_token, queue.claimed_at,
                  queue.lease_expires_at
                """
            ),
            {
                "limit": limit,
                "now": now,
                "lease_expires_at": lease_expires_at,
            },
        ).mappings()
        return tuple(
            ILinkObjectDeletionClaim(
                object_ref=str(row["object_ref"]),
                claim_token=UUID(str(row["claim_token"])),
                claimed_at=row["claimed_at"],
                lease_expires_at=row["lease_expires_at"],
            )
            for row in rows
        )

    def complete_object_deletion(
        self,
        *,
        object_ref: str,
        claim_token: UUID,
        now: datetime,
    ) -> None:
        changed = self._session.execute(
            text(
                "delete from messaging.ilink_object_deletion_queue "
                "where object_ref = :object_ref and state = 'deleting' "
                "and claim_token = :claim_token and lease_expires_at > :now"
            ),
            {
                "object_ref": object_ref,
                "claim_token": str(claim_token),
                "now": now,
            },
        ).rowcount
        if changed != 1:
            raise ILinkConflict(
                "only the active deletion claim can complete object deletion"
            )

    def fail_object_deletion(
        self,
        *,
        object_ref: str,
        claim_token: UUID,
        failure_code: str,
        now: datetime,
    ) -> None:
        changed = self._session.execute(
            text(
                """
                update messaging.ilink_object_deletion_queue set
                  state = 'failed', failure_code = :failure_code,
                  claim_token = null, claimed_at = null, lease_expires_at = null,
                  updated_at = :now
                where object_ref = :object_ref and state = 'deleting'
                  and claim_token = :claim_token
                  and lease_expires_at > :now
                """
            ),
            {
                "object_ref": object_ref,
                "claim_token": str(claim_token),
                "failure_code": failure_code,
                "now": now,
            },
        ).rowcount
        if changed != 1:
            raise ILinkConflict("only the active deletion claim can retry deletion")

    def event(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        user_id: UUID | None,
        event_type: str,
        from_state: str | None,
        to_state: str | None,
        correlation_fingerprint: str | None,
        details: dict[str, object],
        now: datetime | None = None,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into messaging.ilink_channel_events (
                  event_id, aggregate_type, aggregate_id, user_id, event_type,
                  from_state, to_state, correlation_fingerprint, details, created_at
                ) values (
                  :event_id, :aggregate_type, :aggregate_id, :user_id, :event_type,
                  :from_state, :to_state, :correlation_fingerprint,
                  cast(:details as jsonb), :now
                )
                """
            ),
            {
                "event_id": str(uuid4()),
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "user_id": str(user_id) if user_id else None,
                "event_type": event_type,
                "from_state": from_state,
                "to_state": to_state,
                "correlation_fingerprint": correlation_fingerprint,
                "details": json.dumps(details, sort_keys=True),
                "now": now or datetime.now(UTC),
            },
        )
