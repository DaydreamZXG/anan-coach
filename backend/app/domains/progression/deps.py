from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.domains.progression.service import ProgressionService
from app.domains.rank_system.deps import get_rank_service
from app.domains.rank_system.service import RankService


def get_progression_service(
    rank_service: Annotated[RankService, Depends(get_rank_service)],
) -> ProgressionService:
    return ProgressionService(rank_service=rank_service)
