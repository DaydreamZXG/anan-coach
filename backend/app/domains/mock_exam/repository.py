from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
import json

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class MockExamSessionRecord:
    session_id: str
    user_id: str
    status: str
    question_ids: list[str]
    created_at: datetime
    submitted_at: datetime | None
    review_payload: dict[str, Any] | None
    review_viewed_at: datetime | None


@dataclass(frozen=True)
class AssignedQuestionRecord:
    question_id: str
    module_name: str
    correct_answer: str | None


class MockExamRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def select_module_question_ids(
        self,
        *,
        user_id: str,
        module_name: str,
        limit: int,
    ) -> list[str]:
        since = datetime.now(UTC) - timedelta(days=60)
        rows = self._session.execute(
            text(
                """
                select q.question_id
                from product.questions q
                join product.question_tags qt on qt.question_id = q.question_id
                join product.knowledge_map_nodes kmn
                  on kmn.exam_scope = 'guokao'
                 and kmn.track = 'xingce'
                 and kmn.module_name = q.module_name
                 and kmn.tag_type = qt.tag_type
                 and kmn.tag_value = qt.tag_value
                 and kmn.status = 'published'
                 and kmn.support_status = 'text_supported'
                 and kmn.near5_occurrence_count > 0
                where q.status = 'active'
                  and q.domain = 'objective'
                  and q.visual_required = false
                  and q.module_name = :module_name
                  and not exists (
                    select 1
                    from learner.practice_answers pa
                    where pa.user_id = cast(:user_id as uuid)
                      and pa.question_id = q.question_id
                      and pa.answered_at >= :since
                  )
                  and not exists (
                    select 1
                    from learner.mock_exam_sessions mes
                    cross join jsonb_array_elements_text(mes.question_ids) assigned(question_id)
                    where mes.user_id = cast(:user_id as uuid)
                      and mes.created_at >= :since
                      and assigned.question_id = q.question_id
                  )
                group by q.question_id, q.latest_year, q.occurrence_count
                order by
                  max(kmn.near5_weight) desc nulls last,
                  max(kmn.near5_occurrence_count) desc nulls last,
                  q.latest_year desc nulls last,
                  q.occurrence_count desc nulls last,
                  q.question_id
                limit :limit
                """
            ),
            {
                "user_id": user_id,
                "module_name": module_name,
                "since": since,
                "limit": limit,
            },
        ).mappings().all()
        return [str(row["question_id"]) for row in rows]

    def create_session(
        self,
        *,
        user_id: str,
        question_ids: list[str],
        blueprint: list[dict[str, object]],
    ) -> str:
        session_id = str(uuid4())
        self._session.execute(
            text(
                """
                insert into learner.mock_exam_sessions (
                  session_id,
                  user_id,
                  exam_scope,
                  track,
                  status,
                  question_ids,
                  blueprint,
                  created_at,
                  updated_at
                ) values (
                  cast(:session_id as uuid),
                  cast(:user_id as uuid),
                  'guokao',
                  'xingce',
                  'assigned',
                  cast(:question_ids as jsonb),
                  cast(:blueprint as jsonb),
                  now(),
                  now()
                )
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "question_ids": json.dumps(question_ids, ensure_ascii=False),
                "blueprint": json.dumps(blueprint, ensure_ascii=False),
            },
        )
        self._session.commit()
        return session_id

    def get_session(
        self,
        *,
        session_id: str,
        user_id: str,
    ) -> MockExamSessionRecord | None:
        row = self._session.execute(
            text(
                """
                select
                  session_id,
                  user_id,
                  status,
                  question_ids,
                  created_at,
                  submitted_at,
                  review_payload,
                  review_viewed_at
                from learner.mock_exam_sessions
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {"session_id": session_id, "user_id": user_id},
        ).mappings().first()
        if row is None:
            return None
        return MockExamSessionRecord(
            session_id=str(row["session_id"]),
            user_id=str(row["user_id"]),
            status=str(row["status"]),
            question_ids=list(row["question_ids"] or []),
            created_at=row["created_at"],
            submitted_at=row["submitted_at"],
            review_payload=dict(row["review_payload"]) if row["review_payload"] else None,
            review_viewed_at=row["review_viewed_at"],
        )

    def get_assigned_questions(
        self,
        *,
        question_ids: list[str],
    ) -> list[AssignedQuestionRecord]:
        rows = self._session.execute(
            text(
                """
                with assigned as (
                  select value as question_id, ordinality
                  from jsonb_array_elements_text(cast(:question_ids as jsonb))
                    with ordinality
                )
                select
                  q.question_id,
                  q.module_name,
                  q.answer as correct_answer
                from assigned
                join product.questions q on q.question_id = assigned.question_id
                order by assigned.ordinality
                """
            ),
            {"question_ids": json.dumps(question_ids, ensure_ascii=False)},
        ).mappings().all()
        return [
            AssignedQuestionRecord(
                question_id=str(row["question_id"]),
                module_name=str(row["module_name"]),
                correct_answer=row["correct_answer"],
            )
            for row in rows
        ]

    def get_previous_module_accuracy(
        self,
        *,
        user_id: str,
        before_session_id: str,
    ) -> dict[str, float]:
        row = self._session.execute(
            text(
                """
                select review_payload
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and session_id <> cast(:before_session_id as uuid)
                  and review_payload ? 'modules'
                order by submitted_at desc nulls last, created_at desc
                limit 1
                """
            ),
            {"user_id": user_id, "before_session_id": before_session_id},
        ).mappings().first()
        if row is None:
            return {}
        modules = row["review_payload"].get("modules", [])
        return {
            str(item["moduleName"]): float(item["accuracy"])
            for item in modules
            if isinstance(item, dict) and "moduleName" in item and "accuracy" in item
        }

    def get_wrong_clusters(
        self,
        *,
        wrong_question_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not wrong_question_ids:
            return []
        rows = self._session.execute(
            text(
                """
                with wrong_ids as (
                  select value as question_id
                  from jsonb_array_elements_text(cast(:wrong_question_ids as jsonb))
                )
                select
                  kmn.node_id,
                  kmn.display_path,
                  kmn.method_card_ids,
                  count(*) as wrong_count,
                  (
                    select mc.fix_action
                    from product.method_cards mc
                    where mc.status = 'published'
                      and mc.id in (
                        select jsonb_array_elements_text(kmn.method_card_ids)
                      )
                    order by mc.priority desc, mc.id
                    limit 1
                  ) as fix_action
                from wrong_ids wi
                join product.questions q on q.question_id = wi.question_id
                join product.question_tags qt on qt.question_id = q.question_id
                join product.knowledge_map_nodes kmn
                  on kmn.exam_scope = 'guokao'
                 and kmn.track = 'xingce'
                 and kmn.module_name = q.module_name
                 and kmn.tag_type = qt.tag_type
                 and kmn.tag_value = qt.tag_value
                 and kmn.status = 'published'
                 and kmn.support_status = 'text_supported'
                group by kmn.node_id, kmn.display_path, kmn.method_card_ids, kmn.near5_weight
                order by wrong_count desc, kmn.near5_weight desc nulls last, kmn.node_id
                limit 8
                """
            ),
            {"wrong_question_ids": json.dumps(wrong_question_ids, ensure_ascii=False)},
        ).mappings().all()
        return [dict(row) for row in rows]

    def submit_session(
        self,
        *,
        session_id: str,
        user_id: str,
        answers: list[dict[str, str]],
        review_payload: dict[str, Any],
    ) -> None:
        self._session.execute(
            text(
                """
                update learner.mock_exam_sessions
                set
                  status = 'submitted',
                  answers = cast(:answers as jsonb),
                  review_payload = cast(:review_payload as jsonb),
                  submitted_at = now(),
                  updated_at = now()
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "answers": json.dumps(answers, ensure_ascii=False),
                "review_payload": json.dumps(review_payload, ensure_ascii=False),
            },
        )
        self._session.commit()

    def mark_review_viewed(
        self,
        *,
        session_id: str,
        user_id: str,
    ) -> MockExamSessionRecord | None:
        self._session.execute(
            text(
                """
                update learner.mock_exam_sessions
                set
                  status = 'reviewed',
                  review_viewed_at = coalesce(review_viewed_at, now()),
                  updated_at = now()
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and review_payload is not null
                """
            ),
            {"session_id": session_id, "user_id": user_id},
        )
        self._session.commit()
        return self.get_session(session_id=session_id, user_id=user_id)
