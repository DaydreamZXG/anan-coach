from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db.session import (
    get_session_factory,
    get_worker_heartbeat_session_factory,
)
from app.domains.ilink_channel.repository import ILinkRepository
from app.domains.ilink_channel.resolver import PhotoResolutionError, TosTransientImageStore
from app.domains.ilink_channel.settings import ILinkSettings
from app.platform.jobs import JobConsumerHeartbeatRuntime


LOGGER = logging.getLogger("gongkao.ilink_object_deletions")


class ILinkObjectDeletionWorker:
    """Expire transient iLink records and delete their private objects.

    Message transport belongs to OpenILink Hub. This worker deliberately has
    no bot token, cursor, long-polling, message parsing, or reply capability.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._retention = ILinkSettings.from_environment()
        self._storage = TosTransientImageStore(get_settings())
        self._sessions = get_session_factory()
        self._heartbeat_sessions = get_worker_heartbeat_session_factory(
            consumer_unit="gongkao-dazi-ilink-object-deletion-worker",
            consumer_entrypoint="app.workers.ilink_object_deletions",
        )
        self._stop = threading.Event()
        self._clock = clock or (lambda: datetime.now(UTC))

    def stop(self, *_args) -> None:
        self._stop.set()

    def run(self) -> None:
        with JobConsumerHeartbeatRuntime(
            session_factory=self._heartbeat_sessions,
            consumer_unit="gongkao-dazi-ilink-object-deletion-worker",
            consumer_entrypoint="app.workers.ilink_object_deletions",
        ):
            self._recover_object_deletions()
            while not self._stop.is_set():
                try:
                    self._run_retention_maintenance()
                    self._drain_object_deletions()
                except Exception:
                    LOGGER.exception("iLink object deletion cycle failed")
                self._stop.wait(5.0)

    def _run_retention_maintenance(self) -> None:
        now = self._clock()
        with self._sessions() as session:
            repository = ILinkRepository(session)
            stale_count = repository.expire_stale_processing(
                before=now - timedelta(seconds=self._retention.transient_retention_seconds),
                limit=200,
                now=now,
            )
            answered_count = repository.expire_answered_transients(
                before=now - timedelta(seconds=self._retention.transient_retention_seconds),
                limit=200,
                now=now,
            )
            expired_count = repository.expire_uncollected_queries(
                before=now - timedelta(days=self._retention.query_retention_days),
                limit=500,
                now=now,
            )
            session.commit()
        if stale_count or answered_count or expired_count:
            LOGGER.info(
                "iLink retention maintenance stale=%s answered_transients=%s expired=%s",
                stale_count,
                answered_count,
                expired_count,
            )

    def _drain_object_deletions(self) -> None:
        with self._sessions() as session:
            claims = ILinkRepository(session).claim_object_deletions(
                limit=20,
                now=self._clock(),
            )
            session.commit()
        for claim in claims:
            try:
                self._storage.delete(object_ref=claim.object_ref)
            except PhotoResolutionError as exc:
                with self._sessions() as session:
                    ILinkRepository(session).fail_object_deletion(
                        object_ref=claim.object_ref,
                        claim_token=claim.claim_token,
                        failure_code=exc.code,
                        now=self._clock(),
                    )
                    session.commit()
            else:
                with self._sessions() as session:
                    ILinkRepository(session).complete_object_deletion(
                        object_ref=claim.object_ref,
                        claim_token=claim.claim_token,
                        now=self._clock(),
                    )
                    session.commit()

    def _recover_object_deletions(self) -> int:
        with self._sessions() as session:
            recovered = ILinkRepository(session).recover_stale_object_deletions(now=self._clock())
            session.commit()
        if recovered:
            LOGGER.warning(
                "iLink object deletion worker recovered stale claims",
                extra={"recovered_deletions": recovered},
            )
        return recovered


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker = ILinkObjectDeletionWorker()
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
