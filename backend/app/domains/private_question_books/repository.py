from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.task_content.service import (
    TaskContentContractError,
    validate_learner_question,
)
from app.domains.xingce_learning.content_version import (
    question_content_version_id,
)


@dataclass(frozen=True, slots=True)
class PracticeContentResolution:
    content_version_id: str
    question_id: str | None
    delivery_release_id: str | None
    question: dict[str, Any] | None
    exclusion_reason: str | None


class PrivateQuestionBookRepository:
    """Read-only authority adapter plus one transaction-scoped dedupe lock."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def creation_context(
        self,
        *,
        owner_user_id: str,
        import_id: UUID,
        recognition_revision_id: UUID,
    ):
        return (
            self.session.execute(
                text(
                    """
                    select upload.session_id as import_id,
                           artifact.artifact_version_id,
                           recognition.recognition_revision_id,
                           recognition.candidate_payload,
                           recognition.created_at as recognition_created_at
                    from learning.upload_sessions upload
                    join learning.source_artifact_versions artifact
                      on artifact.artifact_version_id = upload.resolved_artifact_version_id
                     and artifact.owner_user_id = upload.owner_user_id
                    join learning.recognition_revisions recognition
                      on recognition.artifact_version_id = artifact.artifact_version_id
                     and recognition.owner_user_id = artifact.owner_user_id
                    where upload.session_id = cast(:import_id as uuid)
                      and upload.owner_user_id = cast(:owner as uuid)
                      and upload.state in ('available', 'deduplicated')
                      and artifact.state = 'available'
                      and recognition.recognition_revision_id = cast(:recognition as uuid)
                      and recognition.recognition_kind = 'exam_segmentation'
                      and recognition.state = 'confirmed'
                      and not exists (
                        select 1 from learning.recognition_revisions successor
                        where successor.supersedes_recognition_revision_id =
                              recognition.recognition_revision_id
                      )
                    """
                ),
                {
                    "owner": owner_user_id,
                    "import_id": str(import_id),
                    "recognition": str(recognition_revision_id),
                },
            )
            .mappings()
            .one_or_none()
        )

    def lock_artifact(self, *, owner_user_id: str, artifact_version_id: UUID) -> None:
        self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {
                "lock_key": (
                    f"private-question-book:{owner_user_id}:{artifact_version_id}"
                )
            },
        )

    def source_for_artifact(
        self, *, owner_user_id: str, artifact_version_id: UUID
    ):
        return (
            self.session.execute(
                text(
                    """
                    select source.*
                    from learning.private_knowledge_source_versions source
                    where source.owner_user_id = cast(:owner as uuid)
                      and source.artifact_version_id = cast(:artifact as uuid)
                    order by source.version_no desc
                    limit 1
                    """
                ),
                {"owner": owner_user_id, "artifact": str(artifact_version_id)},
            )
            .mappings()
            .one_or_none()
        )

    def current_heads(self, *, owner_user_id: str) -> tuple[Any, ...]:
        rows = self.session.execute(
            text(
                """
                select source.*, recognition.candidate_payload,
                       upload.session_id as import_id
                from learning.private_knowledge_source_versions source
                join learning.recognition_revisions recognition
                  on recognition.recognition_revision_id = source.recognition_revision_id
                 and recognition.owner_user_id = source.owner_user_id
                 and recognition.artifact_version_id = source.artifact_version_id
                join lateral (
                  select session.session_id
                  from learning.upload_sessions session
                  where session.owner_user_id = source.owner_user_id
                    and session.resolved_artifact_version_id = source.artifact_version_id
                    and session.state in ('available', 'deduplicated')
                  order by session.updated_at desc, session.session_id desc
                  limit 1
                ) upload on true
                where source.owner_user_id = cast(:owner as uuid)
                  and source.document_kind = 'question_book'
                  and not exists (
                    select 1 from learning.private_knowledge_source_versions successor
                    where successor.supersedes_source_version_id = source.source_version_id
                  )
                  and not exists (
                    select 1 from privacy.private_material_deletion_requests request
                    where request.owner_user_id = source.owner_user_id
                      and request.source_id = source.source_id
                      and request.state in ('queued', 'running')
                  )
                order by source.created_at desc, source.source_id
                """
            ),
            {"owner": owner_user_id},
        ).mappings()
        return tuple(rows)

    def current_head(self, *, owner_user_id: str, book_id: UUID):
        return (
            self.session.execute(
                text(
                    """
                    select source.*, recognition.candidate_payload,
                           upload.session_id as import_id
                    from learning.private_knowledge_source_versions source
                    join learning.recognition_revisions recognition
                      on recognition.recognition_revision_id = source.recognition_revision_id
                     and recognition.owner_user_id = source.owner_user_id
                     and recognition.artifact_version_id = source.artifact_version_id
                    join lateral (
                      select session.session_id
                      from learning.upload_sessions session
                      where session.owner_user_id = source.owner_user_id
                        and session.resolved_artifact_version_id = source.artifact_version_id
                        and session.state in ('available', 'deduplicated')
                      order by session.updated_at desc, session.session_id desc
                      limit 1
                    ) upload on true
                    where source.owner_user_id = cast(:owner as uuid)
                      and source.source_id = cast(:book_id as uuid)
                      and source.document_kind = 'question_book'
                      and not exists (
                        select 1 from learning.private_knowledge_source_versions successor
                        where successor.supersedes_source_version_id = source.source_version_id
                      )
                      and not exists (
                        select 1 from privacy.private_material_deletion_requests request
                        where request.owner_user_id = source.owner_user_id
                          and request.source_id = source.source_id
                          and request.state in ('queued', 'running')
                      )
                    """
                ),
                {"owner": owner_user_id, "book_id": str(book_id)},
            )
            .mappings()
            .one_or_none()
        )

    def match_heads(
        self,
        *,
        owner_user_id: str,
        artifact_version_id: UUID,
        recognition_revision_id: UUID,
    ) -> dict[str, Any]:
        rows = self.session.execute(
            text(
                """
                select distinct on (match.question_unit_id)
                       match.question_unit_id,
                       match.content_match_version_id,
                       match.state,
                       match.confirmed_content_version_id,
                       jsonb_array_length(match.candidates) as candidate_count
                from learning.content_match_versions match
                where match.owner_user_id = cast(:owner as uuid)
                  and match.artifact_version_id = cast(:artifact as uuid)
                  and match.recognition_revision_id = cast(:recognition as uuid)
                order by match.question_unit_id, match.version_no desc,
                         match.created_at desc, match.content_match_version_id desc
                """
            ),
            {
                "owner": owner_user_id,
                "artifact": str(artifact_version_id),
                "recognition": str(recognition_revision_id),
            },
        ).mappings()
        return {str(row["question_unit_id"]): row for row in rows}

    def question_book_head_kind(
        self, *, owner_user_id: str, source_id: UUID
    ) -> str | None:
        value = self.session.execute(
            text(
                """
                select source.document_kind
                from learning.private_knowledge_source_versions source
                where source.owner_user_id = cast(:owner as uuid)
                  and source.source_id = cast(:source_id as uuid)
                order by source.version_no desc
                limit 1
                """
            ),
            {"owner": owner_user_id, "source_id": str(source_id)},
        ).scalar_one_or_none()
        return str(value) if value is not None else None

    def resolve_practice_content(
        self,
        *,
        content_version_ids: tuple[str, ...],
        now: datetime,
    ) -> tuple[PracticeContentResolution, ...]:
        """Resolve exact confirmed versions against current delivery authority.

        The caller owns book/match authorization.  This adapter deliberately
        does not search for replacements: a changed content head must return to
        matching instead of silently turning one teacher-book item into another.
        """

        if not content_version_ids:
            return ()
        rows = self.session.execute(
            text(
                """
                with requested as (
                  select item.content_version_id, item.ordinality::integer
                  from unnest(cast(:content_version_ids as text[]))
                       with ordinality as item(content_version_id, ordinality)
                )
                select
                  requested.content_version_id,
                  content.question_id,
                  content.track,
                  content.snapshot_payload,
                  (head.content_version_id = requested.content_version_id)
                    as is_current_head,
                  question.status as question_status,
                  delivery.release_id as delivery_release_id
                from requested
                left join product.question_content_versions content
                  on content.content_version_id = requested.content_version_id
                left join product.question_content_heads head
                  on head.question_id = content.question_id
                left join product.questions question
                  on question.question_id = content.question_id
                left join lateral (
                  select binding.release_id
                  from product.knowledge_map_question_bindings binding
                  join product.knowledge_map_releases release
                    on release.release_id = binding.release_id
                  where binding.question_id = content.question_id
                    and binding.content_version_id = content.content_version_id
                    and binding.delivery_eligible
                    and release.track = 'xingce'
                    and release.status = 'active'
                    and release.fresh_until > :now
                  order by release.activated_at desc, binding.release_id
                  limit 1
                ) delivery on true
                order by requested.ordinality
                """
            ),
            {"content_version_ids": list(content_version_ids), "now": now},
        ).mappings()
        resolved: list[PracticeContentResolution] = []
        for row in rows:
            content_version_id = str(row["content_version_id"])
            question_id = (
                str(row["question_id"]) if row["question_id"] is not None else None
            )
            reason: str | None = None
            question: dict[str, Any] | None = None
            release_id = (
                str(row["delivery_release_id"])
                if row["delivery_release_id"] is not None
                else None
            )
            if question_id is None or row["snapshot_payload"] is None:
                reason = "not_delivery_eligible"
            elif not bool(row["is_current_head"]):
                reason = "content_head_changed"
            elif str(row["track"]) != "xingce" or str(row["question_status"]) not in {
                "active",
                "canonical",
            }:
                reason = "not_delivery_eligible"
            elif release_id is None:
                reason = "not_delivery_eligible"
            else:
                candidate = dict(row["snapshot_payload"])
                candidate["question_id"] = question_id
                candidate["_content_version_id"] = content_version_id
                if question_content_version_id(candidate) != content_version_id:
                    reason = "content_contract_invalid"
                else:
                    try:
                        validate_learner_question(candidate)
                    except TaskContentContractError:
                        reason = "content_contract_invalid"
                    else:
                        question = candidate
            resolved.append(
                PracticeContentResolution(
                    content_version_id=content_version_id,
                    question_id=question_id,
                    delivery_release_id=release_id,
                    question=question,
                    exclusion_reason=reason,
                )
            )
        return tuple(resolved)
