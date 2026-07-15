from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.memory.repository import LearnerMemoryRepository
from app.domains.memory.service import LearnerMemoryService


def get_memory_repository(
    session: Annotated[Session, Depends(get_db_session)],
) -> LearnerMemoryRepository:
    return LearnerMemoryRepository(session)


def get_memory_service(
    repository: Annotated[LearnerMemoryRepository, Depends(get_memory_repository)],
) -> LearnerMemoryService:
    return LearnerMemoryService(repository)
