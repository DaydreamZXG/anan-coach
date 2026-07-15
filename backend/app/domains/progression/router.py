from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.progression.deps import get_progression_service
from app.domains.progression.schemas import CourtStateSnapshot
from app.domains.progression.service import ProgressionService
from app.domains.rank_system.schemas import OfficialRankProfile

router = APIRouter(prefix="/api/v1/progression", tags=["progression"])


@router.get("/profile", response_model=OfficialRankProfile)
def get_progression_profile(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ProgressionService, Depends(get_progression_service)],
) -> OfficialRankProfile:
    return service.get_profile(user_id=current_user.user_id)


@router.get("/court-state", response_model=CourtStateSnapshot)
def get_court_state(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[ProgressionService, Depends(get_progression_service)],
) -> CourtStateSnapshot:
    return service.get_court_state(user_id=current_user.user_id)
