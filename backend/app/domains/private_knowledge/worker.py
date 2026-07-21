from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.domains.external_ingestion.upload.ports import PrivateObjectStoragePort
from app.domains.private_knowledge.repository import (
    DELETE_JOB_TYPE,
    PrivateKnowledgeRepository,
)
from app.domains.private_knowledge.projection import (
    PROJECTION_INDEX_JOB_TYPE,
    PrivateKnowledgeProjectionError,
    PrivateKnowledgeProjectionGateway,
)
from app.domains.private_knowledge.weknora import WeKnoraError
from app.platform.jobs import BackgroundJob, BackgroundJobRepository


class PrivateKnowledgeDeletionWorker:
    JOB_TYPES = (DELETE_JOB_TYPE, PROJECTION_INDEX_JOB_TYPE)

    def __init__(
        self,
        *,
        storage: PrivateObjectStoragePort,
        jobs: BackgroundJobRepository | None = None,
        projection_gateway: PrivateKnowledgeProjectionGateway | None = None,
    ) -> None:
        self.storage = storage
        self.jobs = jobs or BackgroundJobRepository()
        self.projection_gateway = projection_gateway

    def process_next(self, session: Session) -> BackgroundJob | None:
        job = self.jobs.claim_next(session, job_types=self.JOB_TYPES)
        if job is None:
            session.rollback()
            return None
        session.commit()
        if job.claim_token is None:
            raise RuntimeError("claimed private knowledge deletion has no token")
        owner = str(job.payload["ownerUserId"])
        repository = PrivateKnowledgeRepository(session, jobs=self.jobs)
        try:
            if job.job_type == PROJECTION_INDEX_JOB_TYPE:
                if self.projection_gateway is None:
                    raise PrivateKnowledgeProjectionError(
                        "projection_worker_not_configured"
                    )
                evidence = self.projection_gateway.index_source(
                    owner_user_id=owner,
                    source_version_id=UUID(str(job.payload["sourceVersionId"])),
                )
            elif job.job_type == DELETE_JOB_TYPE:
                deletion_id = UUID(str(job.payload["deletionRequestId"]))
                source_id, object_refs = repository.mark_delete_running(
                    deletion_id=deletion_id, owner_user_id=owner
                )
                session.commit()
                remote_count = 0
                if self.projection_gateway is not None:
                    remote_count = self.projection_gateway.delete_source(
                        owner_user_id=owner, source_id=source_id
                    )
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
                evidence["remoteProjectionsDeleted"] = remote_count
            else:
                raise RuntimeError("unsupported private knowledge job type")
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
            retryable = True
            error_code = "private_knowledge_delete_failed"
            if isinstance(exc, PrivateKnowledgeProjectionError):
                retryable = exc.retryable
                error_code = exc.code
            elif isinstance(exc, WeKnoraError):
                retryable = exc.retryable or exc.uncertain
                error_code = exc.code
            failed = self.jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code=error_code,
                error_message=f"{type(exc).__name__}: {exc}"[:1000],
                retryable=retryable,
                now=datetime.now(UTC),
            )
            if job.job_type == DELETE_JOB_TYPE and failed.status == "failed":
                deletion_id = UUID(str(job.payload["deletionRequestId"]))
                PrivateKnowledgeRepository(session, jobs=self.jobs).mark_delete_failed(
                    deletion_id=deletion_id,
                    owner_user_id=owner,
                    code="private_knowledge_delete_failed",
                    now=datetime.now(UTC),
                )
            elif job.job_type == DELETE_JOB_TYPE:
                deletion_id = UUID(str(job.payload["deletionRequestId"]))
                PrivateKnowledgeRepository(session, jobs=self.jobs).requeue_delete(
                    deletion_id=deletion_id,
                    owner_user_id=owner,
                )
            elif (
                job.job_type == PROJECTION_INDEX_JOB_TYPE
                and failed.status == "failed"
                and self.projection_gateway is not None
            ):
                self.projection_gateway.repository.mark_source_failed(
                    owner_user_id=owner,
                    source_version_id=UUID(str(job.payload["sourceVersionId"])),
                    code=error_code,
                    now=datetime.now(UTC),
                )
            session.commit()
            return failed
