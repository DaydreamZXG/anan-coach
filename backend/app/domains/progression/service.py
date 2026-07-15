from __future__ import annotations

from app.domains.progression.schemas import CourtStateSnapshot, ProgressionEventResult
from app.domains.rank_system.schemas import OfficialRankProfile, XpAwardResult
from app.domains.rank_system.service import (
    RankService,
    XP_MOCK_COMPLETE,
    XP_MOCK_EXCEED_TARGET,
)


class ProgressionService:
    """Canonical owner for官阶、民心 and御史台-facing progression events."""

    def __init__(self, *, rank_service: RankService) -> None:
        self._rank_service = rank_service

    def get_profile(self, *, user_id: str) -> OfficialRankProfile:
        return self._rank_service.get_profile(user_id=user_id)

    def get_court_state(self, *, user_id: str) -> CourtStateSnapshot:
        return court_state_from_profile(self.get_profile(user_id=user_id))

    def record_verdict(
        self,
        *,
        user_id: str,
        correct: bool,
        module_name: str | None,
    ) -> ProgressionEventResult:
        award = self._rank_service.award_xp(
            user_id=user_id,
            correct=correct,
            is_essay=False,
            combo_count=0,
        )
        subject = module_name or "此卷"
        return _event_result(
            award=award,
            court_risk_delta=-1 if correct else 2,
            narrative=(
                f"{subject}准字落印，政绩 +{award.xp_gained}，民心 +{award.fame_delta}。"
                if correct
                else f"{subject}暂驳，民心 {award.fame_delta}，御史台已记下此处破绽。"
            ),
        )

    def record_practice_answer(
        self,
        *,
        user_id: str,
        correct: bool,
    ) -> ProgressionEventResult:
        award = self._rank_service.award_practice_answer(
            user_id=user_id,
            correct=correct,
        )
        return _event_result(
            award=award,
            court_risk_delta=-1 if correct else 2,
            narrative=(
                f"日常政务判准，政绩 +{award.xp_gained}。"
                if correct
                else "日常政务判驳，御史台已收录错卷。"
            ),
        )

    def record_essay_review(self, *, user_id: str) -> ProgressionEventResult:
        award = self._rank_service.award_xp(
            user_id=user_id,
            correct=True,
            is_essay=True,
        )
        return _event_result(
            award=award,
            court_risk_delta=-2,
            narrative=f"平灾奏折已批阅，政绩 +{award.xp_gained}。",
        )

    def record_mock_exam(
        self,
        *,
        user_id: str,
        estimated_score: float,
    ) -> ProgressionEventResult:
        mock_xp = XP_MOCK_COMPLETE
        if estimated_score >= 60:
            mock_xp += XP_MOCK_EXCEED_TARGET
        award = self._rank_service.award_xp_amount(
            user_id=user_id,
            xp_delta=mock_xp,
            reason="完成会试整卷模考",
        )
        return _event_result(
            award=award,
            court_risk_delta=-3,
            narrative=f"会试收卷，政绩 +{award.xp_gained}。",
        )


def court_state_from_profile(rank: OfficialRankProfile) -> CourtStateSnapshot:
    popular_sentiment = max(0, min(100, rank.fame))
    stability = max(0, min(100, popular_sentiment - rank.demerits * 10))
    if popular_sentiment < 20 or rank.demerits >= 3:
        risk_level = "danger"
        background_tone = "dark_red"
    elif popular_sentiment < 40 or rank.demerits > 0:
        risk_level = "watch"
        background_tone = "muted_red"
    else:
        risk_level = "stable"
        background_tone = "paper"
    return CourtStateSnapshot(
        popular_sentiment=popular_sentiment,
        stability=stability,
        demerits=rank.demerits,
        risk_level=risk_level,
        background_tone=background_tone,
    )


def _event_result(
    *,
    award: XpAwardResult,
    court_risk_delta: int,
    narrative: str,
) -> ProgressionEventResult:
    return ProgressionEventResult(
        merit_delta=award.xp_gained,
        fame_delta=award.fame_delta,
        court_risk_delta=court_risk_delta,
        rank_changed=award.promoted,
        old_rank_name=award.old_rank_name,
        new_rank_name=award.new_rank_name,
        locked_privileges=[],
        narrative=(
            f"{narrative} 已由{award.old_rank_name}升任{award.new_rank_name}。"
            if award.promoted and award.old_rank_name and award.new_rank_name
            else narrative
        ),
    )
