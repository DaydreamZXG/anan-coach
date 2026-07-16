from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker


LOGGER = logging.getLogger(__name__)


def run_forever() -> None:
    settings = get_settings()
    worker = CauseAgentWorker(settings=settings)
    session_factory = get_session_factory()
    while True:
        with session_factory() as session:
            worked = worker.process_next(session)
        if worked is None:
            time.sleep(1.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
