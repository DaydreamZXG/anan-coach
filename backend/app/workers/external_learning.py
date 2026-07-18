from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import signal
from threading import Event

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.domains.external_ingestion.composition import build_upload_service
from app.domains.external_ingestion.answering.worker import ExternalAIAnswerWorker
from app.domains.external_ingestion.upload.service import UploadService
from app.domains.learning_packages.composition import build_packet_render_worker
from app.domains.learning_packages.worker import PacketRenderWorker
from app.platform.jobs import BackgroundJobRepository


LOGGER = logging.getLogger("external_learning_worker")


class ExternalLearningWorker:
    """Runs packet rendering and durable upload maintenance in one process."""

    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        packet_worker: PacketRenderWorker | None = None,
        answer_worker: ExternalAIAnswerWorker | None = None,
        upload_service: UploadService | None = None,
        jobs: BackgroundJobRepository | None = None,
    ) -> None:
        _validate_worker_settings(settings)
        self._session = session
        self._settings = settings
        self._packet_worker = packet_worker or build_packet_render_worker(
            session=session,
            settings=settings,
        )
        self._answer_worker = answer_worker or ExternalAIAnswerWorker(settings=settings)
        self._uploads = upload_service or build_upload_service(
            session=session,
            settings=settings,
        )
        self._jobs = jobs or BackgroundJobRepository()
        self._next_maintenance_at = datetime.min.replace(tzinfo=UTC)

    def run_once(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        did_work = False
        if current >= self._next_maintenance_at:
            did_work = self._maintain(now=current) or did_work
            self._next_maintenance_at = current + timedelta(
                seconds=self._settings.external_learning_maintenance_interval_seconds
            )
        try:
            did_work = self._answer_worker.process_next(self._session) is not None or did_work
        except Exception:
            LOGGER.exception("external AI answer job failed unexpectedly")
        try:
            did_work = self._packet_worker.process_next(self._session) is not None or did_work
        except Exception:
            LOGGER.exception("packet render job failed unexpectedly")
        return did_work

    def _maintain(self, *, now: datetime) -> bool:
        try:
            stale_jobs = self._jobs.requeue_stale(
                self._session,
                job_types=(
                    *PacketRenderWorker.JOB_TYPES,
                    *ExternalAIAnswerWorker.JOB_TYPES,
                ),
                stale_before=now
                - timedelta(
                    seconds=self._settings.external_learning_stale_claim_seconds
                ),
                now=now,
            )
            recovered = self._uploads.recover_pending(
                before=now
                - timedelta(
                    seconds=self._settings.external_learning_upload_recovery_age_seconds
                ),
                limit=self._settings.external_learning_maintenance_batch_size,
            )
            expired = self._uploads.expire_stale(
                before=now,
                limit=self._settings.external_learning_maintenance_batch_size,
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            LOGGER.exception("external learning maintenance failed")
            return False
        if stale_jobs or recovered or expired:
            LOGGER.info(
                "external learning maintenance completed",
                extra={
                    "stale_jobs": stale_jobs,
                    "recovered_uploads": len(recovered),
                    "expired_uploads": len(expired),
                },
            )
            return True
        return False


def run() -> None:
    settings = get_settings()
    stop = Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("external learning worker stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    session_factory = get_session_factory()
    with session_factory() as session:
        worker = ExternalLearningWorker(session=session, settings=settings)
        LOGGER.info("external learning worker started")
        while not stop.is_set():
            did_work = worker.run_once()
            if not did_work:
                stop.wait(settings.external_learning_worker_poll_seconds)


def _validate_worker_settings(settings: Settings) -> None:
    positive_values = {
        "EXTERNAL_LEARNING_WORKER_POLL_SECONDS": (
            settings.external_learning_worker_poll_seconds
        ),
        "EXTERNAL_LEARNING_MAINTENANCE_INTERVAL_SECONDS": (
            settings.external_learning_maintenance_interval_seconds
        ),
        "EXTERNAL_LEARNING_UPLOAD_RECOVERY_AGE_SECONDS": (
            settings.external_learning_upload_recovery_age_seconds
        ),
        "EXTERNAL_LEARNING_STALE_CLAIM_SECONDS": (
            settings.external_learning_stale_claim_seconds
        ),
        "EXTERNAL_LEARNING_MAINTENANCE_BATCH_SIZE": (
            settings.external_learning_maintenance_batch_size
        ),
    }
    invalid = [name for name, value in positive_values.items() if value <= 0]
    if invalid:
        raise ValueError(f"worker settings must be positive: {', '.join(invalid)}")
    if settings.external_learning_maintenance_batch_size > 1_000:
        raise ValueError("EXTERNAL_LEARNING_MAINTENANCE_BATCH_SIZE cannot exceed 1000")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
