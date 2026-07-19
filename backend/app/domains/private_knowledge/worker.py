from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.domains.external_ingestion.upload.ports import PrivateObjectStoragePort
from app.domains.private_knowledge.repository import (
    DELETE_JOB_TYPE,
    PrivateKnowledgeRepository,
)
from app.platform.jobs import BackgroundJob, BackgroundJobRepository


class PrivateKnowledgeDeletionWorker:
    JOB_TYPES = (DELETE_JOB_TYPE,)

    def __init__(
        self,
        *,
        storage: PrivateObjectStoragePort,
        jobs: BackgroundJobRepository | None = None,
    ) -> None:
        self.storage = storage
        self.jobs = jobs or BackgroundJobRepository()

    def process_next(self, session: Session) -> BackgroundJob | None:
        job = self.jobs.claim_next(session, job_types=self.JOB_TYPES)
        if job is None:
            session.rollback()
            return None
        session.commit()
        if job.claim_token is None:
            raise RuntimeError("claimed private knowledge deletion has no token")
        owner = str(job.payload["ownerUserId"])
        deletion_id = UUID(str(job.payload["deletionRequestId"]))
        repository = PrivateKnowledgeRepository(session, jobs=self.jobs)
        try:
            source_id, object_refs = repository.mark_delete_running(
                deletion_id=deletion_id, owner_user_id=owner
            )
            session.commit()
            for index, object_ref in enumerate(object_refs):
                self.storage.delete_private(
                    object_ref=object_ref,
                    operation_key=f"private-knowledge-delete:{deletion_id}:{index}",
                )
            evidence = repository.complete_delete(
                deletion_id=deletion_id,
                owner_user_id=owner,
                source_id=source_id,
                object_count=len(object_refs),
                now=datetime.now(UTC),
            )
            completed = self.jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result=evidence,
                completed_at=datetime.now(UTC),
            )
            session.commit()
            return completed
        except Exception as exc:
            session.rollback()
            failed = self.jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code="private_knowledge_delete_failed",
                error_message=f"{type(exc).__name__}: {exc}"[:1000],
                retryable=True,
                now=datetime.now(UTC),
            )
            if failed.status == "failed":
                PrivateKnowledgeRepository(session, jobs=self.jobs).mark_delete_failed(
                    deletion_id=deletion_id,
                    owner_user_id=owner,
                    code="private_knowledge_delete_failed",
                    now=datetime.now(UTC),
                )
            else:
                PrivateKnowledgeRepository(session, jobs=self.jobs).requeue_delete(
                    deletion_id=deletion_id,
                    owner_user_id=owner,
                )
            session.commit()
            return failed
