from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService


def get_commerce_repository(
    session: Annotated[Session, Depends(get_db_session)],
) -> CommerceRepository:
    return CommerceRepository(session)


def get_app_store_transaction_verifier() -> AppStoreSignedTransactionVerifier:
    return AppStoreSignedTransactionVerifier(get_settings())


def get_commerce_service(
    repository: Annotated[CommerceRepository, Depends(get_commerce_repository)],
    app_store_verifier: Annotated[
        AppStoreSignedTransactionVerifier,
        Depends(get_app_store_transaction_verifier),
    ],
) -> CommerceService:
    return CommerceService(
        repository=repository,
        app_store_verifier=app_store_verifier,
    )
