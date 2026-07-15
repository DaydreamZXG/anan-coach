from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.rank_system.deps import get_rank_service
from app.domains.rank_system.schemas import OfficialRankProfile, RankConfigItem
from app.domains.rank_system.service import RankService

router = APIRouter(prefix="/api/v1/rank", tags=["rank"])


@router.get("/profile", response_model=OfficialRankProfile)
def get_rank_profile(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[RankService, Depends(get_rank_service)],
) -> OfficialRankProfile:
    return service.get_profile(user_id=current_user.user_id)


@router.get("/configs", response_model=list[RankConfigItem])
def get_rank_configs(
    service: Annotated[RankService, Depends(get_rank_service)],
) -> list[RankConfigItem]:
    return service.list_rank_configs()
