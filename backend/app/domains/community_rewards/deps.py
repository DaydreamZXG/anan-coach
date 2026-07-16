from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.community_rewards.repository import SqlCommunityRewardRepository
from app.domains.community_rewards.service import CommunityRewardService


def get_community_reward_repository(
    session: Annotated[Session, Depends(get_db_session)],
) -> SqlCommunityRewardRepository:
    return SqlCommunityRewardRepository(session)


def get_community_reward_service(
    repository: Annotated[
        SqlCommunityRewardRepository,
        Depends(get_community_reward_repository),
    ],
) -> CommunityRewardService:
    return CommunityRewardService(repository)
