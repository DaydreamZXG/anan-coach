from __future__ import annotations

import logging
import signal
from threading import Event

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.learning_media.composition import (
    build_learning_media_scanner,
    build_learning_media_storage,
)
from app.domains.learning_media.worker import LearningMediaWorker


LOGGER = logging.getLogger("learning_media_worker")


def run() -> None:
    settings = get_settings()
    stop = Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("learning media worker stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker = LearningMediaWorker(
        storage=build_learning_media_storage(settings),
        scanner=build_learning_media_scanner(settings),
    )
    with get_session_factory()() as session:
        worker.recover_stale_claims(session)
        LOGGER.info("learning media worker started")
        while not stop.is_set():
            did_work = worker.process_next(session) is not None
            if not did_work:
                stop.wait(1.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
