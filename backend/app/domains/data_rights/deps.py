from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.data_rights.repository import DataRightsRepository
from app.domains.data_rights.service import DataRightsService


def get_data_rights_repository(
    session: Annotated[Session, Depends(get_db_session)],
) -> DataRightsRepository:
    return DataRightsRepository(session)


def get_data_rights_service(
    repository: Annotated[DataRightsRepository, Depends(get_data_rights_repository)],
) -> DataRightsService:
    return DataRightsService(repository)
