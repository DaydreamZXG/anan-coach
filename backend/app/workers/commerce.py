from __future__ import annotations

import logging
import signal
from threading import Event

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.commerce.stage8_repository import Stage8CommerceRepository
from app.domains.commerce.wechat_virtual_payment import (
    Stage8VirtualPaymentService,
    WechatVirtualPaymentClient,
)


LOGGER = logging.getLogger("commerce_reconciliation_worker")


def run() -> None:
    settings = get_settings()
    stop = Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("commerce worker stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with get_session_factory()() as session:
        base_repository = CommerceRepository(session)
        service = Stage8VirtualPaymentService(
            repository=Stage8CommerceRepository(session),
            entitlement_service=CommerceService(
                repository=base_repository,
                app_store_verifier=AppStoreSignedTransactionVerifier(settings),
                commercial_mode=settings.commercial_mode,
                usage_access_mode=settings.usage_access_mode,
            ),
            gateway=WechatVirtualPaymentClient(settings),
            settings=settings,
        )
        LOGGER.info("commerce reconciliation worker started")
        while not stop.is_set():
            try:
                result = service.reconcile_pending()
                if any(result.values()):
                    LOGGER.info("commerce reconciliation completed", extra=result)
            except Exception:
                session.rollback()
                LOGGER.exception("commerce reconciliation cycle failed")
            stop.wait(15.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
