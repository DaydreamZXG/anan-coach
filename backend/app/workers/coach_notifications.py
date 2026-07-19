from __future__ import annotations

import logging
import signal
from threading import Event

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.coach_notifications.worker import CoachNotificationWorker


LOGGER = logging.getLogger("coach_notification_worker")


def run() -> None:
    settings = get_settings()
    stop = Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("coach notification worker stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with get_session_factory()() as session:
        worker = CoachNotificationWorker(session=session, settings=settings)
        LOGGER.info("coach notification worker started")
        while not stop.is_set():
            did_work = worker.run_once()
            if not did_work:
                stop.wait(settings.coach_notification_worker_poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
