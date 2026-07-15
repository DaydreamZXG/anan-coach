from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class JobContractError(RuntimeError):
    """Raised when a job key or state transition violates the durable contract."""


@dataclass(frozen=True)
class BackgroundJob:
    job_id: UUID
    job_key: str
    job_type: str
    input_version: str
    payload: dict[str, Any]
    status: str
    attempt_count: int
    max_attempts: int
    run_after: datetime
    claim_token: UUID | None
    result: dict[str, Any] | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class BackgroundJobRepository:
    def requeue_stale(
        self,
        session: Session,
        *,
        job_types: tuple[str, ...],
        stale_before: datetime,
        now: datetime | None = None,
    ) -> int:
        """Release crashed claims without consuming a retry attempt.

        Domain workers use stable provider idempotency keys and durable checkpoints,
        so reclaiming the same attempt cannot silently create a new logical call.
        """
        if not job_types:
            raise JobContractError(
                "Stale recovery must be scoped to explicit job types"
            )
        result = session.execute(
            text(
                """
                update platform.background_jobs
                   set status = 'queued',
                       attempt_count = greatest(attempt_count - 1, 0),
                       run_after = :now,
                       claimed_at = null,
                       claim_token = null,
                       updated_at = :now,
                       last_error_code = 'stale_claim_recovered',
                       last_error_message = null
                 where status = 'running'
                   and claimed_at < :stale_before
                   and job_type = any(cast(:job_types as text[]))
                """
            ),
            {
                "now": now or datetime.now(UTC),
                "stale_before": stale_before,
                "job_types": list(job_types),
            },
        )
        return int(result.rowcount or 0)

    def checkpoint(
        self,
        session: Session,
        *,
        job_id: UUID,
        claim_token: UUID,
        result: dict[str, Any],
        updated_at: datetime | None = None,
    ) -> BackgroundJob:
        row = session.execute(
            text(
                """
                update platform.background_jobs
                   set result = cast(:result as jsonb), updated_at = :updated_at
                 where job_id = :job_id
                   and status = 'running'
                   and claim_token = :claim_token
                 returning *
                """
            ),
            {
                "job_id": str(job_id),
                "claim_token": str(claim_token),
                "result": json.dumps(result, ensure_ascii=False, sort_keys=True),
                "updated_at": updated_at or datetime.now(UTC),
            },
        ).mappings().first()
        if row is None:
            raise JobContractError("Only the active claim can checkpoint a running job")
        return _job(row)

    def by_key(self, session: Session, *, job_key: str) -> BackgroundJob | None:
        row = session.execute(
            text("select * from platform.background_jobs where job_key = :job_key"),
            {"job_key": job_key},
        ).mappings().one_or_none()
        return _job(row) if row is not None else None

    def by_id(self, session: Session, *, job_id: UUID) -> BackgroundJob | None:
        row = session.execute(
            text(
                "select * from platform.background_jobs "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(job_id)},
        ).mappings().one_or_none()
        return _job(row) if row is not None else None

    def enqueue(
        self,
        session: Session,
        *,
        job_key: str,
        job_type: str,
        input_version: str,
        payload: dict[str, Any],
        max_attempts: int = 3,
        run_after: datetime | None = None,
    ) -> BackgroundJob:
        if not job_key or not job_type or not input_version:
            raise JobContractError("job_key, job_type, and input_version are required")
        if max_attempts <= 0:
            raise JobContractError("max_attempts must be positive")

        row = session.execute(
            text(
                """
                insert into platform.background_jobs (
                  job_id,
                  job_key,
                  job_type,
                  input_version,
                  payload,
                  max_attempts,
                  run_after
                ) values (
                  :job_id,
                  :job_key,
                  :job_type,
                  :input_version,
                  cast(:payload as jsonb),
                  :max_attempts,
                  :run_after
                )
                on conflict (job_key) do nothing
                returning *
                """
            ),
            {
                "job_id": str(uuid4()),
                "job_key": job_key,
                "job_type": job_type,
                "input_version": input_version,
                "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "max_attempts": max_attempts,
                "run_after": run_after or datetime.now(UTC),
            },
        ).mappings().first()
        if row is None:
            row = session.execute(
                text("select * from platform.background_jobs where job_key = :job_key"),
                {"job_key": job_key},
            ).mappings().one()
            if (
                row["job_type"] != job_type
                or row["input_version"] != input_version
                or row["payload"] != payload
            ):
                raise JobContractError("An existing job key refers to different immutable input")
        return _job(row)

    def claim_next(
        self,
        session: Session,
        *,
        job_types: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> BackgroundJob | None:
        claim_token = uuid4()
        row = session.execute(
            text(
                """
                with candidate as (
                  select job_id
                  from platform.background_jobs
                  where status = 'queued'
                    and run_after <= :now
                    and (
                      cardinality(cast(:job_types as text[])) = 0
                      or job_type = any(cast(:job_types as text[]))
                    )
                  order by run_after, created_at
                  for update skip locked
                  limit 1
                )
                update platform.background_jobs as job
                set status = 'running',
                    attempt_count = attempt_count + 1,
                    claimed_at = :now,
                    claim_token = :claim_token,
                    updated_at = :now
                from candidate
                where job.job_id = candidate.job_id
                returning job.*
                """
            ),
            {
                "now": now or datetime.now(UTC),
                "job_types": list(job_types),
                "claim_token": str(claim_token),
            },
        ).mappings().first()
        return _job(row) if row else None

    def succeed(
        self,
        session: Session,
        *,
        job_id: UUID,
        claim_token: UUID,
        result: dict[str, Any],
        completed_at: datetime | None = None,
    ) -> BackgroundJob:
        row = session.execute(
            text(
                """
                update platform.background_jobs
                set status = 'succeeded',
                    result = cast(:result as jsonb),
                    claim_token = null,
                    completed_at = :completed_at,
                    updated_at = :completed_at,
                    last_error_code = null,
                    last_error_message = null
                where job_id = :job_id
                  and status = 'running'
                  and claim_token = :claim_token
                returning *
                """
            ),
            {
                "job_id": str(job_id),
                "claim_token": str(claim_token),
                "result": json.dumps(result, ensure_ascii=False, sort_keys=True),
                "completed_at": completed_at or datetime.now(UTC),
            },
        ).mappings().first()
        if row is None:
            raise JobContractError("Only the active claim can complete a running job")
        return _job(row)

    def fail(
        self,
        session: Session,
        *,
        job_id: UUID,
        claim_token: UUID,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_after: timedelta = timedelta(seconds=30),
        now: datetime | None = None,
    ) -> BackgroundJob:
        failed_at = now or datetime.now(UTC)
        current = session.execute(
            text(
                """
                select *
                from platform.background_jobs
                where job_id = :job_id
                  and status = 'running'
                  and claim_token = :claim_token
                for update
                """
            ),
            {"job_id": str(job_id), "claim_token": str(claim_token)},
        ).mappings().first()
        if current is None:
            raise JobContractError("Only the active claim can fail a running job")

        will_retry = retryable and current["attempt_count"] < current["max_attempts"]
        status_value = "queued" if will_retry else "failed"
        row = session.execute(
            text(
                """
                update platform.background_jobs
                set status = :status,
                    run_after = :run_after,
                    claim_token = null,
                    claimed_at = null,
                    last_error_code = :error_code,
                    last_error_message = :error_message,
                    completed_at = :completed_at,
                    updated_at = :now
                where job_id = :job_id
                returning *
                """
            ),
            {
                "job_id": str(job_id),
                "status": status_value,
                "run_after": failed_at + retry_after if will_retry else current["run_after"],
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": None if will_retry else failed_at,
                "now": failed_at,
            },
        ).mappings().one()
        return _job(row)


def _job(row: RowMapping) -> BackgroundJob:
    return BackgroundJob(
        job_id=row["job_id"],
        job_key=row["job_key"],
        job_type=row["job_type"],
        input_version=row["input_version"],
        payload=dict(row["payload"]),
        status=row["status"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        run_after=row["run_after"],
        claim_token=row["claim_token"],
        result=dict(row["result"]) if row["result"] is not None else None,
        last_error_code=row["last_error_code"],
        last_error_message=row["last_error_message"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )
