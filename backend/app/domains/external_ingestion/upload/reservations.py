from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.external_ingestion.upload.models import UploadReservation


class UploadQuotaExceeded(RuntimeError):
    pass


class UploadReservationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadQuotaPolicy:
    policy_version: str
    maximum_pending_bytes_per_user: int
    maximum_stored_bytes_per_user: int

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("upload quota policy version is required")
        if (
            self.maximum_pending_bytes_per_user <= 0
            or self.maximum_stored_bytes_per_user <= 0
        ):
            raise ValueError("upload quota limits must be positive")


class PostgresUploadReservationAdapter:
    """Durable, same-transaction quota reservation with append-only receipts."""

    def __init__(
        self,
        *,
        session: Session,
        policy: UploadQuotaPolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def reserve(
        self, *, owner_user_id: UUID, reservation_key: str, byte_size: int
    ) -> UploadReservation:
        if len(reservation_key) != 64 or any(
            character not in "0123456789abcdef" for character in reservation_key
        ):
            raise ValueError("reservation key must be a SHA-256 value")
        if byte_size <= 0:
            raise ValueError("reservation byte size must be positive")
        self._lock(f"upload-reservation-key:{reservation_key}")
        self._lock(f"upload-reservation-owner:{owner_user_id}")
        existing = self._session.execute(
            text(
                """
                select * from learning.upload_reservations
                where reservation_key = :reservation_key
                """
            ),
            {"reservation_key": reservation_key},
        ).mappings().one_or_none()
        now = self._clock()
        if existing is not None:
            if (
                existing["owner_user_id"] != owner_user_id
                or int(existing["byte_size"]) != byte_size
                or existing["policy_version"] != self._policy.policy_version
            ):
                raise UploadReservationConflict(
                    "reservation key belongs to different immutable input"
                )
            if existing["state"] in {"reserved", "committed"}:
                return UploadReservation(
                    reservation_id=str(existing["reservation_id"]),
                    reservation_key=reservation_key,
                )
            self._assert_capacity(owner_user_id=owner_user_id, byte_size=byte_size)
            attempt_no = int(existing["attempt_no"]) + 1
            self._session.execute(
                text(
                    """
                    update learning.upload_reservations
                    set state = 'reserved', attempt_no = :attempt_no,
                        artifact_version_id = null, release_reason = null,
                        updated_at = :updated_at
                    where reservation_id = :reservation_id
                      and state = 'released'
                    """
                ),
                {
                    "reservation_id": str(existing["reservation_id"]),
                    "attempt_no": attempt_no,
                    "updated_at": now,
                },
            )
            self._event(
                reservation_id=existing["reservation_id"],
                owner_user_id=owner_user_id,
                attempt_no=attempt_no,
                event_type="reserved",
                byte_size=byte_size,
                reference=self._policy.policy_version,
                created_at=now,
            )
            return UploadReservation(
                reservation_id=str(existing["reservation_id"]),
                reservation_key=reservation_key,
            )

        self._assert_capacity(owner_user_id=owner_user_id, byte_size=byte_size)
        reservation_id = uuid4()
        self._session.execute(
            text(
                """
                insert into learning.upload_reservations (
                  reservation_id, reservation_key, owner_user_id, byte_size,
                  policy_version, state, attempt_no, artifact_version_id,
                  release_reason, created_at, updated_at
                ) values (
                  :reservation_id, :reservation_key, :owner_user_id, :byte_size,
                  :policy_version, 'reserved', 1, null, null, :created_at, :updated_at
                )
                """
            ),
            {
                "reservation_id": str(reservation_id),
                "reservation_key": reservation_key,
                "owner_user_id": str(owner_user_id),
                "byte_size": byte_size,
                "policy_version": self._policy.policy_version,
                "created_at": now,
                "updated_at": now,
            },
        )
        self._event(
            reservation_id=reservation_id,
            owner_user_id=owner_user_id,
            attempt_no=1,
            event_type="reserved",
            byte_size=byte_size,
            reference=self._policy.policy_version,
            created_at=now,
        )
        return UploadReservation(
            reservation_id=str(reservation_id), reservation_key=reservation_key
        )

    def commit(self, *, reservation_id: str, artifact_version_id: UUID) -> None:
        reservation_uuid = _uuid(reservation_id)
        self._lock(f"upload-reservation:{reservation_uuid}")
        row = self._required(reservation_uuid)
        if row["state"] == "committed":
            if row["artifact_version_id"] != artifact_version_id:
                raise UploadReservationConflict(
                    "reservation is committed to another artifact"
                )
            return
        if row["state"] != "reserved":
            raise UploadReservationConflict("released reservation cannot be committed")
        now = self._clock()
        self._session.execute(
            text(
                """
                update learning.upload_reservations
                set state = 'committed', artifact_version_id = :artifact_version_id,
                    updated_at = :updated_at
                where reservation_id = :reservation_id and state = 'reserved'
                """
            ),
            {
                "reservation_id": str(reservation_uuid),
                "artifact_version_id": str(artifact_version_id),
                "updated_at": now,
            },
        )
        self._event(
            reservation_id=reservation_uuid,
            owner_user_id=row["owner_user_id"],
            attempt_no=int(row["attempt_no"]),
            event_type="committed",
            byte_size=int(row["byte_size"]),
            reference=str(artifact_version_id),
            created_at=now,
        )

    def release(self, *, reservation_id: str, reason: str) -> None:
        reservation_uuid = _uuid(reservation_id)
        if not reason.strip() or len(reason) > 160:
            raise ValueError("reservation release reason is required")
        self._lock(f"upload-reservation:{reservation_uuid}")
        row = self._required(reservation_uuid)
        if row["state"] == "released":
            if row["release_reason"] != reason:
                raise UploadReservationConflict(
                    "reservation replay changed the release reason"
                )
            return
        if row["state"] != "reserved":
            raise UploadReservationConflict("committed reservation cannot be released")
        now = self._clock()
        self._session.execute(
            text(
                """
                update learning.upload_reservations
                set state = 'released', release_reason = :reason,
                    updated_at = :updated_at
                where reservation_id = :reservation_id and state = 'reserved'
                """
            ),
            {
                "reservation_id": str(reservation_uuid),
                "reason": reason,
                "updated_at": now,
            },
        )
        self._event(
            reservation_id=reservation_uuid,
            owner_user_id=row["owner_user_id"],
            attempt_no=int(row["attempt_no"]),
            event_type="released",
            byte_size=int(row["byte_size"]),
            reference=reason,
            created_at=now,
        )

    def _assert_capacity(self, *, owner_user_id: UUID, byte_size: int) -> None:
        totals = self._session.execute(
            text(
                """
                select
                  coalesce(sum(byte_size) filter (where state = 'reserved'), 0) as pending,
                  coalesce(sum(byte_size) filter (where state = 'committed'), 0) as stored
                from learning.upload_reservations
                where owner_user_id = :owner_user_id
                """
            ),
            {"owner_user_id": str(owner_user_id)},
        ).mappings().one()
        if int(totals["pending"]) + byte_size > (
            self._policy.maximum_pending_bytes_per_user
        ):
            raise UploadQuotaExceeded("pending upload quota would be exceeded")
        if int(totals["stored"]) + byte_size > (
            self._policy.maximum_stored_bytes_per_user
        ):
            raise UploadQuotaExceeded("stored upload quota would be exceeded")

    def _required(self, reservation_id: UUID) -> object:
        row = self._session.execute(
            text(
                """
                select * from learning.upload_reservations
                where reservation_id = :reservation_id
                """
            ),
            {"reservation_id": str(reservation_id)},
        ).mappings().one_or_none()
        if row is None:
            raise UploadReservationConflict("upload reservation does not exist")
        return row

    def _event(
        self,
        *,
        reservation_id: UUID,
        owner_user_id: UUID,
        attempt_no: int,
        event_type: str,
        byte_size: int,
        reference: str,
        created_at: datetime,
    ) -> None:
        deduplication_key = sha256(
            f"{reservation_id}\n{attempt_no}\n{event_type}".encode("utf-8")
        ).hexdigest()
        inserted = self._session.execute(
            text(
                """
                insert into learning.upload_reservation_events (
                  event_id, deduplication_key, reservation_id, owner_user_id,
                  attempt_no, event_type, byte_size, reference, created_at
                ) values (
                  :event_id, :deduplication_key, :reservation_id, :owner_user_id,
                  :attempt_no, :event_type, :byte_size, :reference, :created_at
                ) on conflict (deduplication_key) do nothing
                returning event_id
                """
            ),
            {
                "event_id": str(uuid4()),
                "deduplication_key": deduplication_key,
                "reservation_id": str(reservation_id),
                "owner_user_id": str(owner_user_id),
                "attempt_no": attempt_no,
                "event_type": event_type,
                "byte_size": byte_size,
                "reference": reference,
                "created_at": created_at,
            },
        ).scalar_one_or_none()
        if inserted is None:
            current = self._session.execute(
                text(
                    """
                    select reservation_id, owner_user_id, attempt_no, event_type,
                           byte_size, reference
                    from learning.upload_reservation_events
                    where deduplication_key = :key
                    """
                ),
                {"key": deduplication_key},
            ).mappings().one()
            if (
                current["reservation_id"] != reservation_id
                or current["owner_user_id"] != owner_user_id
                or int(current["attempt_no"]) != attempt_no
                or current["event_type"] != event_type
                or int(current["byte_size"]) != byte_size
                or current["reference"] != reference
            ):
                raise UploadReservationConflict(
                    "reservation event key refers to different immutable content"
                )

    def _lock(self, key: str) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError("reservation ID must be a UUID") from error
