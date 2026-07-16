"""Policy-gated contribution rewards for community method cards."""

from app.domains.community_rewards.deps import get_community_reward_service
from app.domains.community_rewards.repository import (
    InMemoryCommunityRewardRepository,
    SqlCommunityRewardRepository,
)
from app.domains.community_rewards.service import CommunityRewardService

__all__ = [
    "CommunityRewardService",
    "InMemoryCommunityRewardRepository",
    "SqlCommunityRewardRepository",
    "get_community_reward_service",
]
