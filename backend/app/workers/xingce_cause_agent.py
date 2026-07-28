from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker


LOGGER = logging.getLogger(__name__)
STALE_RECOVERY_INTERVAL_SECONDS = 60.0


class _CauseAgentWorker(Protocol):
    def recover_stale_claims(self, session: Session) -> int: ...

    def process_next(self, session: Session) -> object | None: ...


SessionFactory = Callable[[], AbstractContextManager[Session]]


def _recover_stale_claims(*, worker: _CauseAgentWorker, session_factory: SessionFactory) -> None:
    with session_factory() as session:
        recovered = worker.recover_stale_claims(session)
    if recovered:
        LOGGER.warning(
            "cause-agent worker recovered stale claims",
            extra={"recovered_claims": recovered},
        )


def run_forever(
    *,
    worker: _CauseAgentWorker | None = None,
    session_factory: SessionFactory | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if worker is None:
        worker = CauseAgentWorker(settings=get_settings())
    if session_factory is None:
        session_factory = get_session_factory()

    _recover_stale_claims(worker=worker, session_factory=session_factory)
    next_stale_recovery_at = monotonic() + STALE_RECOVERY_INTERVAL_SECONDS
    while True:
        if monotonic() >= next_stale_recovery_at:
            _recover_stale_claims(worker=worker, session_factory=session_factory)
            next_stale_recovery_at = monotonic() + STALE_RECOVERY_INTERVAL_SECONDS
        with session_factory() as session:
            worked = worker.process_next(session)
        if worked is None:
            sleep(1.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
