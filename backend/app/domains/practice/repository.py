from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.knowledge.repository import KnowledgeRepository


@dataclass(frozen=True)
class FrozenPracticeQuestion:
    release_id: str
    release_version: str
    node_id: str
    tag_value: str
    question_id: str
    content_version_id: str
    visual_required: bool
    shared_context_required: bool
    occurrence_count: int
    latest_year: int | None
    snapshot: dict[str, Any]


class PracticeRepository:
    """Reads learner-facing questions from immutable release bindings."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._knowledge = KnowledgeRepository(session)

    def frozen_questions(
        self,
        *,
        release_id: str,
        node_id: str,
        question_ids: list[str],
    ) -> list[FrozenPracticeQuestion]:
        if not question_ids or len(question_ids) != len(set(question_ids)):
            return []
        rows = self._session.execute(
            text(
                """
                select
                  release.release_id::text as release_id,
                  release.release_version,
                  node.node_id,
                  node.tag_value,
                  binding.question_id,
                  binding.content_version_id,
                  binding.visual_required,
                  binding.shared_context_required,
                  binding.occurrence_count,
                  binding.latest_year,
                  content.snapshot_payload
                from product.knowledge_map_releases release
                join product.knowledge_map_nodes node
                  on node.release_id = release.release_id
                 and node.node_id = :node_id
                join product.knowledge_map_question_bindings binding
                  on binding.release_id = release.release_id
                 and binding.node_id = node.node_id
                 and binding.question_id = any(cast(:question_ids as text[]))
                 and binding.delivery_eligible
                 and not binding.visual_required
                join product.question_content_versions content
                  on content.question_id = binding.question_id
                 and content.content_version_id = binding.content_version_id
                 and content.track = 'xingce'
                where release.release_id = cast(:release_id as uuid)
                  and release.status in ('active', 'retired')
                order by array_position(
                  cast(:question_ids as text[]),
                  binding.question_id
                )
                """
            ),
            {
                "release_id": release_id,
                "node_id": node_id,
                "question_ids": question_ids,
            },
        ).mappings().all()
        if [str(row["question_id"]) for row in rows] != question_ids:
            return []
        return [
            FrozenPracticeQuestion(
                release_id=str(row["release_id"]),
                release_version=str(row["release_version"]),
                node_id=str(row["node_id"]),
                tag_value=str(row["tag_value"]),
                question_id=str(row["question_id"]),
                content_version_id=str(row["content_version_id"]),
                visual_required=bool(row["visual_required"]),
                shared_context_required=bool(row["shared_context_required"]),
                occurrence_count=int(row["occurrence_count"]),
                latest_year=(
                    int(row["latest_year"])
                    if row["latest_year"] is not None
                    else None
                ),
                snapshot=dict(row["snapshot_payload"]),
            )
            for row in rows
        ]

    def weighted_frozen_questions(
        self,
        *,
        release_id: str,
        node_id: str,
        limit: int,
    ) -> list[FrozenPracticeQuestion]:
        rows = self._knowledge.select_weighted_practice_questions(
            release_id=release_id,
            node_id=node_id,
            limit=limit,
        )
        return self.frozen_questions(
            release_id=release_id,
            node_id=node_id,
            question_ids=[str(row["question_id"]) for row in rows],
        )
