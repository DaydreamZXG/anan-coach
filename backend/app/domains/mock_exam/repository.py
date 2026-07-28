from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
import json

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.mock_exam.migration_compat import (
    assert_revision_33_migration_context,
    get_revision_33_frozen_question_sources,
)


class MockExamTrajectoryWriteConflict(RuntimeError):
    def __init__(self, *, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.constraint_name = constraint_name


@dataclass(frozen=True, slots=True)
class KnowledgeMapReleaseSnapshot:
    release_id: str
    release_version: str
    source_data_version: str
    source_fingerprint: str
    vocab_version: str
    generator_version: str
    generated_at: datetime
    activated_at: datetime
    fresh_until: datetime
    resolved_at: datetime

    def lineage_payload(self) -> dict[str, Any]:
        return {
            "releaseId": self.release_id,
            "releaseVersion": self.release_version,
            "examScope": "guokao",
            "track": "xingce",
            "source": {
                "dataVersion": self.source_data_version,
                "fingerprint": self.source_fingerprint,
                "vocabVersion": self.vocab_version,
                "generatorVersion": self.generator_version,
            },
            "freshness": {
                "generatedAt": self.generated_at.isoformat(),
                "activatedAt": self.activated_at.isoformat(),
                "freshUntil": self.fresh_until.isoformat(),
                "resolvedAt": self.resolved_at.isoformat(),
            },
        }


@dataclass(frozen=True)
class MockExamSessionRecord:
    session_id: str
    user_id: str
    source_plan_task_id: str | None
    status: str
    question_ids: list[str]
    blueprint: list[dict[str, Any]]
    revision: int
    started_at: datetime
    deadline_at: datetime
    checkpoint: dict[str, Any]
    learner_bundle: dict[str, Any]
    scoring_snapshot: dict[str, Any]
    paper_version_id: str
    paper_equivalence_metadata: dict[str, Any]
    scoring_policy_version_id: str
    score_metric_name: str
    score_metric_unit: str
    score_metric_definition: str
    score_module_weights: dict[str, float]
    next_trajectory_event_seq: int
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

    @property
    def session(self) -> Session:
        return self._session

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

    def resolve_fresh_active_knowledge_release(
        self,
    ) -> KnowledgeMapReleaseSnapshot | None:
        rows = (
            self._session.execute(
                text(
                    """
                select
                  release_id,
                  release_version,
                  source_data_version,
                  source_fingerprint,
                  vocab_version,
                  generator_version,
                  generated_at,
                  activated_at,
                  fresh_until,
                  (
                    release.question_binding_count > 0
                    and manifest.binding_count =
                        release.question_binding_count
                    and manifest.binding_fingerprint =
                        release.question_binding_fingerprint
                  ) as binding_ready,
                  statement_timestamp() as resolved_at
                from product.knowledge_map_releases release
                cross join lateral
                  product.knowledge_map_question_binding_manifest(
                    release.release_id
                  ) manifest
                where exam_scope = 'guokao'
                  and track = 'xingce'
                  and status = 'active'
                """
                )
            )
            .mappings()
            .all()
        )
        if (
            len(rows) != 1
            or rows[0]["fresh_until"] <= rows[0]["resolved_at"]
            or not rows[0]["binding_ready"]
        ):
            return None
        row = rows[0]
        return KnowledgeMapReleaseSnapshot(
            release_id=str(row["release_id"]),
            release_version=str(row["release_version"]),
            source_data_version=str(row["source_data_version"]),
            source_fingerprint=str(row["source_fingerprint"]),
            vocab_version=str(row["vocab_version"]),
            generator_version=str(row["generator_version"]),
            generated_at=row["generated_at"],
            activated_at=row["activated_at"],
            fresh_until=row["fresh_until"],
            resolved_at=row["resolved_at"],
        )

    def knowledge_release_matches(
        self,
        *,
        release: KnowledgeMapReleaseSnapshot,
    ) -> bool:
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1
                      from product.knowledge_map_releases
                      where release_id = cast(:release_id as uuid)
                        and release_version = :release_version
                        and exam_scope = 'guokao'
                        and track = 'xingce'
                        and source_data_version = :source_data_version
                        and source_fingerprint = :source_fingerprint
                        and vocab_version = :vocab_version
                        and generator_version = :generator_version
                        and generated_at = :generated_at
                        and activated_at = :activated_at
                        and fresh_until = :fresh_until
                        and question_binding_count > 0
                        and (
                          select manifest.binding_count
                          from product.knowledge_map_question_binding_manifest(
                            knowledge_map_releases.release_id
                          ) manifest
                        ) = question_binding_count
                        and (
                          select manifest.binding_fingerprint
                          from product.knowledge_map_question_binding_manifest(
                            knowledge_map_releases.release_id
                          ) manifest
                        ) = question_binding_fingerprint
                    )
                    """
                ),
                _knowledge_release_params(release),
            ).scalar_one()
        )

    def select_module_question_ids(
        self,
        *,
        user_id: str,
        module_name: str,
        module_code: str,
        limit: int,
        knowledge_release: KnowledgeMapReleaseSnapshot,
    ) -> list[str]:
        # ``question_type`` is a knowledge-taxonomy leaf in the imported bank
        # (for example ``新思想``), not the response format. Objective domain,
        # answer-label and option integrity are the authoritative single-choice
        # eligibility contract below.
        since = knowledge_release.resolved_at - timedelta(days=60)
        rows = (
            self._session.execute(
                text(
                    """
                select binding.question_id
                from product.knowledge_map_releases release
                join product.knowledge_map_question_bindings binding
                  on binding.release_id = release.release_id
                 and binding.module_code = :module_code
                 and binding.canonical_module = :module_name
                 and binding.delivery_eligible
                join product.knowledge_map_nodes kmn
                  on kmn.release_id = binding.release_id
                 and kmn.node_id = binding.node_id
                 and kmn.status = 'published'
                 and kmn.support_status = 'text_supported'
                 and kmn.near5_occurrence_count > 0
                join product.question_content_versions content
                  on content.question_id = binding.question_id
                 and content.content_version_id = binding.content_version_id
                where release.release_id = cast(:knowledge_release_id as uuid)
                 and release.release_version = :knowledge_release_version
                 and release.exam_scope = 'guokao'
                 and release.track = 'xingce'
                 and release.source_data_version = :knowledge_source_data_version
                 and release.source_fingerprint = :knowledge_source_fingerprint
                 and release.vocab_version = :knowledge_vocab_version
                 and release.generator_version = :knowledge_generator_version
                 and release.generated_at = :knowledge_generated_at
                 and release.activated_at = :knowledge_activated_at
                 and release.fresh_until = :knowledge_fresh_until
                  and not exists (
                    select 1
                    from learner.practice_answers pa
                    where pa.user_id = cast(:user_id as uuid)
                      and pa.question_id = binding.question_id
                      and pa.answered_at >= :since
                  )
                  and not exists (
                    select 1
                    from learner.mock_exam_sessions mes
                    cross join jsonb_array_elements_text(mes.question_ids) assigned(question_id)
                    where mes.user_id = cast(:user_id as uuid)
                      and mes.created_at >= :since
                      and assigned.question_id = binding.question_id
                  )
                group by
                  binding.question_id,
                  binding.latest_year,
                  binding.occurrence_count
                order by
                  max(kmn.near5_weight) desc nulls last,
                  max(kmn.near5_occurrence_count) desc nulls last,
                  binding.latest_year desc nulls last,
                  binding.occurrence_count desc,
                  binding.question_id
                limit :limit
                """
                ),
                {
                    "user_id": user_id,
                    "module_name": module_name,
                    "module_code": module_code,
                    "since": since,
                    "limit": limit,
                    **_knowledge_release_params(
                        knowledge_release,
                        prefix="knowledge_",
                    ),
                },
            )
            .mappings()
            .all()
        )
        return [str(row["question_id"]) for row in rows]

    def get_assigned_session(self, *, user_id: str) -> MockExamSessionRecord | None:
        row = (
            self._session.execute(
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
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self.get_session(session_id=str(row["session_id"]), user_id=user_id)

    def get_frozen_question_sources(
        self,
        *,
        question_ids: list[str],
        knowledge_release: KnowledgeMapReleaseSnapshot | None = None,
    ) -> list[dict[str, Any]]:
        if knowledge_release is None:
            # Published revision 20260716_33 imports this repository and calls
            # the historical one-argument interface. Production call sites
            # must continue to pass an explicit frozen knowledge release.
            assert_revision_33_migration_context(session=self._session)
            return get_revision_33_frozen_question_sources(
                session=self._session,
                question_ids=question_ids,
            )
        rows = (
            self._session.execute(
                text(
                    """
                with assigned as (
                  select value as question_id, ordinality
                  from jsonb_array_elements_text(cast(:question_ids as jsonb))
                    with ordinality
                ), frozen_binding as (
                  select
                    assigned.question_id,
                    assigned.ordinality,
                    min(binding.content_version_id) as content_version_id,
                    min(binding.canonical_module) as canonical_module,
                    bool_or(binding.visual_required) as visual_required,
                    bool_or(
                      binding.shared_context_required
                    ) as shared_context_required
                  from assigned
                  join product.knowledge_map_releases release
                    on release.release_id =
                       cast(:knowledge_release_id as uuid)
                   and release.release_version =
                       :knowledge_release_version
                   and release.exam_scope = 'guokao'
                   and release.track = 'xingce'
                   and release.source_data_version =
                       :knowledge_source_data_version
                   and release.source_fingerprint =
                       :knowledge_source_fingerprint
                   and release.vocab_version = :knowledge_vocab_version
                   and release.generator_version =
                       :knowledge_generator_version
                   and release.generated_at = :knowledge_generated_at
                   and release.activated_at = :knowledge_activated_at
                   and release.fresh_until = :knowledge_fresh_until
                  join product.knowledge_map_question_bindings binding
                    on binding.release_id = release.release_id
                   and binding.question_id = assigned.question_id
                   and binding.delivery_eligible
                  group by assigned.question_id, assigned.ordinality
                  having count(distinct binding.content_version_id) = 1
                     and count(distinct binding.canonical_module) = 1
                     and count(distinct binding.visual_required) = 1
                     and count(
                       distinct binding.shared_context_required
                     ) = 1
                )
                select
                  frozen.question_id,
                  frozen.content_version_id,
                  frozen.canonical_module,
                  frozen.visual_required,
                  frozen.shared_context_required,
                  content.snapshot_payload,
                  frozen.ordinality
                from frozen_binding frozen
                join product.question_content_versions content
                  on content.question_id = frozen.question_id
                 and content.content_version_id =
                     frozen.content_version_id
                order by frozen.ordinality
                """
                ),
                {
                    "question_ids": json.dumps(question_ids, ensure_ascii=False),
                    **_knowledge_release_params(
                        knowledge_release,
                        prefix="knowledge_",
                    ),
                },
            )
            .mappings()
            .all()
        )
        sources: list[dict[str, Any]] = []
        for row in rows:
            source = dict(row["snapshot_payload"])
            source["question_id"] = str(row["question_id"])
            source["module_name"] = str(row["canonical_module"])
            source["visual_required"] = bool(row["visual_required"])
            source["shared_context_required"] = bool(row["shared_context_required"])
            source["_content_version_id"] = str(row["content_version_id"])
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
        paper_version_id: str,
        scoring_policy_version_id: str,
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
                  paper_version_id,
                  scoring_policy_version_id,
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
                  cast(:paper_version_id as uuid),
                  :scoring_policy_version_id,
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
                "paper_version_id": paper_version_id,
                "scoring_policy_version_id": scoring_policy_version_id,
                "started_at": started_at,
                "deadline_at": deadline_at,
            },
        )
        return session_id

    def bind_source_plan_task(
        self,
        *,
        session_id: str,
        user_id: str,
        plan_task_id: str,
        updated_at: datetime,
    ) -> MockExamSessionRecord | None:
        row = (
            self._session.execute(
                text(
                    """
                update learner.mock_exam_sessions
                set source_plan_task_id = cast(:plan_task_id as uuid),
                    updated_at = :updated_at
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and status = 'assigned'
                  and (
                    source_plan_task_id is null
                    or source_plan_task_id = cast(:plan_task_id as uuid)
                  )
                returning session_id
                """
                ),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "plan_task_id": plan_task_id,
                    "updated_at": updated_at,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self.get_session(session_id=session_id, user_id=user_id)

    def get_session(
        self,
        *,
        session_id: str,
        user_id: str,
    ) -> MockExamSessionRecord | None:
        row = (
            self._session.execute(
                text(
                    """
                select
                  session_id,
                  user_id,
                  source_plan_task_id,
                  status,
                  question_ids,
                  blueprint,
                  revision,
                  started_at,
                  deadline_at,
                  checkpoint,
                  learner_bundle,
                  scoring_snapshot,
                  paper_version_id,
                  paper_equivalence_metadata,
                  scoring_policy_version_id,
                  score_metric_name,
                  score_metric_unit,
                  score_metric_definition,
                  score_module_weights,
                  next_trajectory_event_seq,
                  checkpoint_updated_at,
                  created_at,
                  submitted_at,
                  review_payload,
                  review_viewed_at
                from (
                  select session.*,
                         paper.equivalence_metadata
                           as paper_equivalence_metadata,
                         policy.metric_name as score_metric_name,
                         policy.metric_unit as score_metric_unit,
                         policy.definition as score_metric_definition,
                         policy.module_weights as score_module_weights,
                         (
                           select coalesce(max(event.event_seq), 0) + 1
                           from learner.mock_exam_trajectory_events event
                           where event.session_id = session.session_id
                         ) as next_trajectory_event_seq
                  from learner.mock_exam_sessions session
                  join product.mock_exam_paper_versions paper
                    on paper.paper_version_id = session.paper_version_id
                  join product.mock_exam_scoring_policy_versions policy
                    on policy.scoring_policy_version_id =
                       session.scoring_policy_version_id
                ) mock_exam_sessions
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
                ),
                {"session_id": session_id, "user_id": user_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return MockExamSessionRecord(
            session_id=str(row["session_id"]),
            user_id=str(row["user_id"]),
            source_plan_task_id=(
                str(row["source_plan_task_id"]) if row["source_plan_task_id"] is not None else None
            ),
            status=str(row["status"]),
            question_ids=list(row["question_ids"] or []),
            blueprint=list(row["blueprint"] or []),
            revision=int(row["revision"]),
            started_at=row["started_at"],
            deadline_at=row["deadline_at"],
            checkpoint=dict(row["checkpoint"]),
            learner_bundle=dict(row["learner_bundle"]),
            scoring_snapshot=dict(row["scoring_snapshot"]),
            paper_version_id=str(row["paper_version_id"]),
            paper_equivalence_metadata=dict(row["paper_equivalence_metadata"]),
            scoring_policy_version_id=str(row["scoring_policy_version_id"]),
            score_metric_name=str(row["score_metric_name"]),
            score_metric_unit=str(row["score_metric_unit"]),
            score_metric_definition=str(row["score_metric_definition"]),
            score_module_weights={
                str(key): float(value) for key, value in dict(row["score_module_weights"]).items()
            },
            next_trajectory_event_seq=int(row["next_trajectory_event_seq"]),
            checkpoint_updated_at=row["checkpoint_updated_at"],
            created_at=row["created_at"],
            submitted_at=row["submitted_at"],
            review_payload=dict(row["review_payload"]) if row["review_payload"] else None,
            review_viewed_at=row["review_viewed_at"],
        )

    def get_or_create_paper_version(
        self,
        *,
        paper_key: str,
        title: str,
        duration_minutes: int,
        question_ids: list[str],
        blueprint: list[dict[str, object]],
        learner_bundle_version_id: str,
        scoring_policy_version_id: str,
        exposure_policy: dict[str, Any],
        equivalence_metadata: dict[str, Any],
        content_fingerprint: str,
        published_at: datetime,
    ) -> str:
        paper_version_id = str(uuid4())
        row = self._session.execute(
            text(
                """
                insert into product.mock_exam_paper_versions (
                  paper_version_id, paper_key, version_no, state, exam_scope,
                  track, title, duration_minutes, question_ids, blueprint,
                  learner_bundle_version_id, scoring_policy_version_id,
                  exposure_policy, equivalence_metadata, content_fingerprint,
                  published_at, created_at
                ) values (
                  cast(:paper_version_id as uuid), :paper_key, 1, 'published',
                  'guokao', 'xingce', :title, :duration_minutes,
                  cast(:question_ids as jsonb), cast(:blueprint as jsonb),
                  :learner_bundle_version_id, :scoring_policy_version_id,
                  cast(:exposure_policy as jsonb),
                  cast(:equivalence_metadata as jsonb), :content_fingerprint,
                  :published_at, :published_at
                ) on conflict (content_fingerprint, scoring_policy_version_id)
                  do nothing
                returning paper_version_id::text
                """
            ),
            {
                "paper_version_id": paper_version_id,
                "paper_key": paper_key,
                "title": title,
                "duration_minutes": duration_minutes,
                "question_ids": json.dumps(question_ids, ensure_ascii=False),
                "blueprint": json.dumps(blueprint, ensure_ascii=False),
                "learner_bundle_version_id": learner_bundle_version_id,
                "scoring_policy_version_id": scoring_policy_version_id,
                "exposure_policy": json.dumps(exposure_policy, ensure_ascii=False),
                "equivalence_metadata": json.dumps(equivalence_metadata, ensure_ascii=False),
                "content_fingerprint": content_fingerprint,
                "published_at": published_at,
            },
        ).scalar_one_or_none()
        if row is not None:
            return str(row)
        existing = self._session.execute(
            text(
                """
                select paper_version_id::text
                from product.mock_exam_paper_versions
                where content_fingerprint = :content_fingerprint
                  and scoring_policy_version_id = :scoring_policy_version_id
                """
            ),
            {
                "content_fingerprint": content_fingerprint,
                "scoring_policy_version_id": scoring_policy_version_id,
            },
        ).scalar_one_or_none()
        if existing is None:
            raise RuntimeError("mock paper identity conflicted without an equivalent version")
        return str(existing)

    def record_trajectory_event(
        self,
        *,
        trajectory_event_id: str,
        command_id: str,
        user_id: str,
        session_id: str,
        event_seq: int,
        event_type: str,
        question_id: str | None,
        client_occurred_at: datetime,
        session_revision: int,
        payload: dict[str, Any],
        request_fingerprint: str,
        created_at: datetime,
    ) -> None:
        try:
            with self._session.begin_nested():
                self._session.execute(
                    text(
                        """
                        insert into learner.mock_exam_trajectory_events (
                          trajectory_event_id, command_id, user_id, session_id,
                          event_seq, event_type, question_id, client_occurred_at,
                          session_revision, payload, request_fingerprint, created_at
                        ) values (
                          cast(:trajectory_event_id as uuid), :command_id,
                          cast(:user_id as uuid), cast(:session_id as uuid),
                          :event_seq, :event_type, :question_id, :client_occurred_at,
                          :session_revision, cast(:payload as jsonb),
                          :request_fingerprint, :created_at
                        )
                        """
                    ),
                    {
                        "trajectory_event_id": trajectory_event_id,
                        "command_id": command_id,
                        "user_id": user_id,
                        "session_id": session_id,
                        "event_seq": event_seq,
                        "event_type": event_type,
                        "question_id": question_id,
                        "client_occurred_at": client_occurred_at,
                        "session_revision": session_revision,
                        "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        "request_fingerprint": request_fingerprint,
                        "created_at": created_at,
                    },
                )
        except IntegrityError as exc:
            constraint_name = _integrity_constraint_name(exc)
            if constraint_name in {
                "mock_exam_trajectory_user_command_key",
                "mock_exam_trajectory_session_seq_key",
            }:
                raise MockExamTrajectoryWriteConflict(constraint_name=constraint_name) from exc
            raise

    def trajectory_event_by_command(
        self, *, user_id: str, command_id: str
    ) -> dict[str, Any] | None:
        row = (
            self._session.execute(
                text(
                    """
                select * from learner.mock_exam_trajectory_events
                where user_id = cast(:user_id as uuid) and command_id = :command_id
                """
                ),
                {"user_id": user_id, "command_id": command_id},
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    def next_trajectory_event_seq(self, *, session_id: str) -> int:
        value = self._session.execute(
            text(
                """
                select coalesce(max(event_seq), 0) + 1
                from learner.mock_exam_trajectory_events
                where session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": session_id},
        ).scalar_one()
        return int(value)

    def get_previous_module_accuracy(
        self,
        *,
        user_id: str,
        before_session_id: str,
    ) -> dict[str, float]:
        row = (
            self._session.execute(
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
            )
            .mappings()
            .first()
        )
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
        knowledge_release: KnowledgeMapReleaseSnapshot,
    ) -> list[dict[str, Any]]:
        if not wrong_question_ids:
            return []
        if not self.knowledge_release_matches(release=knowledge_release):
            raise LookupError("frozen mock knowledge release identity changed")
        rows = (
            self._session.execute(
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
                  count(distinct binding.question_id) as wrong_count,
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
                join product.knowledge_map_releases release
                  on release.release_id = cast(:knowledge_release_id as uuid)
                 and release.release_version = :knowledge_release_version
                 and release.exam_scope = 'guokao'
                 and release.track = 'xingce'
                 and release.source_data_version = :knowledge_source_data_version
                 and release.source_fingerprint = :knowledge_source_fingerprint
                 and release.vocab_version = :knowledge_vocab_version
                 and release.generator_version = :knowledge_generator_version
                 and release.generated_at = :knowledge_generated_at
                 and release.activated_at = :knowledge_activated_at
                 and release.fresh_until = :knowledge_fresh_until
                join product.knowledge_map_question_bindings binding
                  on binding.release_id = release.release_id
                 and binding.question_id = wi.question_id
                join product.knowledge_map_nodes kmn
                  on kmn.release_id = binding.release_id
                 and kmn.node_id = binding.node_id
                 and kmn.exam_scope = 'guokao'
                 and kmn.track = 'xingce'
                 and kmn.status = 'published'
                 and kmn.support_status = 'text_supported'
                group by kmn.node_id, kmn.display_path, kmn.method_card_ids, kmn.near5_weight
                order by wrong_count desc, kmn.near5_weight desc nulls last, kmn.node_id
                limit 8
                """
                ),
                {
                    "wrong_question_ids": json.dumps(wrong_question_ids, ensure_ascii=False),
                    **_knowledge_release_params(
                        knowledge_release,
                        prefix="knowledge_",
                    ),
                },
            )
            .mappings()
            .all()
        )
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
        row = (
            self._session.execute(
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
            )
            .mappings()
            .one_or_none()
        )
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
        row = (
            self._session.execute(
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
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self.get_session(session_id=session_id, user_id=user_id)

    def get_command(self, *, user_id: str, idempotency_key: str) -> dict[str, Any] | None:
        row = (
            self._session.execute(
                text(
                    """
                select command_type, session_id, request_hash, response_payload
                from learner.mock_exam_commands
                where user_id = cast(:user_id as uuid)
                  and idempotency_key = :idempotency_key
                """
                ),
                {"user_id": user_id, "idempotency_key": idempotency_key},
            )
            .mappings()
            .one_or_none()
        )
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
        row = (
            self._session.execute(
                text(
                    """
                select * from learning.xingce_question_disputes
                where user_id = cast(:user_id as uuid)
                  and idempotency_key = :idempotency_key
                """
                ),
                {"user_id": user_id, "idempotency_key": idempotency_key},
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    def insert_question_dispute(self, *, values: dict[str, Any]) -> dict[str, Any]:
        row = (
            self._session.execute(
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
            )
            .mappings()
            .one()
        )
        return dict(row)

    def commit(self) -> None:
        self._session.commit()


def _knowledge_release_params(
    release: KnowledgeMapReleaseSnapshot,
    *,
    prefix: str = "",
) -> dict[str, Any]:
    return {
        f"{prefix}release_id": release.release_id,
        f"{prefix}release_version": release.release_version,
        f"{prefix}source_data_version": release.source_data_version,
        f"{prefix}source_fingerprint": release.source_fingerprint,
        f"{prefix}vocab_version": release.vocab_version,
        f"{prefix}generator_version": release.generator_version,
        f"{prefix}generated_at": release.generated_at,
        f"{prefix}activated_at": release.activated_at,
        f"{prefix}fresh_until": release.fresh_until,
    }


def _integrity_constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    return str(constraint_name) if constraint_name else None
