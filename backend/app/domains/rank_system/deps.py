from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.rank_system.repository import RankRepository
from app.domains.rank_system.service import RankService


def get_rank_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> RankService:
    return RankService(repository=RankRepository(session))
