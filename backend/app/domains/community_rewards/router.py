from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.community_rewards.deps import get_community_reward_service
from app.domains.community_rewards.schemas import CommunityRewardLedgerResponse
from app.domains.community_rewards.service import CommunityRewardService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/community-rewards",
    tags=["community-rewards"],
    responses=COMMON_API_ERROR_RESPONSES,
)


@router.get("/ledger", response_model=PublicApiEnvelope[CommunityRewardLedgerResponse])
def get_reward_ledger(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[
        CommunityRewardService,
        Depends(get_community_reward_service),
    ],
) -> PublicApiEnvelope[CommunityRewardLedgerResponse]:
    summary = service.get_ledger_summary(
        beneficiary_user_id=UUID(current_user.user_id),
    )
    return public_success_envelope(
        request=request,
        response=response,
        data=CommunityRewardLedgerResponse.from_record(summary),
    )
