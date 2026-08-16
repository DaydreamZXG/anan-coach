from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ai.governance.routing import prompt_injection_detected
from app.domains.companion.schemas import (
    CompanionCoachPreference,
    CompanionCoachRecheck,
    CompanionCoachRecheckCommand,
    CompanionCoachRecheckReceipt,
    CompanionCoachRetest,
    CompanionCoachUnderstanding,
    CompanionMemoryCategory,
    CompanionMemoryItem,
    CompanionMemoryState,
    CompanionMemorySummary,
)


MAX_PROVIDER_MEMORY_ITEMS = 20


class CompanionMemoryNotFound(LookupError):
    pass


class CompanionMemoryConflict(RuntimeError):
    pass


class CompanionMemoryUnsafeContent(ValueError):
    pass


class CompanionCoachRecheckConflict(RuntimeError):
    pass


class CompanionMemoryStore:
    """User-controlled durable preferences plus expiring conversation controls."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def state(self, *, user_id: str | UUID, now: datetime | None = None) -> CompanionMemoryState:
        checked_at = now or datetime.now(UTC)
        setting = self._session.execute(
            text(
                "select long_term_enabled from platform.companion_memory_settings "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": str(user_id)},
        ).scalar_one_or_none()
        rows = self._session.execute(
            text(
                """
                select memory_id, category, content, source, revision,
                       created_at, updated_at
                from platform.companion_memory_items
                where user_id = cast(:user_id as uuid)
                order by updated_at desc, memory_id
                """
            ),
            {"user_id": str(user_id)},
        ).mappings()
        summary = self._short_term_summary(user_id=user_id, now=checked_at)
        return CompanionMemoryState(
            longTermEnabled=True if setting is None else bool(setting),
            items=[_memory_item(row) for row in rows],
            shortTerm=summary,
            maximumLongTermItems=None,
        )

    def active_provider_items(
        self,
        *,
        user_id: str | UUID,
    ) -> tuple[dict[str, str | int], ...]:
        enabled = self._session.execute(
            text(
                "select long_term_enabled from platform.companion_memory_settings "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": str(user_id)},
        ).scalar_one_or_none()
        if enabled is False:
            return ()
        rows = self._session.execute(
            text(
                """
                select memory_id, category, content, revision
                from platform.companion_memory_items
                where user_id = cast(:user_id as uuid)
                order by updated_at desc, memory_id
                limit :item_limit
                """
            ),
            {"user_id": str(user_id), "item_limit": MAX_PROVIDER_MEMORY_ITEMS},
        ).mappings()
        return tuple(
            {
                "memoryId": str(row["memory_id"]),
                "category": str(row["category"]),
                "content": str(row["content"]),
                "revision": int(row["revision"]),
            }
            for row in rows
        )

    def coach_understanding(
        self,
        *,
        user_id: str | UUID,
        coach_context: dict[str, object],
    ) -> CompanionCoachUnderstanding:
        items = self.active_provider_items(user_id=user_id)
        effects = {
            "explanation_preference": ("applied_to_answers", None),
            "study_constraint": ("needs_confirmation", "/plans/study-preference/index"),
            "personal_note": ("context_only", None),
        }
        profile = dict(coach_context.get("profile") or {})
        decision = dict(profile.get("decisionSummary") or {})
        modules = list(profile.get("modules") or [])
        return CompanionCoachUnderstanding(
            coachContext=coach_context,
            explicitPreferences=[
                CompanionCoachPreference(
                    memoryId=str(item["memoryId"]),
                    revision=int(item["revision"]),
                    category=str(item["category"]),
                    content=str(item["content"]),
                    effect=effects[str(item["category"])][0],
                    actionUrl=effects[str(item["category"])][1],
                )
                for item in items
            ],
            pendingRechecks=[
                CompanionCoachRecheck(
                    requestId=row["request_id"],
                    revision=int(row["revision"]),
                    profileVersionId=row["profile_version_id"],
                    patternId=str(row["pattern_id"]),
                    state="pending_reverification",
                    note=row["note"],
                    createdAt=row["created_at"],
                    retestUrl="/xingce/xingce-mastery/index?bucket=due_verification",
                )
                for row in self._pending_rechecks(user_id=user_id)
            ],
            retest=CompanionCoachRetest(
                dueNow=int(decision.get("reviewDueCount") or 0),
                scheduled=sum(int(dict(item).get("dueCount") or 0) for item in modules),
                stable=sum(int(dict(item).get("stableMasteryCount") or 0) for item in modules),
                actionUrl="/xingce/xingce-mastery/index?bucket=due_verification",
            ),
        )

    def request_coach_recheck(
        self,
        *,
        user_id: str | UUID,
        command: CompanionCoachRecheckCommand,
        coach_context: dict[str, object],
        now: datetime | None = None,
    ) -> CompanionCoachRecheckReceipt:
        recorded_at = now or datetime.now(UTC)
        self._lock_user(user_id=user_id)
        payload = command.model_dump(mode="json", by_alias=True)
        request_fingerprint = _fingerprint(payload)
        existing = self._session.execute(
            text(
                "select * from learning.coach_judgment_recheck_versions "
                "where user_id = cast(:user_id as uuid) and command_id = :command_id"
            ),
            {"user_id": str(user_id), "command_id": command.command_id},
        ).mappings().one_or_none()
        if existing is not None:
            if str(existing["request_fingerprint"]) != request_fingerprint:
                raise CompanionCoachRecheckConflict("recheck command payload changed")
            return _coach_recheck_receipt(existing)
        if str(coach_context.get("profileVersionId")) != str(command.profile_version_id):
            raise CompanionCoachRecheckConflict("coach context changed")
        profile = dict(coach_context.get("profile") or {})
        patterns = list(profile.get("errorPatterns") or [])
        if not any(str(dict(item).get("patternId")) == command.pattern_id for item in patterns):
            raise CompanionMemoryNotFound("error pattern is not available")
        row = self._session.execute(
            text(
                """
                insert into learning.coach_judgment_recheck_versions (
                  request_version_id, request_id, command_id, request_fingerprint,
                  user_id, revision, profile_version_id, pattern_id, action, note,
                  state, supersedes_request_version_id, created_at
                ) values (
                  cast(:request_version_id as uuid), cast(:request_id as uuid),
                  :command_id, :request_fingerprint, cast(:user_id as uuid), 1,
                  cast(:profile_version_id as uuid), :pattern_id, 'not_accurate',
                  :note, 'pending_reverification', null, :created_at
                ) returning *
                """
            ),
            {
                "request_version_id": str(uuid5(NAMESPACE_URL, f"coach-recheck-version:{user_id}:{command.command_id}")),
                "request_id": str(uuid5(NAMESPACE_URL, f"coach-recheck:{user_id}:{command.command_id}")),
                "command_id": command.command_id,
                "request_fingerprint": request_fingerprint,
                "user_id": str(user_id),
                "profile_version_id": str(command.profile_version_id),
                "pattern_id": command.pattern_id,
                "note": command.note,
                "created_at": recorded_at,
            },
        ).mappings().one()
        return _coach_recheck_receipt(row)

    def _pending_rechecks(self, *, user_id: str | UUID) -> tuple[Mapping[str, object], ...]:
        return tuple(
            self._session.execute(
                text(
                    """
                    select item.* from learning.coach_judgment_recheck_versions item
                    where item.user_id = cast(:user_id as uuid)
                      and item.state = 'pending_reverification'
                      and not exists (
                        select 1 from learning.coach_judgment_recheck_versions successor
                        where successor.supersedes_request_version_id = item.request_version_id
                      )
                    order by item.created_at desc, item.request_id
                    """
                ),
                {"user_id": str(user_id)},
            ).mappings()
        )

    def create(
        self,
        *,
        user_id: str | UUID,
        idempotency_key: str,
        category: CompanionMemoryCategory,
        content: str,
        now: datetime | None = None,
    ) -> CompanionMemoryItem:
        created_at = now or datetime.now(UTC)
        normalized = _safe_content(content)
        command_fingerprint = sha256(idempotency_key.encode("utf-8")).hexdigest()
        request_fingerprint = _fingerprint(
            {"category": category, "content": normalized}
        )
        memory_id = uuid5(
            NAMESPACE_URL,
            f"anan-companion-memory:{user_id}:{command_fingerprint}",
        )
        self._lock_user(user_id=user_id)
        existing = self._session.execute(
            text(
                """
                select memory_id, category, content, source, revision,
                       created_at, updated_at, request_fingerprint
                from platform.companion_memory_items
                where user_id = cast(:user_id as uuid)
                  and source_command_fingerprint = :command_fingerprint
                """
            ),
            {
                "user_id": str(user_id),
                "command_fingerprint": command_fingerprint,
            },
        ).mappings().one_or_none()
        if existing is not None:
            if str(existing["request_fingerprint"]) != request_fingerprint:
                raise CompanionMemoryConflict("idempotency key was reused with another memory")
            return _memory_item(existing)
        row = self._session.execute(
            text(
                """
                insert into platform.companion_memory_items (
                  memory_id, user_id, source_command_fingerprint,
                  request_fingerprint, category, content, source,
                  revision, created_at, updated_at
                ) values (
                  cast(:memory_id as uuid), cast(:user_id as uuid),
                  :command_fingerprint, :request_fingerprint, :category,
                  :content, 'explicit_user', 1, :created_at, :created_at
                )
                returning memory_id, category, content, source, revision,
                          created_at, updated_at
                """
            ),
            {
                "memory_id": str(memory_id),
                "user_id": str(user_id),
                "command_fingerprint": command_fingerprint,
                "request_fingerprint": request_fingerprint,
                "category": category,
                "content": normalized,
                "created_at": created_at,
            },
        ).mappings().one()
        return _memory_item(row)

    def update(
        self,
        *,
        user_id: str | UUID,
        memory_id: UUID,
        revision: int,
        category: CompanionMemoryCategory,
        content: str,
        now: datetime | None = None,
    ) -> CompanionMemoryItem:
        updated_at = now or datetime.now(UTC)
        normalized = _safe_content(content)
        row = self._session.execute(
            text(
                """
                update platform.companion_memory_items
                set category = :category, content = :content,
                    revision = revision + 1, updated_at = :updated_at
                where memory_id = cast(:memory_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and revision = :revision
                returning memory_id, category, content, source, revision,
                          created_at, updated_at
                """
            ),
            {
                "memory_id": str(memory_id),
                "user_id": str(user_id),
                "revision": revision,
                "category": category,
                "content": normalized,
                "updated_at": updated_at,
            },
        ).mappings().one_or_none()
        if row is not None:
            return _memory_item(row)
        owned = bool(
            self._session.execute(
                text(
                    "select exists (select 1 from platform.companion_memory_items "
                    "where memory_id = cast(:memory_id as uuid) "
                    "and user_id = cast(:user_id as uuid))"
                ),
                {"memory_id": str(memory_id), "user_id": str(user_id)},
            ).scalar_one()
        )
        if owned:
            raise CompanionMemoryConflict("memory revision is stale")
        raise CompanionMemoryNotFound("memory item is not available")

    def delete(self, *, user_id: str | UUID, memory_id: UUID) -> int:
        result = self._session.execute(
            text(
                "delete from platform.companion_memory_items "
                "where memory_id = cast(:memory_id as uuid) "
                "and user_id = cast(:user_id as uuid)"
            ),
            {"memory_id": str(memory_id), "user_id": str(user_id)},
        )
        return max(int(result.rowcount or 0), 0)

    def clear_long_term(self, *, user_id: str | UUID) -> int:
        result = self._session.execute(
            text(
                "delete from platform.companion_memory_items "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": str(user_id)},
        )
        return max(int(result.rowcount or 0), 0)

    def clear_short_term(self, *, user_id: str | UUID) -> int:
        changed = 0
        for statement in (
            "delete from platform.companion_short_term_memory_turns "
            "where user_id = cast(:user_id as uuid)",
            "delete from messaging.ilink_short_term_memory_turns "
            "where user_id = cast(:user_id as uuid)",
        ):
            result = self._session.execute(text(statement), {"user_id": str(user_id)})
            changed += max(int(result.rowcount or 0), 0)
        return changed

    def set_enabled(
        self,
        *,
        user_id: str | UUID,
        enabled: bool,
        now: datetime | None = None,
    ) -> bool:
        updated_at = now or datetime.now(UTC)
        self._session.execute(
            text(
                """
                insert into platform.companion_memory_settings (
                  user_id, long_term_enabled, updated_at
                ) values (
                  cast(:user_id as uuid), :enabled, :updated_at
                )
                on conflict (user_id) do update
                set long_term_enabled = excluded.long_term_enabled,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "user_id": str(user_id),
                "enabled": enabled,
                "updated_at": updated_at,
            },
        )
        return enabled

    def _short_term_summary(
        self,
        *,
        user_id: str | UUID,
        now: datetime,
    ) -> CompanionMemorySummary:
        row = self._session.execute(
            text(
                """
                select count(*) as turn_count, max(expires_at) as expires_at
                from (
                  select expires_at
                  from platform.companion_short_term_memory_turns
                  where user_id = cast(:user_id as uuid) and expires_at > :now
                  union all
                  select expires_at
                  from messaging.ilink_short_term_memory_turns
                  where user_id = cast(:user_id as uuid) and expires_at > :now
                ) live_turns
                """
            ),
            {"user_id": str(user_id), "now": now},
        ).mappings().one()
        return CompanionMemorySummary(
            turnCount=int(row["turn_count"]),
            expiresAt=row["expires_at"],
            retentionHours=2,
        )

    def _lock_user(self, *, user_id: str | UUID) -> None:
        owned = self._session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": str(user_id)},
        ).scalar_one_or_none()
        if owned is None:
            raise CompanionMemoryNotFound("account is not available")


def _safe_content(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("memory content cannot be empty")
    if prompt_injection_detected(normalized):
        raise CompanionMemoryUnsafeContent("memory content cannot contain control instructions")
    return normalized


def _fingerprint(value: Mapping[str, object]) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _memory_item(row: Mapping[str, object]) -> CompanionMemoryItem:
    return CompanionMemoryItem(
        memoryId=row["memory_id"],
        category=row["category"],
        content=row["content"],
        source=row["source"],
        revision=row["revision"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def _coach_recheck_receipt(row: Mapping[str, object]) -> CompanionCoachRecheckReceipt:
    return CompanionCoachRecheckReceipt(
        requestId=row["request_id"],
        state="pending_reverification",
        revision=int(row["revision"]),
        retestUrl="/xingce/xingce-mastery/index?bucket=due_verification",
    )
