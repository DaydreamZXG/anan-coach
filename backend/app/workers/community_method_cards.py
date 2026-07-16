from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.orm import Session

from app.domains.community_method_cards.schemas import CardSubmit
from app.domains.community_method_cards.service import CommunityMethodCardService
from app.platform.jobs import BackgroundJob, BackgroundJobRepository, JobContractError


CANDIDATE_JOB_TYPE: Final = "community_method_card_candidate_v2"
REVIEW_JOB_TYPE: Final = "community_method_card_review_v2"
JOB_INPUT_VERSION: Final = "community-method-card-job@2"


class CommunityMethodCardJobScheduler:
    def __init__(self, jobs: BackgroundJobRepository | None = None) -> None:
        self._jobs = jobs or BackgroundJobRepository()

    def enqueue_candidate(
        self,
        session: Session,
        *,
        author_user_id: str,
        card_id: str,
        source_version_id: str,
    ) -> BackgroundJob:
        return self._jobs.enqueue(
            session,
            job_key=f"community-method-card:candidate:{source_version_id}",
            job_type=CANDIDATE_JOB_TYPE,
            input_version=JOB_INPUT_VERSION,
            payload={"authorUserId": author_user_id, "cardId": card_id},
        )

    def enqueue_review(
        self,
        session: Session,
        *,
        author_user_id: str,
        command: CardSubmit,
    ) -> BackgroundJob:
        return self._jobs.enqueue(
            session,
            job_key=f"community-method-card:review:{command.version_id}",
            job_type=REVIEW_JOB_TYPE,
            input_version=JOB_INPUT_VERSION,
            payload={
                "authorUserId": author_user_id,
                "command": command.model_dump(by_alias=True, mode="json"),
            },
        )


class CommunityMethodCardWorker:
    JOB_TYPES: Final = (CANDIDATE_JOB_TYPE, REVIEW_JOB_TYPE)

    def __init__(
        self,
        *,
        service_factory: Callable[[Session], CommunityMethodCardService],
        jobs: BackgroundJobRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._jobs = jobs or BackgroundJobRepository()
        self._clock = clock or (lambda: datetime.now(UTC))

    def process_next(self, session: Session) -> BackgroundJob | None:
        job = self._jobs.claim_next(
            session, job_types=self.JOB_TYPES, now=self._clock()
        )
        if job is None:
            session.rollback()
            return None
        session.commit()
        if job.claim_token is None:
            raise JobContractError("claimed community method card job has no token")
        try:
            service = self._service_factory(session)
            if job.input_version != JOB_INPUT_VERSION:
                raise JobContractError("community method card job input version mismatch")
            if job.job_type == CANDIDATE_JOB_TYPE:
                result = service.generate_candidate(
                    author_user_id=str(job.payload["authorUserId"]),
                    card_id=str(job.payload["cardId"]),
                )
                payload = {
                    "candidateId": result.candidate_id,
                    "candidateStatus": result.status.value,
                }
            elif job.job_type == REVIEW_JOB_TYPE:
                result = service.submit_for_review(
                    author_user_id=str(job.payload["authorUserId"]),
                    command=CardSubmit.model_validate(job.payload["command"]),
                )
                payload = {
                    "reviewId": result.review_id,
                    "reviewStatus": result.status.value,
                }
            else:
                raise JobContractError("unsupported community method card job type")
            completed = self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result=payload,
                completed_at=self._clock(),
            )
            session.commit()
            return completed
        except Exception as exc:
            session.rollback()
            failed = self._jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code="community_method_card_job_failed",
                error_message=type(exc).__name__,
                retryable=not isinstance(exc, JobContractError),
                retry_after=timedelta(seconds=30),
                now=self._clock(),
            )
            session.commit()
            if isinstance(exc, JobContractError):
                return failed
            raise
