from __future__ import annotations

from datetime import date

from app.domains.rank_system.repository import RankRepository
from app.domains.rank_system.schemas import (
    OfficialRankProfile,
    RankConfigItem,
    XpAwardResult,
)

# 经验值规则
XP_PER_CORRECT = 10
XP_PER_ESSAY_CORRECT = 30
XP_COMBO_BONUS = 20
XP_COMBO_THRESHOLD = 5
XP_DAILY_COMPLETE = 50
XP_MOCK_COMPLETE = 100
XP_MOCK_EXCEED_TARGET = 200

# 民心值规则
FAME_CORRECT = 1
FAME_WRONG = -1


class RankService:
    def __init__(self, *, repository: RankRepository) -> None:
        self._repository = repository

    def initialize_rank(self, *, user_id: str) -> None:
        """注册完成时调用，创建九品县令记录。"""
        self._repository.create_rank(user_id=user_id)
        self._repository.log_event(
            user_id=user_id,
            event_type="promotion",
            new_rank="nine_pin_xianling",
            reason="初授官职，九品县令",
        )

    def get_profile(self, *, user_id: str) -> OfficialRankProfile:
        """获取用户官职完整信息。"""
        rank = self._repository.get_rank(user_id=user_id)
        if rank is None:
            # 用户还没有官职记录，自动初始化
            self.initialize_rank(user_id=user_id)
            rank = self._repository.get_rank(user_id=user_id)
            if rank is None:
                raise RuntimeError("failed to initialize rank")

        next_rank = self._repository.get_next_rank(
            current_level=rank["rank_level"]
        )

        # 累计解锁：当前品级及以下所有品级（rank_level >= 当前）的解锁特性并集
        # 品级数字越大官越小（9=九品县令最低），升迁 = level 递减，
        # 故已获解锁 = 所有 rank_level >= 当前 level 的配置项 unlock_features 之和。
        all_configs = self._repository.list_all_rank_configs()
        current_level = rank["rank_level"]
        cumulative_unlocks: list[str] = []
        seen: set[str] = set()
        for cfg in sorted(all_configs, key=lambda c: c["rank_level"], reverse=True):
            if cfg["rank_level"] < current_level:
                continue
            feats = cfg.get("unlock_features") or []
            if isinstance(feats, str):
                import json
                feats = json.loads(feats)
            for feature in feats:
                if feature not in seen:
                    seen.add(feature)
                    cumulative_unlocks.append(feature)
        unlock_features = cumulative_unlocks

        xp_to_next = None
        if next_rank:
            xp_to_next = max(0, next_rank["xp_threshold"] - rank["xp"])

        return OfficialRankProfile(
            user_id=str(rank["user_id"]),
            rank_code=rank["rank_code"],
            rank_level=rank["rank_level"],
            rank_name=rank["rank_name"],
            xp=rank["xp"],
            fame=rank["fame"],
            stamina=rank["stamina"],
            consecutive_days=rank["consecutive_days"],
            demerits=rank["demerits"],
            ui_theme=rank["ui_theme"],
            next_rank_name=next_rank["rank_name"] if next_rank else None,
            next_rank_xp_threshold=next_rank["xp_threshold"] if next_rank else None,
            xp_to_next=xp_to_next,
            unlock_features=unlock_features,
        )

    def award_xp(
        self,
        *,
        user_id: str,
        correct: bool,
        is_essay: bool = False,
        combo_count: int = 0,
    ) -> XpAwardResult:
        """答题后调用，更新经验值和民心值。"""
        rank = self._repository.get_rank(user_id=user_id)
        if rank is None:
            self.initialize_rank(user_id=user_id)
            rank = self._repository.get_rank(user_id=user_id)
            if rank is None:
                raise RuntimeError("failed to initialize rank")

        old_rank_name = rank["rank_name"]
        old_rank_code = rank["rank_code"]
        old_rank_level = rank["rank_level"]

        # 计算经验值
        base_xp = 0
        if correct:
            base_xp = XP_PER_ESSAY_CORRECT if is_essay else XP_PER_CORRECT
        else:
            base_xp = 0  # 答错不加经验

        # 连击奖励
        combo_bonus = 0
        if correct and combo_count > 0 and combo_count % XP_COMBO_THRESHOLD == 0:
            combo_bonus = XP_COMBO_BONUS

        xp_delta = base_xp + combo_bonus

        # 民心值
        fame_delta = FAME_CORRECT if correct else FAME_WRONG

        # 更新
        updated = self._repository.add_xp_and_fame(
            user_id=user_id,
            xp_delta=xp_delta,
            fame_delta=fame_delta,
        )
        if updated is None:
            raise RuntimeError("failed to update xp")

        # 触摸活跃日期
        self._repository.touch_active_date(
            user_id=user_id, today=date.today()
        )

        # 记录事件
        self._repository.log_event(
            user_id=user_id,
            event_type="xp_gain",
            xp_delta=xp_delta,
            fame_delta=fame_delta,
            reason=f"答题{'正确' if correct else '错误'}" +
                   (f"，{combo_count}连击" if combo_bonus else ""),
        )

        # 检查升迁
        promoted = False
        new_rank_name = old_rank_name
        new_rank_code = old_rank_code
        new_rank_level = old_rank_level

        if correct:
            configs = self._repository.list_all_rank_configs()
            for cfg in sorted(configs, key=lambda c: c["rank_level"], reverse=True):
                if updated["xp"] >= cfg["xp_threshold"] and cfg["rank_level"] < old_rank_level:
                    new_rank_code = cfg["rank_code"]
                    new_rank_level = cfg["rank_level"]
                    new_rank_name = cfg["rank_name"]
                    promoted = True
                    break

            if promoted:
                self._repository.update_rank(
                    user_id=user_id,
                    rank_code=new_rank_code,
                    rank_level=new_rank_level,
                )
                self._repository.log_event(
                    user_id=user_id,
                    event_type="promotion",
                    old_rank=old_rank_code,
                    new_rank=new_rank_code,
                    xp_delta=xp_delta,
                    reason=f"政绩卓著，由{old_rank_name}升任{new_rank_name}",
                )

        return XpAwardResult(
            xp_gained=xp_delta,
            fame_delta=fame_delta,
            new_xp=updated["xp"],
            new_fame=updated["fame"],
            promoted=promoted,
            old_rank_name=old_rank_name if promoted else None,
            new_rank_name=new_rank_name if promoted else None,
            combo_bonus=combo_bonus,
        )

    def award_practice_answer(
        self,
        *,
        user_id: str,
        correct: bool,
    ) -> XpAwardResult:
        combo = self._repository.consecutive_correct_count(user_id=user_id)
        return self.award_xp(
            user_id=user_id,
            correct=correct,
            is_essay=False,
            combo_count=combo,
        )

    def award_xp_amount(
        self,
        *,
        user_id: str,
        xp_delta: int,
        reason: str,
    ) -> XpAwardResult:
        """按固定经验值发放（用于会试等整卷事件），并复用升迁判定。"""
        rank = self._repository.get_rank(user_id=user_id)
        if rank is None:
            self.initialize_rank(user_id=user_id)
            rank = self._repository.get_rank(user_id=user_id)
            if rank is None:
                raise RuntimeError("failed to initialize rank")

        old_rank_name = rank["rank_name"]
        old_rank_code = rank["rank_code"]
        old_rank_level = rank["rank_level"]

        updated = self._repository.add_xp_and_fame(
            user_id=user_id, xp_delta=xp_delta, fame_delta=0
        )
        if updated is None:
            raise RuntimeError("failed to update xp")

        self._repository.touch_active_date(user_id=user_id, today=date.today())
        self._repository.log_event(
            user_id=user_id,
            event_type="xp_gain",
            xp_delta=xp_delta,
            reason=reason,
        )

        promoted = False
        new_rank_name = old_rank_name
        new_rank_code = old_rank_code
        new_rank_level = old_rank_level

        configs = self._repository.list_all_rank_configs()
        for cfg in sorted(configs, key=lambda c: c["rank_level"], reverse=True):
            if updated["xp"] >= cfg["xp_threshold"] and cfg["rank_level"] < old_rank_level:
                new_rank_code = cfg["rank_code"]
                new_rank_level = cfg["rank_level"]
                new_rank_name = cfg["rank_name"]
                promoted = True
                break

        if promoted:
            self._repository.update_rank(
                user_id=user_id,
                rank_code=new_rank_code,
                rank_level=new_rank_level,
            )
            self._repository.log_event(
                user_id=user_id,
                event_type="promotion",
                old_rank=old_rank_code,
                new_rank=new_rank_code,
                xp_delta=xp_delta,
                reason=f"政绩卓著，由{old_rank_name}升任{new_rank_name}",
            )

        return XpAwardResult(
            xp_gained=xp_delta,
            fame_delta=0,
            new_xp=updated["xp"],
            new_fame=updated["fame"],
            promoted=promoted,
            old_rank_name=old_rank_name if promoted else None,
            new_rank_name=new_rank_name if promoted else None,
            combo_bonus=0,
        )

    def list_rank_configs(self) -> list[RankConfigItem]:
        """获取全部品级配置（供前端展示九品体系）。"""
        configs = self._repository.list_all_rank_configs()
        return [
            RankConfigItem(
                rank_code=c["rank_code"],
                rank_level=c["rank_level"],
                rank_name=c["rank_name"],
                xp_threshold=c["xp_threshold"],
                ui_theme=c["ui_theme"],
                ai_privilege=c.get("ai_privilege"),
                unlock_features=list(c.get("unlock_features") or []),
            )
            for c in configs
        ]
