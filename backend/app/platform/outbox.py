from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class OutboxContractError(RuntimeError):
    """Raised when an event key or claim violates the durable outbox contract."""


@dataclass(frozen=True)
class OutboxEvent:
    event_id: UUID
    deduplication_key: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, Any]
    status: str
    attempt_count: int
    available_at: datetime
    claim_token: UUID | None


class OutboxRepository:
    def append(
        self,
        session: Session,
        *,
        event_id: UUID | str | None = None,
        deduplication_key: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> OutboxEvent:
        if not all((deduplication_key, aggregate_type, aggregate_id, event_type)):
            raise OutboxContractError("Outbox identity fields are required")
        row = session.execute(
            text(
                """
                insert into platform.outbox_events (
                  event_id,
                  deduplication_key,
                  aggregate_type,
                  aggregate_id,
                  event_type,
                  payload
                ) values (
                  :event_id,
                  :deduplication_key,
                  :aggregate_type,
                  :aggregate_id,
                  :event_type,
                  cast(:payload as jsonb)
                )
                on conflict (deduplication_key) do nothing
                returning *
                """
            ),
            {
                "event_id": str(event_id or uuid4()),
                "deduplication_key": deduplication_key,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ).mappings().first()
        if row is None:
            row = session.execute(
                text(
                    "select * from platform.outbox_events where deduplication_key = :key"
                ),
                {"key": deduplication_key},
            ).mappings().one()
            if (
                row["aggregate_type"] != aggregate_type
                or row["aggregate_id"] != aggregate_id
                or row["event_type"] != event_type
                or row["payload"] != payload
            ):
                raise OutboxContractError(
                    "An existing deduplication key refers to a different event"
                )
        return _event(row)

    def claim_next(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> OutboxEvent | None:
        claimed_at = now or datetime.now(UTC)
        claim_token = uuid4()
        row = session.execute(
            text(
                """
                with candidate as (
                  select event_id
                  from platform.outbox_events
                  where status = 'pending'
                    and available_at <= :now
                  order by available_at, created_at
                  for update skip locked
                  limit 1
                )
                update platform.outbox_events as event
                set status = 'publishing',
                    attempt_count = attempt_count + 1,
                    claimed_at = :now,
                    claim_token = :claim_token
                from candidate
                where event.event_id = candidate.event_id
                returning event.*
                """
            ),
            {"now": claimed_at, "claim_token": str(claim_token)},
        ).mappings().first()
        return _event(row) if row else None

    def published(
        self,
        session: Session,
        *,
        event_id: UUID,
        claim_token: UUID,
        published_at: datetime | None = None,
    ) -> OutboxEvent:
        row = session.execute(
            text(
                """
                update platform.outbox_events
                set status = 'published',
                    published_at = :published_at,
                    claim_token = null,
                    last_error = null
                where event_id = :event_id
                  and status = 'publishing'
                  and claim_token = :claim_token
                returning *
                """
            ),
            {
                "event_id": str(event_id),
                "claim_token": str(claim_token),
                "published_at": published_at or datetime.now(UTC),
            },
        ).mappings().first()
        if row is None:
            raise OutboxContractError("Only the active claim can publish an event")
        return _event(row)

    def failed(
        self,
        session: Session,
        *,
        event_id: UUID,
        claim_token: UUID,
        error: str,
        max_attempts: int = 8,
        retry_after: timedelta = timedelta(seconds=30),
        now: datetime | None = None,
    ) -> OutboxEvent:
        failed_at = now or datetime.now(UTC)
        current = session.execute(
            text(
                """
                select *
                from platform.outbox_events
                where event_id = :event_id
                  and status = 'publishing'
                  and claim_token = :claim_token
                for update
                """
            ),
            {"event_id": str(event_id), "claim_token": str(claim_token)},
        ).mappings().first()
        if current is None:
            raise OutboxContractError("Only the active claim can fail an event")

        will_retry = current["attempt_count"] < max_attempts
        row = session.execute(
            text(
                """
                update platform.outbox_events
                set status = :status,
                    available_at = :available_at,
                    claimed_at = null,
                    claim_token = null,
                    last_error = :error
                where event_id = :event_id
                returning *
                """
            ),
            {
                "event_id": str(event_id),
                "status": "pending" if will_retry else "failed",
                "available_at": failed_at + retry_after if will_retry else current["available_at"],
                "error": error,
            },
        ).mappings().one()
        return _event(row)


def _event(row: RowMapping) -> OutboxEvent:
    return OutboxEvent(
        event_id=row["event_id"],
        deduplication_key=row["deduplication_key"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        event_type=row["event_type"],
        payload=dict(row["payload"]),
        status=row["status"],
        attempt_count=row["attempt_count"],
        available_at=row["available_at"],
        claim_token=row["claim_token"],
    )
