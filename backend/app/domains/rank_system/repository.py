from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class RankRepository:
    def __init__(self, session: Session, *, auto_commit: bool = True) -> None:
        self._session = session
        self._auto_commit = auto_commit

    def get_rank(self, *, user_id: str) -> dict[str, Any] | None:
        row = (
            self._session.execute(
                text(
                    """
                select r.*, c.rank_name, c.ui_theme, c.unlock_features
                from learner.official_ranks r
                join product.official_rank_config c on c.rank_code = r.rank_code
                where r.user_id = cast(:uid as uuid)
                """
                ),
                {"uid": user_id},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def get_next_rank(self, *, current_level: int) -> dict[str, Any] | None:
        row = (
            self._session.execute(
                text(
                    """
                select rank_code, rank_level, rank_name, xp_threshold
                from product.official_rank_config
                where rank_level < :level
                order by rank_level desc
                limit 1
                """
                ),
                {"level": current_level},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def get_rank_config(self, *, rank_code: str) -> dict[str, Any] | None:
        row = (
            self._session.execute(
                text(
                    """
                select * from product.official_rank_config
                where rank_code = :code
                """
                ),
                {"code": rank_code},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def list_all_rank_configs(self) -> list[dict[str, Any]]:
        rows = (
            self._session.execute(
                text(
                    """
                select * from product.official_rank_config
                order by rank_level desc
                """
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    def consecutive_correct_count(self, *, user_id: str, limit: int = 20) -> int:
        """统计用户最近连续答对题数（从最新一条向前，遇错即断）。用于连击奖励。"""
        rows = self._session.execute(
            text(
                """
                select correct from learner.practice_answers
                where user_id = cast(:uid as uuid)
                order by answered_at desc
                limit :lim
                """
            ),
            {"uid": user_id, "lim": limit},
        ).all()
        streak = 0
        for (correct,) in rows:
            if correct:
                streak += 1
            else:
                break
        return streak

    def create_rank(self, *, user_id: str) -> None:
        self._session.execute(
            text(
                """
                insert into learner.official_ranks (user_id)
                values (cast(:uid as uuid))
                on conflict (user_id) do nothing
                """
            ),
            {"uid": user_id},
        )
        if self._auto_commit:
            self._session.commit()

    def add_xp_and_fame(
        self,
        *,
        user_id: str,
        xp_delta: int,
        fame_delta: int,
    ) -> dict[str, Any] | None:
        row = (
            self._session.execute(
                text(
                    """
                update learner.official_ranks
                set xp = xp + :xp,
                    fame = greatest(0, fame + :fame),
                    updated_at = now()
                where user_id = cast(:uid as uuid)
                returning *
                """
                ),
                {"uid": user_id, "xp": xp_delta, "fame": fame_delta},
            )
            .mappings()
            .first()
        )
        if self._auto_commit:
            self._session.commit()
        return dict(row) if row else None

    def update_rank(
        self,
        *,
        user_id: str,
        rank_code: str,
        rank_level: int,
    ) -> None:
        self._session.execute(
            text(
                """
                update learner.official_ranks
                set rank_code = :code,
                    rank_level = :level,
                    promoted_at = now(),
                    updated_at = now()
                where user_id = cast(:uid as uuid)
                """
            ),
            {"uid": user_id, "code": rank_code, "level": rank_level},
        )
        if self._auto_commit:
            self._session.commit()

    def log_event(
        self,
        *,
        user_id: str,
        event_type: str,
        old_rank: str | None = None,
        new_rank: str | None = None,
        xp_delta: int | None = None,
        fame_delta: int | None = None,
        reason: str | None = None,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into learner.rank_events
                    (user_id, event_type, old_rank, new_rank, xp_delta, fame_delta, reason)
                values
                    (cast(:uid as uuid), :etype, :old, :new, :xp, :fame, :reason)
                """
            ),
            {
                "uid": user_id,
                "etype": event_type,
                "old": old_rank,
                "new": new_rank,
                "xp": xp_delta,
                "fame": fame_delta,
                "reason": reason,
            },
        )
        if self._auto_commit:
            self._session.commit()

    def touch_active_date(
        self,
        *,
        user_id: str,
        today: date,
    ) -> None:
        self._session.execute(
            text(
                """
                update learner.official_ranks
                set consecutive_days = case
                        when last_active_date = :yesterday then consecutive_days + 1
                        when last_active_date = :today then consecutive_days
                        else 1
                    end,
                    last_active_date = :today,
                    updated_at = now()
                where user_id = cast(:uid as uuid)
                """
            ),
            {
                "uid": user_id,
                "today": today,
                "yesterday": today.fromordinal(today.toordinal() - 1),
            },
        )
        if self._auto_commit:
            self._session.commit()
