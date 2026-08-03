from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.data_rights.repository import (
    DELETION_JOB_TYPE,
    EXPORT_JOB_TYPE,
    DataRightsRepository,
)
from app.domains.external_ingestion.upload.ports import PrivateObjectStoragePort
from app.platform.jobs import BackgroundJob, BackgroundJobRepository, JobContractError


class DataRightsWorker:
    JOB_TYPES = (EXPORT_JOB_TYPE, DELETION_JOB_TYPE)

    def __init__(
        self,
        *,
        storage: PrivateObjectStoragePort,
        settings: Settings,
        jobs: BackgroundJobRepository | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._jobs = jobs or BackgroundJobRepository()
        self._now = now or (lambda: datetime.now(UTC))
        if not 3600 <= settings.privacy_export_expiry_seconds <= 604800:
            raise ValueError("PRIVACY_EXPORT_EXPIRY_SECONDS must be 1 hour to 7 days")

    def process_next(self, session: Session) -> BackgroundJob | None:
        DataRightsRepository(session, jobs=self._jobs).expire_export_payloads(
            now=self._now()
        )
        job = self._jobs.claim_next(session, job_types=self.JOB_TYPES, now=self._now())
        if job is None:
            session.rollback()
            return None
        session.commit()
        if job.claim_token is None:
            raise JobContractError("claimed data-rights job has no claim token")
        repository = DataRightsRepository(session, jobs=self._jobs)
        try:
            if job.job_type == EXPORT_JOB_TYPE:
                result = self._export(repository, job)
            elif job.job_type == DELETION_JOB_TYPE:
                result = self._delete(repository, job)
            else:
                raise JobContractError("unsupported data-rights job type")
            completed = self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result=result,
                completed_at=self._now(),
            )
            session.commit()
            return completed
        except Exception as exc:
            session.rollback()
            failed = self._jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code="data_rights_processing_failed",
                error_message=f"{type(exc).__name__}: {exc}"[:1000],
                retryable=True,
                retry_after=timedelta(minutes=2),
                now=self._now(),
            )
            if failed.status == "failed":
                repository = DataRightsRepository(session, jobs=self._jobs)
                if job.job_type == EXPORT_JOB_TYPE:
                    repository.mark_export_failed(
                        export_id=UUID(str(job.payload["exportRequestId"])),
                        user_id=str(job.payload["userId"]),
                        failure_code="data_rights_processing_failed",
                    )
                elif job.job_type == DELETION_JOB_TYPE:
                    repository.mark_deletion_failed(
                        deletion_id=UUID(str(job.payload["deletionRequestId"])),
                        user_id=str(job.payload["userId"]),
                        failure_code="data_rights_processing_failed",
                        now=self._now(),
                    )
            session.commit()
            return failed

    def _export(
        self, repository: DataRightsRepository, job: BackgroundJob
    ) -> dict[str, object]:
        export_id = UUID(str(job.payload["exportRequestId"]))
        user_id = str(job.payload["userId"])
        repository.mark_export_running(export_id=export_id, user_id=user_id)
        payload = repository.build_export_payload(user_id=user_id, now=self._now())
        repository.complete_export(
            export_id=export_id,
            user_id=user_id,
            payload=payload,
            now=self._now(),
            expires_after=timedelta(seconds=self._settings.privacy_export_expiry_seconds),
        )
        return {"exportRequestId": str(export_id), "state": "completed"}

    def _delete(
        self, repository: DataRightsRepository, job: BackgroundJob
    ) -> dict[str, object]:
        deletion_id = UUID(str(job.payload["deletionRequestId"]))
        user_id = str(job.payload["userId"])
        repository.mark_deletion_running(deletion_id=deletion_id, user_id=user_id)
        object_refs = repository.deletion_object_refs(user_id=user_id)
        for index, object_ref in enumerate(object_refs):
            self._storage.delete_private(
                object_ref=object_ref,
                operation_key=sha256(
                    f"privacy-delete:{deletion_id}:{index}".encode("utf-8")
                ).hexdigest(),
            )
        manifest = repository.complete_deletion(
            deletion_id=deletion_id,
            user_id=user_id,
            deleted_object_count=len(object_refs),
            now=self._now(),
        )
        return {"deletionRequestId": str(deletion_id), "manifest": manifest}
