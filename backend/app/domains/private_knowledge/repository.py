from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from math import sqrt
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session
import yaml

from app.domains.private_knowledge.schemas import (
    MarkdownExport,
    PrivateCitation,
    PrivateSearchResult,
    PrivateSourceDeletionView,
    PrivateSourceList,
    PrivateSourceSummary,
)
from app.platform.jobs import BackgroundJobRepository


SEARCH_POLICY_VERSION = "private-knowledge-search@v1"
DELETE_JOB_TYPE = "private_knowledge_source_delete@v1"


class PrivateKnowledgeConflict(RuntimeError):
    pass


class PrivateKnowledgeNotFound(LookupError):
    pass


class PrivateKnowledgeRepository:
    def __init__(
        self, session: Session, *, jobs: BackgroundJobRepository | None = None
    ) -> None:
        self.session = session
        self.jobs = jobs or BackgroundJobRepository()

    def command(self, *, owner_user_id: str, command_type: str, key: str):
        return (
            self.session.execute(
                text(
                    "select command.request_fingerprint, source.* "
                    "from learning.private_knowledge_commands command "
                    "join learning.private_knowledge_source_versions source "
                    "on source.source_version_id = command.source_version_id "
                    "where command.owner_user_id = cast(:owner as uuid) "
                    "and command.command_type = :command_type "
                    "and command.idempotency_key = :key"
                ),
                {"owner": owner_user_id, "command_type": command_type, "key": key},
            )
            .mappings()
            .one_or_none()
        )

    def artifact(self, *, owner_user_id: str, artifact_version_id: UUID):
        return (
            self.session.execute(
                text(
                    "select * from learning.source_artifact_versions "
                    "where owner_user_id = cast(:owner as uuid) "
                    "and artifact_version_id = cast(:artifact as uuid) "
                    "and state = 'available'"
                ),
                {"owner": owner_user_id, "artifact": str(artifact_version_id)},
            )
            .mappings()
            .one_or_none()
        )

    def confirmed_recognition(
        self,
        *,
        owner_user_id: str,
        recognition_revision_id: UUID,
        artifact_version_id: UUID,
    ):
        return (
            self.session.execute(
                text(
                    """
                select recognition.*
                from learning.recognition_revisions recognition
                where recognition.owner_user_id = cast(:owner as uuid)
                  and recognition.recognition_revision_id = cast(:recognition as uuid)
                  and recognition.artifact_version_id = cast(:artifact as uuid)
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
                    "recognition": str(recognition_revision_id),
                    "artifact": str(artifact_version_id),
                },
            )
            .mappings()
            .one_or_none()
        )

    def head(self, *, owner_user_id: str, source_id: UUID):
        return (
            self.session.execute(
                text(
                    """
                select source.*
                from learning.private_knowledge_source_versions source
                where source.owner_user_id = cast(:owner as uuid)
                  and source.source_id = cast(:source_id as uuid)
                  and not exists (
                    select 1 from learning.private_knowledge_source_versions successor
                    where successor.supersedes_source_version_id = source.source_version_id
                  )
                """
                ),
                {"owner": owner_user_id, "source_id": str(source_id)},
            )
            .mappings()
            .one_or_none()
        )

    def insert_source(
        self,
        *,
        owner_user_id: str,
        command_type: str,
        idempotency_key: str,
        request_fingerprint: str,
        source_id: UUID,
        version_no: int,
        title: str,
        document_kind: str,
        artifact_version_id: UUID,
        recognition_revision_id: UUID | None,
        markdown: str,
        supersedes_source_version_id: UUID | None,
        pages: tuple[tuple[int, tuple[str, ...], str], ...],
        created_at: datetime,
    ) -> UUID:
        source_version_id = uuid4()
        digest = _digest(markdown)
        self.session.execute(
            text(
                """
                insert into learning.private_knowledge_source_versions (
                  source_version_id, source_id, owner_user_id, version_no, title,
                  document_kind, artifact_version_id, recognition_revision_id,
                  canonical_markdown, content_sha256, supersedes_source_version_id,
                  created_at
                ) values (
                  cast(:version_id as uuid), cast(:source_id as uuid),
                  cast(:owner as uuid), :version_no, :title, :document_kind,
                  cast(:artifact as uuid), cast(:recognition as uuid), :markdown,
                  :digest, cast(:supersedes as uuid), :created_at
                )
                """
            ),
            {
                "version_id": str(source_version_id),
                "source_id": str(source_id),
                "owner": owner_user_id,
                "version_no": version_no,
                "title": title,
                "document_kind": document_kind,
                "artifact": str(artifact_version_id),
                "recognition": str(recognition_revision_id)
                if recognition_revision_id
                else None,
                "markdown": markdown,
                "digest": digest,
                "supersedes": (
                    str(supersedes_source_version_id)
                    if supersedes_source_version_id
                    else None
                ),
                "created_at": created_at,
            },
        )
        for page_no, heading_path, content in pages:
            page_id = uuid4()
            self.session.execute(
                text(
                    """
                    insert into learning.private_knowledge_page_versions (
                      page_version_id, source_version_id, owner_user_id, page_no,
                      heading_path, content, content_sha256, created_at
                    ) values (
                      cast(:page_id as uuid), cast(:version_id as uuid),
                      cast(:owner as uuid), :page_no, cast(:headings as jsonb),
                      :content, :digest, :created_at
                    )
                    """
                ),
                {
                    "page_id": str(page_id),
                    "version_id": str(source_version_id),
                    "owner": owner_user_id,
                    "page_no": page_no,
                    "headings": json.dumps(heading_path, ensure_ascii=False),
                    "content": content,
                    "digest": _digest(content),
                    "created_at": created_at,
                },
            )
            for ordinal, (anchor, chunk) in enumerate(_chunks(content), start=1):
                chunk_id = uuid4()
                terms = sorted(_terms(chunk))
                vector = _feature_vector(terms)
                self.session.execute(
                    text(
                        """
                        insert into learning.private_knowledge_chunks (
                          chunk_id, page_version_id, source_version_id,
                          owner_user_id, ordinal, anchor, content, content_sha256,
                          created_at
                        ) values (
                          cast(:chunk_id as uuid), cast(:page_id as uuid),
                          cast(:version_id as uuid), cast(:owner as uuid),
                          :ordinal, :anchor, :content, :digest, :created_at
                        )
                        """
                    ),
                    {
                        "chunk_id": str(chunk_id),
                        "page_id": str(page_id),
                        "version_id": str(source_version_id),
                        "owner": owner_user_id,
                        "ordinal": ordinal,
                        "anchor": anchor,
                        "content": chunk,
                        "digest": _digest(chunk),
                        "created_at": created_at,
                    },
                )
                self.session.execute(
                    text(
                        """
                        insert into learning.private_knowledge_search_index (
                          index_entry_id, chunk_id, source_version_id,
                          owner_user_id, policy_version, lexical_terms,
                          feature_vector, created_at
                        ) values (
                          gen_random_uuid(), cast(:chunk_id as uuid),
                          cast(:version_id as uuid), cast(:owner as uuid),
                          :policy, cast(:terms as text[]), cast(:vector as real[]),
                          :created_at
                        )
                        """
                    ),
                    {
                        "chunk_id": str(chunk_id),
                        "version_id": str(source_version_id),
                        "owner": owner_user_id,
                        "policy": SEARCH_POLICY_VERSION,
                        "terms": terms,
                        "vector": vector,
                        "created_at": created_at,
                    },
                )
        self.session.execute(
            text(
                """
                insert into learning.private_knowledge_commands (
                  command_id, owner_user_id, command_type, idempotency_key,
                  request_fingerprint, source_version_id, created_at
                ) values (
                  gen_random_uuid(), cast(:owner as uuid), :command_type, :key,
                  :fingerprint, cast(:version_id as uuid), :created_at
                )
                """
            ),
            {
                "owner": owner_user_id,
                "command_type": command_type,
                "key": idempotency_key,
                "fingerprint": request_fingerprint,
                "version_id": str(source_version_id),
                "created_at": created_at,
            },
        )
        return source_version_id

    def summary(
        self, *, owner_user_id: str, source_version_id: UUID
    ) -> PrivateSourceSummary:
        row = (
            self.session.execute(
                text(
                    """
                select source.*,
                  (select count(*) from learning.private_knowledge_page_versions page
                   where page.source_version_id = source.source_version_id) page_count,
                  (select count(*) from learning.private_knowledge_chunks chunk
                   where chunk.source_version_id = source.source_version_id) chunk_count
                from learning.private_knowledge_source_versions source
                where source.owner_user_id = cast(:owner as uuid)
                  and source.source_version_id = cast(:version_id as uuid)
                """
                ),
                {"owner": owner_user_id, "version_id": str(source_version_id)},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise PrivateKnowledgeNotFound("private source version")
        return _summary(row)

    def list_current(self, *, owner_user_id: str) -> PrivateSourceList:
        rows = self.session.execute(
            text(
                """
                select source.*,
                  (select count(*) from learning.private_knowledge_page_versions page
                   where page.source_version_id = source.source_version_id) page_count,
                  (select count(*) from learning.private_knowledge_chunks chunk
                   where chunk.source_version_id = source.source_version_id) chunk_count
                from learning.private_knowledge_source_versions source
                where source.owner_user_id = cast(:owner as uuid)
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
                order by source.created_at desc
                """
            ),
            {"owner": owner_user_id},
        ).mappings()
        return PrivateSourceList(
            sources=tuple(_summary(row) for row in rows),
            generatedAt=datetime.now(UTC),
        )

    def search(
        self, *, owner_user_id: str, query: str, maximum_sources: int = 8
    ) -> PrivateSearchResult:
        terms = sorted(_terms(query))
        if not terms:
            return PrivateSearchResult(queryFingerprint=_digest(query), citations=())
        rows = self.session.execute(
            text(
                """
                select chunk.chunk_id, chunk.source_version_id, chunk.anchor,
                       chunk.content, page.page_no, source.source_id, source.title,
                       index.lexical_terms, index.feature_vector
                from learning.private_knowledge_search_index index
                join learning.private_knowledge_chunks chunk
                  on chunk.chunk_id = index.chunk_id
                 and chunk.owner_user_id = index.owner_user_id
                join learning.private_knowledge_page_versions page
                  on page.page_version_id = chunk.page_version_id
                 and page.owner_user_id = chunk.owner_user_id
                join learning.private_knowledge_source_versions source
                  on source.source_version_id = chunk.source_version_id
                 and source.owner_user_id = chunk.owner_user_id
                where index.owner_user_id = cast(:owner as uuid)
                  and index.policy_version = :policy
                  and index.lexical_terms && cast(:terms as text[])
                  and not exists (
                    select 1 from learning.private_knowledge_source_versions successor
                    where successor.supersedes_source_version_id = source.source_version_id
                  )
                limit 200
                """
            ),
            {"owner": owner_user_id, "policy": SEARCH_POLICY_VERSION, "terms": terms},
        ).mappings()
        query_set = set(terms)
        query_vector = _feature_vector(terms)
        ranked: list[tuple[float, PrivateCitation]] = []
        for row in rows:
            row_terms = set(row["lexical_terms"])
            lexical = len(query_set & row_terms) / len(query_set | row_terms)
            vector = _cosine(
                query_vector, [float(item) for item in row["feature_vector"]]
            )
            final = min(1.0, (0.72 * lexical) + (0.28 * vector))
            ranked.append(
                (
                    final,
                    PrivateCitation(
                        sourceId=row["source_id"],
                        sourceVersionId=row["source_version_id"],
                        chunkId=row["chunk_id"],
                        pageNo=row["page_no"],
                        anchor=row["anchor"],
                        title=str(row["title"]),
                        excerpt=" ".join(str(row["content"]).split())[:2000],
                        lexicalScore=round(lexical, 6),
                        vectorScore=round(vector, 6),
                        finalScore=round(final, 6),
                    ),
                )
            )
        ranked.sort(key=lambda item: (-item[0], str(item[1].chunk_id)))
        return PrivateSearchResult(
            queryFingerprint=_digest(query),
            citations=tuple(item[1] for item in ranked[:maximum_sources]),
        )

    def markdown_export(self, *, owner_user_id: str, source_id: UUID) -> MarkdownExport:
        row = self.head(owner_user_id=owner_user_id, source_id=source_id)
        if row is None:
            raise PrivateKnowledgeNotFound("private source")
        frontmatter = {
            "anan_source_id": str(row["source_id"]),
            "anan_source_version_id": str(row["source_version_id"]),
            "anan_version": int(row["version_no"]),
            "anan_content_sha256": str(row["content_sha256"]),
            "title": str(row["title"]),
            "document_kind": str(row["document_kind"]),
        }
        content = _export_markdown_document(str(row["canonical_markdown"]), frontmatter)
        filename = (
            re.sub(r"[\\/:*?\"<>|]+", "-", str(row["title"])).strip() or "岸岸私人资料"
        )
        return MarkdownExport(
            sourceId=row["source_id"],
            sourceVersionId=row["source_version_id"],
            versionNo=row["version_no"],
            filename=f"{filename}.md",
            content=content,
            contentSha256=_digest(content),
        )

    def request_delete(
        self, *, owner_user_id: str, source_id: UUID, idempotency_key: str
    ) -> PrivateSourceDeletionView:
        existing = (
            self.session.execute(
                text(
                    "select * from privacy.private_material_deletion_requests "
                    "where owner_user_id = cast(:owner as uuid) and idempotency_key = :key"
                ),
                {"owner": owner_user_id, "key": idempotency_key},
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if existing["source_id"] != source_id:
                raise PrivateKnowledgeConflict(
                    "deletion idempotency key changed source"
                )
            return _deletion(existing)
        rows = list(
            self.session.execute(
                text(
                    """
                    select source.source_version_id, artifact.object_ref
                    from learning.private_knowledge_source_versions source
                    join learning.source_artifact_versions artifact
                      on artifact.artifact_version_id = source.artifact_version_id
                     and artifact.owner_user_id = source.owner_user_id
                    where source.owner_user_id = cast(:owner as uuid)
                      and source.source_id = cast(:source_id as uuid)
                    order by source.version_no
                    """
                ),
                {"owner": owner_user_id, "source_id": str(source_id)},
            ).mappings()
        )
        if not rows:
            raise PrivateKnowledgeNotFound("private source")
        deletion_id = uuid4()
        object_refs = sorted({str(row["object_ref"]) for row in rows})
        now = datetime.now(UTC)
        self.session.execute(
            text(
                """
                insert into privacy.private_material_deletion_requests (
                  deletion_request_id, owner_user_id, source_id, idempotency_key,
                  state, source_version_count, object_refs, requested_at
                ) values (
                  cast(:deletion_id as uuid), cast(:owner as uuid),
                  cast(:source_id as uuid), :key, 'queued', :version_count,
                  cast(:object_refs as jsonb), :now
                ) returning *
                """
            ),
            {
                "deletion_id": str(deletion_id),
                "owner": owner_user_id,
                "source_id": str(source_id),
                "key": idempotency_key,
                "version_count": len(rows),
                "object_refs": json.dumps(object_refs),
                "now": now,
            },
        )
        self._deletion_event(
            deletion_id=deletion_id,
            owner_user_id=owner_user_id,
            phase="requested",
            evidence={"sourceVersionCount": len(rows), "objectCount": len(object_refs)},
            now=now,
        )
        self.jobs.enqueue(
            self.session,
            job_key=f"private-knowledge-delete:{deletion_id}",
            job_type=DELETE_JOB_TYPE,
            input_version="private-knowledge-delete@v1",
            payload={
                "deletionRequestId": str(deletion_id),
                "ownerUserId": owner_user_id,
                "sourceId": str(source_id),
            },
            max_attempts=5,
        )
        return self.deletion(owner_user_id=owner_user_id, deletion_id=deletion_id)

    def deletion(
        self, *, owner_user_id: str, deletion_id: UUID
    ) -> PrivateSourceDeletionView:
        row = (
            self.session.execute(
                text(
                    "select * from privacy.private_material_deletion_requests "
                    "where deletion_request_id = cast(:deletion_id as uuid) "
                    "and owner_user_id = cast(:owner as uuid)"
                ),
                {"deletion_id": str(deletion_id), "owner": owner_user_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise PrivateKnowledgeNotFound("private source deletion")
        return _deletion(row)

    def mark_delete_running(
        self, *, deletion_id: UUID, owner_user_id: str
    ) -> tuple[UUID, tuple[str, ...]]:
        row = (
            self.session.execute(
                text(
                    """
                update privacy.private_material_deletion_requests
                set state = 'running', failure_code = null
                where deletion_request_id = cast(:deletion_id as uuid)
                  and owner_user_id = cast(:owner as uuid)
                  and state in ('queued', 'failed')
                returning source_id, object_refs
                """
                ),
                {"deletion_id": str(deletion_id), "owner": owner_user_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise PrivateKnowledgeConflict("private source deletion is not claimable")
        return UUID(str(row["source_id"])), tuple(
            str(item) for item in row["object_refs"]
        )

    def complete_delete(
        self,
        *,
        deletion_id: UUID,
        owner_user_id: str,
        source_id: UUID,
        object_count: int,
        now: datetime,
    ) -> dict[str, Any]:
        self.session.execute(
            text("select set_config('app.private_material_deletion_id', :value, true)"),
            {"value": str(deletion_id)},
        )
        self.session.execute(
            text(
                "select set_config('app.private_material_owner_user_id', :value, true)"
            ),
            {"value": owner_user_id},
        )
        artifact_ids = list(
            self.session.execute(
                text(
                    "select artifact_version_id from learning.private_knowledge_source_versions "
                    "where owner_user_id = cast(:owner as uuid) "
                    "and source_id = cast(:source_id as uuid)"
                ),
                {"owner": owner_user_id, "source_id": str(source_id)},
            ).scalars()
        )
        self._deletion_event(
            deletion_id=deletion_id,
            owner_user_id=owner_user_id,
            phase="objects_deleted",
            evidence={"objectCount": object_count},
            now=now,
        )
        if artifact_ids:
            self.session.execute(
                text(
                    """
                    update learning.recognition_revisions
                    set state = 'expired', candidate_payload = '{}'::jsonb,
                        output_sha256 = repeat('0', 64), confidence = null,
                        confirmed_at = null
                    where owner_user_id = cast(:owner as uuid)
                      and artifact_version_id = any(cast(:artifacts as uuid[]))
                    """
                ),
                {
                    "owner": owner_user_id,
                    "artifacts": [str(item) for item in artifact_ids],
                },
            )
            self.session.execute(
                text(
                    """
                    update learning.content_match_versions
                    set state = 'expired', candidates = '[]'::jsonb,
                        confirmed_content_version_id = null,
                        contract_payload = '{}'::jsonb
                    where owner_user_id = cast(:owner as uuid)
                      and artifact_version_id = any(cast(:artifacts as uuid[]))
                    """
                ),
                {
                    "owner": owner_user_id,
                    "artifacts": [str(item) for item in artifact_ids],
                },
            )
        deleted_versions = self.session.execute(
            text(
                "delete from learning.private_knowledge_source_versions "
                "where owner_user_id = cast(:owner as uuid) "
                "and source_id = cast(:source_id as uuid)"
            ),
            {"owner": owner_user_id, "source_id": str(source_id)},
        ).rowcount
        if artifact_ids:
            self.session.execute(
                text(
                    """
                    update learning.source_artifact_versions
                    set state = 'deleted',
                        object_ref = 'deleted/' || artifact_version_id::text,
                        contract_payload = '{}'::jsonb
                    where owner_user_id = cast(:owner as uuid)
                      and artifact_version_id = any(cast(:artifacts as uuid[]))
                    """
                ),
                {
                    "owner": owner_user_id,
                    "artifacts": [str(item) for item in artifact_ids],
                },
            )
        evidence = {
            "sourceVersionsDeleted": int(deleted_versions or 0),
            "pagesChunksIndexesDeletedByCascade": True,
            "recognitionPayloadsErased": len(artifact_ids),
            "privateObjectsDeleted": object_count,
        }
        self._deletion_event(
            deletion_id=deletion_id,
            owner_user_id=owner_user_id,
            phase="derived_deleted",
            evidence=evidence,
            now=now,
        )
        self.session.execute(
            text(
                """
                update privacy.private_material_deletion_requests
                set state = 'completed', evidence = cast(:evidence as jsonb),
                    completed_at = :now, failure_code = null
                where deletion_request_id = cast(:deletion_id as uuid)
                  and owner_user_id = cast(:owner as uuid)
                """
            ),
            {
                "deletion_id": str(deletion_id),
                "owner": owner_user_id,
                "evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                "now": now,
            },
        )
        self._deletion_event(
            deletion_id=deletion_id,
            owner_user_id=owner_user_id,
            phase="completed",
            evidence=evidence,
            now=now,
        )
        return evidence

    def mark_delete_failed(
        self, *, deletion_id: UUID, owner_user_id: str, code: str, now: datetime
    ) -> None:
        self.session.execute(
            text(
                "update privacy.private_material_deletion_requests "
                "set state = 'failed', failure_code = :code "
                "where deletion_request_id = cast(:deletion_id as uuid) "
                "and owner_user_id = cast(:owner as uuid)"
            ),
            {"deletion_id": str(deletion_id), "owner": owner_user_id, "code": code},
        )
        self._deletion_event(
            deletion_id=deletion_id,
            owner_user_id=owner_user_id,
            phase="failed",
            evidence={"failureCode": code},
            now=now,
        )

    def requeue_delete(self, *, deletion_id: UUID, owner_user_id: str) -> None:
        self.session.execute(
            text(
                "update privacy.private_material_deletion_requests "
                "set state = 'queued', failure_code = null "
                "where deletion_request_id = cast(:deletion_id as uuid) "
                "and owner_user_id = cast(:owner as uuid) and state = 'running'"
            ),
            {"deletion_id": str(deletion_id), "owner": owner_user_id},
        )

    def _deletion_event(
        self,
        *,
        deletion_id: UUID,
        owner_user_id: str,
        phase: str,
        evidence: dict[str, Any],
        now: datetime,
    ) -> None:
        self.session.execute(
            text(
                """
                insert into privacy.private_material_deletion_events (
                  event_id, deletion_request_id, owner_user_id, phase,
                  evidence, created_at
                ) values (
                  gen_random_uuid(), cast(:deletion_id as uuid),
                  cast(:owner as uuid), :phase, cast(:evidence as jsonb), :now
                )
                """
            ),
            {
                "deletion_id": str(deletion_id),
                "owner": owner_user_id,
                "phase": phase,
                "evidence": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                "now": now,
            },
        )


def _summary(row: Any) -> PrivateSourceSummary:
    return PrivateSourceSummary(
        sourceId=row["source_id"],
        sourceVersionId=row["source_version_id"],
        versionNo=row["version_no"],
        title=row["title"],
        documentKind=row["document_kind"],
        artifactVersionId=row["artifact_version_id"],
        recognitionRevisionId=row["recognition_revision_id"],
        pageCount=row["page_count"],
        chunkCount=row["chunk_count"],
        contentSha256=row["content_sha256"],
        createdAt=row["created_at"],
    )


def _deletion(row: Any) -> PrivateSourceDeletionView:
    return PrivateSourceDeletionView(
        deletionRequestId=row["deletion_request_id"],
        sourceId=row["source_id"],
        state=row["state"],
        sourceVersionCount=row["source_version_count"],
        failureCode=row["failure_code"],
        requestedAt=row["requested_at"],
        completedAt=row["completed_at"],
    )


def _export_markdown_document(
    canonical_markdown: str, generated_metadata: dict[str, object]
) -> str:
    user_metadata: dict[str, object] = {}
    body = canonical_markdown.strip()
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end > 0:
            parsed = yaml.safe_load(body[4:end]) or {}
            if isinstance(parsed, dict):
                user_metadata = {
                    str(key): value
                    for key, value in parsed.items()
                    if isinstance(key, str) and not key.startswith("anan_")
                }
                body = body[end + 5 :].strip()
    metadata = {**user_metadata, **generated_metadata}
    header = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{header}\n---\n\n{body}\n"


def _chunks(content: str) -> tuple[tuple[str, str], ...]:
    blocks = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
    chunks: list[tuple[str, str]] = []
    current: list[str] = []
    current_length = 0
    heading = "正文"
    for block in blocks:
        if block.startswith("#"):
            heading = block.lstrip("#").strip() or heading
        if current and current_length + len(block) + 2 > 1500:
            chunks.append((heading[:300], "\n\n".join(current)))
            current = []
            current_length = 0
        if len(block) > 1500:
            for offset in range(0, len(block), 1400):
                part = block[offset : offset + 1500]
                if current:
                    chunks.append((heading[:300], "\n\n".join(current)))
                    current = []
                    current_length = 0
                chunks.append((heading[:300], part))
            continue
        current.append(block)
        current_length += len(block) + 2
    if current:
        chunks.append((heading[:300], "\n\n".join(current)))
    if not chunks:
        raise PrivateKnowledgeConflict("private source produced no indexable chunks")
    return tuple(chunks)


def _terms(value: str) -> set[str]:
    normalized = "".join(value.casefold().split())
    latin = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    grams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    return latin | grams


def _feature_vector(terms: list[str]) -> list[float]:
    vector = [0.0] * 64
    for term in terms:
        digest = sha256(term.encode("utf-8")).digest()
        index = digest[0] % 64
        vector[index] += 1.0 if digest[1] % 2 == 0 else -1.0
    magnitude = sqrt(sum(item * item for item in vector)) or 1.0
    return [round(item / magnitude, 8) for item in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    value = sum(a * b for a, b in zip(left, right, strict=True))
    return max(0.0, min(1.0, (value + 1.0) / 2.0))


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
