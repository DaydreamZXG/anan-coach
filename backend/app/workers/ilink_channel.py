from __future__ import annotations

import logging
import signal
import threading
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.ilink_channel.crypto import ILinkIdentityCipher
from app.domains.ilink_channel.protocol import ILinkProtocolClient, ILinkProtocolError
from app.domains.ilink_channel.repository import ILinkRepository
from app.domains.ilink_channel.resolver import PhotoResolutionError, TosTransientImageStore
from app.domains.ilink_channel.settings import ILinkSettings
from app.domains.ilink_channel.workflow import ILinkPhotoWorkflow


LOGGER = logging.getLogger("gongkao.ilink")


class ILinkWorker:
    def __init__(self) -> None:
        self._settings = ILinkSettings.from_environment()
        self._settings.validate_worker()
        self._client = ILinkProtocolClient(self._settings)
        self._workflow = ILinkPhotoWorkflow(
            core_settings=get_settings(), channel_settings=self._settings
        )
        self._storage = TosTransientImageStore(get_settings())
        self._sessions = get_session_factory()
        self._cipher = ILinkIdentityCipher(
            encryption_key=self._settings.encryption_key_bytes(),
            hmac_key=self._settings.identity_hmac_key,
            key_id=self._settings.identity_key_id,
        )
        self._stop = threading.Event()

    def stop(self, *_args) -> None:
        self._stop.set()

    def run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                self._run_retention_maintenance()
                self._drain_object_deletions()
                cursor = self._load_cursor()
                batch = self._client.get_updates(updates_buf=cursor)
                for message in batch.messages:
                    if self._stop.is_set():
                        return
                    with self._sessions() as session:
                        self._workflow.process(
                            session, message=message, client=self._client
                        )
                self._save_cursor(batch.updates_buf)
                self._drain_object_deletions()
                failures = 0
            except ILinkProtocolError as exc:
                failures += 1
                LOGGER.error("iLink protocol cycle failed code=%s", exc.code)
                if exc.code == "bot_token_stale":
                    self._clear_cursor()
                    raise RuntimeError(
                        "iLink bot authorization expired; scan the official QR code again"
                    ) from exc
                self._stop.wait(min(30.0, 2.0 ** min(failures, 5)))
            except Exception:
                failures += 1
                LOGGER.exception("iLink worker cycle failed without learner content")
                self._stop.wait(min(30.0, 2.0 ** min(failures, 5)))

    def _run_retention_maintenance(self) -> None:
        now = datetime.now(UTC)
        with self._sessions() as session:
            repository = ILinkRepository(session)
            stale_count = repository.expire_stale_processing(
                before=now
                - timedelta(seconds=self._settings.transient_retention_seconds),
                limit=200,
                now=now,
            )
            expired_count = repository.expire_uncollected_queries(
                before=now - timedelta(days=self._settings.query_retention_days),
                limit=500,
                now=now,
            )
            session.commit()
        if stale_count or expired_count:
            LOGGER.info(
                "iLink retention maintenance stale=%s expired=%s",
                stale_count,
                expired_count,
            )

    def _load_cursor(self) -> str:
        with self._sessions() as session:
            row = ILinkRepository(session).cursor(
                bot_account_id=self._settings.bot_account_id
            )
            if row is None:
                return ""
            return self._cipher.decrypt(
                str(row["encrypted_updates_buf"]),
                purpose=f"updates-cursor:{self._settings.bot_account_id}",
            )

    def _save_cursor(self, value: str) -> None:
        with self._sessions() as session:
            ILinkRepository(session).save_cursor(
                bot_account_id=self._settings.bot_account_id,
                encrypted_buf=self._cipher.encrypt(
                    value, purpose=f"updates-cursor:{self._settings.bot_account_id}"
                ),
                key_id=self._cipher.key_id,
                now=datetime.now(UTC),
            )
            session.commit()

    def _drain_object_deletions(self) -> None:
        with self._sessions() as session:
            refs = ILinkRepository(session).claim_object_deletions(
                limit=20, now=datetime.now(UTC)
            )
            session.commit()
        for object_ref in refs:
            try:
                self._storage.delete(object_ref=object_ref)
            except PhotoResolutionError as exc:
                with self._sessions() as session:
                    ILinkRepository(session).fail_object_deletion(
                        object_ref=object_ref,
                        failure_code=exc.code,
                        now=datetime.now(UTC),
                    )
                    session.commit()
            else:
                with self._sessions() as session:
                    ILinkRepository(session).complete_object_deletion(
                        object_ref=object_ref
                    )
                    session.commit()

    def _clear_cursor(self) -> None:
        with self._sessions() as session:
            ILinkRepository(session).clear_cursor(
                bot_account_id=self._settings.bot_account_id
            )
            session.commit()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker = ILinkWorker()
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
