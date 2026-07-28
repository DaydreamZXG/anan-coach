from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


class QuestionFavoriteRepository:
    def lock_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> None:
        self._lock(
            session,
            key=f"xingce-favorite-command:{user_id}:{command_id}",
        )

    def lock_question(
        self,
        session: Session,
        *,
        user_id: str,
        question_id: str,
    ) -> None:
        self._lock(
            session,
            key=f"xingce-favorite-question:{user_id}:{question_id}",
        )

    def _lock(self, session: Session, *, key: str) -> None:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    def command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select *
                    from learning.xingce_question_favorite_commands
                    where user_id = cast(:user_id as uuid)
                      and command_id = :command_id
                    """
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )

    def catalog(
        self,
        session: Session,
        *,
        question_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select
                      question.question_id,
                      question.domain,
                      question.status,
                      head.content_version_id as current_content_version_id,
                      content.track as current_track
                    from product.questions question
                    left join product.question_content_heads head
                      on head.question_id = question.question_id
                    left join product.question_content_versions content
                      on content.question_id = head.question_id
                     and content.content_version_id = head.content_version_id
                    where question.question_id = :question_id
                    """
                ),
                {"question_id": question_id},
            )
            .mappings()
            .one_or_none()
        )

    def source_session_contains(
        self,
        session: Session,
        *,
        user_id: str,
        source_session_id: str,
        question_id: str,
        content_version_id: str,
    ) -> bool:
        return bool(
            session.execute(
                text(
                    """
                    select exists (
                      select 1
                      from learning.xingce_task_sessions task_session
                      cross join lateral jsonb_array_elements(
                        coalesce(
                          task_session.safe_bundle -> 'questions',
                          '[]'::jsonb
                        )
                      ) served_question
                      join product.question_content_versions content
                        on content.question_id =
                           served_question ->> 'questionId'
                       and content.content_version_id =
                           served_question ->> 'contentVersionId'
                       and content.track = 'xingce'
                      where task_session.session_id =
                            cast(:source_session_id as uuid)
                        and task_session.user_id = cast(:user_id as uuid)
                        and served_question ->> 'questionId' = :question_id
                        and served_question ->> 'contentVersionId' =
                            :content_version_id
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "source_session_id": source_session_id,
                    "question_id": question_id,
                    "content_version_id": content_version_id,
                },
            ).scalar_one()
        )

    def state(
        self,
        session: Session,
        *,
        user_id: str,
        question_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select *
                    from learning.xingce_question_favorite_states
                    where user_id = cast(:user_id as uuid)
                      and question_id = :question_id
                    """
                ),
                {"user_id": user_id, "question_id": question_id},
            )
            .mappings()
            .one_or_none()
        )

    def set_favorited(
        self,
        session: Session,
        *,
        user_id: str,
        question_id: str,
        content_version_id: str,
        source_session_id: str,
        revision: int,
        now: datetime,
    ) -> RowMapping:
        if revision == 1:
            statement = """
                insert into learning.xingce_question_favorite_states (
                  user_id, question_id, saved_content_version_id,
                  source_session_id, favorited, revision, favorited_at,
                  created_at, updated_at
                ) values (
                  cast(:user_id as uuid), :question_id,
                  :content_version_id, cast(:source_session_id as uuid),
                  true, 1, :now, :now, :now
                )
                returning *
            """
        else:
            statement = """
                update learning.xingce_question_favorite_states
                set saved_content_version_id = :content_version_id,
                    source_session_id = cast(:source_session_id as uuid),
                    favorited = true,
                    revision = :revision,
                    favorited_at = :now,
                    updated_at = :now
                where user_id = cast(:user_id as uuid)
                  and question_id = :question_id
                returning *
            """
        return (
            session.execute(
                text(statement),
                {
                    "user_id": user_id,
                    "question_id": question_id,
                    "content_version_id": content_version_id,
                    "source_session_id": source_session_id,
                    "revision": revision,
                    "now": now,
                },
            )
            .mappings()
            .one()
        )

    def set_not_favorited(
        self,
        session: Session,
        *,
        user_id: str,
        question_id: str,
        revision: int,
        now: datetime,
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    update learning.xingce_question_favorite_states
                    set saved_content_version_id = null,
                        source_session_id = null,
                        favorited = false,
                        revision = :revision,
                        favorited_at = null,
                        updated_at = :now
                    where user_id = cast(:user_id as uuid)
                      and question_id = :question_id
                    returning *
                    """
                ),
                {
                    "user_id": user_id,
                    "question_id": question_id,
                    "revision": revision,
                    "now": now,
                },
            )
            .mappings()
            .one()
        )

    def insert_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        question_id: str,
        request_fingerprint: str,
        response_payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.xingce_question_favorite_commands (
                  user_id, command_id, question_id, request_fingerprint,
                  response_payload, created_at
                ) values (
                  cast(:user_id as uuid), :command_id, :question_id,
                  :request_fingerprint, cast(:response_payload as jsonb),
                  :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "command_id": command_id,
                "question_id": question_id,
                "request_fingerprint": request_fingerprint,
                "response_payload": json.dumps(
                    response_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "created_at": created_at,
            },
        )

    def list_favorited(
        self,
        session: Session,
        *,
        user_id: str,
        cursor_at: datetime | None,
        cursor_question_id: str | None,
        limit: int,
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select
                      favorite.*,
                      question.domain,
                      question.status,
                      head.content_version_id as current_content_version_id,
                      content.track as current_track,
                      content.snapshot_payload
                        ->> 'module_name' as current_module_name,
                      content.snapshot_payload ->> 'stem' as current_stem
                    from learning.xingce_question_favorite_states favorite
                    join product.questions question
                      on question.question_id = favorite.question_id
                    left join product.question_content_heads head
                      on head.question_id = question.question_id
                    left join product.question_content_versions content
                      on content.question_id = head.question_id
                     and content.content_version_id = head.content_version_id
                    where favorite.user_id = cast(:user_id as uuid)
                      and favorite.favorited
                      and (
                        cast(:cursor_at as timestamptz) is null
                        or favorite.favorited_at < :cursor_at
                        or (
                          favorite.favorited_at = :cursor_at
                          and favorite.question_id > :cursor_question_id
                        )
                      )
                    order by favorite.favorited_at desc, favorite.question_id
                    limit :limit
                    """
                ),
                {
                    "user_id": user_id,
                    "cursor_at": cursor_at,
                    "cursor_question_id": cursor_question_id,
                    "limit": limit,
                },
            )
            .mappings()
            .all()
        )
