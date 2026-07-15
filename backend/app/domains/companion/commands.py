from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


class CompanionCommandConflict(RuntimeError):
    """An idempotency key was reused with different immutable input."""


class CompanionCommandStateError(RuntimeError):
    """The durable command is in a state the service cannot safely execute."""


@dataclass(frozen=True)
class CompanionCommandRecord:
    command_id: UUID
    user_id: str
    feature: str
    idempotency_key: str
    request_fingerprint: str
    request_contract_version: str
    execution_payload: dict[str, Any]
    status: str
    response_status_code: int | None
    response_payload: dict[str, Any] | None
    response_fingerprint: str | None
    response_contract_version: str
    reservation_key: str
    provider_idempotency_key: str | None
    delivery_count: int
    provider_attempt_count: int
    provider_request_id_header: str | None
    provider_request_id_fingerprint: str | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed"}


class CompanionCommandRepository:
    """Durable command ledger for paid synchronous Companion operations.

    Claiming and executing are deliberately separate transactions. The first
    transaction preserves the exact execution input and quota reservation before
    an external provider can charge us. The execution transaction takes the same
    advisory lock, so concurrent deliveries serialize and replay the winner.
    """

    def find(
        self,
        session: Session,
        *,
        user_id: str,
        feature: str,
        idempotency_key: str,
    ) -> CompanionCommandRecord | None:
        return self._by_identity(
            session,
            user_id=user_id,
            feature=feature,
            idempotency_key=idempotency_key,
            for_update=False,
        )

    def claim(
        self,
        session: Session,
        *,
        user_id: str,
        feature: str,
        idempotency_key: str,
        request_fingerprint: str,
        request_contract_version: str,
        execution_payload: dict[str, Any],
        response_contract_version: str,
        reservation_key: str,
        provider_idempotency_key: str | None,
    ) -> CompanionCommandRecord:
        _lock_identity(
            session,
            user_id=user_id,
            feature=feature,
            idempotency_key=idempotency_key,
        )
        current = self._by_identity(
            session,
            user_id=user_id,
            feature=feature,
            idempotency_key=idempotency_key,
            for_update=True,
        )
        if current is not None:
            _require_same_input(
                current,
                request_fingerprint=request_fingerprint,
                request_contract_version=request_contract_version,
                response_contract_version=response_contract_version,
                reservation_key=reservation_key,
                provider_idempotency_key=provider_idempotency_key,
            )
            if current.terminal:
                return current
            row = session.execute(
                text(
                    """
                    update platform.companion_paid_commands
                       set delivery_count = delivery_count + 1,
                           updated_at = now()
                     where command_id = cast(:command_id as uuid)
                       and status in ('processing', 'provider_uncertain')
                    returning *
                    """
                ),
                {"command_id": str(current.command_id)},
            ).mappings().one_or_none()
            if row is None:
                raise CompanionCommandStateError(
                    "Companion command changed while it was being claimed"
                )
            return _record(row)

        row = session.execute(
            text(
                """
                insert into platform.companion_paid_commands (
                  command_id, user_id, feature, idempotency_key,
                  request_fingerprint, request_contract_version,
                  execution_payload, status, response_status_code,
                  response_payload, response_fingerprint,
                  response_contract_version, reservation_key,
                  provider_idempotency_key, delivery_count,
                  provider_attempt_count, provider_request_id_header,
                  provider_request_id_fingerprint, last_error_code,
                  created_at, updated_at, completed_at
                ) values (
                  cast(:command_id as uuid), cast(:user_id as uuid), :feature,
                  :idempotency_key, :request_fingerprint,
                  :request_contract_version, cast(:execution_payload as jsonb),
                  'processing', null, null, null,
                  :response_contract_version, :reservation_key,
                  :provider_idempotency_key, 1, 0, null, null, null,
                  now(), now(), null
                )
                returning *
                """
            ),
            {
                "command_id": str(uuid4()),
                "user_id": user_id,
                "feature": feature,
                "idempotency_key": idempotency_key,
                "request_fingerprint": request_fingerprint,
                "request_contract_version": request_contract_version,
                "execution_payload": _json(execution_payload),
                "response_contract_version": response_contract_version,
                "reservation_key": reservation_key,
                "provider_idempotency_key": provider_idempotency_key,
            },
        ).mappings().one()
        return _record(row)

    def lock_for_execution(
        self,
        session: Session,
        *,
        user_id: str,
        feature: str,
        idempotency_key: str,
    ) -> CompanionCommandRecord:
        _lock_identity(
            session,
            user_id=user_id,
            feature=feature,
            idempotency_key=idempotency_key,
        )
        current = self._by_identity(
            session,
            user_id=user_id,
            feature=feature,
            idempotency_key=idempotency_key,
            for_update=False,
        )
        if current is None:
            raise CompanionCommandStateError("Prepared Companion command does not exist")
        return current

    def begin_provider_attempt(
        self,
        session: Session,
        *,
        command_id: UUID,
    ) -> CompanionCommandRecord:
        # This checkpoint must survive a later provider success followed by a
        # failed final application commit. The caller already owns the command's
        # transaction-scoped advisory lock, so normal competing deliveries stay
        # blocked while this deliberately independent transaction commits.
        engine = _new_checkpoint_engine(session)
        try:
            with engine.begin() as connection:
                row = connection.execute(
                    text(
                        """
                        update platform.companion_paid_commands
                           set status = 'processing',
                               provider_attempt_count = provider_attempt_count + 1,
                               updated_at = now()
                         where command_id = cast(:command_id as uuid)
                           and status in ('processing', 'provider_uncertain')
                        returning *
                        """
                    ),
                    {"command_id": str(command_id)},
                ).mappings().one_or_none()
        finally:
            engine.dispose()
        if row is None:
            raise CompanionCommandStateError(
                "Only an open Companion command can call the provider"
            )
        return _record(row)

    def succeed(
        self,
        session: Session,
        *,
        command_id: UUID,
        response_payload: dict[str, Any],
        provider_request_id_header: str | None = None,
        provider_request_id_fingerprint: str | None = None,
    ) -> CompanionCommandRecord:
        fingerprint = payload_fingerprint(response_payload)
        row = session.execute(
            text(
                """
                update platform.companion_paid_commands
                   set status = 'succeeded', response_status_code = 200,
                       response_payload = cast(:response_payload as jsonb),
                       response_fingerprint = :response_fingerprint,
                       provider_request_id_header = :provider_request_id_header,
                       provider_request_id_fingerprint =
                           :provider_request_id_fingerprint,
                       last_error_code = null, updated_at = now(),
                       completed_at = now()
                 where command_id = cast(:command_id as uuid)
                   and status = 'processing'
                returning *
                """
            ),
            {
                "command_id": str(command_id),
                "response_payload": _json(response_payload),
                "response_fingerprint": fingerprint,
                "provider_request_id_header": provider_request_id_header,
                "provider_request_id_fingerprint": provider_request_id_fingerprint,
            },
        ).mappings().one_or_none()
        if row is None:
            raise CompanionCommandStateError(
                "Only a processing Companion command can succeed"
            )
        return _record(row)

    def fail(
        self,
        session: Session,
        *,
        command_id: UUID,
        status_code: int,
        response_payload: dict[str, Any],
        error_code: str,
        provider_request_id_header: str | None = None,
        provider_request_id_fingerprint: str | None = None,
    ) -> CompanionCommandRecord:
        fingerprint = payload_fingerprint(response_payload)
        row = session.execute(
            text(
                """
                update platform.companion_paid_commands
                   set status = 'failed', response_status_code = :status_code,
                       response_payload = cast(:response_payload as jsonb),
                       response_fingerprint = :response_fingerprint,
                       provider_request_id_header = :provider_request_id_header,
                       provider_request_id_fingerprint =
                           :provider_request_id_fingerprint,
                       last_error_code = :error_code, updated_at = now(),
                       completed_at = now()
                 where command_id = cast(:command_id as uuid)
                   and status = 'processing'
                returning *
                """
            ),
            {
                "command_id": str(command_id),
                "status_code": status_code,
                "response_payload": _json(response_payload),
                "response_fingerprint": fingerprint,
                "error_code": error_code,
                "provider_request_id_header": provider_request_id_header,
                "provider_request_id_fingerprint": provider_request_id_fingerprint,
            },
        ).mappings().one_or_none()
        if row is None:
            raise CompanionCommandStateError(
                "Only a processing Companion command can fail"
            )
        return _record(row)

    def mark_provider_uncertain(
        self,
        session: Session,
        *,
        command_id: UUID,
        error_code: str,
    ) -> CompanionCommandRecord:
        row = session.execute(
            text(
                """
                update platform.companion_paid_commands
                   set status = 'provider_uncertain',
                       last_error_code = :error_code,
                       updated_at = now()
                 where command_id = cast(:command_id as uuid)
                   and status = 'processing'
                returning *
                """
            ),
            {"command_id": str(command_id), "error_code": error_code},
        ).mappings().one_or_none()
        if row is None:
            raise CompanionCommandStateError(
                "Only a processing Companion command can become uncertain"
            )
        return _record(row)

    def _by_identity(
        self,
        session: Session,
        *,
        user_id: str,
        feature: str,
        idempotency_key: str,
        for_update: bool,
    ) -> CompanionCommandRecord | None:
        suffix = " for update" if for_update else ""
        row = session.execute(
            text(
                "select * from platform.companion_paid_commands "
                "where user_id = cast(:user_id as uuid) and feature = :feature "
                f"and idempotency_key = :idempotency_key{suffix}"
            ),
            {
                "user_id": user_id,
                "feature": feature,
                "idempotency_key": idempotency_key,
            },
        ).mappings().one_or_none()
        return _record(row) if row is not None else None


def request_fingerprint(
    *,
    user_id: str,
    feature: str,
    contract_version: str,
    payload: dict[str, Any],
) -> str:
    return payload_fingerprint(
        {
            "userId": user_id,
            "feature": feature,
            "contractVersion": contract_version,
            "request": payload,
        }
    )


def stable_command_key(*, user_id: str, feature: str, idempotency_key: str) -> str:
    digest = sha256(
        f"{user_id}\n{feature}\n{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"companion:{feature}:{digest}"


def stable_provider_key(*, user_id: str, feature: str, idempotency_key: str) -> str:
    digest = sha256(
        f"provider\n{user_id}\n{feature}\n{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"companion-{digest}"


def payload_fingerprint(payload: dict[str, Any]) -> str:
    return sha256(_json(payload).encode("utf-8")).hexdigest()


def _lock_identity(
    session: Session,
    *,
    user_id: str,
    feature: str,
    idempotency_key: str,
) -> None:
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"{user_id}:{feature}:{idempotency_key}"},
    )


def _require_same_input(
    current: CompanionCommandRecord,
    *,
    request_fingerprint: str,
    request_contract_version: str,
    response_contract_version: str,
    reservation_key: str,
    provider_idempotency_key: str | None,
) -> None:
    immutable = (
        current.request_fingerprint == request_fingerprint,
        current.request_contract_version == request_contract_version,
        current.response_contract_version == response_contract_version,
        current.reservation_key == reservation_key,
        current.provider_idempotency_key == provider_idempotency_key,
    )
    if not all(immutable):
        raise CompanionCommandConflict(
            "Idempotency key already belongs to a different Companion command"
        )


def _record(row: RowMapping) -> CompanionCommandRecord:
    return CompanionCommandRecord(
        command_id=row["command_id"],
        user_id=str(row["user_id"]),
        feature=str(row["feature"]),
        idempotency_key=str(row["idempotency_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        request_contract_version=str(row["request_contract_version"]),
        execution_payload=dict(row["execution_payload"]),
        status=str(row["status"]),
        response_status_code=(
            int(row["response_status_code"])
            if row["response_status_code"] is not None
            else None
        ),
        response_payload=(
            dict(row["response_payload"])
            if row["response_payload"] is not None
            else None
        ),
        response_fingerprint=(
            str(row["response_fingerprint"])
            if row["response_fingerprint"] is not None
            else None
        ),
        response_contract_version=str(row["response_contract_version"]),
        reservation_key=str(row["reservation_key"]),
        provider_idempotency_key=(
            str(row["provider_idempotency_key"])
            if row["provider_idempotency_key"] is not None
            else None
        ),
        delivery_count=int(row["delivery_count"]),
        provider_attempt_count=int(row["provider_attempt_count"]),
        provider_request_id_header=row["provider_request_id_header"],
        provider_request_id_fingerprint=row[
            "provider_request_id_fingerprint"
        ],
        last_error_code=row["last_error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _new_checkpoint_engine(session: Session) -> Engine:
    bind = session.get_bind()
    if isinstance(bind, Connection):
        bind = bind.engine
    if not isinstance(bind, Engine):
        raise CompanionCommandStateError(
            "Companion provider checkpoint requires a SQLAlchemy Engine bind"
        )
    # A separate NullPool avoids deadlocking the API pool when every in-flight
    # request holds its main transaction lock and needs one short checkpoint
    # connection. The checkpoint connection is closed before provider I/O.
    return create_engine(
        bind.url,
        poolclass=NullPool,
        pool_pre_ping=True,
        future=True,
    )
