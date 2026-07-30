from __future__ import annotations

import logging
import signal
from threading import Event
import time

from app.core.config import Settings, get_settings
from app.db.session import (
    get_session_factory,
    get_worker_heartbeat_session_factory,
)
from app.domains.essay_intelligence.review_worker import GroundedEssayReviewWorker
from app.platform.jobs import JobConsumerHeartbeatRuntime


LOGGER = logging.getLogger("essay_review_worker")


def run() -> None:
    settings = get_settings()
    _validate_worker_settings(settings)
    worker = GroundedEssayReviewWorker(settings=settings)
    stop = Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("essay review worker stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    session_factory = get_session_factory()
    heartbeat_session_factory = get_worker_heartbeat_session_factory(
        consumer_unit="gongkao-dazi-essay-review-worker",
        consumer_entrypoint="app.workers.essay_reviews",
    )
    with JobConsumerHeartbeatRuntime(
        session_factory=heartbeat_session_factory,
        consumer_unit="gongkao-dazi-essay-review-worker",
        consumer_entrypoint="app.workers.essay_reviews",
    ):
        next_stale_recovery_at = 0.0
        LOGGER.info(
            "essay review worker started",
            extra={"grading_plan_release_id": settings.ai_essay_grading_plan_release_id},
        )
        while not stop.is_set():
            did_work = False
            monotonic_now = time.monotonic()
            try:
                if monotonic_now >= next_stale_recovery_at:
                    with session_factory() as session:
                        recovered = worker.recover_stale_claims(session)
                    next_stale_recovery_at = (
                        monotonic_now + settings.essay_review_worker_stale_recovery_seconds
                    )
                    if recovered:
                        LOGGER.warning(
                            "essay review worker recovered stale claims",
                            extra={"recovered_claims": recovered},
                        )
                with session_factory() as session:
                    did_work = worker.process_next(session) is not None
            except Exception:
                LOGGER.exception("essay review worker cycle failed")
            if not did_work:
                stop.wait(settings.essay_review_worker_poll_seconds)


def _validate_worker_settings(settings: Settings) -> None:
    if not settings.essay_review_worker_enabled:
        raise ValueError("ESSAY_REVIEW_WORKER_ENABLED must be true")
    if not 0.1 <= settings.essay_review_worker_poll_seconds <= 30:
        raise ValueError("ESSAY_REVIEW_WORKER_POLL_SECONDS must be between 0.1 and 30")
    if not 10 <= settings.essay_review_worker_stale_recovery_seconds <= 300:
        raise ValueError("ESSAY_REVIEW_WORKER_STALE_RECOVERY_SECONDS must be between 10 and 300")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
