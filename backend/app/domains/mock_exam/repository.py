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
    blueprint: list[dict[str, Any]]
    revision: int
    started_at: datetime
    deadline_at: datetime
    checkpoint: dict[str, Any]
    learner_bundle: dict[str, Any]
    scoring_snapshot: dict[str, Any]
    checkpoint_updated_at: datetime
    created_at: datetime
    submitted_at: datetime | None
    review_payload: dict[str, Any] | None
    review_viewed_at: datetime | None


@dataclass(frozen=True)
class AssignedQuestionRecord:
    question_id: str
    module_name: str
    correct_option_ids: frozenset[str]
    valid_option_ids: frozenset[str]


class MockExamRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_user(self, *, user_id: str) -> None:
        row = self._session.execute(
            text(
                """
                select user_id
                from learner.users
                where user_id = cast(:user_id as uuid)
                for update
                """
            ),
            {"user_id": user_id},
        ).first()
        if row is None:
            raise LookupError("mock exam owner does not exist")

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
                  and q.question_type = 'single_choice'
                  and nullif(trim(q.answer), '') is not null
                  and q.module_name = :module_name
                  and exists (
                    select 1 from product.question_options qo
                    where qo.question_id = q.question_id
                  )
                  and exists (
                    select 1 from product.question_options answer_option
                    where answer_option.question_id = q.question_id
                      and upper(trim(answer_option.label)) = upper(trim(q.answer))
                  )
                  and (
                    not q.visual_required
                    or exists (
                      select 1 from product.question_assets qa
                      where qa.question_id = q.question_id
                        and qa.image_src ~
                          '^https://kg\\.xingguoxian\\.cn(?::443)?/assets/'
                    )
                  )
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

    def get_assigned_session(self, *, user_id: str) -> MockExamSessionRecord | None:
        row = self._session.execute(
            text(
                """
                select session_id
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and exam_scope = 'guokao'
                  and track = 'xingce'
                  and status = 'assigned'
                order by created_at
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        return self.get_session(session_id=str(row["session_id"]), user_id=user_id)

    def get_frozen_question_sources(
        self, *, question_ids: list[str]
    ) -> list[dict[str, Any]]:
        rows = self._session.execute(
            text(
                """
                with assigned as (
                  select value as question_id, ordinality
                  from jsonb_array_elements_text(cast(:question_ids as jsonb))
                    with ordinality
                )
                select
                  q.question_id, q.module_name, q.question_type,
                  q.stem, q.stem_html, q.answer,
                  q.material_text, q.material_html,
                  q.shared_context_required, q.visual_required,
                  e.preferred_analysis, e.analysis_source, e.analysis_status,
                  e.prefer_ai_analysis, e.analysis_needs_review,
                  assigned.ordinality
                from assigned
                join product.questions q on q.question_id = assigned.question_id
                left join product.question_explanations e
                  on e.question_id = q.question_id
                order by assigned.ordinality
                """
            ),
            {"question_ids": json.dumps(question_ids, ensure_ascii=False)},
        ).mappings().all()
        sources: list[dict[str, Any]] = []
        for row in rows:
            source = dict(row)
            source.pop("ordinality", None)
            source["options"] = [
                dict(item)
                for item in self._session.execute(
                    text(
                        """
                        select label, option_text, option_html
                        from product.question_options
                        where question_id = :question_id
                        order by option_index
                        """
                    ),
                    {"question_id": row["question_id"]},
                ).mappings()
            ]
            source["assets"] = [
                dict(item)
                for item in self._session.execute(
                    text(
                        """
                        select asset_id, part_kind, part_index, image_ordinal,
                               image_src, object_key, is_representative_asset
                        from product.question_assets
                        where question_id = :question_id
                        order by part_kind nulls last, part_index nulls last,
                                 image_ordinal nulls last, asset_id
                        """
                    ),
                    {"question_id": row["question_id"]},
                ).mappings()
            ]
            sources.append(source)
        return sources

    def create_session(
        self,
        *,
        user_id: str,
        question_ids: list[str],
        blueprint: list[dict[str, object]],
        checkpoint: dict[str, Any],
        learner_bundle: dict[str, Any],
        scoring_snapshot: dict[str, Any],
        started_at: datetime,
        deadline_at: datetime,
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
                  revision,
                  started_at,
                  deadline_at,
                  checkpoint,
                  learner_bundle,
                  scoring_snapshot,
                  checkpoint_updated_at,
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
                  1,
                  :started_at,
                  :deadline_at,
                  cast(:checkpoint as jsonb),
                  cast(:learner_bundle as jsonb),
                  cast(:scoring_snapshot as jsonb),
                  :started_at,
                  :started_at,
                  :started_at
                )
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "question_ids": json.dumps(question_ids, ensure_ascii=False),
                "blueprint": json.dumps(blueprint, ensure_ascii=False),
                "checkpoint": json.dumps(checkpoint, ensure_ascii=False),
                "learner_bundle": json.dumps(learner_bundle, ensure_ascii=False),
                "scoring_snapshot": json.dumps(scoring_snapshot, ensure_ascii=False),
                "started_at": started_at,
                "deadline_at": deadline_at,
            },
        )
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
                  blueprint,
                  revision,
                  started_at,
                  deadline_at,
                  checkpoint,
                  learner_bundle,
                  scoring_snapshot,
                  checkpoint_updated_at,
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
            blueprint=list(row["blueprint"] or []),
            revision=int(row["revision"]),
            started_at=row["started_at"],
            deadline_at=row["deadline_at"],
            checkpoint=dict(row["checkpoint"]),
            learner_bundle=dict(row["learner_bundle"]),
            scoring_snapshot=dict(row["scoring_snapshot"]),
            checkpoint_updated_at=row["checkpoint_updated_at"],
            created_at=row["created_at"],
            submitted_at=row["submitted_at"],
            review_payload=dict(row["review_payload"]) if row["review_payload"] else None,
            review_viewed_at=row["review_viewed_at"],
        )

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
        expected_revision: int,
    ) -> bool:
        row = self._session.execute(
            text(
                """
                update learner.mock_exam_sessions
                set
                  status = 'submitted',
                  answers = cast(:answers as jsonb),
                  review_payload = cast(:review_payload as jsonb),
                  revision = revision + 1,
                  submitted_at = now(),
                  updated_at = now()
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and status = 'assigned'
                  and revision = :expected_revision
                returning revision
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "answers": json.dumps(answers, ensure_ascii=False),
                "review_payload": json.dumps(review_payload, ensure_ascii=False),
                "expected_revision": expected_revision,
            },
        ).mappings().one_or_none()
        return row is not None

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
        return self.get_session(session_id=session_id, user_id=user_id)

    def save_checkpoint(
        self,
        *,
        session_id: str,
        user_id: str,
        expected_revision: int,
        checkpoint: dict[str, Any],
        updated_at: datetime,
    ) -> MockExamSessionRecord | None:
        row = self._session.execute(
            text(
                """
                update learner.mock_exam_sessions
                set revision = revision + 1,
                    checkpoint = cast(:checkpoint as jsonb),
                    checkpoint_updated_at = :updated_at,
                    updated_at = :updated_at
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and status = 'assigned'
                  and revision = :expected_revision
                  and deadline_at > :updated_at
                returning session_id
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "expected_revision": expected_revision,
                "checkpoint": json.dumps(checkpoint, ensure_ascii=False),
                "updated_at": updated_at,
            },
        ).mappings().one_or_none()
        if row is None:
            return None
        return self.get_session(session_id=session_id, user_id=user_id)

    def get_command(self, *, user_id: str, idempotency_key: str) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select command_type, session_id, request_hash, response_payload
                from learner.mock_exam_commands
                where user_id = cast(:user_id as uuid)
                  and idempotency_key = :idempotency_key
                """
            ),
            {"user_id": user_id, "idempotency_key": idempotency_key},
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def record_command(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        command_type: str,
        session_id: str,
        request_hash: str,
        response_payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into learner.mock_exam_commands (
                  user_id, idempotency_key, command_type, session_id,
                  request_hash, response_payload, created_at
                ) values (
                  cast(:user_id as uuid), :idempotency_key, :command_type,
                  cast(:session_id as uuid), :request_hash,
                  cast(:response_payload as jsonb), :created_at
                )
                """
            ),
            {
                "user_id": user_id,
                "idempotency_key": idempotency_key,
                "command_type": command_type,
                "session_id": session_id,
                "request_hash": request_hash,
                "response_payload": json.dumps(response_payload, ensure_ascii=False),
                "created_at": created_at,
            },
        )

    def question_dispute_by_idempotency_key(
        self, *, user_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        row = self._session.execute(
            text(
                """
                select * from learning.xingce_question_disputes
                where user_id = cast(:user_id as uuid)
                  and idempotency_key = :idempotency_key
                """
            ),
            {"user_id": user_id, "idempotency_key": idempotency_key},
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def insert_question_dispute(self, *, values: dict[str, Any]) -> dict[str, Any]:
        row = self._session.execute(
            text(
                """
                insert into learning.xingce_question_disputes (
                  dispute_id, user_id, session_kind, session_id, question_id,
                  content_version_id, category, description, evidence_context,
                  idempotency_key, created_at
                ) values (
                  cast(:dispute_id as uuid), cast(:user_id as uuid),
                  'mock_exam', cast(:session_id as uuid), :question_id,
                  :content_version_id, :category, :description,
                  cast(:evidence_context as jsonb), :idempotency_key, :created_at
                ) returning *
                """
            ),
            values,
        ).mappings().one()
        return dict(row)

    def commit(self) -> None:
        self._session.commit()
