from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class TaskHelpSessionContext:
    state: str
    runtime_mode: str


class TaskHelpRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def session_context(
        self, *, user_id: str, session_id: str, requested_mode: str
    ) -> TaskHelpSessionContext | None:
        if requested_mode == "mock":
            row = self._session.execute(
                text(
                    """
                    select status as state, 'mock' as runtime_mode
                    from learner.mock_exam_sessions
                    where session_id = cast(:session_id as uuid)
                      and user_id = cast(:user_id as uuid)
                    """
                ),
                {"session_id": session_id, "user_id": user_id},
            ).mappings().one_or_none()
        else:
            row = self._session.execute(
                text(
                    """
                    select state, mode as runtime_mode
                    from learning.xingce_task_sessions
                    where session_id = cast(:session_id as uuid)
                      and user_id = cast(:user_id as uuid)
                    """
                ),
                {"session_id": session_id, "user_id": user_id},
            ).mappings().one_or_none()
        if row is None:
            return None
        return TaskHelpSessionContext(
            state=str(row["state"]), runtime_mode=str(row["runtime_mode"])
        )
