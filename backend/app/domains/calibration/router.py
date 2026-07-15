from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.calibration.repository import CalibrationRepository
from app.domains.calibration.schemas import AbilityEstimate
from app.domains.calibration.service import CalibrationService

router = APIRouter(prefix="/api/v1/calibration", tags=["calibration"])


def get_calibration_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> CalibrationService:
    return CalibrationService(CalibrationRepository(session))


@router.get("/ability", response_model=AbilityEstimate)
def get_ability_estimate(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[CalibrationService, Depends(get_calibration_service)],
) -> AbilityEstimate:
    return service.get_ability_estimate(user_id=current_user.user_id)
